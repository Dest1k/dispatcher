"""Task DAG: a validated dependency graph of implementation subtasks.

The lead planner may produce sub-tasks with dependencies (e.g. "add API" needs
"add model" first). This module validates that graph and schedules it into
ordered *layers* of independent tasks:

  * unknown dependencies and unknown providers are rejected;
  * cycles are rejected (a DAG must be acyclic);
  * tasks that run **concurrently** (same layer) must own disjoint file zones —
    reused from `planning.zones_overlap`, the same PathPolicy-aware check the
    single-pass planner uses. Tasks in *different* layers may touch the same
    files, because they run sequentially and each layer sees the previous
    layer's integrated result.

Pure logic, no I/O — the orchestrator turns valid layers into isolated
worktree rounds.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .planning import zones_overlap


@dataclass
class TaskNode:
    id: str
    provider: str
    title: str
    objective: str
    files: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"id": self.id, "provider": self.provider, "title": self.title,
                "objective": self.objective, "files": list(self.files),
                "depends_on": list(self.depends_on)}


def _parse(nodes: list) -> list[TaskNode]:
    out: list[TaskNode] = []
    for n in nodes:
        if isinstance(n, TaskNode):
            out.append(n)
            continue
        out.append(TaskNode(
            id=str(n.get("id") or "").strip(),
            provider=str(n.get("provider") or "").strip(),
            title=str(n.get("title") or n.get("id") or "").strip(),
            objective=str(n.get("objective") or "").strip(),
            files=list(n.get("files") or []),
            depends_on=[str(d).strip() for d in (n.get("depends_on") or [])]))
    return out


def validate_dag(nodes: list, provider_ids: set[str]) -> list[str]:
    """Return a list of problems; empty means the DAG is safe to schedule."""
    tasks = _parse(nodes)
    issues: list[str] = []
    ids = [t.id for t in tasks]
    seen: set[str] = set()

    for t in tasks:
        if not t.id:
            issues.append("есть задача без идентификатора")
        elif t.id in seen:
            issues.append(f"повторяющийся идентификатор задачи: {t.id}")
        seen.add(t.id)
        if t.provider and t.provider not in provider_ids:
            issues.append(f"задача «{t.id}»: неизвестный исполнитель {t.provider}")

    id_set = set(ids)
    for t in tasks:
        for dep in t.depends_on:
            if dep not in id_set:
                issues.append(f"задача «{t.id}» зависит от неизвестной «{dep}»")
            elif dep == t.id:
                issues.append(f"задача «{t.id}» зависит сама от себя")

    # Cycle detection is only meaningful once deps are known.
    if not issues:
        try:
            layers = topological_layers(tasks)
        except ValueError as exc:
            issues.append(str(exc))
            return issues
        # Concurrent (same-layer) tasks must own disjoint zones.
        for depth, layer in enumerate(layers):
            zones = [(t.id, f) for t in layer for f in (t.files or [""])]
            for i in range(len(zones)):
                for j in range(i + 1, len(zones)):
                    (id_a, pa), (id_b, pb) = zones[i], zones[j]
                    if id_a != id_b and zones_overlap(pa or "", pb or ""):
                        issues.append(
                            f"параллельные задачи «{id_a}» и «{id_b}» (слой "
                            f"{depth + 1}) пересекаются по файлам")
    return issues


def topological_layers(nodes: list) -> list[list[TaskNode]]:
    """Group tasks into ordered layers (Kahn's algorithm). Each layer holds
    tasks whose dependencies are all satisfied by earlier layers. Raises
    ValueError on a cycle."""
    tasks = _parse(nodes)
    by_id = {t.id: t for t in tasks}
    remaining = {t.id: set(d for d in t.depends_on if d in by_id) for t in tasks}
    layers: list[list[TaskNode]] = []
    placed: set[str] = set()

    while remaining:
        ready = [tid for tid, deps in remaining.items() if deps <= placed]
        if not ready:
            cyclic = ", ".join(sorted(remaining))
            raise ValueError(f"в графе задач обнаружен цикл: {cyclic}")
        # deterministic order within a layer
        layer = [by_id[tid] for tid in sorted(ready)]
        layers.append(layer)
        placed |= set(ready)
        for tid in ready:
            remaining.pop(tid, None)
    return layers


def describe(layers: list[list[TaskNode]]) -> str:
    """Russian, human-readable schedule."""
    lines = ["## Граф задач (по слоям исполнения)"]
    for depth, layer in enumerate(layers, 1):
        lines.append(f"Слой {depth} (параллельно):")
        for t in layer:
            deps = f" ← {', '.join(t.depends_on)}" if t.depends_on else ""
            files = ", ".join(t.files) or "(в своей зоне)"
            lines.append(f"  • [{t.provider}] {t.title}{deps} — файлы: {files}")
    return "\n".join(lines)
