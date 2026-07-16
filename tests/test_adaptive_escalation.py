"""Adaptive mode: a failed solo verification escalates to a pair and retries.

Network is mocked; verification is mocked to fail once then pass; git is real.
The whole thing is driven synchronously (orc.run() in-thread) with signals on
DirectConnection, so no Qt event loop is needed.
"""
import json
import subprocess

import pytest

from PySide6.QtCore import QCoreApplication, Qt


class _Resp:
    def __init__(self, payload):
        self.status_code = 200
        self._p = payload
        self.text = json.dumps(payload)
        self.headers = {}

    def json(self):
        return self._p


def _anthropic(body):
    tools = body.get("tools") or []
    if not tools:                                   # planning / report / synth
        return _Resp({"content": [{"type": "text", "text": "Резюме."}],
                      "usage": {"input_tokens": 5, "output_tokens": 3},
                      "stop_reason": "end_turn"})
    last = body["messages"][-1]
    did = isinstance(last.get("content"), list) and any(
        b.get("type") == "tool_result" for b in last["content"])
    if did:
        return _Resp({"content": [{"type": "text", "text": "Готово."}],
                      "usage": {"input_tokens": 4, "output_tokens": 2},
                      "stop_reason": "end_turn"})
    return _Resp({"content": [{"type": "tool_use", "id": "t1", "name": "write_file",
                               "input": {"path": "feature.txt",
                                         "content": "attempt output\n"}}],
                  "usage": {"input_tokens": 9, "output_tokens": 4},
                  "stop_reason": "tool_use"})


def _openai(body):
    return _Resp({"choices": [{"message": {"role": "assistant",
                  "content": "Ревью: ок."}, "finish_reason": "stop"}],
                  "usage": {"prompt_tokens": 3, "completion_tokens": 2}})


@pytest.fixture
def escalation_env(monkeypatch, tmp_path):
    import app.cliagents as ca
    import app.config as cfgmod
    import app.providers.anthropic as anth
    import app.providers.openai_compat as oai

    monkeypatch.setattr(ca, "quick_ready", lambda flavor, home=None: False)

    def dispatch(url, headers=None, timeout=None, **kw):
        body = kw.get("json")
        return _anthropic(body) if "/messages" in url else _openai(body)

    monkeypatch.setattr(anth.requests, "post", dispatch)
    monkeypatch.setattr(oai.requests, "post", dispatch)

    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", tmp_path / "cfg" / "config.json")
    cfg = cfgmod.Config(cfgmod._default_config())
    for pid in list(cfg.providers):
        cfg.providers[pid]["enabled"] = pid in ("anthropic", "openai")
        if pid in ("anthropic", "openai"):
            cfg.providers[pid]["api_key"] = "k"
    cfg.orchestration["execution_mode"] = "adaptive"
    cfg.save()
    return cfg


def _drive(orc):
    QCoreApplication.instance() or QCoreApplication([])
    events = {"verifications": []}
    orc.verification_ready.connect(
        lambda v: events["verifications"].append(v["status"]), Qt.DirectConnection)
    orc.report_ready.connect(
        lambda r, u: events.__setitem__("report", r), Qt.DirectConnection)
    orc.run_finished.connect(
        lambda r: events.__setitem__("result", r), Qt.DirectConnection)
    orc.run_error.connect(
        lambda m: events.__setitem__("error", m), Qt.DirectConnection)
    orc.awaiting_approval.connect(
        lambda p: orc.approve(push=False), Qt.DirectConnection)
    orc.run()
    return events


def _branches(repo):
    return subprocess.run(["git", "branch", "--format=%(refname:short)"], cwd=repo,
                          capture_output=True, text=True).stdout.split()


def test_solo_failure_escalates_to_pair_then_passes(
        has_git, git_repo, tmp_path, escalation_env, qapp, monkeypatch):
    import app.orchestrator as orch
    from app.orchestrator import Orchestrator
    from app.verification import Check, VerificationResult

    calls = {"n": 0}

    def flaky_verify(self, root):
        calls["n"] += 1
        if calls["n"] == 1:                         # first (solo) attempt fails
            return VerificationResult(
                "fail", [Check("t", "x", "fail", 1, "exit=1 boom")], "high",
                ["t: fail"])
        return VerificationResult(                  # escalated attempt passes
            "pass", [Check("t", "x", "pass", 0, "exit=0")], "low", [])

    monkeypatch.setattr(orch.Orchestrator, "_verify", flaky_verify)

    project = {"id": "p1", "name": "Demo", "local_path": str(git_repo),
               "github_repo": "", "github_url": "", "branch": "main",
               "github_token": "", "verify_commands": [], "chat": [], "runs": []}
    orc = Orchestrator(escalation_env, project, "Добавь feature.txt")
    events = _drive(orc)

    assert "error" not in events, events.get("error")
    # verification ran twice: solo failed, escalated pair passed
    assert events["verifications"] == ["fail", "pass"]
    assert calls["n"] == 2
    # the report documents the escalation honestly
    assert "Эскалация" in events["report"] or "эскалац" in events["report"].lower()
    assert orc._escalation_note
    # published after the passing attempt; source tree untouched throughout
    assert events["result"]["status"] == "done" and events["result"]["commit"]
    integ = [b for b in _branches(str(git_repo)) if b.endswith("/integration")]
    assert integ
    # reputation saw both outcomes for the implementer (fail then pass)
    rec = orc.reputation.snapshot().get("anthropic", {})
    assert rec.get("verify_fail", 0) >= 1 and rec.get("verify_pass", 0) >= 1


def test_non_adaptive_mode_makes_single_attempt(
        has_git, git_repo, tmp_path, escalation_env, qapp, monkeypatch):
    """Guard: only adaptive escalates. A failing pair run does NOT retry."""
    import app.orchestrator as orch
    from app.orchestrator import Orchestrator
    from app.verification import Check, VerificationResult

    escalation_env.orchestration["execution_mode"] = "pair"
    calls = {"n": 0}

    def failing_verify(self, root):
        calls["n"] += 1
        return VerificationResult(
            "fail", [Check("t", "x", "fail", 1, "exit=1")], "high", ["t: fail"])

    monkeypatch.setattr(orch.Orchestrator, "_verify", failing_verify)
    project = {"id": "p1", "name": "Demo", "local_path": str(git_repo),
               "github_repo": "", "github_url": "", "branch": "main",
               "github_token": "", "verify_commands": [], "chat": [], "runs": []}
    orc = Orchestrator(escalation_env, project, "task")
    events = _drive(orc)
    assert calls["n"] == 1                          # no retry in pair mode
    assert not orc._escalation_note
    assert events["verifications"] == ["fail"]      # single attempt, no escalation
