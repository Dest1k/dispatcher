from app.domain import (
    RunState, can_transition, is_resumable, is_terminal,
)
from app.persistence import RunStore


# ---- state machine ---------------------------------------------------
def test_state_machine_transitions():
    assert can_transition(RunState.CREATED, RunState.PLANNING)
    assert can_transition(RunState.VERIFYING, RunState.AWAITING_APPROVAL)
    assert can_transition(RunState.AWAITING_APPROVAL, RunState.PUBLISHING)
    assert not can_transition(RunState.CREATED, RunState.PUBLISHING)
    # cancel/fail reachable from a live state
    assert can_transition(RunState.EXECUTING, RunState.CANCELLED)
    assert can_transition(RunState.EXECUTING, RunState.FAILED)


def test_terminal_and_resumable():
    assert is_terminal(RunState.COMPLETED)
    assert not is_terminal(RunState.EXECUTING)
    assert is_resumable(RunState.AWAITING_APPROVAL)
    assert not is_resumable(RunState.EXECUTING)


# ---- store -----------------------------------------------------------
def _store(tmp_path):
    return RunStore(tmp_path / "db.sqlite")


def test_run_lifecycle(tmp_path):
    store = _store(tmp_path)
    rid = store.create_run("proj1", "do X", base_commit="abc123")
    store.set_state(rid, RunState.PLANNING)
    store.set_workspace(rid, str(tmp_path / "run"), "dispatcher/x/integration")
    store.add_event(rid, "log", "started")
    store.add_usage(rid, "anthropic", 100, 50, 0.001)
    store.save_review(rid, "diff text", {"status": "pass"}, "report md", ["a.txt"])
    store.set_state(rid, RunState.AWAITING_APPROVAL)

    rec = store.get(rid)
    assert rec.task == "do X" and rec.base_commit == "abc123"
    assert rec.verification["status"] == "pass"
    assert rec.changed_files == ["a.txt"]
    assert rec.integration_branch == "dispatcher/x/integration"
    assert len(store.usage_for(rid)) == 1
    store.close()


def test_incomplete_and_list(tmp_path):
    store = _store(tmp_path)
    a = store.create_run("p", "a")
    b = store.create_run("p", "b")
    store.finish(b, RunState.COMPLETED, "done")
    incomplete_ids = {r.id for r in store.incomplete()}
    assert a in incomplete_ids and b not in incomplete_ids
    assert len(store.list_runs("p")) == 2
    store.close()


def test_reconcile_on_startup(tmp_path):
    store = _store(tmp_path)
    # awaiting approval with existing worktree -> resumable
    run_dir = tmp_path / "run-keep"
    run_dir.mkdir()
    a = store.create_run("p", "resumable")
    store.set_workspace(a, str(run_dir), "dispatcher/a/integration")
    store.set_state(a, RunState.AWAITING_APPROVAL)
    # awaiting approval but worktree gone -> failed
    b = store.create_run("p", "lost")
    store.set_workspace(b, str(tmp_path / "gone"), "dispatcher/b/integration")
    store.set_state(b, RunState.AWAITING_APPROVAL)
    # mid-execution -> failed
    c = store.create_run("p", "midway")
    store.set_state(c, RunState.EXECUTING)

    result = store.reconcile_on_startup()
    assert a in result["resumable"]
    assert set(result["failed"]) == {b, c}
    assert store.get(c).state == RunState.FAILED.value
    assert store.get(a).state == RunState.AWAITING_APPROVAL.value
    store.close()
