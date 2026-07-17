"""Safe mid-run hot-join: disjoint-zone validation + tracked-thread execution.

Unit tests drive add_agent against a constructed orchestrator (no QThread run);
an e2e test hot-joins a real agent mid-execution over mocked providers + real
git and asserts its patch integrates alongside the original agent's.
"""
import json
import subprocess
import threading
import time

from PySide6.QtCore import QCoreApplication, Qt


def _orc(tmp_path, monkeypatch, active_ids=("anthropic", "openai")):
    import app.cliagents as ca
    import app.config as cfgmod
    from app.orchestrator import Orchestrator
    from app.persistence import RunStore
    monkeypatch.setattr(ca, "quick_ready", lambda flavor, home=None: False)
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", tmp_path / "cfg" / "config.json")
    cfg = cfgmod.Config(cfgmod._default_config())
    for pid in list(cfg.providers):
        cfg.providers[pid]["enabled"] = pid in active_ids
        if pid in active_ids and cfg.providers[pid]["auth"] == "api_key":
            cfg.providers[pid]["api_key"] = "k"
    project = {"id": "p1", "name": "Demo", "local_path": str(tmp_path)}
    return Orchestrator(cfg, project, "инструкция", store=RunStore(tmp_path / "db.sqlite"))


# ---- unit: add_agent guard rails --------------------------------------------

def test_declines_when_not_executing(tmp_path, monkeypatch):
    orc = _orc(tmp_path, monkeypatch)
    # phase is "" (not executing) → decline regardless of zone
    assert orc.add_agent({"id": "xai", "short": "Grok", "strength": "rt"},
                         files=["free.py"]) is False


def test_declines_without_zone_during_execution(tmp_path, monkeypatch):
    orc = _orc(tmp_path, monkeypatch)
    orc._phase = "executing"
    orc.rw = object()                    # non-None sentinel; zone check comes first
    assert orc.add_agent({"id": "xai", "short": "Grok"}, files=[]) is False


def test_declines_on_zone_overlap(tmp_path, monkeypatch):
    orc = _orc(tmp_path, monkeypatch)
    orc._phase = "executing"
    orc.rw = object()
    orc.live_ids.add("anthropic")
    orc.assignments["anthropic"] = {"files": ["src/"]}
    # joiner wants src/main.py which lives under the active zone src/ → overlap
    logs = []
    orc.log.connect(lambda m: logs.append(m))
    assert orc.add_agent({"id": "xai", "short": "Grok"},
                         files=["src/main.py"]) is False
    assert any("пересекается" in m for m in logs)


def test_already_live_returns_true(tmp_path, monkeypatch):
    orc = _orc(tmp_path, monkeypatch)
    orc.live_ids.add("openai")
    assert orc.add_agent({"id": "openai"}) is True


# ---- e2e: real hot-join mid-execution ---------------------------------------

class _Resp:
    def __init__(self, payload):
        self.status_code = 200
        self._p = payload
        self.text = json.dumps(payload)
        self.headers = {}

    def json(self):
        return self._p


def _writer(path, content, gate=None):
    """Anthropic-style tool loop that writes one file (optionally after a gate)."""
    def handler(body):
        tools = body.get("tools") or []
        if not tools:                                    # planning/report/review
            return _Resp({"content": [{"type": "text", "text": "ok"}],
                          "usage": {"input_tokens": 1, "output_tokens": 1},
                          "stop_reason": "end_turn"})
        last = body["messages"][-1]
        did = isinstance(last.get("content"), list) and any(
            b.get("type") == "tool_result" for b in last["content"])
        if did:
            return _Resp({"content": [{"type": "text", "text": "done"}],
                          "usage": {"input_tokens": 1, "output_tokens": 1},
                          "stop_reason": "end_turn"})
        if gate is not None:
            gate.wait(10)
        return _Resp({"content": [{"type": "tool_use", "id": "t", "name": "write_file",
                                   "input": {"path": path, "content": content}}],
                      "usage": {"input_tokens": 2, "output_tokens": 1},
                      "stop_reason": "tool_use"})
    return handler


