import os
import threading
import time

import pytest

from app.sandbox import LocalRestrictedSandbox, make_sandbox
from app.security import register_secret

POSIX = os.name != "nt"
pytestmark = pytest.mark.skipif(not POSIX, reason="shell tests assume POSIX sh")


def test_basic_command(tmp_path):
    sb = LocalRestrictedSandbox(str(tmp_path), timeout=30)
    res = sb.run("echo integration-ok")
    sb.close()
    assert res.exit_code == 0
    assert "integration-ok" in res.output


def test_runs_in_workdir(tmp_path):
    sb = LocalRestrictedSandbox(str(tmp_path), timeout=30)
    res = sb.run("pwd")
    sb.close()
    assert str(tmp_path) in res.output


def test_host_env_not_inherited(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak-123456")
    monkeypatch.setenv("MY_RANDOM_SECRET", "leakme-9999")
    sb = LocalRestrictedSandbox(str(tmp_path), timeout=30)
    res = sb.run("env")
    sb.close()
    assert "sk-should-not-leak-123456" not in res.output
    assert "leakme-9999" not in res.output
    # a throwaway HOME, not the real one
    assert os.path.expanduser("~") not in res.output or "dispatcher-home-" in res.output


def test_provider_key_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xyz-abcdef123456")
    sb = LocalRestrictedSandbox(str(tmp_path), timeout=30)
    res = sb.run("printenv ANTHROPIC_API_KEY || echo ABSENT")
    sb.close()
    assert "ABSENT" in res.output
    assert "sk-ant-xyz" not in res.output


def test_timeout_kills(tmp_path):
    sb = LocalRestrictedSandbox(str(tmp_path), timeout=1)
    start = time.monotonic()
    res = sb.run("sleep 8")
    sb.close()
    assert res.timed_out
    assert time.monotonic() - start < 6


def test_cancel_kills_tree(tmp_path):
    sb = LocalRestrictedSandbox(str(tmp_path), timeout=60)
    cancel = threading.Event()
    threading.Timer(0.4, cancel.set).start()
    start = time.monotonic()
    res = sb.run("sleep 10 & sleep 10; wait", cancel=cancel)
    sb.close()
    assert res.cancelled
    assert time.monotonic() - start < 5


def test_output_redacted(tmp_path):
    register_secret("supersecret-token-in-output-42")
    sb = LocalRestrictedSandbox(str(tmp_path), timeout=30)
    res = sb.run("echo supersecret-token-in-output-42")
    sb.close()
    assert "supersecret-token-in-output-42" not in res.output


def test_factory_defaults_restricted(tmp_path):
    sb = make_sandbox("restricted", str(tmp_path))
    assert sb.backend == "restricted"
    sb.close()


def test_factory_docker_falls_back_when_unavailable(tmp_path, monkeypatch):
    import app.sandbox as sbx
    monkeypatch.setattr(sbx.shutil, "which", lambda name: None)
    sb = sbx.make_sandbox("docker", str(tmp_path))
    assert sb.backend == "restricted"
    assert getattr(sb, "fell_back", False) is True
    sb.close()
