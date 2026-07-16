"""Agent reputation: measured outcomes per provider, persisted across runs.

Counters are only incremented from *observable* events (a run finished, a
verification ran, the human approved/rejected, a published branch was later
reverted). The routing engine consumes a bounded multiplier so reputation
tunes capability priors without ever fully overriding them; with no history
the multiplier is exactly 1.0.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path


def _default_path() -> Path:
    from .config import CONFIG_DIR
    return CONFIG_DIR / "reputation.json"


_FIELDS = ("tasks_ok", "tasks_failed", "verify_pass", "verify_fail",
           "approved", "rejected", "rollbacks")


def _blank() -> dict:
    rec = {f: 0 for f in _FIELDS}
    rec["updated_at"] = 0.0
    return rec


class ReputationStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else _default_path()
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        self._load()

    # ---- persistence -------------------------------------------------
    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._data = {pid: {**_blank(), **rec}
                              for pid, rec in raw.items() if isinstance(rec, dict)}
        except (OSError, json.JSONDecodeError):
            self._data = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(self.path)

    def _rec(self, provider: str) -> dict:
        return self._data.setdefault(provider, _blank())

    def _bump(self, provider: str, field: str) -> None:
        with self._lock:
            rec = self._rec(provider)
            rec[field] += 1
            rec["updated_at"] = time.time()
            self._save()

    # ---- recording ----------------------------------------------------
    def record_task(self, provider: str, success: bool) -> None:
        self._bump(provider, "tasks_ok" if success else "tasks_failed")

    def record_verification(self, provider: str, passed: bool) -> None:
        self._bump(provider, "verify_pass" if passed else "verify_fail")

    def record_review(self, provider: str, approved: bool) -> None:
        self._bump(provider, "approved" if approved else "rejected")

    def record_rollback(self, provider: str) -> None:
        self._bump(provider, "rollbacks")

    # ---- scoring -------------------------------------------------------
    @staticmethod
    def _rate(ok: int, bad: int) -> float:
        """Laplace-smoothed success rate; 0.5 with no data."""
        return (ok + 1) / (ok + bad + 2)

    def multiplier(self, provider: str) -> float:
        """Bounded routing multiplier in [0.85, 1.15]; 1.0 with no history."""
        with self._lock:
            rec = self._data.get(provider)
        if not rec:
            return 1.0
        s = self._rate(rec["tasks_ok"], rec["tasks_failed"])
        v = self._rate(rec["verify_pass"], rec["verify_fail"])
        a = self._rate(rec["approved"], rec["rejected"])
        blended = 0.4 * s + 0.35 * v + 0.25 * a
        penalty = min(0.05 * rec.get("rollbacks", 0), 0.15)
        return round(max(0.85, min(1.15, 0.85 + 0.6 * blended - penalty)), 3)

    def explain(self, provider: str) -> str:
        with self._lock:
            rec = self._data.get(provider)
        if not rec:
            return "истории нет — множитель 1.0"
        return (f"задачи {rec['tasks_ok']}✓/{rec['tasks_failed']}✗ · "
                f"верификация {rec['verify_pass']}✓/{rec['verify_fail']}✗ · "
                f"одобрения {rec['approved']}✓/{rec['rejected']}✗ · "
                f"откаты {rec['rollbacks']} → множитель {self.multiplier(provider)}")

    def snapshot(self) -> dict[str, dict]:
        with self._lock:
            return {pid: dict(rec) for pid, rec in self._data.items()}