def test_hot_join_integrates_alongside_original(
        has_git, git_repo, tmp_path, monkeypatch, qapp):
    import app.cliagents as ca
    import app.config as cfgmod
    import app.orchestrator as orch
    import app.providers.anthropic as anth
    import app.providers.openai_compat as oai
    from app.orchestrator import Orchestrator
    from app.verification import Check, VerificationResult

    monkeypatch.setattr(ca, "quick_ready", lambda flavor, home=None: False)
    # anthropic (original) writes a.txt but waits on a gate so the run is still
    # executing when we hot-join; openai (joiner) writes b.txt.
    gate = threading.Event()
    anth_handler = _writer("a.txt", "from A\n", gate=gate)

    def oai_handler(body):
        # openai adapter speaks a different wire format; emulate a one-shot write
        msgs = body.get("messages", [])
        if any(m.get("role") == "tool" for m in msgs) or not body.get("tools"):
            return _Resp({"choices": [{"message": {"role": "assistant",
                          "content": "done"}, "finish_reason": "stop"}],
                          "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
        return _Resp({"choices": [{"message": {"role": "assistant", "content": "",
                      "tool_calls": [{"id": "c", "type": "function",
                      "function": {"name": "write_file",
                      "arguments": json.dumps({"path": "b.txt", "content": "from B\n"})}}]},
                      "finish_reason": "tool_calls"}],
                      "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    # anth.requests and oai.requests are the SAME module — a single dispatcher
    # routed by URL must serve both (patching twice would clobber).
    def dispatch(url, **kw):
        body = kw.get("json")
        return anth_handler(body) if "/messages" in url else oai_handler(body)

    monkeypatch.setattr(anth.requests, "post", dispatch)
    monkeypatch.setattr(oai.requests, "post", dispatch)
    monkeypatch.setattr(orch.Orchestrator, "_verify",
                        lambda self, root: VerificationResult(
                            "pass", [Check("t", "x", "pass", 0, "e")], "low", []))

    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", tmp_path / "cfg" / "config.json")
    cfg = cfgmod.Config(cfgmod._default_config())
    for pid in list(cfg.providers):
        cfg.providers[pid]["enabled"] = pid == "anthropic"      # start solo
        if pid == "anthropic":
            cfg.providers[pid]["api_key"] = "k"
    cfg.orchestration["execution_mode"] = "solo"
    cfg.providers["openai"]["api_key"] = "k"                    # joiner (not active)

    project = {"id": "p1", "name": "Demo", "local_path": str(git_repo),
               "github_repo": "", "github_url": "", "branch": "main",
               "github_token": "", "verify_commands": [], "chat": [], "runs": []}
    orc = Orchestrator(cfg, project, "Добавь a.txt", store=None)

    QCoreApplication.instance() or QCoreApplication([])
    ev = {}
    orc.integration_ready.connect(lambda i: ev.setdefault("integ", i), Qt.DirectConnection)
    orc.run_finished.connect(lambda r: ev.setdefault("result", r), Qt.DirectConnection)
    orc.run_error.connect(lambda m: ev.setdefault("error", m), Qt.DirectConnection)
    orc.awaiting_approval.connect(lambda p: orc.approve(push=False), Qt.DirectConnection)

    # hot-join openai on a disjoint zone (b.txt) once execution is underway,
    # then release the original agent's gate.
    def joiner():
        for _ in range(200):
            if orc._phase == "executing":
                break
            time.sleep(0.02)
        joined = orc.add_agent(dict(cfg.providers["openai"]), files=["b.txt"])
        ev["joined"] = joined
        gate.set()

    t = threading.Thread(target=joiner, daemon=True)
    t.start()
    orc.run()
    t.join(5)

    assert "error" not in ev, ev.get("error")
    assert ev.get("joined") is True
    files = set(ev["integ"]["changed_files"])
    assert {"a.txt", "b.txt"} <= files, files          # both agents integrated
    assert ev["result"]["status"] == "done" and ev["result"]["commit"]
    assert _status(str(git_repo)).strip() == ""        # source tree untouched


def _status(repo):
    return subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                          capture_output=True, text=True, encoding="utf-8").stdout
