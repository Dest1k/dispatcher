"""Pre-implementation council deliberation feeding the orchestrator.

Network mocked; verification mocked to pass; git real. Driven synchronously.
The deliberation council answers are captured so we can assert the approach is
produced, injected, recorded in memory, and reflected in the report.
"""
import json

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
    if not tools:                                   # reasoning (deliberation/plan/report)
        return _Resp({"content": [{"type": "text",
                      "text": "ПОДХОД: сделать через feature flag."}],
                      "usage": {"input_tokens": 6, "output_tokens": 4},
                      "stop_reason": "end_turn"})
    last = body["messages"][-1]
    did = isinstance(last.get("content"), list) and any(
        b.get("type") == "tool_result" for b in last["content"])
    if did:
        return _Resp({"content": [{"type": "text", "text": "Готово."}],
                      "usage": {"input_tokens": 4, "output_tokens": 2},
                      "stop_reason": "end_turn"})
    return _Resp({"content": [{"type": "tool_use", "id": "t1", "name": "write_file",
                               "input": {"path": "feature.txt", "content": "done\n"}}],
                  "usage": {"input_tokens": 9, "output_tokens": 4},
                  "stop_reason": "tool_use"})


def _openai(body):
    return _Resp({"choices": [{"message": {"role": "assistant",
                  "content": "РИСК: учесть обратную совместимость."},
                  "finish_reason": "stop"}],
                  "usage": {"prompt_tokens": 3, "completion_tokens": 2}})


@pytest.fixture
def delib_env(monkeypatch, tmp_path):
    import app.cliagents as ca
    import app.config as cfgmod
    import app.orchestrator as orch
    import app.providers.anthropic as anth
    import app.providers.openai_compat as oai
    from app.verification import Check, VerificationResult

    monkeypatch.setattr(ca, "quick_ready", lambda flavor, home=None: False)

    def dispatch(url, headers=None, timeout=None, **kw):
        body = kw.get("json")
        return _anthropic(body) if "/messages" in url else _openai(body)

    monkeypatch.setattr(anth.requests, "post", dispatch)
    monkeypatch.setattr(oai.requests, "post", dispatch)
    monkeypatch.setattr(
        orch.Orchestrator, "_verify",
        lambda self, root: VerificationResult(
            "pass", [Check("t", "x", "pass", 0, "exit=0")], "low", []))

    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", tmp_path / "cfg" / "config.json")
    cfg = cfgmod.Config(cfgmod._default_config())
    for pid in list(cfg.providers):
        cfg.providers[pid]["enabled"] = pid in ("anthropic", "openai")
        if pid in ("anthropic", "openai"):
            cfg.providers[pid]["api_key"] = "k"
    cfg.orchestration["execution_mode"] = "solo"
    cfg.save()
    return cfg


def _project(git_repo):
    return {"id": "p1", "name": "Demo", "local_path": str(git_repo),
            "github_repo": "", "github_url": "", "branch": "main",
            "github_token": "", "verify_commands": [], "chat": [], "runs": []}


def _drive(orc):
    QCoreApplication.instance() or QCoreApplication([])
    events = {"delib": None}
    orc.deliberation_ready.connect(
        lambda d: events.__setitem__("delib", d), Qt.DirectConnection)
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


def test_deliberation_runs_and_feeds_pipeline(
        has_git, git_repo, tmp_path, delib_env, qapp, monkeypatch):
    from app.memory_graph import MemoryGraph
    from app.orchestrator import Orchestrator
    delib_env.orchestration["deliberate"] = True
    memory = MemoryGraph(tmp_path / "mem.db")
    orc = Orchestrator(delib_env, _project(git_repo), "Добавь feature",
                       memory=memory)
    events = _drive(orc)

    assert "error" not in events, events.get("error")
    # the council deliberated: a CouncilResult was emitted with opinions
    assert events["delib"] is not None
    assert events["delib"]["opinions"]
    assert events["delib"]["mode"] == "full_council"
    # the agreed approach was captured and put in the report
    assert orc._deliberation_text
    assert "Обсуждение подхода (совет)" in events["report"]
    # …and recorded in project memory as an 'approach' node
    approaches = memory.recent("p1", kinds=("approach",))
    assert approaches and "Подход совета" in approaches[0].title
    memory.close()


def test_no_deliberation_by_default(
        has_git, git_repo, tmp_path, delib_env, qapp):
    from app.orchestrator import Orchestrator
    # deliberate defaults to False
    orc = Orchestrator(delib_env, _project(git_repo), "task")
    events = _drive(orc)
    assert events["delib"] is None
    assert not orc._deliberation_text
    assert "Обсуждение подхода" not in events.get("report", "")


def test_full_council_mode_auto_deliberates(
        has_git, git_repo, tmp_path, delib_env, qapp):
    """full_council is the vision's 'complete reasoning pipeline' — it
    deliberates automatically even without the explicit deliberate flag."""
    from app.memory_graph import MemoryGraph
    from app.orchestrator import Orchestrator
    delib_env.orchestration["execution_mode"] = "full_council"
    assert delib_env.orchestration.get("deliberate") is False   # not set on
    memory = MemoryGraph(tmp_path / "mem.db")
    orc = Orchestrator(delib_env, _project(git_repo), "task", memory=memory)
    events = _drive(orc)
    assert events["delib"] is not None
    assert orc._deliberation_text
    memory.close()


def test_deliberation_skipped_with_single_provider(
        has_git, git_repo, tmp_path, delib_env, qapp):
    from app.orchestrator import Orchestrator
    delib_env.orchestration["deliberate"] = True
    for pid in list(delib_env.providers):
        delib_env.providers[pid]["enabled"] = pid == "anthropic"
    orc = Orchestrator(delib_env, _project(git_repo), "task")
    events = _drive(orc)
    # one provider → no council to deliberate with
    assert events["delib"] is None
    assert not orc._deliberation_text


def test_deliberation_via_run_flag(
        has_git, git_repo, tmp_path, delib_env, qapp, monkeypatch, capsys):
    import app.cli as cli
    from app.memory_graph import MemoryGraph

    # route the orchestrator's default memory/reputation to tmp so the CLI path
    # (which builds its own) stays hermetic
    monkeypatch.setattr("app.memory_graph._default_path",
                        lambda: tmp_path / "cli-mem.db")
    monkeypatch.setattr("app.reputation._default_path",
                        lambda: tmp_path / "cli-rep.json")
    code = cli.main(["run", "Добавь feature", "--project", str(git_repo),
                     "--mode", "solo", "--deliberate", "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Обсуждение совета" in out
    assert "Синтез" in out
    # even on dry-run the approach was recorded in memory
    mem = MemoryGraph(tmp_path / "cli-mem.db")
    assert mem.recent("adhoc:repo", kinds=("approach",)) or \
        mem.search("adhoc:repo", "Подход")
    mem.close()
