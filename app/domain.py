"""Domain types for runs: an explicit state machine (not a bag of booleans)."""
from __future__ import annotations

from enum import Enum


class RunState(str, Enum):
    CREATED = "created"
    PLANNING = "planning"
    EXECUTING = "executing"
    INTEGRATING = "integrating"
    VERIFYING = "verifying"
    AWAITING_APPROVAL = "awaiting_approval"
    PUBLISHING = "publishing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL = {RunState.COMPLETED, RunState.PARTIAL, RunState.FAILED, RunState.CANCELLED}

# Only a run parked at the approval gate is safely resumable after a restart:
# its integration worktree/branch still exist on disk and the diff + verification
# were persisted. Runs interrupted mid-execution cannot resurrect their in-memory
# agent threads and are reconciled to FAILED.
RESUMABLE = {RunState.AWAITING_APPROVAL}

_ALLOWED: dict[RunState, set[RunState]] = {
    RunState.CREATED: {RunState.PLANNING, RunState.FAILED, RunState.CANCELLED},
    RunState.PLANNING: {RunState.EXECUTING, RunState.FAILED, RunState.CANCELLED},
    RunState.EXECUTING: {RunState.INTEGRATING, RunState.FAILED, RunState.CANCELLED},
    RunState.INTEGRATING: {RunState.VERIFYING, RunState.FAILED, RunState.CANCELLED},
    RunState.VERIFYING: {RunState.AWAITING_APPROVAL, RunState.FAILED, RunState.CANCELLED},
    RunState.AWAITING_APPROVAL: {RunState.PUBLISHING, RunState.CANCELLED, RunState.FAILED},
    RunState.PUBLISHING: {RunState.COMPLETED, RunState.PARTIAL, RunState.FAILED},
}


def can_transition(src: RunState, dst: RunState) -> bool:
    if dst in TERMINAL and src not in TERMINAL:
        # terminal states are reachable from most places (cancel/fail)
        if dst in (RunState.FAILED, RunState.CANCELLED):
            return True
    return dst in _ALLOWED.get(src, set())


def is_terminal(state: RunState) -> bool:
    return state in TERMINAL


def is_resumable(state: RunState) -> bool:
    return state in RESUMABLE
