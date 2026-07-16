"""Persistent project memory as a graph, not a log.

Nodes are typed facts (decisions, bugs, solutions, approaches, lessons,
tasks); edges are typed, *reasoned* relations (`solved_by`, `caused_by`,
`supersedes`, `rejected_for`, …). The orchestrator records run outcomes here
and injects a compact digest (`context_pack`) into agent prompts, so the
system remembers **why** decisions were made, what failed, and which
approaches were already rejected — across restarts.

SQLite-backed (own file next to dispatcher.db), thread-safe, no ORM.
"""
from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

NODE_KINDS = ("decision", "bug", "solution", "approach", "lesson", "task")
NODE_STATUSES = ("active", "rejected", "superseded")
REL_KINDS = ("relates_to", "caused_by", "solved_by", "supersedes",
             "rejected_for", "learned_from")

_KIND_RU = {"decision": "решение", "bug": "проблема", "solution": "решение-фикс",
            "approach": "подход", "lesson": "урок", "task": "задача"}
_REL_RU = {"relates_to": "связано с", "caused_by": "вызвано",
           "solved_by": "решено через", "supersedes": "заменяет",
           "rejected_for": "отклонено в пользу", "learned_from": "урок из"}


def _default_path() -> Path:
    from .config import CONFIG_DIR
    return CONFIG_DIR / "memory.db"


@dataclass
class MemoryNode:
    id: str
    project_id: str
    kind: str
    title: str
    body: str = ""
    status: str = "active"
    run_id: str = ""
    created_at: float = 0.0

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class MemoryEdge:
    src: str
    dst: str
    rel: str
    reason: str = ""
    created_at: float = 0.0


class MemoryGraph:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else _default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS nodes (
                    id TEXT PRIMARY KEY, project_id TEXT, kind TEXT,
                    title TEXT, body TEXT, status TEXT, run_id TEXT,
                    created_at REAL)""")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS edges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    src TEXT, dst TEXT, rel TEXT, reason TEXT, created_at REAL)""")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_nodes_project ON nodes(project_id)")

    # ---- writes -------------------------------------------------------
    def add_node(self, project_id: str, kind: str, title: str, body: str = "",
                 run_id: str = "", status: str = "active") -> str:
        if kind not in NODE_KINDS:
            raise ValueError(f"неизвестный тип узла памяти: {kind}")
        if status not in NODE_STATUSES:
            raise ValueError(f"неизвестный статус узла памяти: {status}")
        node_id = uuid.uuid4().hex[:12]
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO nodes (id, project_id, kind, title, body, status, "
                "run_id, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (node_id, project_id, kind, title[:300], body[:8000], status,
                 run_id, time.time()))
        return node_id

    def add_edge(self, src: str, dst: str, rel: str, reason: str = "") -> None:
        if rel not in REL_KINDS:
            raise ValueError(f"неизвестный тип связи памяти: {rel}")
        with self._lock:
            for node_id in (src, dst):
                row = self._conn.execute("SELECT id FROM nodes WHERE id=?",
                                         (node_id,)).fetchone()
                if row is None:
                    raise ValueError(f"узел памяти не найден: {node_id}")
            with self._conn:
                self._conn.execute(
                    "INSERT INTO edges (src, dst, rel, reason, created_at) "
                    "VALUES (?,?,?,?,?)", (src, dst, rel, reason[:500], time.time()))

    def set_status(self, node_id: str, status: str) -> None:
        if status not in NODE_STATUSES:
            raise ValueError(f"неизвестный статус узла памяти: {status}")
        with self._lock, self._conn:
            self._conn.execute("UPDATE nodes SET status=? WHERE id=?",
                               (status, node_id))

    # ---- reads --------------------------------------------------------
    def _row_node(self, row: sqlite3.Row) -> MemoryNode:
        return MemoryNode(id=row["id"], project_id=row["project_id"],
                          kind=row["kind"], title=row["title"],
                          body=row["body"] or "", status=row["status"],
                          run_id=row["run_id"] or "",
                          created_at=row["created_at"] or 0.0)

    def get_node(self, node_id: str) -> MemoryNode | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM nodes WHERE id=?",
                                     (node_id,)).fetchone()
        return self._row_node(row) if row else None

    def neighbors(self, node_id: str) -> list[tuple[MemoryEdge, MemoryNode]]:
        """Edges touching the node, with the node on the other end."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT e.src, e.dst, e.rel, e.reason, e.created_at "
                "FROM edges e WHERE e.src=? OR e.dst=? ORDER BY e.id",
                (node_id, node_id)).fetchall()
        out = []
        for r in rows:
            edge = MemoryEdge(src=r["src"], dst=r["dst"], rel=r["rel"],
                              reason=r["reason"] or "",
                              created_at=r["created_at"] or 0.0)
            other_id = edge.dst if edge.src == node_id else edge.src
            other = self.get_node(other_id)
            if other is not None:
                out.append((edge, other))
        return out

    def search(self, project_id: str, query: str, limit: int = 20) -> list[MemoryNode]:
        like = f"%{query}%"
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM nodes WHERE project_id=? AND "
                "(title LIKE ? OR body LIKE ?) ORDER BY created_at DESC LIMIT ?",
                (project_id, like, like, limit)).fetchall()
        return [self._row_node(r) for r in rows]

    def recent(self, project_id: str, limit: int = 20,
               kinds: tuple[str, ...] | None = None) -> list[MemoryNode]:
        with self._lock:
            if kinds:
                qmarks = ",".join("?" * len(kinds))
                rows = self._conn.execute(
                    f"SELECT * FROM nodes WHERE project_id=? AND kind IN ({qmarks}) "
                    "ORDER BY created_at DESC LIMIT ?",
                    (project_id, *kinds, limit)).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM nodes WHERE project_id=? "
                    "ORDER BY created_at DESC LIMIT ?", (project_id, limit)).fetchall()
        return [self._row_node(r) for r in rows]

    def counts(self, project_id: str) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT kind, COUNT(*) AS n FROM nodes WHERE project_id=? "
                "GROUP BY kind", (project_id,)).fetchall()
        return {r["kind"]: r["n"] for r in rows}

    # ---- digest for prompts --------------------------------------------
    def context_pack(self, project_id: str, query: str = "",
                     limit: int = 8) -> str:
        """Compact Russian digest of the most relevant knowledge, with the
        *reasons* on the relations — empty string when nothing is stored."""
        picked: list[MemoryNode] = []
        seen: set[str] = set()
        if query:
            for token in [query] + query.split()[:4]:
                if len(token) < 4:
                    continue
                for node in self.search(project_id, token, limit=limit):
                    if node.id not in seen:
                        seen.add(node.id)
                        picked.append(node)
                if len(picked) >= limit:
                    break
        for node in self.recent(project_id, limit=limit,
                                kinds=("decision", "lesson", "bug")):
            if node.id not in seen and len(picked) < limit:
                seen.add(node.id)
                picked.append(node)
        if not picked:
            return ""
        lines = ["Память проекта (решения, уроки, известные проблемы):"]
        for node in picked[:limit]:
            status = "" if node.status == "active" else f" [{node.status}]"
            line = f"- ({_KIND_RU.get(node.kind, node.kind)}{status}) {node.title}"
            if node.body:
                line += f" — {node.body[:200]}"
            rels = self.neighbors(node.id)[:2]
            for edge, other in rels:
                rel_ru = _REL_RU.get(edge.rel, edge.rel)
                line += f"\n  · {rel_ru}: {other.title[:80]}"
                if edge.reason:
                    line += f" (причина: {edge.reason[:100]})"
            lines.append(line)
        return "\n".join(lines)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
