"""End-to-end DAG execution in the orchestrator: layered isolated worktrees,
each layer building on the previous layer's integrated commit. Mocked network,
real git, driven synchronously.
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


# The lead's DAG plan: t1 (base file) → t2 (depends on t1, appends to a NEW file).
_DAG = {"tasks": [
    {"id": "t1", "provider": "anthropic", "title": "База",
     "objective": "создать base.txt", "files": ["base.txt"], "depends_on": []},
    {"id": "t2", "provider": "openai", "title": "Надстройка",
     "objective": "создать built.txt на основе base.txt",
     "files": ["built.txt"], "depends_on": ["t1"]},
]}


def _anthropic(body):
    tools = body.get("tools") or []
    blob = json.dumps(body, ensure_ascii=False)
    if not tools:                                    # planning (DAG) / report / review
        if "ациклический" in blob:                   # the DAG-planning system prompt
            return _Resp({"content": [{"type": "text",
                          "text": json.dumps(_DAG, ensure_ascii=False)}],
                          "usage": {"input_tokens": 5, "output_tokens": 5},
                          "stop_reason": "end_turn"})
        return _Resp({"content": [{"type": "text", "text": "Резюме."}],
                      "usage": {"input_tokens": 3, "output_tokens": 2},
                      "stop_reason": "end_turn"})
    # tool loop: write the file named in the objective, once
    last = body["messages"][-1]
    did = isinstance(last.get("content"), list) and any(
        b.get("type") == "tool_result" for b in last["content"])
    if did:
        return _Resp({"content": [{"type": "text", "text": "Готово."}],
                      "usage": {"input_tokens": 2, "output_tokens": 1},
                      "stop_reason": "end_turn"})
    # which file? infer from the system/first user message objective
    blob = json.dumps(body, ensure_ascii=False)
    path = "built.txt" if "built.txt" in blob else "base.txt"
    return _Resp({"content": [{"type": "tool_use", "id": "t", "name": "write_file",
                               "input": {"path": path, "content": path + "\n"}}],
                  "usage": {"input_tokens": 6, "output_tokens": 3},
                  "stop_reason": "tool_use"})


@pytest.fixture
def dag_env(monkeypatch, tmp_path):
    import app.cliagents as ca
    import app.config as cfgmod
    import app.orchestrator as orch
    import app.providers.anthropic as anth
    import app.providers.openai_compat as oai
    from app.verification import Check, VerificationResult

    monkeypatch.setattr(ca, "quick_ready", lambda flavor, home=None: False)

    # anth.requests and oai.requests are the SAME module object, so a single
    # dispatcher routed by URL must handle both (patching twice clobbers).
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
    cfg.orchestration["dag_execution"] = True
    cfg.orchestration["lead_provider"] = "anthropic"
    cfg.save()
    return cfg


def _openai(body):
    # openai as an implementer: same write-once tool loop shape
    msgs = body.get("messages", [])
    did = any(m.get("role") == "tool" for m in msgs)
    if did or not body.get("tools"):
        return _Resp({"choices": [{"message": {"role": "assistant",
                      "content": "Готово."}, "finish_reason": "stop"}],
                      "usage": {"prompt_tokens": 2, "completion_tokens": 1}})
    return _Resp({"choices": [{"message": {"role": "assistant", "content": "",
                  "tool_calls": [{"id": "c1", "type": "function",
                  "function": {"name": "write_file",
                  "arguments": json.dumps({"path": "built.txt",
                                           "content": "built.txt\n"})}}]},
                  "finish_reason": "tool_calls"}],
                  "usage": {"prompt_tokens": 4, "completion_tokens": 2}})


def _drive(orc):
    QCoreApplication.instance() or QCoreApplication([])
    ev = {}
    orc.plan_ready.connect(lambda p: ev.setdefault("plan", p), Qt.DirectConnection)
    orc.integration_ready.connect(lambda i: ev.__setitem__("integ", i), Qt.DirectConnection)
    orc.run_finished.connect(lambda r: ev.__setitem__("result", r), Qt.DirectConnection)
    orc.run_error.connect(lambda m: ev.__setitem__("error", m), Qt.DirectConnection)
    orc.awaiting_approval.connect(lambda p: orc.approve(push=False), Qt.DirectConnection)
    orc.run()
    return ev


def _branches(repo):
    return subprocess.run(["git", "branch", "--format=%(refname:short)"], cwd=repo,
                          capture_output=True, text=True, encoding="utf-8").stdout.split()


def test_dag_layers_execute_and_integrate(
        has_git, git_repo, tmp_path, dag_env, qapp):
    from app.orchestrator import Orchestrator
    project = {"id": "p1", "name": "Demo", "local_path": str(git_repo),
               "github_repo": "", "github_url": "", "branch": "main",
               "github_token": "", "verify_commands": [], "chat": [], "runs": []}
    orc = Orchestrator(dag_env, project, "Построить фичу поэтапно")
    ev = _drive(orc)

    assert "error" not in ev, ev.get("error")
    # the DAG plan was surfaced with a layered overview
    assert "Граф задач" in ev["plan"]["overview"]
    # both layers integrated: base.txt AND built.txt present in the result
    changed = set(ev["integ"]["changed_files"])
    assert {"base.txt", "built.txt"} <= changed, changed
    # published: the integration branch carries both, source tree untouched
    assert ev["result"]["status"] == "done" and ev["result"]["commit"]
    integ = [b for b in _branches(str(git_repo)) if b.endswith("/integration")]
    assert integ
    for fname in ("base.txt", "built.txt"):
        show = subprocess.run(["git", "show", f"{integ[0]}:{fname}"],
                              cwd=str(git_repo), capture_output=True, text=True,
                              encoding="utf-8")
        assert fname in show.stdout
    assert not (git_repo / "base.txt").exists()


def test_dag_disabled_uses_normal_flow(
        has_git, git_repo, tmp_path, dag_env, qapp, monkeypatch):
    # with dag_execution off, the DAG planner must not run
    dag_env.orchestration["dag_execution"] = False
    from app.orchestrator import Orchestrator
    called = {"dag": False}
    monkeypatch.setattr(Orchestrator, "_plan_dag",
                        lambda self, ctx: called.__setitem__("dag", True) or None)
    project = {"id": "p1", "name": "Demo", "local_path": str(git_repo),
               "github_repo": "", "github_url": "", "branch": "main",
               "github_token": "", "verify_commands": [], "chat": [], "runs": []}
    orc = Orchestrator(dag_env, project, "task")
    _drive(orc)
    assert called["dag"] is False
