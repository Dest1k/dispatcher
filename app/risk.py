"""Deterministic risk scoring for an integrated change.

Looks only at observable properties of the diff (paths, size, deletions,
test presence) — no model calls, fully explainable. The score feeds the
approval dialog and the run report; it never replaces verification or the
human gate, it informs them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Path fragments that make a change security- or infrastructure-sensitive.
_SENSITIVE = [
    ("auth/креды", r"auth|login|session|credential|password|token|secret"),
    ("криптография", r"crypt|cipher|signing|certificate"),
    ("CI/CD и сборка", r"\.github/workflows|Dockerfile|Jenkinsfile|\.gitlab-ci"),
    ("зависимости", r"requirements.*\.txt|pyproject\.toml|package(-lock)?\.json|"
                    r"poetry\.lock|go\.mod|Cargo\.toml"),
    ("политики безопасности", r"security|pathpolicy|sandbox|redact"),
]

_CODE_EXT = (".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java",
             ".c", ".cc", ".cpp", ".h", ".cs", ".rb", ".php")


@dataclass
class RiskFactor:
    name: str
    detail: str
    weight: float

    def to_dict(self) -> dict:
        return {"name": self.name, "detail": self.detail, "weight": self.weight}


@dataclass
class RiskAssessment:
    level: str = "low"                      # low | medium | high
    score: float = 0.0
    factors: list[RiskFactor] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"level": self.level, "score": round(self.score, 2),
                "factors": [f.to_dict() for f in self.factors]}

    def describe(self) -> str:
        label = {"low": "низкий", "medium": "средний", "high": "высокий"}[self.level]
        lines = [f"Риск изменений: **{label}** (счёт {self.score:.2f})"]
        lines.extend(f"- {f.name} (+{f.weight:.2f}): {f.detail}"
                     for f in self.factors)
        if not self.factors:
            lines.append("- факторов риска не обнаружено")
        return "\n".join(lines)


def _diff_stats(diff_text: str) -> tuple[int, int]:
    added = deleted = 0
    for line in diff_text.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            deleted += 1
    return added, deleted


def assess_change(diff_text: str, changed_files: list[str]) -> RiskAssessment:
    a = RiskAssessment()
    files = [f.replace("\\", "/") for f in changed_files]
    lower = [f.lower() for f in files]

    for label, pattern in _SENSITIVE:
        hits = [f for f, lf in zip(files, lower) if re.search(pattern, lf)]
        if hits:
            a.factors.append(RiskFactor(
                f"чувствительные пути: {label}",
                ", ".join(hits[:4]) + ("…" if len(hits) > 4 else ""), 0.30))

    added, deleted = _diff_stats(diff_text)
    total = added + deleted
    if total > 1500:
        a.factors.append(RiskFactor("очень большой дифф",
                                    f"{added}+ / {deleted}−", 0.45))
    elif total > 400:
        a.factors.append(RiskFactor("большой дифф",
                                    f"{added}+ / {deleted}−", 0.25))

    if len(files) > 10:
        a.factors.append(RiskFactor("много файлов", f"{len(files)} файлов", 0.15))

    if deleted > 50 and deleted > 3 * max(added, 1):
        a.factors.append(RiskFactor("преобладают удаления",
                                    f"{deleted}− против {added}+", 0.20))

    code_changed = any(f.lower().endswith(_CODE_EXT) for f in files)
    tests_changed = any("test" in f.lower() for f in files)
    if code_changed and not tests_changed:
        a.factors.append(RiskFactor("код без тестов",
                                    "изменён код, но ни одного тестового файла",
                                    0.15))

    a.score = round(sum(f.weight for f in a.factors), 2)
    a.level = "low" if a.score < 0.25 else ("medium" if a.score < 0.6 else "high")
    return a
