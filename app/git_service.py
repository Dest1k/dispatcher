"""Thin git wrapper around the system `git` binary."""
from __future__ import annotations

import subprocess
from pathlib import Path
from urllib.parse import urlparse, urlunparse


class GitError(RuntimeError):
    pass


def _run(args: list[str], cwd: str, timeout: int = 180) -> str:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError as exc:
        raise GitError("git не найден в системе") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError("git превысил лимит времени") from exc
    if proc.returncode != 0:
        raise GitError((proc.stderr or proc.stdout or "git error").strip())
    return (proc.stdout or "").strip()


def has_repo(path: str) -> bool:
    return (Path(path) / ".git").exists()


def _authed_url(url: str, token: str) -> str:
    """Embed a token into an https GitHub URL for non-interactive push/clone."""
    if not token or not url.startswith("http"):
        return url
    parsed = urlparse(url)
    netloc = f"x-access-token:{token}@{parsed.hostname}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


def init_repo(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)
    if not has_repo(path):
        _run(["init"], path)


def clone(url: str, dest: str, token: str = "") -> str:
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    return _run(["clone", _authed_url(url, token), dest], ".", timeout=600)


def current_branch(path: str) -> str:
    try:
        return _run(["rev-parse", "--abbrev-ref", "HEAD"], path)
    except GitError:
        return ""


def status(path: str) -> str:
    return _run(["status", "--short", "--branch"], path)


def diff_stat(path: str) -> str:
    # staged + unstaged combined
    _run(["add", "-A"], path)
    return _run(["diff", "--cached", "--stat"], path) or "(изменений нет)"


def changed_files(path: str) -> list[str]:
    out = _run(["diff", "--cached", "--name-only"], path)
    return [line for line in out.splitlines() if line.strip()]


def ensure_branch(path: str, branch: str) -> None:
    if branch:
        _run(["checkout", "-B", branch], path)


def commit_all(path: str, message: str) -> str | None:
    _run(["add", "-A"], path)
    # Nothing to commit?
    porcelain = _run(["status", "--porcelain"], path)
    if not porcelain.strip():
        return None
    # Make sure an identity exists so commits never fail on a fresh machine.
    try:
        _run(["config", "user.email"], path)
    except GitError:
        _run(["config", "user.email", "multi-ai@local"], path)
        _run(["config", "user.name", "Multi-AI Control Center"], path)
    _run(["commit", "-m", message], path)
    return _run(["rev-parse", "--short", "HEAD"], path)


def push(path: str, branch: str, github_url: str = "", token: str = "") -> str:
    branch = branch or current_branch(path) or "main"
    if github_url and token:
        return _run(["push", _authed_url(github_url, token), f"HEAD:{branch}"],
                    path, timeout=600)
    return _run(["push", "-u", "origin", branch], path, timeout=600)
