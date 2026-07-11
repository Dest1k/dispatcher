"""The orchestration engine.

Flow for one task:
  1. Build a snapshot of the project (file tree).
  2. LEAD mode: the lead model (Claude by default) splits the work ~evenly
     across the available models, assigning a role and a non-overlapping set of
     files to each. AUTO mode: every model gets the whole task and coordinates
     loosely.
  3. All models run concurrently as tool-using agents inside the local repo.
  4. The lead composes a single Markdown report from everyone's summaries + the
     git diff.
  5. Commit everything and push to the project's branch.

Runs on its own QThread; progress is streamed to the UI via signals. Mid-run
user instructions are broadcast to every running agent via a Steering queue.
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from . import git_service
from .providers import Steering, Usage, make_adapter, run_agent
from .providers.base import Message
from .tools import TOOL_SPECS, ProjectTools

_IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
                 "build", ".mypy_cache", ".pytest_cache", ".idea", ".vscode"}


class _Cancel:
    """A cancel view that is set if the global stop OR this agent's stop fires."""

    def __init__(self, *events):
        self._events = events

    def is_set(self) -> bool:
        return any(e.is_set() for e in self._events)


def build_context(root: str, max_entries: int = 220) -> str:
    """A compact, cheap file listing so models understand the project layout.

    Uses os.walk with in-place pruning of ignored directories so huge trees
    (node_modules, .git, …) are never traversed.
    """
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
    """Robustly pull a JSON object out of a model response."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            candidate = text[start:end + 1]
    if candidate is None:
        return None
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


class Orchestrator(QThread):
    plan_ready = Signal(dict)
    agent_role = Signal(str, dict)             # provider_id, {title, role}
    agent_event = Signal(str, str, str)        # provider_id, kind, payload
    log = Signal(str)
    report_ready = Signal(str, dict)           # markdown report, usage summary
    run_finished = Signal(dict)                # {status, branch, commit, pushed, message}
    run_error = Signal(str)

    def __init__(self, config, project: dict, instruction: str):
        super().__init__()
        self.config = config
        self.project = project
        self.instruction = instruction
        self.steering = Steering()
        self.cancel_event = threading.Event()
        # snapshot providers so a settings change mid-run can't corrupt the run
        self.providers = [dict(p) for p in config.active_providers()]
        self.orch = dict(config.orchestration)
        self._usage: dict[str, Usage] = {}
        self.agent_cancels: dict[str, threading.Event] = {}
        self.assignments: dict[str, dict] = {}
        self.disabled: set[str] = set()
        self.live_ids: set[str] = set()
        # populated during the agent phase, used for hot add/remove
        self._context = ""
        self._summaries: dict[str, str] = {}
        self._max_iters = 24
        self._agent_threads: list[threading.Thread] = []
        self._agents_lock = threading.Lock()
        self._agents_phase = False

    # ---- external controls ------------------------------------------
    def add_steering(self, text: str) -> None:
        self.steering.add(text)
        self.log.emit("→ Инструкция отправлена работающим ИИ")

    def cancel(self) -> None:
        self.cancel_event.set()
        for ev in self.agent_cancels.values():
            ev.set()
        self.log.emit("⏹ Останавливаю работу…")

    def disable_agent(self, provider_id: str) -> None:
        """Turn off one model mid-run; the rest take over its work."""
        with self._agents_lock:
            if provider_id in self.disabled or provider_id not in self.live_ids:
                return
            ev = self.agent_cancels.get(provider_id)
            if ev is None:
                return
            self.disabled.add(provider_id)
            self.live_ids.discard(provider_id)
            ev.set()
            assignment = self.assignments.get(provider_id, {})
            provider = self._provider_cfg(provider_id)
            remaining = [self._provider_cfg(pid).get("short", pid)
                         for pid in self.live_ids]
        files = ", ".join(assignment.get("files") or []) or "все её файлы"
        objective = assignment.get("objective", "её часть задачи")
        self.agent_event.emit(provider_id, "status", "отключён пользователем")
        self.log.emit(f"⛔ {provider.get('short', provider_id)} отключён — "
                      f"работу подхватывают: {', '.join(remaining) or 'никто'}")
        # Broadcast the takeover to every still-running agent.
        self.steering.add(
            f"Модель «{provider.get('short', provider_id)}» отключена пользователем. "
            f"Возьмите на себя её работу. Её подзадача: {objective}. "
            f"Её файлы: {files}. Доведите эту часть до конца вместе со своей."
        )

    def add_agent(self, provider_cfg: dict) -> bool:
        """Hot-join a model to the running council. Returns True if it joined."""
        pid = provider_cfg["id"]
        with self._agents_lock:
            if not self._agents_phase:
                return False
            if pid in self.live_ids:
                return False
            self.disabled.discard(pid)
            self.live_ids.add(pid)
            if not any(p["id"] == pid for p in self.providers):
                self.providers.append(dict(provider_cfg))
            assignment = self.assignments.get(pid) or {
                "provider": pid,
                "role": provider_cfg.get("strength", ""),
                "title": provider_cfg.get("short", pid),
                "objective": self.instruction,
                "files": [],
            }
            self.assignments[pid] = assignment
            usage = self._usage.setdefault(pid, Usage())
            joining_late = True
            self._spawn_agent(provider_cfg, assignment, usage, joining_late)
        provider = self._provider_cfg(pid)
        self.log.emit(f"➕ {provider.get('short', pid)} подключился к консилиуму")
        self.steering.add(
            f"К работе подключилась модель «{provider.get('short', pid)}». "
            "Скоординируйтесь, чтобы не дублировать усилия."
        )
        return True

    def _provider_cfg(self, pid: str) -> dict:
        return next((p for p in self.providers if p["id"] == pid), {"id": pid})

    def _spawn_agent(self, provider: dict, assignment: dict, usage: Usage,
                     joining_late: bool = False) -> None:
        """Create + start one agent thread. Caller holds _agents_lock."""
        pid = provider["id"]
        self.agent_role.emit(pid, assignment)
        agent_cancel = threading.Event()
        self.agent_cancels[pid] = agent_cancel
        combined = _Cancel(self.cancel_event, agent_cancel)
        thread = threading.Thread(
            target=self._run_one_agent,
            args=(provider, assignment, self._context, self._summaries, usage,
                  self._max_iters, combined, joining_late),
            daemon=True,
        )
        self._agent_threads.append(thread)
        thread.start()

    # ---- helpers ----------------------------------------------------
    def _emit_agent(self, provider_id: str):
        def cb(kind: str, payload: str) -> None:
            self.agent_event.emit(provider_id, kind, payload)
        return cb

    def _lead_provider(self) -> dict:
        pref = self.orch.get("lead_provider", "anthropic")
        for p in self.providers:
            if p["id"] == pref:
                return p
        return self.providers[0]

    # ---- the run ----------------------------------------------------
    def run(self) -> None:  # executes on the orchestrator thread
        try:
            self._run_inner()
        except Exception as exc:  # never let the thread die silently
            self.run_error.emit(f"Сбой оркестратора: {exc}")

    def _run_inner(self) -> None:
        root = self.project["local_path"]
        if not root or not Path(root).exists():
            self.run_error.emit("Локальная папка проекта не найдена. Проверь путь или клонируй репозиторий.")
            return
        if not self.providers:
            self.run_error.emit("Не настроен ни один ИИ. Добавь API-ключи в настройках.")
            return

        context = build_context(root)
        self.log.emit(f"Активны модели: {', '.join(p['short'] for p in self.providers)}")

        # 1) Planning ------------------------------------------------
        assignments = self._plan(context)
        if self.cancel_event.is_set():
            self._finish_cancelled()
            return

        # 2) Concurrent agents (dynamic council) --------------------
        self.assignments = assignments
        self._context = context
        self._summaries = summaries = {}
        self._max_iters = int(self.orch.get("max_tool_iterations", 24))

        with self._agents_lock:
            self._agents_phase = True
            for provider in self.providers:
                assignment = assignments[provider["id"]]
                usage = Usage()
                self._usage[provider["id"]] = usage
                self.live_ids.add(provider["id"])
                self._spawn_agent(provider, assignment, usage)

        # Wait until every agent thread finishes — including any that were
        # hot-added mid-run. New threads are appended to _agent_threads under
        # the lock, so this loop naturally picks them up.
        while True:
            with self._agents_lock:
                pending = [t for t in self._agent_threads if t.is_alive()]
                if not pending:
                    self._agents_phase = False
                    break
            for thread in pending:
                thread.join(timeout=0.3)

        if self.cancel_event.is_set():
            self._finish_cancelled()
            return

        # 3) Report --------------------------------------------------
        self.log.emit("Собираю единый отчёт…")
        report = self._compose_report(summaries, root)
        usage_summary = self._usage_summary()
        report += "\n\n" + usage_summary["table"]
        self.report_ready.emit(report, usage_summary)

        # Persist the report as a file so it becomes part of the deliverable.
        report_path = self._write_report_file(root, report)
        if report_path:
            self.log.emit(f"Отчёт сохранён: {report_path}")

        # 4) Commit & push ------------------------------------------
        self._git_finalize(root, report)

    def _write_report_file(self, root: str, report: str) -> str | None:
        try:
            reports_dir = Path(root) / "ai-reports"
            reports_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = reports_dir / f"report-{stamp}.md"
            path.write_text(report, encoding="utf-8")
            return str(path.relative_to(root))
        except OSError:
            return None

    # ---- step 1: plan ----------------------------------------------
    def _plan(self, context: str) -> dict[str, dict]:
        ids = [p["id"] for p in self.providers]
        fallback = self._fallback_assignments()

        if self.orch.get("mode") != "lead" or len(self.providers) == 1:
            self.plan_ready.emit({"overview": "Режим «каждый сам»: модели работают параллельно над общей задачей.",
                                  "assignments": fallback})
            return {a["provider"]: a for a in fallback}

        lead = self._lead_provider()
        self.log.emit(f"{lead['short']} распределяет роли…")
        roster = "\n".join(
            f"- {p['id']}: {p['label']} — {p['strength']}" for p in self.providers
        )
        system = (
            "Ты — ведущий архитектор и самый разумный из троицы ИИ. "
            "Тебе дана задача пользователя. Раздели её примерно поровну между "
            "доступными моделями. Назначь каждой понятную роль, конкретную "
            "подзадачу и НЕПЕРЕСЕКАЮЩИЙСЯ набор файлов, чтобы избежать конфликтов. "
            "Ответь СТРОГО одним JSON-объектом без пояснений."
        )
        prompt = (
            f"Задача пользователя:\n{self.instruction}\n\n"
            f"Доступные модели:\n{roster}\n\n"
            f"Структура проекта:\n{context}\n\n"
            "Верни JSON вида:\n"
            "{\n"
            '  "overview": "краткий план в 1-3 предложениях",\n'
            '  "assignments": [\n'
            '    {"provider": "<id>", "role": "...", "title": "...", '
            '"objective": "что именно сделать", "files": ["путь1", "путь2"]}\n'
            "  ]\n"
            "}\n"
            f"Обязательно по одному назначению на каждый id из: {', '.join(ids)}."
        )
        try:
            adapter = make_adapter(lead)
            result = adapter.complete(system, [Message("user", text=prompt)], [])
            self._usage.setdefault(lead["id"], Usage()).add(result.usage)
            plan = _extract_json(result.text)
        except Exception as exc:
            self.log.emit(f"Планирование не удалось ({exc}); работаю по равному делению.")
            plan = None

        if not plan or "assignments" not in plan:
            plan = {"overview": "Равное деление задачи.", "assignments": fallback}

        by_id = {a.get("provider"): a for a in plan.get("assignments", []) if a.get("provider")}
        # make sure every active provider has an assignment
        merged: dict[str, dict] = {}
        for provider in self.providers:
            a = by_id.get(provider["id"]) or next(
                (f for f in fallback if f["provider"] == provider["id"]), None)
            a.setdefault("role", provider["strength"])
            a.setdefault("title", provider["short"])
            a.setdefault("objective", self.instruction)
            a.setdefault("files", [])
            merged[provider["id"]] = a

        self.plan_ready.emit({"overview": plan.get("overview", ""),
                              "assignments": list(merged.values())})
        return merged

    def _fallback_assignments(self) -> list[dict]:
        return [
            {
                "provider": p["id"],
                "role": p["strength"],
                "title": p["short"],
                "objective": self.instruction,
                "files": [],
            }
            for p in self.providers
        ]

    # ---- step 2: one agent -----------------------------------------
    def _run_one_agent(self, provider, assignment, context, summaries, usage,
                       max_iters, cancel, joining_late=False):
        pid = provider["id"]
        on_event = self._emit_agent(pid)
        on_event("status", "работает")
        try:
            tools = ProjectTools(self.project["local_path"])
            adapter = make_adapter(provider)
            files_hint = ", ".join(assignment.get("files") or []) or "(на твоё усмотрение, но не трогай чужие файлы)"
            system = (
                f"Ты — {provider['label']}, один из сильнейших ИИ планеты, "
                "работающий над проектом в составе консилиума моделей. "
                "У тебя есть инструменты для чтения, записи и редактирования файлов "
                "репозитория и запуска команд. Пиши качественный рабочий код. "
                "Работай в рамках своей зоны ответственности и НЕ переписывай "
                "файлы, назначенные другим. Действуй самостоятельно и доведи "
                "свою часть до конца. Когда закончишь — вызови инструмент finish "
                "с кратким отчётом на русском о том, что ты сделал."
            )
            join_note = (
                "\n\nВНИМАНИЕ: ты подключаешься к УЖЕ ИДУЩЕЙ работе. Сначала изучи "
                "текущее состояние файлов (что уже сделано другими), чтобы ничего "
                "не сломать и не дублировать, затем помоги довести задачу до конца.\n"
                if joining_late else ""
            )
            initial = Message(
                "user",
                text=(
                    f"Общая задача:\n{self.instruction}\n\n"
                    f"Твоя роль: {assignment.get('role')}\n"
                    f"Твоя подзадача: {assignment.get('objective')}\n"
                    f"Твои файлы: {files_hint}"
                    f"{join_note}\n\n"
                    f"Структура проекта:\n{context}\n\n"
                    "Начинай: изучи нужные файлы и внеси изменения."
                ),
            )
            final = run_agent(
                adapter=adapter,
                system=system,
                initial_messages=[initial],
                tools=TOOL_SPECS,
                tool_executor=tools.execute,
                steering=self.steering,
                on_event=on_event,
                max_iters=max_iters,
                cancel=cancel,
                total_usage=usage,
            )
            summaries[pid] = final
            self.live_ids.discard(pid)
            on_event("status", "отключён" if pid in self.disabled else "готово")
        except Exception as exc:
            summaries[pid] = f"(ошибка: {exc})"
            self.live_ids.discard(pid)
            on_event("error", str(exc))
            on_event("status", "ошибка")

    # ---- step 3: report --------------------------------------------
    def _compose_report(self, summaries: dict[str, str], root: str) -> str:
        try:
            diff = git_service.diff_stat(root)
            files = git_service.changed_files(root)
        except Exception as exc:
            diff, files = f"(git недоступен: {exc})", []

        parts = []
        for provider in self.providers:
            pid = provider["id"]
            parts.append(f"### {provider['label']}\n{summaries.get(pid, '(нет отчёта)')}")
        joined = "\n\n".join(parts)

        lead = self._lead_provider()
        system = (
            "Ты — ведущий архитектор. Составь единый структурированный отчёт о "
            "проделанной работе трёх ИИ на русском языке в Markdown: краткое "
            "резюме, что сделала каждая модель, изменённые файлы и рекомендации "
            "по дальнейшим шагам. Пиши по делу."
        )
        prompt = (
            f"Задача пользователя:\n{self.instruction}\n\n"
            f"Отчёты моделей:\n{joined}\n\n"
            f"git diff --stat:\n{diff}\n\n"
            f"Изменённые файлы:\n{chr(10).join(files) or '(нет)'}"
        )
        try:
            adapter = make_adapter(lead)
            result = adapter.complete(system, [Message("user", text=prompt)], [])
            self._usage.setdefault(lead["id"], Usage()).add(result.usage)
            if result.text.strip():
                return result.text
        except Exception as exc:
            self.log.emit(f"Синтез отчёта не удался ({exc}); собираю базовый отчёт.")

        # Fallback report if the lead call failed.
        header = "# Отчёт по задаче\n\n" + f"**Задача:** {self.instruction}\n\n"
        return header + joined + f"\n\n## Изменённые файлы\n```\n{diff}\n```"

    def _usage_summary(self) -> dict:
        rows = ["| Модель | Вход (ток.) | Выход (ток.) | Стоимость |",
                "|---|---|---|---|"]
        total_cost = 0.0
        total_in = total_out = 0
        by_provider = {p["id"]: p for p in self.providers}
        for pid, usage in self._usage.items():
            provider = by_provider.get(pid) or self.config.providers.get(pid, {})
            price_in = provider.get("price_in", 0.0)
            price_out = provider.get("price_out", 0.0)
            cost = usage.input_tokens / 1e6 * price_in + usage.output_tokens / 1e6 * price_out
            total_cost += cost
            total_in += usage.input_tokens
            total_out += usage.output_tokens
            rows.append(f"| {provider.get('short', pid)} | {usage.input_tokens} | "
                        f"{usage.output_tokens} | ${cost:.4f} |")
        rows.append(f"| **Итого** | {total_in} | {total_out} | **${total_cost:.4f}** |")
        table = "## Расход токенов и стоимость\n" + "\n".join(rows)
        return {"table": table, "total_cost": total_cost,
                "input_tokens": total_in, "output_tokens": total_out}

    # ---- step 4: git -----------------------------------------------
    def _git_finalize(self, root: str, report: str) -> None:
        result = {"status": "done", "branch": self.project.get("branch", "main"),
                  "commit": None, "pushed": False, "message": ""}
        try:
            if not git_service.has_repo(root):
                git_service.init_repo(root)
            branch = self.project.get("branch") or git_service.current_branch(root) or "main"
            git_service.ensure_branch(root, branch)
            first_line = report.strip().splitlines()[0].lstrip("# ").strip()
            prefix = self.orch.get("commit_prefix", "")
            title = (prefix + first_line)[:100] or "Работа трёх ИИ"
            commit = git_service.commit_all(root, title)
            result["branch"] = branch
            result["commit"] = commit
            if commit is None:
                result["message"] = "Изменений для коммита нет."
                self.run_finished.emit(result)
                return
            self.log.emit(f"Коммит {commit} в ветку {branch}")
            if self.orch.get("auto_push", True):
                git_service.push(root, branch,
                                 self.project.get("github_url", ""),
                                 self.project.get("github_token", ""))
                result["pushed"] = True
                self.log.emit(f"✔ Запушено в {branch}")
                result["message"] = f"Коммит {commit} запушен в ветку {branch}."
            else:
                result["message"] = f"Коммит {commit} создан (пуш выключен)."
        except Exception as exc:
            result["status"] = "partial"
            result["message"] = f"Работа выполнена, но git-операция не удалась: {exc}"
            self.log.emit(result["message"])
        self.run_finished.emit(result)

    def _finish_cancelled(self) -> None:
        self.run_finished.emit({"status": "cancelled", "branch": self.project.get("branch"),
                                "commit": None, "pushed": False,
                                "message": "Задача остановлена пользователем."})
