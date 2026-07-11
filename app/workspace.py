"""Isolated per-agent Git worktrees and safe integration.

Each run records an immutable base commit and gives every implementation agent
its own git worktree on its own temporary branch, created from that base. Agents
never share a writable tree, and the user's actual working tree is never
touched. Accepted work is collected as a patch and applied sequentially into a
dedicated integration worktree using explicit staging — never a broad
`git add -A` on the source repo, never a branch reset, never a force push.
"""
from __future__ import annotations

import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .security import PathPolicy


class WorkspaceError(RuntimeError):
    pass


def _git(args: list[str], cwd: str, timeout: int = 180) -> str:
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                              text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise WorkspaceError("git не найден") from exc
    except subprocess.TimeoutExpired as exc:
        raise WorkspaceError("git timeout") from exc
    if proc.returncode != 0:
        raise WorkspaceError((proc.stderr or proc.stdout or "git error").strip())
    return (proc.stdout or "").strip()


def _git_raw(args: list[str], cwd: str, timeout: int = 180) -> str:
    """Like _git but does NOT strip output — required for patch text, whose
    trailing newline is significant to `git apply`."""
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, timeout=timeout)
    if proc.returncode != 0:
        raise WorkspaceError((proc.stderr or proc.stdout or "git error").strip())
    return proc.stdout or ""


def _git_ok(args: list[str], cwd: str) -> tuple[int, str]:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


@dataclass
class AgentWorkspace:
    agent_id: str
    path: Path
    branch: str
    base_commit: str
    policy: PathPolicy

    def stage_and_diff(self) -> str:
        """Patch of everything the agent changed in its isolated worktree."""
        _git(["add", "-A"], str(self.path))          # safe: throwaway worktree
        return _git_raw(["diff", "--cached", self.base_commit], str(self.path))

    def changed_files(self) -> list[str]:
        _git(["add", "-A"], str(self.path))
        out = _git(["diff", "--cached", "--name-only", self.base_commit], str(self.path))
        return [line for line in out.splitlines() if line.strip()]


class RunWorkspaces:
    def __init__(self, repo_root: str, run_id: str | None = None,
                 runs_root: str | Path | None = None):
        self.repo_root = str(Path(repo_root).resolve())
        self.run_id = run_id or uuid.uuid4().hex[:10]
        if runs_root is None:
            from .config import CONFIG_DIR
            runs_root = CONFIG_DIR / "runs"
        self.run_dir = Path(runs_root) / f"run-{self.run_id}"
        self.base_commit: str = ""
        self.agents: dict[str, AgentWorkspace] = {}
        self.integration_path: Path | None = None
        self.integration_branch = f"dispatcher/{self.run_id}/integration"
        self._created_branches: list[str] = []

    # ---- lifecycle --------------------------------------------------
    def prepare(self) -> dict:
        if not (Path(self.repo_root) / ".git").exists():
            raise WorkspaceError("Каталог проекта не является git-репозиторием")
        try:
            self.base_commit = _git(["rev-parse", "HEAD"], self.repo_root)
        except WorkspaceError as exc:
            raise WorkspaceError(
                "В репозитории нет ни одного коммита — сделай первый коммит") from exc
        self.run_dir.mkdir(parents=True, exist_ok=True)
        return {"base_commit": self.base_commit, "dirty": self.is_dirty()}

    def is_dirty(self) -> bool:
        return bool(_git(["status", "--porcelain"], self.repo_root).strip())

    def current_branch(self) -> str:
        try:
            return _git(["rev-parse", "--abbrev-ref", "HEAD"], self.repo_root)
        except WorkspaceError:
            return ""

    def create_agent_worktree(self, agent_id: str,
                              allowed_paths: list[str] | None = None,
                              denied: list[str] | None = None,
                              read_only: list[str] | None = None) -> AgentWorkspace:
        path = self.run_dir / "agents" / agent_id / "worktree"
        path.parent.mkdir(parents=True, exist_ok=True)
        branch = f"dispatcher/{self.run_id}/{agent_id}"
        _git(["worktree", "add", "-b", branch, str(path), self.base_commit],
             self.repo_root)
        self._created_branches.append(branch)
        policy = PathPolicy(str(path), allowed=allowed_paths, denied=denied,
                            read_only=read_only)
        ws = AgentWorkspace(agent_id, path, branch, self.base_commit, policy)
        self.agents[agent_id] = ws
        return ws

    def create_integration_worktree(self) -> Path:
        path = self.run_dir / "integration"
        path.parent.mkdir(parents=True, exist_ok=True)
        _git(["worktree", "add", "-b", self.integration_branch, str(path),
              self.base_commit], self.repo_root)
        self._created_branches.append(self.integration_branch)
        self.integration_path = path
        return path

    # ---- integration ------------------------------------------------
    def apply_patch(self, patch_text: str) -> tuple[bool, str]:
        """Apply a reviewed patch into the integration worktree (staged)."""
        if self.integration_path is None:
            self.create_integration_worktree()
        if not patch_text.strip():
            return True, "пустой патч"
        if not patch_text.endswith("\n"):
            patch_text += "\n"
        patch_file = self.run_dir / f"patch-{uuid.uuid4().hex[:8]}.diff"
        patch_file.write_text(patch_text, encoding="utf-8")
        code, out = _git_ok(["apply", "--index", "--3way", str(patch_file)],
                            str(self.integration_path))
        if code != 0:
            return False, out.strip()
        return True, "применено"

    def integration_changed_files(self) -> list[str]:
        if self.integration_path is None:
            return []
        out = _git(["diff", "--cached", "--name-only", self.base_commit],
                   str(self.integration_path))
        return [line for line in out.splitlines() if line.strip()]

    def integration_diff(self) -> str:
        if self.integration_path is None:
            return ""
        return _git_raw(["diff", "--cached", self.base_commit],
                        str(self.integration_path))

    def commit_integration(self, message: str) -> str | None:
        if self.integration_path is None:
            return None
        # Only what has been explicitly staged by apply --index; never add -A.
        status = _git(["status", "--porcelain"], str(self.integration_path))
        if not status.strip():
            return None
        try:
            _git(["config", "user.email"], str(self.integration_path))
        except WorkspaceError:
            _git(["config", "user.email", "dispatcher@local"], str(self.integration_path))
            _git(["config", "user.name", "Dispatcher"], str(self.integration_path))
        _git(["commit", "-m", message], str(self.integration_path))
        return _git(["rev-parse", "--short", "HEAD"], str(self.integration_path))

    # ---- cleanup ----------------------------------------------------
    def cleanup(self, keep_branches: bool = False) -> None:
        """Remove worktrees + temporary branches. Never touches user branches."""
        for ws in self.agents.values():
            _git_ok(["worktree", "remove", "--force", str(ws.path)], self.repo_root)
        if self.integration_path is not None:
            _git_ok(["worktree", "remove", "--force", str(self.integration_path)],
                    self.repo_root)
        _git_ok(["worktree", "prune"], self.repo_root)
        if not keep_branches:
            for branch in self._created_branches:
                _git_ok(["branch", "-D", branch], self.repo_root)
        shutil.rmtree(self.run_dir, ignore_errors=True)
