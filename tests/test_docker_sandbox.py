"""Container-per-agent Docker sandbox: lifecycle + command construction.

No real Docker is invoked — the `docker` CLI calls (`_docker`) and the host
exec (`_run_proc`) are stubbed. A live smoke against real Docker is done
separately.
"""
import app.sandbox as sbx
from app.sandbox import DockerAgentSandbox, SandboxResult, make_sandbox


class FakeCompleted:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _agent(tmp_path, monkeypatch):
    monkeypatch.setattr(sbx.shutil, "which", lambda name: "/usr/bin/docker")
    sb = DockerAgentSandbox(str(tmp_path), image="python:3.12-slim", timeout=30)
    calls = []

    def fake_docker(args, timeout=120):
        calls.append(list(args))
        if args[0] == "run":                         # start container
            return FakeCompleted(stdout="container123\n")
        if args[0] == "inspect":                     # alive check
            return FakeCompleted(stdout="true\n")
        return FakeCompleted(stdout="")              # rm -f, etc.

    monkeypatch.setattr(sb, "_docker", fake_docker)
    return sb, calls


def test_container_started_once_and_reused(tmp_path, monkeypatch):
    sb, calls = _agent(tmp_path, monkeypatch)
    execed = []

    def fake_run_proc(args, env, cancel, network):
        execed.append(args)
        return SandboxResult(exit_code=0, output="ok", backend="docker",
                             network=network)

    monkeypatch.setattr(sb, "_run_proc", fake_run_proc)

    sb.run("echo one")
    sb.run("echo two")
    # exactly one `docker run` (start), reused for both commands
    runs = [c for c in calls if c and c[0] == "run"]
    assert len(runs) == 1
    # both commands went through `docker exec` into the same container
    assert len(execed) == 2
    for args in execed:
        assert args[:2] == ["docker", "exec"]
        assert "container123" in args
        assert args[-3:] == ["sh", "-c", "echo one"] or args[-3:] == ["sh", "-c", "echo two"]
    # start flags: worktree mount, network none, caps, no env/home/socket
    run_args = " ".join(runs[0])
    assert f"{tmp_path}:/work:rw" in run_args
    assert "--network none" in run_args
    assert "--pids-limit 512" in run_args
    assert "-d" in runs[0] and "--rm" in runs[0]
    sb.close()


def test_close_removes_container(tmp_path, monkeypatch):
    sb, calls = _agent(tmp_path, monkeypatch)
    monkeypatch.setattr(sb, "_run_proc",
                        lambda a, e, c, n: SandboxResult(0, "ok", network=n))
    sb.run("echo hi")
    sb.close()
    assert ["rm", "-f", "container123"] in calls


def test_timeout_destroys_container(tmp_path, monkeypatch):
    sb, calls = _agent(tmp_path, monkeypatch)
    monkeypatch.setattr(
        sb, "_run_proc",
        lambda a, e, c, n: SandboxResult(None, "slow", timed_out=True, network=n))
    res = sb.run("sleep 999")
    assert res.timed_out
    assert ["rm", "-f", "container123"] in calls        # cleaned up
    assert sb._cid is None                              # will recreate next time


def test_network_bridge_when_allowed(tmp_path, monkeypatch):
    monkeypatch.setattr(sbx.shutil, "which", lambda name: "/usr/bin/docker")
    sb = DockerAgentSandbox(str(tmp_path), allow_network=True, timeout=30)
    calls = []
    monkeypatch.setattr(sb, "_docker",
                        lambda args, timeout=120: (calls.append(list(args))
                                                   or FakeCompleted(stdout="cid\n")))
    monkeypatch.setattr(sb, "_run_proc",
                        lambda a, e, c, n: SandboxResult(0, "ok", network=n))
    sb.run("curl example.com")
    run_args = " ".join(next(c for c in calls if c[0] == "run"))
    assert "--network bridge" in run_args
    sb.close()


def test_run_failure_when_start_errors(tmp_path, monkeypatch):
    sb, _ = _agent(tmp_path, monkeypatch)
    monkeypatch.setattr(sb, "_docker",
                        lambda args, timeout=120: FakeCompleted(
                            stderr="no space left", returncode=1))
    res = sb.run("echo hi")
    assert res.exit_code is None
    assert "docker" in res.output


# ---- factory routing --------------------------------------------------------

def test_factory_docker_is_container_per_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(sbx.shutil, "which", lambda name: "/usr/bin/docker")
    sb = make_sandbox("docker", str(tmp_path))
    assert isinstance(sb, DockerAgentSandbox)
    assert sb.backend == "docker"


def test_factory_docker_command_is_per_command(tmp_path, monkeypatch):
    monkeypatch.setattr(sbx.shutil, "which", lambda name: "/usr/bin/docker")
    sb = make_sandbox("docker_command", str(tmp_path))
    assert isinstance(sb, sbx.DockerSandbox)


def test_factory_docker_falls_back_without_docker(tmp_path, monkeypatch):
    monkeypatch.setattr(sbx.shutil, "which", lambda name: None)
    sb = make_sandbox("docker", str(tmp_path))
    assert sb.backend == "restricted"
    assert getattr(sb, "fell_back", False) is True
    sb.close()
