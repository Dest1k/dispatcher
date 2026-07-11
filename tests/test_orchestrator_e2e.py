"""End-to-end orchestrator run with a mocked network layer.

Proves the safety-critical properties: agents work in isolated worktrees, the
SOURCE working tree is never modified, verification runs, and nothing is
published without approval. No real network.
"""
import json
import os
import subprocess

import pytest

POSIX = os.name != "nt"
pytestmark = pytest.mark.skipif(not POSIX, reason="verify uses POSIX 'true'")


class _Resp:
    def __init__(self, payload, headers=None):
        self.status_code = 200
        self._p = payload
        self.text = json.dumps(payload)
        self.headers = headers or {}

    def json(self):
        return self._p


def _anthropic(body):
    tools = body.get("tools") or []
    if not tools:                                   # planning/report/synthesis
        return _Resp({"content": [{"type": "text", "text": "Резюме: задача выполнена."}],
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
                               "input": {"path": "feature.txt", "content": "hello from agent\n"}}],
                  "usage": {"input_tokens": 9, "output_tokens": 4},
                  "stop_reason": "tool_use"})


def _openai(body):
    return _Resp({"choices": [{"message": {"role": "assistant",
                  "content": "Ревью: замечаний нет."}, "finish_reason": "stop"}],
                  "usage": {"prompt_tokens": 3, "completion_tokens": 2}})


@pytest.fixture
def mocked_net(monkeypatch):
    import app.providers.anthropic as anth
    import app.providers.openai_compat as oai

    def dispatch(url, headers=None, timeout=None, **kw):
        body = kw.get("json")
        return _anthropic(body) if "/messages" in url else _openai(body)

    monkeypatch.setattr(anth.requests, "post", dispatch)
    monkeypatch.setattr(oai.requests, "post", dispatch)


def _run(orc, approve=None):
    """Drive the QThread to completion; approve/reject at the gate."""
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    loop = QEventLoop()
    events = {}
    orc.awaiting_approval.connect(lambda p: events.setdefault("approval", p))
    orc.verification_ready.connect(lambda v: events.setdefault("verification", v))
    orc.run_finished.connect(lambda r: events.setdefault("finished", r))
    orc.run_error.connect(lambda m: events.setdefault("error", m))
    orc.run_finished.connect(lambda *_: loop.quit())
    orc.run_error.connect(lambda *_: loop.quit())
    if approve is not None:
        def on_gate(_p):
            orc.approve(push=False) if approve else orc.reject()
        orc.awaiting_approval.connect(on_gate)
    QTimer.singleShot(30000, loop.quit)
    orc.start()
    loop.exec()
    orc.wait(5000)
    return events


def _make_config(monkeypatch, tmp_path, active_ids=("anthropic", "openai")):
    import app.config as cfgmod
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", tmp_path / "cfg" / "config.json")
    cfg = cfgmod.Config(cfgmod._default_config())
    for pid in ("anthropic", "openai", "xai", "local"):
        cfg.providers[pid]["enabled"] = pid in active_ids
        if pid in active_ids and cfg.providers[pid]["auth"] == "api_key":
            cfg.providers[pid]["api_key"] = "k"
    return cfg


def _status(repo):
    return subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                          capture_output=True, text=True).stdout


def _branches(repo):
    return subprocess.run(["git", "branch", "--format=%(refname:short)"], cwd=repo,
                          capture_output=True, text=True).stdout.split()


def test_full_run_source_untouched_until_approved(has_git, git_repo, tmp_path,
                                                  mocked_net, monkeypatch, qapp):
    from app.orchestrator import Orchestrator
    cfg = _make_config(monkeypatch, tmp_path)
    project = {"id": "p1", "name": "Demo", "local_path": str(git_repo),
               "github_repo": "", "github_url": "", "branch": "main",
               "github_token": "", "verify_commands": [{"name": "ok", "command": "true"}],
               "chat": [], "runs": []}
    orc = Orchestrator(cfg, project, "Добавь feature.txt")
    events = _run(orc, approve=True)

    assert "error" not in events
    # SOURCE working tree stayed clean the whole time
    assert _status(str(git_repo)).strip() == ""
    # verification passed
    assert events["verification"]["status"] == "pass"
    fin = events["finished"]
    assert fin["status"] == "done" and fin["commit"]
    # integration branch kept with the work; agent temp branches removed
    branches = _branches(str(git_repo))
    integ = [b for b in branches if b.startswith("dispatcher/") and b.endswith("/integration")]
    assert integ, branches
    # the change lives on the integration branch, not the source working tree
    assert not (git_repo / "feature.txt").exists()
    show = subprocess.run(["git", "show", f"{integ[0]}:feature.txt"], cwd=str(git_repo),
                          capture_output=True, text=True)
    assert "hello from agent" in show.stdout


def test_rejection_leaves_repo_pristine(has_git, git_repo, tmp_path, mocked_net,
                                        monkeypatch, qapp):
    from app.orchestrator import Orchestrator
    cfg = _make_config(monkeypatch, tmp_path)
    project = {"id": "p1", "name": "Demo", "local_path": str(git_repo),
               "github_repo": "", "github_url": "", "branch": "main",
               "github_token": "", "verify_commands": [{"name": "ok", "command": "true"}],
               "chat": [], "runs": []}
    orc = Orchestrator(cfg, project, "Добавь feature.txt")
    events = _run(orc, approve=False)

    assert events["finished"]["status"] == "cancelled"
    assert _status(str(git_repo)).strip() == ""
    assert not (git_repo / "feature.txt").exists()
    # no dispatcher branches left behind
    assert not any(b.startswith("dispatcher/") for b in _branches(str(git_repo)))


def test_dirty_source_is_blocked(has_git, git_repo, tmp_path, mocked_net,
                                 monkeypatch, qapp):
    from app.orchestrator import Orchestrator
    (git_repo / "uncommitted.txt").write_text("user work in progress")
    cfg = _make_config(monkeypatch, tmp_path)
    project = {"id": "p1", "name": "Demo", "local_path": str(git_repo),
               "github_repo": "", "github_url": "", "branch": "main",
               "github_token": "", "verify_commands": [{"name": "ok", "command": "true"}],
               "chat": [], "runs": []}
    orc = Orchestrator(cfg, project, "task")
    events = _run(orc)          # no approval needed; should error out first
    assert "error" in events
    assert "незакоммиченные" in events["error"]
    # user's uncommitted file is still there, untouched
    assert (git_repo / "uncommitted.txt").read_text() == "user work in progress"


def test_verification_failure_blocks_autopublish(has_git, git_repo, tmp_path,
                                                 mocked_net, monkeypatch, qapp):
    from app.orchestrator import Orchestrator
    cfg = _make_config(monkeypatch, tmp_path)
    project = {"id": "p1", "name": "Demo", "local_path": str(git_repo),
               "github_repo": "", "github_url": "", "branch": "main",
               "github_token": "", "verify_commands": [{"name": "bad", "command": "false"}],
               "chat": [], "runs": []}
    orc = Orchestrator(cfg, project, "task")

    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    captured = {}
    orc.awaiting_approval.connect(lambda p: captured.setdefault("gate", p))
    orc.awaiting_approval.connect(lambda p: orc.reject())
    orc.run_finished.connect(lambda *_: loop.quit())
    orc.run_error.connect(lambda *_: loop.quit())
    QTimer.singleShot(30000, loop.quit)
    orc.start()
    loop.exec()
    orc.wait(5000)

    assert captured["gate"]["status"] == "fail"
    assert captured["gate"]["can_autopublish"] is False
