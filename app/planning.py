"""Plan validation before execution.

The planner assigns disjoint file sets to implementers. This validates that plan
so an invalid one (overlapping exclusive zones, unknown providers) triggers a
safe fallback instead of concurrent edits that would collide at integration.

Ownership is enforced by `PathPolicy`, whose matching is prefix/glob based: a
zone `src` also covers `src/main.py`. So "disjoint" must be checked with those
same semantics — two agents owning `src` and `src/main.py` is an overlap even
though the strings differ. Otherwise both agents' policies would permit writes
to the same file and their patches would collide at integration.
"""
from __future__ import annotations

import fnmatch

_GLOB_CHARS = "*?["


def _norm(pattern: str) -> str:
    """Normalize a zone pattern to a comparable path (drop trailing / and /*)."""
    p = pattern.strip().rstrip("/")
    if p.endswith("/*"):
        p = p[:-2]
    return p.rstrip("/")


def _within(prefix: str, path: str) -> bool:
    """True if `path` is `prefix` itself or lies under directory `prefix`,
    matching on path components so `src` does not swallow `src2`."""
    return path == prefix or path.startswith(prefix + "/")


def zones_overlap(a: str, b: str) -> bool:
    """Whether two ownership patterns could both match a common file.

    Mirrors PathPolicy's prefix/glob semantics closely enough to catch the
    plans that would actually collide: identical zones, directory containment
    in either direction, and a glob on one side matching the other's literal
    zone.
    """
    an, bn = _norm(a), _norm(b)
    if not an or not bn:
        # an empty zone means "everything under root" -> overlaps with anything
        return True
    if an == bn:
        return True
    a_glob = any(c in an for c in _GLOB_CHARS)
    b_glob = any(c in bn for c in _GLOB_CHARS)
    if not a_glob and not b_glob:
        return _within(an, bn) or _within(bn, an)
    # At least one side is a glob: test each glob against the other side's
    # literal form (and, for directory globs like "src/*", its base dir).
    if a_glob and fnmatch.fnmatch(bn, an):
        return True
    if b_glob and fnmatch.fnmatch(an, bn):
        return True
    return False


def validate_assignments(assignments: list[dict], provider_ids: set[str]) -> list[str]:
    issues: list[str] = []
    # (pid, pattern) list for cross-owner overlap detection
    zones: list[tuple[str, str]] = []
    for a in assignments:
        pid = a.get("provider")
        if pid not in provider_ids:
            issues.append(f"неизвестный исполнитель: {pid}")
        for path in a.get("files") or []:
            for other_pid, other_path in zones:
                if other_pid == pid:
                    continue
                if zones_overlap(path, other_path):
                    if path == other_path:
                        issues.append(
                            f"файл «{path}» назначен двум исполнителям "
                            f"({other_pid} и {pid})")
                    else:
                        issues.append(
                            f"зоны «{other_path}» ({other_pid}) и «{path}» ({pid}) "
                            f"пересекаются")
            zones.append((pid, path))
    return issues
