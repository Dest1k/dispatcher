"""Model capability registry: what each model family is actually good at.

Scores are 0..1 **expert priors** (provenance recorded, never presented as
benchmark facts) that seed the routing engine; the reputation system then
adjusts them with *measured* outcomes on this user's real tasks. Unknown
models get a neutral profile and an honest "no data" provenance instead of
invented numbers — the same philosophy as `catalog.py`.
"""
from __future__ import annotations

from dataclasses import dataclass

# The capability axes Dispatcher tracks (the routing engine and the UI use
# exactly these keys).
SKILLS = [
    "coding", "architecture", "debugging", "research", "creativity",
    "security_review", "documentation", "testing", "refactoring",
    "red_team", "large_context", "alternative",
]

# Agent roles -> skill weights used to score a provider for a role.
ROLE_SKILLS: dict[str, dict[str, float]] = {
    "architect": {"architecture": 0.50, "large_context": 0.20,
                  "refactoring": 0.15, "documentation": 0.15},
    "developer": {"coding": 0.55, "debugging": 0.25, "testing": 0.20},
    "reviewer": {"security_review": 0.35, "testing": 0.25,
                 "debugging": 0.25, "architecture": 0.15},
    "researcher": {"research": 0.60, "alternative": 0.25, "creativity": 0.15},
    "red_team": {"red_team": 0.60, "security_review": 0.25, "alternative": 0.15},
}

ROLE_TITLES_RU = {
    "architect": "архитектор",
    "developer": "разработчик",
    "reviewer": "ревьюер",
    "researcher": "исследователь",
    "red_team": "red team",
}


@dataclass
class CapabilityProfile:
    family: str               # model-id prefix this profile applies to
    scores: dict[str, float]
    verified_at: str
    source: str
    note: str = ""


def _p(family: str, note: str, **scores: float) -> CapabilityProfile:
    return CapabilityProfile(
        family=family, scores=scores, verified_at="2026-07-17",
        source="экспертные приоры Dispatcher v3 (корректируются репутацией)",
        note=note)


# Longest-prefix match wins, so "claude-opus" beats "claude".
_REGISTRY: list[CapabilityProfile] = [
    _p("claude-opus",
       "сильнейшая архитектура/рефакторинг, большой контекст",
       coding=0.88, architecture=0.95, debugging=0.85, research=0.75,
       creativity=0.80, security_review=0.85, documentation=0.90,
       testing=0.80, refactoring=0.92, red_team=0.65, large_context=0.95,
       alternative=0.70),
    _p("opus",
       "алиас Claude Opus в Claude Code CLI",
       coding=0.88, architecture=0.95, debugging=0.85, research=0.75,
       creativity=0.80, security_review=0.85, documentation=0.90,
       testing=0.80, refactoring=0.92, red_team=0.65, large_context=0.95,
       alternative=0.70),
    _p("claude",
       "семейство Claude (sonnet/haiku): универсал",
       coding=0.82, architecture=0.85, debugging=0.80, research=0.70,
       creativity=0.75, security_review=0.78, documentation=0.85,
       testing=0.75, refactoring=0.82, red_team=0.60, large_context=0.90,
       alternative=0.65),
    _p("gpt-5.6-sol",
       "фронтир-модель для агентного кодинга (Codex CLI)",
       coding=0.95, architecture=0.82, debugging=0.92, research=0.78,
       creativity=0.75, security_review=0.80, documentation=0.80,
       testing=0.92, refactoring=0.85, red_team=0.70, large_context=0.80,
       alternative=0.75),
    _p("gpt",
       "семейство GPT: сильная реализация и отладка",
       coding=0.90, architecture=0.78, debugging=0.88, research=0.75,
       creativity=0.72, security_review=0.75, documentation=0.78,
       testing=0.88, refactoring=0.80, red_team=0.65, large_context=0.75,
       alternative=0.70),
    _p("grok",
       "исследование, red team, альтернативное мышление",
       coding=0.78, architecture=0.75, debugging=0.75, research=0.90,
       creativity=0.88, security_review=0.85, documentation=0.70,
       testing=0.70, refactoring=0.70, red_team=0.95, large_context=0.85,
       alternative=0.92),
]

_NEUTRAL = 0.5


def profile_for(model_id: str) -> CapabilityProfile | None:
    """Longest matching family prefix, or None when nothing is known."""
    mid = (model_id or "").strip().lower()
    best: CapabilityProfile | None = None
    for prof in _REGISTRY:
        if mid.startswith(prof.family) and (
                best is None or len(prof.family) > len(best.family)):
            best = prof
    return best


def score(model_id: str, skill: str) -> tuple[float, str]:
    """(score, provenance). Unknown model/skill -> neutral 0.5, honest label."""
    prof = profile_for(model_id)
    if prof is None:
        return _NEUTRAL, "нет данных о модели — нейтральная оценка 0.5"
    if skill not in prof.scores:
        return _NEUTRAL, f"навык «{skill}» не оценён — нейтральная 0.5"
    return prof.scores[skill], f"{prof.source} · проверено {prof.verified_at}"


def role_score(model_id: str, role: str) -> tuple[float, list[str]]:
    """Weighted role fit + a per-skill breakdown for explainability."""
    weights = ROLE_SKILLS.get(role)
    if not weights:
        return _NEUTRAL, [f"роль «{role}» не описана — нейтральная 0.5"]
    total, parts = 0.0, []
    for skill, w in weights.items():
        s, _ = score(model_id, skill)
        total += w * s
        parts.append(f"{skill} {s:.2f}×{w:.2f}")
    return total, parts


def describe(model_id: str) -> str:
    """Honest one-liner for the UI/CLI."""
    prof = profile_for(model_id)
    if prof is None:
        return "нет данных о способностях — используется нейтральный профиль"
    top = sorted(prof.scores.items(), key=lambda kv: -kv[1])[:3]
    tops = ", ".join(f"{k} {v:.2f}" for k, v in top)
    return f"{prof.note}; сильное: {tops} ({prof.source})"
