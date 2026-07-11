"""Per-run financial budget guard.

Accumulates measured API cost during a run and signals when a configured cap is
reached so the orchestrator can stop agents gracefully (finishing the current
turn, then integrating/verifying whatever exists) instead of overrunning.
"""
from __future__ import annotations

import threading


class BudgetGuard:
    def __init__(self, cap_usd: float = 0.0, warn_ratio: float = 0.8):
        self.cap = float(cap_usd or 0.0)
        self.warn_ratio = warn_ratio
        self.total = 0.0
        self._warned = False
        self._lock = threading.Lock()

    def add(self, cost: float) -> float:
        with self._lock:
            self.total += max(0.0, cost)
            return self.total

    def exceeded(self) -> bool:
        return self.cap > 0 and self.total >= self.cap

    def should_warn(self) -> bool:
        if self.cap <= 0 or self._warned:
            return False
        if self.total >= self.cap * self.warn_ratio:
            self._warned = True
            return True
        return False

    def remaining(self) -> float:
        return max(0.0, self.cap - self.total) if self.cap > 0 else float("inf")
