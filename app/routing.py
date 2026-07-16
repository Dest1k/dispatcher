"""Adaptive task routing: which agent takes which role — and *why*.

score(provider, role) = role-weighted capability priors (capabilities.py)
                        × bounded reputation multiplier (reputation.py)

Every decision carries a human-readable explanation; nothing here is a black
box. Distinct providers are preferred across roles when the fit is close, so
a council does not collapse into one model reviewing itself.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import capabilities
from .capabilities import ROLE_TITLES_RU

# Task classification: keyword patterns (RU + EN) -> extra skill emphasis.
_TASK_PATTERNS: list[tuple[str, str, dict[str, float]]] = [
    ("рефакторинг", r"refactor|рефактор", {"refactoring": 0.15, "architecture": 0.10}),
    ("исправление бага", r"\bbug\b|\bfix\b|ошибк|почин|баг|crash|падает",
     {"debugging": 0.15, "testing": 0.10}),
    ("тестирование", r"\btest|тест", {"testing": 0.15}),
    ("безопасность", r"secur|безопасн|уязвим|auth|аутентифика|crypto|шифр",
     {"security_review": 0.15, "red_team": 0.10}),
    ("исследование", r"research|исследуй|исследован|сравни|изучи|analy[sz]e|анализ",
     {"research": 0.15, "alternative": 0.10}),
    ("документация", r"\bdoc|документ|readme", {"documentation": 0.15}),
    ("архитектура", r"architect|архитектур|спроектируй|design|дизайн",
     {"architecture": 0.15, "large_context": 0.05}),
    ("реализация", r"implement|реализуй|добавь|создай|feature|фич",
     {"coding": 0.10}),
]


def classify_task(task: str) -> tuple[list[str], dict[str, float]]:
    """(detected kinds, extra skill emphasis). Explainable and deterministic."""
    text = (task or "").lower()
    kinds: list[str] = []
    emphasis: dict[str, float] = {}
    for kind, pattern, extra in _TASK_PATTERNS:
        if re.search(pattern, text):
            kinds.append(kind)
            for skill, w in extra.items():
                emphasis[skill] = emphasis.get(skill, 0.0) + w
    return kinds, emphasis


@dataclass
class RoleAssignment:
    role: str
    provider_id: str
    provider_label: str
    score: float
    explanation: str

    def to_dict(self) -> dict:
        return {"role": self.role, "provider": self.provider_id,
                "label": self.provider_label, "score": round(self.score, 3),
                "explanation": self.explanation}


@dataclass
class RoutingDecision:
    task: str
    kinds: list[str] = field(default_factory=list)
    assignments: list[RoleAssignment] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def by_role(self, role: str) -> RoleAssignment | None:
        return next((a for a in self.assignments if a.role == role), None)

    def to_dict(self) -> dict:
        return {"task": self.task, "kinds": self.kinds,
                "assignments": [a.to_dict() for a in self.assignments],
                "notes": self.notes}

    def describe(self) -> str:
        """Russian, human-readable decision trace."""
        lines = ["## Маршрутизация задачи", f"Задача: {self.task}"]
        lines.append("Определённый тип: "
                     + (", ".join(self.kinds) if self.kinds else "общая задача"))
        for a in self.assignments:
            role_ru = ROLE_TITLES_RU.get(a.role, a.role)
            lines.append(f"- **{role_ru}** → {a.provider_label} "
                         f"(счёт {a.score:.2f}): {a.explanation}")
        lines.extend(f"> {n}" for n in self.notes)
        return "\n".join(lines)


def _provider_role_score(provider: dict, role: str,
                         emphasis: dict[str, float],
                         reputation) -> tuple[float, str]:
    model = provider.get("model", "")
    base, parts = capabilities.role_score(model, role)
    bonus = 0.0
    bonus_parts = []
    for skill, w in emphasis.items():
        s, _ = capabilities.score(model, skill)
        bonus += w * s
        bonus_parts.append(f"{skill} +{w * s:.2f}")
    mult = reputation.multiplier(provider.get("id", "")) if reputation else 1.0
    total = (base + bonus) * mult
    explanation = f"способности {base:.2f} ({', '.join(parts)})"
    if bonus_parts:
        explanation += f" · акцент задачи: {', '.join(bonus_parts)}"
    if reputation is not None and mult != 1.0:
        explanation += (f" · репутация ×{mult} "
                        f"({reputation.explain(provider.get('id', ''))})")
    return total, explanation


def route(task: str, providers: list[dict],
          roles: tuple[str, ...] = ("architect", "developer", "reviewer"),
          reputation=None, distinct: bool = True) -> RoutingDecision:
    """Assign a provider to every role, explainably.

    Providers is the *active* provider list (dicts from config). With
    `distinct` (default), a provider already holding a role is only reused
    when no free provider remains — independent roles are the point of a
    council; the trade-off is recorded in the notes.
    """
    decision = RoutingDecision(task=task)
    kinds, emphasis = classify_task(task)
    decision.kinds = kinds
    if not providers:
        decision.notes.append("нет активных провайдеров — маршрутизация невозможна")
        return decision

    used: set[str] = set()
    for role in roles:
        scored: list[tuple[float, dict, str]] = []
        for p in providers:
            s, expl = _provider_role_score(p, role, emphasis, reputation)
            scored.append((s, p, expl))
        scored.sort(key=lambda t: -t[0])
        best_s, best_p, _ = scored[0]
        free = [t for t in scored if t[1]["id"] not in used]
        pick = free[0] if (distinct and free) else scored[0]
        s, p, expl = pick
        if p["id"] != best_p["id"]:
            decision.notes.append(
                f"для роли «{ROLE_TITLES_RU.get(role, role)}» лучший счёт у "
                f"{best_p.get('short', best_p['id'])} ({best_s:.2f}), но он уже "
                f"занят — роль отдана свободному {p.get('short', p['id'])} "
                f"({s:.2f}), чтобы роли совета оставались независимыми")
        elif p["id"] in used:
            decision.notes.append(
                f"роль «{ROLE_TITLES_RU.get(role, role)}» повторно у "
                f"{p.get('short', p['id'])} — свободных участников не осталось")
        used.add(p["id"])
        decision.assignments.append(RoleAssignment(
            role=role, provider_id=p["id"],
            provider_label=p.get("label", p["id"]), score=s, explanation=expl))
    if len(providers) == 1:
        decision.notes.append("активен только один провайдер — все роли у него")
    return decision
