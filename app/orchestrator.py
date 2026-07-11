"""Run controller.

Safe flow for one task:
  1. Record base commit; refuse to run on a dirty source tree by default.
  2. Select a team by execution mode (solo / pair / full_council); plan.
  3. Each implementer works in its OWN git worktree (never the source tree),
     with file ownership enforced by a PathPolicy and commands run in a sandbox
     (no secrets, killable).
  4. Collect each agent's patch; apply patches sequentially into an integration
     worktree (explicit staging, no `git add -A`, no branch reset).
  5. Optional cross-review of the integrated diff.
  6. Deterministic verification (real commands, exit codes).
  7. Emit report + diff + verification evidence and WAIT for human approval.
     Nothing is committed or pushed automatically. Failed/unknown/cancelled
     verification blocks publication. On approval, commit the integration branch
     and (only if asked and allowed) push that branch — never the target branch.

Runs on its own QThread; the source repository is fully recoverable after any
cancelled or failed run.
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .providers import Steering, Usage, make_adapter, run_agent
from .providers.base import Message
from .sandbox import make_sandbox
from .tools import TOOL_SPECS, ProjectTools
from .verification import detect_commands, run_verification
from .workspace import RunWorkspaces, WorkspaceError

_IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
                 "build", ".mypy_cache", ".pytest_cache", ".idea", ".vscode"}

# Files an agent must never read or write, regardless of assignment.
SECRET_DENY = [".env", ".env.*", "*.pem", "*.key", "id_rsa*", "id_ed25519*",
               "secrets/*", ".ssh/*", ".git/*", ".aws/*", "*.p12"]


def build_context(root: str, max_entries: int = 220) -> str:
    if not Path(root).exists():
        return "(локальная папка не найдена)"
    files: list[str] = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _IGNORED_DIRS)
        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth > 4:
            dirnames[:] = []
            continue
        for fn in sorted(filenames):
            rp = fn if rel == "." else os.path.join(rel, fn).replace(os.sep, "/")
            try:
                size = os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                size = 0
            files.append(f"{rp} ({size}б)")
            if len(files) >= max_entries:
                truncated = True
                break
        if truncated:
            break
    if truncated:
        files.append("...(список обрезан)")
    return "\n".join(files) or "(пустой репозиторий)"


def _extract_json(text: str) -> dict | None:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            candidate = text[start:end + 1]
    if candidate is None:
        return None
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


class _Cancel:
    def __init__(self, *events):
        self._events = events

    def is_set(self) -> bool:
        return any(e.is_set() for e in self._events)


class Orchestrator(QThread):
    plan_ready = Signal(dict)
    agent_role = Signal(str, dict)
    agent_event = Signal(str, str, str)
    log = Signal(str)
    integration_ready = Signal(dict)      # {changed_files, conflicts, diff}
    verification_ready = Signal(dict)     # VerificationResult.to_dict()
    report_ready = Signal(str, dict)      # report md, usage
    awaiting_approval = Signal(dict)      # {status, can_autopublish, ...}
    run_finished = Signal(dict)
    run_error = Signal(str)

    def __init__(self, config, project: dict, instruction: str):
        super().__init__()
        self.config = config
        self.project = project
        self.instruction = instruction
        self.steering = Steering()
        self.cancel_event = threading.Event()
        self.providers = [dict(p) for p in config.active_providers()]
        self.orch = dict(config.orchestration)
        self._usage: dict[str, Usage] = {}
        self.agent_cancels: dict[str, threading.Event] = {}
        self.assignments: dict[str, dict] = {}
        self.disabled: set[str] = set()
        self.live_ids: set[str] = set()
        self.rw: RunWorkspaces | None = None
        self._decision = threading.Event()
        self._decision_value: dict = {}
        self._report = ""
        self._verification: dict = {}
        self._published_branch: str | None = None

    # ---- external controls ------------------------------------------
    def add_steering(self, text: str) -> None:
        self.steering.add(text)
        self.log.emit("→ Инструкция отправлена работающим агентам")

    def cancel(self) -> None:
        self.cancel_event.set()
        for ev in self.agent_cancels.values():
            ev.set()
        self._decision_value = {"action": "reject"}
        self._decision.set()
        self.log.emit("⏹ Останавливаю работу…")

    def disable_agent(self, provider_id: str) -> None:
        if provider_id in self.disabled or provider_id not in self.live_ids:
            return
        ev = self.agent_cancels.get(provider_id)
        if ev is None:
            return
        self.disabled.add(provider_id)
        self.live_ids.discard(provider_id)
        ev.set()
        # Reassign to ONE designated agent (not a broadcast "everyone take over").
        taker = next((pid for pid in self.live_ids), None)
        prov = self._cfg(provider_id).get("short", provider_id)
        if taker:
            assignment = self.assignments.get(provider_id, {})
            files = ", ".join(assignment.get("files") or []) or "её файлы"
            taker_name = self._cfg(taker).get("short", taker)
            self.steering.add(
                f"Модель «{prov}» отключена. Её задачу ({assignment.get('objective','')}, "
                f"файлы: {files}) берёт на себя ТОЛЬКО «{taker_name}». Остальные — не дублируйте.")
            self.log.emit(f"⛔ {prov} отключён — задачу берёт {taker_name}")
        else:
            self.log.emit(f"⛔ {prov} отключён — других исполнителей нет")
        self.agent_event.emit(provider_id, "status", "отключён пользователем")

    def approve(self, push: bool = False) -> None:
        self._decision_value = {"action": "approve", "push": push}
        self._decision.set()

    def reject(self) -> None:
        self._decision_value = {"action": "reject"}
        self._decision.set()

    # ---- helpers ----------------------------------------------------
    def _emit_agent(self, pid: str):
        return lambda kind, payload: self.agent_event.emit(pid, kind, payload)

    def _cfg(self, pid: str) -> dict:
        return next((p for p in self.providers if p["id"] == pid), {"id": pid})

    def _lead_provider(self) -> dict:
        pref = self.orch.get("lead_provider", "anthropic")
        return next((p for p in self.providers if p["id"] == pref), self.providers[0])

    def _select_team(self):
        mode = self.orch.get("execution_mode", "pair")
        active = self.providers
        lead = self._lead_provider()
        ordered = [lead] + [p for p in active if p["id"] != lead["id"]]
        if len(active) == 1 or mode == "solo":
            return [ordered[0]], None
        if mode == "full_council":
            return active, lead
        # pair / adaptive
        return [ordered[0]], ordered[1]

    # ---- run --------------------------------------------------------
    def run(self) -> None:
        try:
            self._run_inner()
        except WorkspaceError as exc:
            self.run_error.emit(f"Ошибка рабочей области: {exc}")
            self._safe_cleanup()
        except Exception as exc:
            self.run_error.emit(f"Сбой оркестратора: {exc}")
            self._safe_cleanup()

    def _run_inner(self) -> None:
        root = self.project.get("local_path", "")
        if not root or not Path(root).exists():
            self.run_error.emit("Локальная папка проекта не найдена.")
            return
        if not self.providers:
            self.run_error.emit("Не настроен ни один ИИ. Добавь API-ключи.")
            return

        self.rw = RunWorkspaces(root)
        try:
            info = self.rw.prepare()
        except WorkspaceError as exc:
            self.run_error.emit(
                f"{exc}. Инициализируй git и сделай первый коммит, затем повтори.")
            return
        if info["dirty"]:
            self.run_error.emit(
                "В рабочем дереве есть незакоммиченные изменения. Закоммить или "
                "убери их — Dispatcher не трогает и не коммитит чужие правки.")
            return

        context = build_context(root)
        implementers, reviewer = self._select_team()
        self.log.emit(
            f"Режим: {self.orch.get('execution_mode', 'pair')} · "
            f"исполнители: {', '.join(p['short'] for p in implementers)}"
            + (f" · ревьюер: {reviewer['short']}" if reviewer else ""))

        assignments = self._plan(context, implementers)
        if self.cancel_event.is_set():
            return self._finish({"status": "cancelled", "message": "Остановлено."})

        summaries = self._execute(implementers, assignments, context)
        if self.cancel_event.is_set():
            return self._finish({"status": "cancelled", "message": "Остановлено."})

        # Integration
        conflicts = self._integrate(implementers)
        integ = {
            "changed_files": self.rw.integration_changed_files(),
            "conflicts": conflicts,
            "diff": self.rw.integration_diff()[:20000],
        }
        self.integration_ready.emit(integ)

        # Review (optional)
        review_notes = self._review(reviewer, integ["diff"]) if reviewer else ""

        # Verification
        verification = self._verify(root)
        self._verification = verification.to_dict()
        self.verification_ready.emit(self._verification)

        # Report (stored as an artifact OUTSIDE the target repo)
        report = self._compose_report(summaries, review_notes, integ, verification)
        self._report = report
        self.report_ready.emit(report, self._usage_summary())
        self._save_report_artifact(report)

        can_autopublish = not (
            self.orch.get("require_verification", True)
            and verification.blocks_publication())
        self.awaiting_approval.emit({
            "status": verification.status,
            "can_autopublish": can_autopublish,
            "changed_files": integ["changed_files"],
            "conflicts": conflicts,
            "verification": self._verification,
        })

        # Wait for the human decision.
        self._decision.wait()
        if self._decision_value.get("action") != "approve":
            self.rw.cleanup()          # discard all worktrees + temp branches
            return self._finish({
                "status": "cancelled",
                "message": "Публикация отклонена. Исходный репозиторий не тронут."})
        self._publish(push=self._decision_value.get("push", False),
                      blocked=not can_autopublish)

    # ---- planning ---------------------------------------------------
    def _plan(self, context: str, implementers: list[dict]) -> dict[str, dict]:
        fallback = {p["id"]: {"provider": p["id"], "role": p["strength"],
                              "title": p["short"], "objective": self.instruction,
                              "files": []} for p in implementers}
        if len(implementers) == 1 or self.orch.get("mode") != "lead":
            self.plan_ready.emit({"overview": "Один исполнитель работает над задачей.",
                                  "assignments": list(fallback.values())})
            return fallback

        lead = self._lead_provider()
        self.log.emit(f"{lead['short']} распределяет роли…")
        roster = "\n".join(f"- {p['id']}: {p['label']} — {p['strength']}"
                           for p in implementers)
        system = ("Ты — ведущий архитектор. Раздели задачу между исполнителями с "
                  "НЕПЕРЕСЕКАЮЩИМИСЯ наборами файлов (каждый работает в своей "
                  "изолированной ветке). Ответь строго одним JSON-объектом.")
        prompt = (f"Задача:\n{self.instruction}\n\nИсполнители:\n{roster}\n\n"
                  f"Структура проекта:\n{context}\n\n"
                  'JSON: {"overview": "...", "assignments": [{"provider": "<id>", '
                  '"role": "...", "title": "...", "objective": "...", '
                  '"files": ["путь"]}]}')
        try:
            result = make_adapter(lead).complete(system, [Message("user", text=prompt)], [])
            self._usage.setdefault(lead["id"], Usage()).add(result.usage)
            plan = _extract_json(result.text)
        except Exception as exc:
            self.log.emit(f"Планирование не удалось ({exc}) — безопасный откат: один исполнитель.")
            plan = None

        if not plan or "assignments" not in plan:
            # SAFE fallback: a single executor, NOT unsafe full parallelism.
            solo = implementers[0]
            single = {solo["id"]: fallback[solo["id"]]}
            self.plan_ready.emit({
                "overview": "Планирование не удалось — безопасный откат к одному исполнителю.",
                "assignments": list(single.values())})
            return single

        by_id = {a.get("provider"): a for a in plan.get("assignments", []) if a.get("provider")}
        merged = {}
        for p in implementers:
            a = by_id.get(p["id"]) or fallback[p["id"]]
            a.setdefault("role", p["strength"])
            a.setdefault("title", p["short"])
            a.setdefault("objective", self.instruction)
            a.setdefault("files", [])
            merged[p["id"]] = a
        self.plan_ready.emit({"overview": plan.get("overview", ""),
                              "assignments": list(merged.values())})
        return merged

    # ---- execution --------------------------------------------------
    def _execute(self, implementers, assignments, context) -> dict[str, str]:
        self.assignments = assignments
        summaries: dict[str, str] = {}
        worktrees = {}
        # Create worktrees serially (git worktree add must not race).
        for p in implementers:
            pid = p["id"]
            if pid not in assignments:
                continue
            allowed = assignments[pid].get("files") or None
            ws = self.rw.create_agent_worktree(pid, allowed_paths=allowed,
                                               denied=SECRET_DENY)
            worktrees[pid] = ws
            self._usage[pid] = Usage()
            self.live_ids.add(pid)
            self.agent_cancels[pid] = threading.Event()

        threads = []
        for p in implementers:
            pid = p["id"]
            if pid not in worktrees:
                continue
            self.agent_role.emit(pid, assignments[pid])
            t = threading.Thread(target=self._run_agent_in_worktree,
                                 args=(p, assignments[pid], worktrees[pid],
                                       context, summaries), daemon=True)
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        # Collect patches from each isolated worktree.
        self._patches = {}
        for pid, ws in worktrees.items():
            try:
                self._patches[pid] = ws.stage_and_diff()
            except WorkspaceError as exc:
                self._patches[pid] = ""
                self.log.emit(f"Патч {pid} не собран: {exc}")
        return summaries

    def _run_agent_in_worktree(self, provider, assignment, ws, context, summaries):
        pid = provider["id"]
        on_event = self._emit_agent(pid)
        on_event("status", "работает")
        combined = _Cancel(self.cancel_event, self.agent_cancels[pid])
        sandbox = make_sandbox(self.orch.get("sandbox_mode", "restricted"),
                               str(ws.path),
                               allow_network=self.orch.get("allow_network", False))
        try:
            tools = ProjectTools(str(ws.path), policy=ws.policy, sandbox=sandbox,
                                 cancel=combined)
            files_hint = ", ".join(assignment.get("files") or []) or "(в пределах твоей зоны)"
            system = (f"Ты — {provider['label']}. Работаешь в СВОЕЙ изолированной "
                      "копии репозитория. Пиши качественный рабочий код только в "
                      "своей зоне ответственности; попытки записи вне зоны вернут "
                      "ошибку. Команды выполняются в песочнице без доступа к "
                      "секретам и сети. Заверши вызовом finish с кратким отчётом.")
            initial = Message("user", text=(
                f"Задача:\n{self.instruction}\n\nТвоя роль: {assignment.get('role')}\n"
                f"Подзадача: {assignment.get('objective')}\nТвои файлы: {files_hint}\n\n"
                f"Структура проекта:\n{context}\n\nНачинай."))
            final = run_agent(make_adapter(provider), system, [initial], TOOL_SPECS,
                              tools.execute, self.steering, on_event,
                              int(self.orch.get("max_tool_iterations", 14)),
                              combined, self._usage[pid])
            summaries[pid] = final
            self.live_ids.discard(pid)
            on_event("status", "отключён" if pid in self.disabled else "готово")
        except Exception as exc:
            summaries[pid] = f"(ошибка: {exc})"
            self.live_ids.discard(pid)
            on_event("error", str(exc))
            on_event("status", "ошибка")
        finally:
            sandbox.close()

    # ---- integration ------------------------------------------------
    def _integrate(self, implementers) -> list[dict]:
        self.rw.create_integration_worktree()
        conflicts = []
        for p in implementers:
            pid = p["id"]
            patch = getattr(self, "_patches", {}).get(pid, "")
            if not patch.strip():
                continue
            ok, msg = self.rw.apply_patch(patch)
            if not ok:
                conflicts.append({"provider": pid, "message": msg[:400]})
                self.log.emit(f"Конфликт интеграции {self._cfg(pid)['short']}: {msg[:120]}")
        return conflicts

    # ---- review -----------------------------------------------------
    def _review(self, reviewer, diff) -> str:
        if not diff.strip():
            return "(нет изменений для ревью)"
        self.log.emit(f"{reviewer['short']} проверяет объединённый дифф…")
        system = ("Ты — независимый ревьюер. Проверь дифф на баги, риски и "
                  "нарушения требований. Кратко перечисли найденное и вердикт.")
        try:
            res = make_adapter(reviewer).complete(
                system, [Message("user", text=f"Задача:\n{self.instruction}\n\n"
                                              f"Дифф:\n{diff[:16000]}")], [])
            self._usage.setdefault(reviewer["id"], Usage()).add(res.usage)
            self.agent_event.emit(reviewer["id"], "text", res.text[:1500])
            return res.text
        except Exception as exc:
            return f"(ревью не выполнено: {exc})"

    # ---- verification -----------------------------------------------
    def _verify(self, root):
        commands = self.project.get("verify_commands") or detect_commands(
            str(self.rw.integration_path))
        if commands:
            self.log.emit("Проверка: " + ", ".join(c["name"] for c in commands))
        return run_verification(str(self.rw.integration_path), commands,
                                mode=self.orch.get("sandbox_mode", "restricted"),
                                allow_network=self.orch.get("allow_network", False),
                                cancel=self.cancel_event)

    # ---- report -----------------------------------------------------
    def _compose_report(self, summaries, review_notes, integ, verification) -> str:
        lead = self._lead_provider()
        agent_parts = "\n\n".join(
            f"### {self._cfg(pid)['label']}\n{summ}" for pid, summ in summaries.items())
        vlines = "\n".join(f"- **{c.name}**: {c.status} ({c.summary})"
                           for c in verification.checks) or "- (проверок нет)"
        conflicts = "\n".join(f"- {c['provider']}: {c['message']}"
                              for c in integ["conflicts"]) or "- нет"
        base = (
            f"# Отчёт по задаче\n\n**Задача:** {self.instruction}\n\n"
            f"## Что сделали исполнители\n{agent_parts}\n\n"
            f"## Ревью\n{review_notes or '(без ревью)'}\n\n"
            f"## Интеграция\nИзменённые файлы: "
            f"{', '.join(integ['changed_files']) or 'нет'}\n\nКонфликты:\n{conflicts}\n\n"
            f"## Верификация — статус: **{verification.status}** (риск: {verification.risk})\n"
            f"{vlines}\n")
        # Optional lead synthesis on top (best-effort).
        try:
            res = make_adapter(lead).complete(
                "Ты — ведущий архитектор. Сделай краткое резюме (2-4 предложения) "
                "по результатам работы, честно отметив риски и что НЕ проверено.",
                [Message("user", text=base[:14000])], [])
            self._usage.setdefault(lead["id"], Usage()).add(res.usage)
            if res.text.strip():
                base = f"# Отчёт по задаче\n\n{res.text}\n\n" + base[len("# Отчёт по задаче\n\n"):]
        except Exception:
            pass
        return base + "\n\n" + self._usage_summary()["table"]

    def _usage_summary(self) -> dict:
        rows = ["| Модель | Вход | Выход | Стоимость | Оплата |", "|---|---|---|---|---|"]
        total_cost = total_in = total_out = 0
        for pid, usage in self._usage.items():
            p = self._cfg(pid)
            cost = usage.input_tokens / 1e6 * p.get("price_in", 0) \
                + usage.output_tokens / 1e6 * p.get("price_out", 0)
            total_cost += cost
            total_in += usage.input_tokens
            total_out += usage.output_tokens
            billing = {"api": "API", "subscription": p.get("subscription_tier") or "подписка",
                       "local": "локально"}.get(p.get("billing_source"), "—")
            rows.append(f"| {p.get('short', pid)} | {usage.input_tokens} | "
                        f"{usage.output_tokens} | ${cost:.4f} | {billing} |")
        rows.append(f"| **Итого (API)** | {total_in} | {total_out} | **${total_cost:.4f}** | |")
        return {"table": "## Расход токенов и стоимость\n" + "\n".join(rows),
                "total_cost": total_cost, "input_tokens": total_in,
                "output_tokens": total_out}

    def _save_report_artifact(self, report: str) -> None:
        try:
            from .config import CONFIG_DIR
            reports = CONFIG_DIR / "reports"
            reports.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = reports / f"report-{self.rw.run_id}-{stamp}.md"
            path.write_text(report, encoding="utf-8")
            self.log.emit(f"Отчёт сохранён (вне репозитория): {path}")
        except OSError:
            pass

    # ---- publish ----------------------------------------------------
    def _publish(self, push: bool, blocked: bool) -> None:
        result = {"status": "done", "branch": self.rw.integration_branch,
                  "commit": None, "pushed": False, "message": ""}
        try:
            first = self._report.strip().splitlines()
            title = (self.orch.get("commit_prefix", "") +
                     (first[0].lstrip("# ").strip() if first else "Работа консилиума"))[:100]
            commit = self.rw.commit_integration(title or "Работа консилиума")
            result["commit"] = commit
            if commit is None:
                result["message"] = "Изменений для коммита нет."
                self.rw.cleanup()
                return self._finish(result)
            self._published_branch = self.rw.integration_branch
            self.log.emit(f"Коммит {commit} в ветку {self.rw.integration_branch}")
            if push and blocked:
                result["message"] = (f"Коммит {commit} создан в ветке "
                                     f"{self.rw.integration_branch}. Пуш отменён: "
                                     "верификация не пройдена.")
            elif push:
                self.rw.push_integration(self.project.get("github_url", ""),
                                         self.project.get("github_token", ""))
                result["pushed"] = True
                result["message"] = (f"Коммит {commit} запушен в ветку "
                                     f"{self.rw.integration_branch}. Открой из неё PR "
                                     "(в целевую ветку напрямую не пушим).")
            else:
                result["message"] = (f"Коммит {commit} в локальной ветке "
                                     f"{self.rw.integration_branch}. Пуш не запрашивался.")
        except Exception as exc:
            result["status"] = "partial"
            result["message"] = f"Работа выполнена, но git-операция не удалась: {exc}"
        # keep the integration branch (published), drop agent temp branches + worktrees
        self.rw.cleanup(keep=[self.rw.integration_branch])
        self._finish(result)

    def _finish(self, result: dict) -> None:
        self.run_finished.emit(result)

    def _safe_cleanup(self) -> None:
        try:
            if self.rw is not None:
                keep = [self.rw.integration_branch] if self._published_branch else []
                self.rw.cleanup(keep=keep)
        except Exception:
            pass
