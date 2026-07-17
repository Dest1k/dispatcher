"""Task planning engine: a phased execution plan generated before any work.

Realizes the vision's TASK PLANNING ENGINE — «Before execution: Generate
plan». The plan is a fixed, sensible sequence of phases (analyze → design →
implement → test → security → review, plus release when a remote exists), each
assigned to the best-fit agent via the explainable routing engine, and — best
effort — enriched with a one-line, task-specific detail by the lead model. It
is deterministic without any model call, so it is safe and testable; the model
only sharpens the wording.

The security phase bakes the VERIFICATION LOOP's «security check» into the plan
and is deliberately routed to a red-team-capable agent that is not the primary
implementer, honoring «no agent approves its own critical changes».
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import capabilities
from .capabilities import ROLE_TITLES_RU
from .providers import make_adapter
from .providers.base import Message
from .routing import route

# (phase id, role, default detail). Order matters — it is the execution order.
_PHASES = [
    ("analyze", "researcher",
     "Проанализировать текущий код, требования и ограничения"),
    ("design", "architect",
     "Спроектировать решение, зафиксировать компромиссы и план внедрения"),
    ("implement", "developer",
     "Реализовать изменения в своей зоне ответственности"),
    ("test", "developer",
     "Написать и прогнать автоматические тесты, покрыть краевые случаи"),
    ("security", "red_team",
     "Проверка безопасности: уязвимости, утечки секретов, поверхность атаки"),
    ("review", "reviewer",
     "Независимое ревью: баги, риски, соответствие требованиям, вердикт"),
]
_RELEASE = ("release", "reviewer",
            "Подготовить публикацию: интеграционная ветка, дифф и свидетельства "
            "для одобрения человеком")

_PHASE_RU = {"analyze": "анализ", "design": "проектирование",
             "implement": "реализация", "test": "тестирование",
             "security": "безопасность", "review": "ревью",
             "release": "релиз"}

_ROLES = ("researcher", "architect", "developer", "red_team", "reviewer")


@dataclass
class PlanStep:
    phase: str
    role: str
    provider_id: str
    provider_label: str
    detail: str

    def to_dict(self) -> dict:
        return {"phase": self.phase, "role": self.role,
                "provider": self.provider_id, "label": self.provider_label,
                "detail": self.detail}


@dataclass
class TaskPlan:
    task: str
    steps: list[PlanStep] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    enriched_by: str = ""

    def to_dict(self) -> dict:
        return {"task": self.task, "enriched_by": self.enriched_by,
                "steps": [s.to_dict() for s in self.steps], "notes": self.notes}

    def to_markdown(self) -> str:
        lines = ["# План выполнения", "", f"**Задача:** {self.task}", ""]
        for i, s in enumerate(self.steps, 1):
            phase = _PHASE_RU.get(s.phase, s.phase)
            role = ROLE_TITLES_RU.get(s.role, s.role)
            lines.append(f"{i}. **{phase}** — {s.detail}")
            lines.append(f"   · исполнитель: {s.provider_label} ({role})")
        if self.enriched_by:
            lines += ["", f"_Детали уточнены моделью: {self.enriched_by}_"]
        for n in self.notes:
            lines += ["", f"> {n}"]
        return "\n".join(lines)


def _phase_list(has_remote: bool) -> list[tuple[str, str, str]]:
    return _PHASES + ([_RELEASE] if has_remote else [])


def _enrich(task: str, phases, lead: dict, adapter_factory, workdir: str
            ) -> tuple[dict[str, str], str]:
    """Best-effort per-phase detail tailored to the task. Returns (details, by).
    Any failure falls back to the template details silently."""
    if not lead:
        return {}, ""
    ids = [p[0] for p in phases]
    system = ("Ты — ведущий архитектор. Для КАЖДОЙ фазы дай одну конкретную "
              "строку — что именно сделать для этой задачи. Ответь строго JSON: "
              '{"analyze": "…", "design": "…", ...}. Только перечисленные ключи.')
    prompt = (f"Задача:\n{task}\n\nФазы: {', '.join(ids)}\n"
              "Верни JSON с деталями по каждой фазе.")
    cfg = dict(lead)
    if workdir:
        cfg["workdir"] = workdir
    try:
        res = adapter_factory(cfg).complete(system, [Message("user", text=prompt)], [])
        start, end = res.text.find("{"), res.text.rfind("}")
        data = json.loads(res.text[start:end + 1]) if start != -1 else {}
        details = {k: str(v).strip() for k, v in data.items()
                   if k in ids and str(v).strip()}
        return details, (lead.get("label", lead.get("id", "")) if details else "")
    except Exception:
        return {}, ""


def generate_plan(task: str, providers: list[dict], lead: dict | None = None,
                  reputation=None, has_remote: bool = False,
                  adapter_factory=make_adapter, workdir: str = "",
                  enrich: bool = True) -> TaskPlan:
    """Build a phased, agent-assigned plan. Deterministic without a model call;
    the lead only enriches per-phase wording when `enrich` is on."""
    plan = TaskPlan(task=task)
    if not providers:
        plan.notes.append("нет активных провайдеров — план не сформирован")
        return plan

    phases = _phase_list(has_remote)
    decision = route(task, providers, roles=_ROLES, reputation=reputation)
    by_role = {a.role: a for a in decision.assignments}
    lead = lead or providers[0]

    details, enriched_by = ({}, "")
    if enrich:
        details, enriched_by = _enrich(task, phases, lead, adapter_factory, workdir)
    plan.enriched_by = enriched_by

    for phase, role, default_detail in phases:
        a = by_role.get(role)
        pid = a.provider_id if a else lead["id"]
        label = a.provider_label if a else lead.get("label", lead["id"])
        plan.steps.append(PlanStep(
            phase=phase, role=role, provider_id=pid, provider_label=label,
            detail=details.get(phase, default_detail)))

    # «No agent secures/reviews its own critical code»: actively reassign the
    # security and review phases away from the implementer to the best-fit
    # OTHER provider; only note it when there is genuinely no alternative.
    impl = next((s.provider_id for s in plan.steps if s.phase == "implement"), None)
    for s in plan.steps:
        if s.phase not in ("security", "review") or s.provider_id != impl:
            continue
        others = [p for p in providers if p["id"] != impl]
        if not others:
            plan.notes.append(
                f"фаза «{_PHASE_RU[s.phase]}» у исполнителя {s.provider_label} — "
                "нет другого участника для независимой проверки")
            continue
        best = max(others, key=lambda p: capabilities.role_score(
            p.get("model", ""), s.role)[0])
        s.provider_id = best["id"]
        s.provider_label = best.get("label", best["id"])
    return plan
