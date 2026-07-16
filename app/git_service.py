"""Read-only git helpers used by the UI (status / clone / branch inspection).

Mutating run operations (worktrees, staging, integration, push) live in
`app/workspace.py`, which enforces the safety rules. This module deliberately no
longer exposes destructive helpers like broad `add -A`, branch reset, or push to
an arbitrary branch.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from urllib.parse import urlparse, urlunparse


class GitError(RuntimeError):
    pass


def _run(args: list[str], cwd: str, timeout: int = 600) -> str:
    try:
        # Explicit UTF-8: the locale codec (cp1251 on Windows) would mojibake
        # non-ASCII branch names / status output shown in the UI.
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
    except FileNotFoundError as exc:
        raise GitError("git не найден в системе") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError("git превысил лимит времени") from exc
    if proc.returncode != 0:
        raise GitError((proc.stderr or proc.stdout or "git error").strip())
    return (proc.stdout or "").strip()


def has_repo(path: str) -> bool:
    return (Path(path) / ".git").exists()


def current_branch(path: str) -> str:
    try:
        return _run(["rev-parse", "--abbrev-ref", "HEAD"], path)
    except GitError:
        return ""


def status(path: str) -> str:
    return _run(["status", "--short", "--branch"], path)


def _authed_url(url: str, token: str) -> str:
    """Embed a token into an https GitHub URL for a one-off clone."""
    if not token or not url.startswith("http"):
        return url
    parsed = urlparse(url)
    netloc = f"x-access-token:{token}@{parsed.hostname}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


def clone(url: str, dest: str, token: str = "") -> str:
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    return _run(["clone", _authed_url(url, token), dest], ".", timeout=600)
