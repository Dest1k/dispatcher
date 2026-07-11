"""Plan validation before execution.

The planner assigns disjoint file sets to implementers. This validates that plan
so an invalid one (overlapping exclusive paths, unknown providers) triggers a
safe fallback instead of concurrent edits that would collide at integration.
"""
from __future__ import annotations


def validate_assignments(assignments: list[dict], provider_ids: set[str]) -> list[str]:
    issues: list[str] = []
    owner: dict[str, str] = {}
    for a in assignments:
        pid = a.get("provider")
        if pid not in provider_ids:
            issues.append(f"неизвестный исполнитель: {pid}")
        for path in a.get("files") or []:
            if path in owner and owner[path] != pid:
                issues.append(
                    f"файл «{path}» назначен двум исполнителям ({owner[path]} и {pid})")
            owner[path] = pid
    return issues
