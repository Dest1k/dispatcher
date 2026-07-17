"""Command execution isolation.

`cwd` alone is not a sandbox: a model-controlled shell run with the host
environment can read SSH keys, API keys and git credentials, reach the network,
and touch files anywhere. This module runs commands with:

  * a scrubbed environment (host env is NOT inherited, so provider keys and
    tokens are absent) and a throwaway HOME;
  * a dedicated process group so the whole child tree can be terminated;
  * a hard timeout and cooperative cancellation;
  * output redaction and size caps.

Backends:
  * `restricted` (default) — the above, on the host. Network is best-effort
    (isolated only if a Linux network namespace can be created).
  * `docker` — one long-lived container **per agent worktree**
    (container-per-agent): only the worktree mounted, no home, no docker
    socket, `--network none` by default, CPU/memory/pids limits; state persists
    across the agent's commands.
  * `docker_command` — one container **per command** (stateless, max isolation).
  * `unsafe_local` — full host environment; the old, dangerous behavior. Only
    when explicitly selected; still redacts output.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass

from .security import redact as _redact

_MAX_OUTPUT = 12_000
_IS_WIN = os.name == "nt"


@dataclass
class SandboxResult:
    exit_code: int | None
    output: str
    timed_out: bool = False
    cancelled: bool = False
    backend: str = "restricted"
    network: str = "unknown"

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.cancelled


def _safe_path() -> str:
    return os.environ.get("PATH", "") or "/usr/local/bin:/usr/bin:/bin"


class _BaseSandbox:
    backend = "restricted"

    def __init__(self, workdir: str, allow_network: bool = False,
                 timeout: int = 300):
        self.workdir = str(workdir)
        self.allow_network = allow_network
        self.timeout = timeout

    def run(self, command: str, cancel: threading.Event | None = None) -> SandboxResult:
        raise NotImplementedError

    def close(self) -> None:
        pass

    # shared kill + wait loop --------------------------------------
    def _spawn(self, args: list[str], env: dict) -> subprocess.Popen:
        # Explicit UTF-8 so non-ASCII command output isn't mojibake'd by the
        # locale codec (cp1251 on Windows) in verification reports / the UI.
        kwargs: dict = dict(cwd=self.workdir, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
        if _IS_WIN:
            kwargs["creationflags"] = 0x00000200  # CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True     # own process group for killpg
        return subprocess.Popen(args, **kwargs)

    def _kill_tree(self, proc: subprocess.Popen) -> None:
        try:
            if _IS_WIN:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True)
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                time.sleep(0.4)
                if proc.poll() is None:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def _run_proc(self, args: list[str], env: dict,
                  cancel: threading.Event | None, network: str) -> SandboxResult:
        proc = self._spawn(args, env)
        holder: dict = {}

        def reader():
            try:
                holder["out"] = proc.communicate()[0]
            except Exception as exc:  # pragma: no cover
                holder["out"] = f"(ошибка чтения вывода: {exc})"

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        deadline = time.monotonic() + self.timeout
        timed_out = cancelled = False
        while thread.is_alive():
            thread.join(0.15)
            if cancel is not None and cancel.is_set():
                cancelled = True
                self._kill_tree(proc)
                break
            if time.monotonic() > deadline:
                timed_out = True
                self._kill_tree(proc)
                break
        thread.join(5)
        output = holder.get("out", "") or ""
        if timed_out:
            output += "\n[остановлено по таймауту]"
        if cancelled:
            output += "\n[остановлено пользователем]"
        output = _redact(output)
        if len(output) > _MAX_OUTPUT:
            output = output[:_MAX_OUTPUT] + "\n...(вывод обрезан)"
        return SandboxResult(exit_code=proc.returncode, output=output.strip(),
                             timed_out=timed_out, cancelled=cancelled,
                             backend=self.backend, network=network)


class LocalRestrictedSandbox(_BaseSandbox):
    backend = "restricted"

    def __init__(self, workdir: str, allow_network: bool = False, timeout: int = 300):
        super().__init__(workdir, allow_network, timeout)
        self._home = tempfile.mkdtemp(prefix="dispatcher-home-")
        self._can_netns = self._probe_netns()

    def _probe_netns(self) -> bool:
        if _IS_WIN or self.allow_network or shutil.which("unshare") is None:
            return False
        try:
            r = subprocess.run(["unshare", "-n", "true"], capture_output=True,
                               timeout=5)
            return r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def _env(self) -> dict:
        # Start empty; add only non-secret essentials. Host env (and thus every
        # API key / token) is deliberately NOT inherited.
        env = {"PATH": _safe_path(), "HOME": self._home, "TMPDIR": self._home,
               "TEMP": self._home, "TMP": self._home, "LANG": "C.UTF-8"}
        if _IS_WIN:
            for k in ("SystemRoot", "COMSPEC", "PATHEXT", "WINDIR",
                      "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE"):
                if k in os.environ:
                    env[k] = os.environ[k]
            env["USERPROFILE"] = self._home
        return env

    def _wrap(self, command: str) -> tuple[list[str], str]:
        if _IS_WIN:
            return ["cmd", "/c", command], "host (не изолирована)"
        shell = ["/bin/sh", "-c", command]
        if not self.allow_network and self._can_netns:
            return ["unshare", "-n", *shell], "none"
        return shell, "host" if self.allow_network else "host (не изолирована)"

    def run(self, command: str, cancel: threading.Event | None = None) -> SandboxResult:
        args, network = self._wrap(command)
        return self._run_proc(args, self._env(), cancel, network)

    def close(self) -> None:
        shutil.rmtree(self._home, ignore_errors=True)


class UnsafeLocalSandbox(_BaseSandbox):
    backend = "unsafe_local"

    def run(self, command: str, cancel: threading.Event | None = None) -> SandboxResult:
        # Full host environment — dangerous. Only reachable when explicitly chosen.
        args = ["cmd", "/c", command] if _IS_WIN else ["/bin/sh", "-c", command]
        return self._run_proc(args, dict(os.environ), cancel, "host")


class DockerSandbox(_BaseSandbox):
    """One container per command (stateless, maximum isolation)."""

    backend = "docker"

    def __init__(self, workdir: str, image: str = "python:3.12-slim",
                 allow_network: bool = False, timeout: int = 300):
        super().__init__(workdir, allow_network, timeout)
        self.image = image
        if shutil.which("docker") is None:
            raise RuntimeError("docker недоступен")

    def run(self, command: str, cancel: threading.Event | None = None) -> SandboxResult:
        network = "bridge" if self.allow_network else "none"
        args = [
            "docker", "run", "--rm", "-i",
            "--network", "bridge" if self.allow_network else "none",
            "-v", f"{self.workdir}:/work:rw", "-w", "/work",
            "--memory", "2g", "--cpus", "2", "--pids-limit", "512",
            # no --env passthrough, no docker socket, no home mount
            self.image, "sh", "-c", command,
        ]
        return self._run_proc(args, {"PATH": _safe_path()}, cancel, network)


class DockerAgentSandbox(_BaseSandbox):
    """One long-lived container **per agent worktree** (container-per-agent).

    Started lazily on the first command and reused for all of the agent's
    commands, so installed packages and created state persist across commands
    the way a real dev environment does. Mounts only the worktree at /work,
    with no home, no docker socket, no host-env passthrough, `--network none`
    by default, and CPU/memory/pids caps. On a command timeout or cancel the
    container is force-removed so nothing runs away; the next command recreates
    it (worktree files survive on the host mount). `close()` stops it.
    """

    backend = "docker"

    def __init__(self, workdir: str, image: str = "python:3.12-slim",
                 allow_network: bool = False, timeout: int = 300):
        super().__init__(workdir, allow_network, timeout)
        self.image = image
        self._cid: str | None = None
        self._lock = threading.Lock()
        if shutil.which("docker") is None:
            raise RuntimeError("docker недоступен")

    def _docker(self, args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
        return subprocess.run(["docker", *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)

    def _alive(self) -> bool:
        if not self._cid:
            return False
        try:
            r = self._docker(["inspect", "-f", "{{.State.Running}}", self._cid],
                             timeout=30)
        except (OSError, subprocess.SubprocessError):
            return False
        return r.returncode == 0 and r.stdout.strip() == "true"

    def _ensure(self) -> str:
        with self._lock:
            if self._cid and self._alive():
                return self._cid
            net = "bridge" if self.allow_network else "none"
            r = self._docker([
                "run", "-d", "--rm", "--network", net,
                "-v", f"{self.workdir}:/work:rw", "-w", "/work",
                "--memory", "2g", "--cpus", "2", "--pids-limit", "512",
                # no --env passthrough, no docker socket, no home mount
                self.image, "sh", "-c", "sleep 86400"])
            if r.returncode != 0:
                raise RuntimeError(
                    (r.stderr or r.stdout or "docker run failed").strip()[:200])
            self._cid = r.stdout.strip()
            return self._cid

    def run(self, command: str, cancel: threading.Event | None = None) -> SandboxResult:
        network = "bridge" if self.allow_network else "none"
        try:
            cid = self._ensure()
        except Exception as exc:
            return SandboxResult(exit_code=None,
                                 output=_redact(f"docker: {exc}"),
                                 backend=self.backend, network=network)
        args = ["docker", "exec", "-i", "-w", "/work", cid, "sh", "-c", command]
        result = self._run_proc(args, {"PATH": _safe_path()}, cancel, network)
        if result.timed_out or result.cancelled:
            self._destroy()          # no runaway process; next run recreates
        return result

    def _destroy(self) -> None:
        with self._lock:
            cid, self._cid = self._cid, None
        if cid:
            try:
                self._docker(["rm", "-f", cid], timeout=30)
            except (OSError, subprocess.SubprocessError):
                pass

    def close(self) -> None:
        self._destroy()


def make_sandbox(mode: str, workdir: str, allow_network: bool = False,
                 timeout: int = 300) -> _BaseSandbox:
    """Factory. Falls back to restricted (with .fell_back=True) if docker asked
    for but unavailable. `docker` = container-per-agent (persistent);
    `docker_command` = one container per command (stateless)."""
    if mode == "unsafe_local":
        return UnsafeLocalSandbox(workdir, allow_network, timeout)
    if mode in ("docker", "docker_command"):
        cls = DockerSandbox if mode == "docker_command" else DockerAgentSandbox
        try:
            return cls(workdir, allow_network=allow_network, timeout=timeout)
        except Exception:
            sb = LocalRestrictedSandbox(workdir, allow_network, timeout)
            sb.fell_back = True  # type: ignore[attr-defined]
            return sb
    return LocalRestrictedSandbox(workdir, allow_network, timeout)
