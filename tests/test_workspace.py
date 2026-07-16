"""Isolated worktree + safe integration behavior (real git)."""
import subprocess

import pytest

from app.security import PathViolation
from app.workspace import RunWorkspaces, WorkspaceError


def _status(repo):
    return subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                          capture_output=True, text=True).stdout


def _branches(repo):
    return subprocess.run(["git", "branch", "--format=%(refname:short)"], cwd=repo,
                          capture_output=True, text=True).stdout.split()


def test_requires_commit(has_git, tmp_path):
    repo = tmp_path / "empty"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    rw = RunWorkspaces(str(repo), runs_root=tmp_path / "runs")
    with pytest.raises(WorkspaceError):
        rw.prepare()


def test_isolated_worktrees_do_not_touch_source(has_git, git_repo, tmp_path):
    rw = RunWorkspaces(str(git_repo), runs_root=tmp_path / "runs")
    info = rw.prepare()
    assert info["dirty"] is False
    a = rw.create_agent_worktree("agentA", allowed_paths=["a.txt"])
    b = rw.create_agent_worktree("agentB", allowed_paths=["b.txt"])
    (a.path / "a.txt").write_text("from A")
    (b.path / "b.txt").write_text("from B")

    # source working tree stays clean; source has neither file
    assert _status(str(git_repo)).strip() == ""
    assert not (git_repo / "a.txt").exists()
    assert not (git_repo / "b.txt").exists()

    # each agent's patch contains only its own change
    pa, pb = a.stage_and_diff(), b.stage_and_diff()
    assert "a.txt" in pa and "b.txt" not in pa
    assert "b.txt" in pb and "a.txt" not in pb

    rw.cleanup()
    assert _status(str(git_repo)).strip() == ""


def test_path_policy_blocks_out_of_scope_writes(has_git, git_repo, tmp_path):
    rw = RunWorkspaces(str(git_repo), runs_root=tmp_path / "runs")
    rw.prepare()
    a = rw.create_agent_worktree("agentA", allowed_paths=["app/**"])
    assert a.policy.can_write("app/x.py")
    with pytest.raises(PathViolation):
        a.policy.resolve_write("other/y.py")
    rw.cleanup()


def test_sequential_integration_no_add_all(has_git, git_repo, tmp_path):
    rw = RunWorkspaces(str(git_repo), runs_root=tmp_path / "runs")
    rw.prepare()
    a = rw.create_agent_worktree("agentA", allowed_paths=["a.txt"])
    b = rw.create_agent_worktree("agentB", allowed_paths=["b.txt"])
    (a.path / "a.txt").write_text("from A\n")
    (b.path / "b.txt").write_text("from B\n")

    rw.create_integration_worktree()
    ok_a, _ = rw.apply_patch(a.stage_and_diff())
    ok_b, _ = rw.apply_patch(b.stage_and_diff())
    assert ok_a and ok_b
    files = set(rw.integration_changed_files())
    assert files == {"a.txt", "b.txt"}
    sha = rw.commit_integration("integrate A+B")
    assert sha

    # user branch (main/master) is untouched, source tree clean
    assert _status(str(git_repo)).strip() == ""
    rw.cleanup()
    # temp branches removed, user branch remains
    remaining = _branches(str(git_repo))
    assert not any(br.startswith("dispatcher/") for br in remaining)


def test_patch_paths_extraction():
    from app.workspace import patch_paths
    patch = (
        "diff --git a/app/main.py b/app/main.py\n"
        "--- a/app/main.py\n+++ b/app/main.py\n@@ -1 +1 @@\n-x\n+y\n"
        "diff --git a/new file.txt b/new file.txt\n"
        "--- /dev/null\n+++ b/new file.txt\n@@ -0,0 +1 @@\n+hello\n"
        "diff --git a/gone.py b/gone.py\n"
        "--- a/gone.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-bye\n")
    paths = patch_paths(patch)
    assert "app/main.py" in paths
    assert "new file.txt" in paths
    assert "gone.py" in paths
    assert "/dev/null" not in paths
    assert patch_paths("") == []


def test_patch_policy_boundary_catches_out_of_zone(has_git, git_repo, tmp_path):
    """The integration-boundary check used for CLI-native agents: a patch
    touching files outside the agent's zone is detectable via PathPolicy."""
    from app.workspace import patch_paths
    rw = RunWorkspaces(str(git_repo), runs_root=tmp_path / "runs")
    rw.prepare()
    ws = rw.create_agent_worktree("agentA", allowed_paths=["app/**"],
                                  denied=[".env", "secrets/*"])
    # the "CLI" edits files directly, bypassing the tool layer
    (ws.path / "app").mkdir()
    (ws.path / "app" / "ok.py").write_text("fine\n")
    (ws.path / "rogue.txt").write_text("outside the zone\n")
    (ws.path / ".env").write_text("SECRET=1\n")
    patch = ws.stage_and_diff()
    touched = patch_paths(patch)
    violations = [p for p in touched if not ws.policy.can_write(p)]
    assert "rogue.txt" in violations
    assert ".env" in violations
    assert "app/ok.py" not in violations
    rw.cleanup()


def test_conflicting_patches_reported(has_git, git_repo, tmp_path):
    rw = RunWorkspaces(str(git_repo), runs_root=tmp_path / "runs")
    rw.prepare()
    a = rw.create_agent_worktree("agentA")
    b = rw.create_agent_worktree("agentB")
    (a.path / "README.md").write_text("# A version\n")
    (b.path / "README.md").write_text("# B version\n")
    rw.create_integration_worktree()
    ok_a, _ = rw.apply_patch(a.stage_and_diff())
    ok_b, msg = rw.apply_patch(b.stage_and_diff())
    assert ok_a
    assert not ok_b            # conflicting edit to the same file is reported
    rw.cleanup()
