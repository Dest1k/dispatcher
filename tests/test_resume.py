"""Resuming a run parked at the approval gate after a restart."""
import os
import subprocess


from app.domain import RunState
from app.persistence import RunStore
from app.resume import discard_resumed, publish_resumed
from app.workspace import RunWorkspaces

POSIX = os.name != "nt"


def _park_run(git_repo, tmp_path):
    """Build a run staged for approval (integration worktree with staged work)."""
    store = RunStore(tmp_path / "db.sqlite")
    rw = RunWorkspaces(str(git_repo), runs_root=tmp_path / "runs")
    info = rw.prepare()
    a = rw.create_agent_worktree("A", allowed_paths=["feature.txt"])
    (a.path / "feature.txt").write_text("resumed work\n")
    patch = a.stage_and_diff()
    rw.create_integration_worktree()
    ok, _ = rw.apply_patch(patch)
    assert ok
    rid = store.create_run("p", "task", info["base_commit"])
    store.set_workspace(rid, str(rw.run_dir), rw.integration_branch)
    store.save_review(rid, rw.integration_diff(), {"status": "pass"}, "# Отчёт\nтело",
                      rw.integration_changed_files())
    store.set_state(rid, RunState.AWAITING_APPROVAL)
    return store, rid, rw.integration_branch


def _branches(repo):
    return subprocess.run(["git", "branch", "--format=%(refname:short)"], cwd=repo,
                          capture_output=True, text=True).stdout.split()


def test_publish_resumed_commits(has_git, git_repo, tmp_path):
    store, rid, branch = _park_run(git_repo, tmp_path)
    project = {"local_path": str(git_repo), "github_url": "", "github_token": ""}
    result = publish_resumed(store, store.get(rid), project, push=False)
    assert result["commit"]
    assert store.get(rid).state == RunState.COMPLETED.value
    # the work is on the kept integration branch, source tree untouched
    assert branch in _branches(str(git_repo))
    show = subprocess.run(["git", "show", f"{branch}:feature.txt"], cwd=str(git_repo),
                          capture_output=True, text=True)
    assert "resumed work" in show.stdout
    assert subprocess.run(["git", "status", "--porcelain"], cwd=str(git_repo),
                          capture_output=True, text=True).stdout.strip() == ""


def test_discard_resumed_cleans_up(has_git, git_repo, tmp_path):
    store, rid, branch = _park_run(git_repo, tmp_path)
    project = {"local_path": str(git_repo)}
    discard_resumed(store, store.get(rid), project)
    assert store.get(rid).state == RunState.CANCELLED.value
    assert branch not in _branches(str(git_repo))
    assert subprocess.run(["git", "status", "--porcelain"], cwd=str(git_repo),
                          capture_output=True, text=True).stdout.strip() == ""
