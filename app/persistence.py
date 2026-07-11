"""SQLite persistence for runs, events, usage, and review artifacts.

A run is checkpointed after every state transition so incomplete runs are
discoverable after an application restart. Runs parked at the approval gate are
resumable (their integration worktree still exists on disk); runs interrupted
mid-execution are reconciled to FAILED on startup.

Projects and provider settings continue to live in config.json; this database
owns run history and recovery state.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .domain import RunState, is_terminal

SCHEMA_VERSION = 1


def _db_path() -> Path:
    from .config import CONFIG_DIR
    return CONFIG_DIR / "dispatcher.db"


@dataclass
class RunRecord:
    id: str
    project_id: str
    task: str
    base_commit: str = ""
    integration_branch: str = ""
    run_dir: str = ""
    state: str = RunState.CREATED.value
    created_at: float = 0.0
    updated_at: float = 0.0
    diff: str = ""
    verification: dict = field(default_factory=dict)
    report: str = ""
    changed_files: list = field(default_factory=list)
    message: str = ""


class RunStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else _db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    # ---- schema -----------------------------------------------------
    def _migrate(self) -> None:
        with self._lock, self._conn:
            self._conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER)")
            row = self._conn.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                self._conn.execute("INSERT INTO schema_version (version) VALUES (?)",
                                   (SCHEMA_VERSION,))
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, project_id TEXT, task TEXT,
                    base_commit TEXT, integration_branch TEXT, run_dir TEXT,
                    state TEXT, created_at REAL, updated_at REAL,
                    diff TEXT, verification TEXT, report TEXT,
                    changed_files TEXT, message TEXT)""")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, ts REAL,
                    kind TEXT, payload TEXT)""")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, provider TEXT,
                    input_tokens INTEGER, output_tokens INTEGER, cost REAL)""")

    # ---- writes -----------------------------------------------------
    def create_run(self, project_id: str, task: str, base_commit: str = "") -> str:
        run_id = uuid.uuid4().hex[:12]
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO runs (id, project_id, task, base_commit, state, "
                "created_at, updated_at, verification, changed_files) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (run_id, project_id, task, base_commit, RunState.CREATED.value,
                 now, now, "{}", "[]"))
        return run_id

    def set_state(self, run_id: str, state: RunState) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE runs SET state=?, updated_at=? WHERE id=?",
                               (state.value, time.time(), run_id))

    def set_workspace(self, run_id: str, run_dir: str, integration_branch: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE runs SET run_dir=?, integration_branch=?, updated_at=? WHERE id=?",
                (run_dir, integration_branch, time.time(), run_id))

    def add_event(self, run_id: str, kind: str, payload: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO events (run_id, ts, kind, payload) VALUES (?,?,?,?)",
                (run_id, time.time(), kind, payload[:8000]))

    def add_usage(self, run_id: str, provider: str, inp: int, out: int, cost: float) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO usage (run_id, provider, input_tokens, output_tokens, cost) "
                "VALUES (?,?,?,?,?)", (run_id, provider, inp, out, cost))

    def save_review(self, run_id: str, diff: str, verification: dict, report: str,
                    changed_files: list) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE runs SET diff=?, verification=?, report=?, changed_files=?, "
                "updated_at=? WHERE id=?",
                (diff[:200000], json.dumps(verification), report[:200000],
                 json.dumps(changed_files), time.time(), run_id))

    def finish(self, run_id: str, state: RunState, message: str = "") -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE runs SET state=?, message=?, updated_at=? WHERE id=?",
                               (state.value, message, time.time(), run_id))

    # ---- reads ------------------------------------------------------
    def _row_to_record(self, row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            id=row["id"], project_id=row["project_id"], task=row["task"],
            base_commit=row["base_commit"] or "",
            integration_branch=row["integration_branch"] or "",
            run_dir=row["run_dir"] or "", state=row["state"],
            created_at=row["created_at"] or 0, updated_at=row["updated_at"] or 0,
            diff=row["diff"] or "",
            verification=json.loads(row["verification"] or "{}"),
            report=row["report"] or "",
            changed_files=json.loads(row["changed_files"] or "[]"),
            message=row["message"] or "")

    def get(self, run_id: str) -> RunRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return self._row_to_record(row) if row else None

    def list_runs(self, project_id: str | None = None, limit: int = 50) -> list[RunRecord]:
        with self._lock:
            if project_id:
                rows = self._conn.execute(
                    "SELECT * FROM runs WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
                    (project_id, limit)).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._row_to_record(r) for r in rows]

    def incomplete(self) -> list[RunRecord]:
        terminal = tuple(s.value for s in RunState if is_terminal(s))
        qmarks = ",".join("?" * len(terminal))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM runs WHERE state NOT IN ({qmarks})", terminal).fetchall()
        return [self._row_to_record(r) for r in rows]

    def usage_for(self, run_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM usage WHERE run_id=?", (run_id,)).fetchall()
        return [dict(r) for r in rows]

    # ---- recovery ---------------------------------------------------
    def reconcile_on_startup(self) -> dict:
        """Resolve non-terminal runs after a restart. Returns counts.

        A run at AWAITING_APPROVAL whose integration worktree still exists is
        left resumable; everything else non-terminal is marked FAILED.
        """
        resumable, failed = [], []
        for rec in self.incomplete():
            state = RunState(rec.state)
            worktree_ok = rec.run_dir and Path(rec.run_dir).exists()
            if state == RunState.AWAITING_APPROVAL and worktree_ok:
                resumable.append(rec.id)
            else:
                self.finish(rec.id, RunState.FAILED,
                            "Прервано перезапуском приложения")
                failed.append(rec.id)
        return {"resumable": resumable, "failed": failed}

    def close(self) -> None:
        with self._lock:
            self._conn.close()
