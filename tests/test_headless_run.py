"""Headless `dispatcher run`: publication-decision policy + end-to-end wiring.

The decision policy is a pure function tested exhaustively. The end-to-end test
mocks the provider network and the verification stage (cross-platform), keeps
git real, and asserts the safety invariants hold from the CLI entry point.
"""
import json
import subprocess

import pytest

import app.cli as cli
from app.cli import decide_publication


# ---- pure decision policy ---------------------------------------------------

def test_decide_dry_run_always_rejects():
    action, push, _ = decide_publication(
        "pass", True, "low", dry_run=True, yes=True, push=True)
    assert action == "reject" and push is False


def test_decide_pass_low_risk_approves_local_by_default():
    action, push, reason = decide_publication(
        "pass", True, "low", dry_run=False, yes=False, push=False)
    assert action == "approve" and push is False
    assert "локально" in reason


def test_decide_pass_with_push():
    action, push, _ = decide_publication(
        "pass", True, "low", dry_run=False, yes=False, push=True)
    assert action == "approve" and push is True


def test_decide_blocked_verification_needs_yes():
    action, push, _ = decide_publication(
        "fail", False, "high", dry_run=False, yes=False, push=False)
    assert action == "reject" and push is False
    action2, push2, reason2 = decide_publication(
        "fail", False, "high", dry_run=False, yes=True, push=True)
    assert action2 == "approve"          # override commits locally
    assert "пуш будет отменён" in reason2  # but push is refused downstream


def test_decide_partial_needs_yes():
    action, _, _ = decide_publication(
        "partial", True, "low", dry_run=False, yes=False, push=False)
    assert action == "reject"
    action2, _, _ = decide_publication(
        "partial", True, "low", dry_run=False, yes=True, push=False)
    assert action2 == "approve"


def test_decide_high_risk_needs_yes_even_when_verification_ok():
    action, _, _ = decide_publication(
        "pass", True, "high", dry_run=False, yes=False, push=False)
    assert action == "reject"
    action2, _, _ = decide_publication(
        "pass", True, "high", dry_run=False, yes=True, push=False)
    assert action2 == "approve"


# ---- end-to-end through the CLI --------------------------------------------

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
    if not tools:
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
                                         "content": "hello from agent\n"}}],
                  "usage": {"input_tokens": 9, "output_tokens": 4},
                  "stop_reason": "tool_use"})


@pytest.fixture
def mocked_run_env(monkeypatch):
    import app.cliagents as ca
    import app.orchestrator as orch
    import app.providers.anthropic as anth
    import app.providers.openai_compat as oai
    from app.verification import Check, VerificationResult

    # no real CLIs, no real network, no real sandboxed verification
    monkeypatch.setattr(ca, "quick_ready", lambda flavor, home=None: False)

    def dispatch(url, headers=None, timeout=None, **kw):
        return _anthropic(kw.get("json"))

    monkeypatch.setattr(anth.requests, "post", dispatch)
    monkeypatch.setattr(oai.requests, "post", dispatch)

    def fake_verify(self, root):
        return VerificationResult(
            "pass", [Check("smoke", "true", "pass", 0, "exit=0")], "low", [])

    monkeypatch.setattr(orch.Orchestrator, "_verify", fake_verify)


def _cfg_at(monkeypatch, tmp_path):
    import app.config as cfgmod
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", tmp_path / "cfg" / "config.json")
    cfg = cfgmod.Config(cfgmod._default_config())
    for pid in list(cfg.providers):
        cfg.providers[pid]["enabled"] = pid == "anthropic"
    cfg.providers["anthropic"]["api_key"] = "k"
    cfg.orchestration["execution_mode"] = "solo"
    cfg.save()


def _branches(repo):
    return subprocess.run(["git", "branch", "--format=%(refname:short)"], cwd=repo,
                          capture_output=True, text=True).stdout.split()


def _status(repo):
    return subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                          capture_output=True, text=True).stdout


def test_run_approves_and_commits_integration_branch(
        has_git, git_repo, tmp_path, mocked_run_env, monkeypatch, qapp, capsys):
    _cfg_at(monkeypatch, tmp_path)
    code = cli.main(["run", "Добавь feature.txt", "--project", str(git_repo)])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "Решение: approve" in out
    # SOURCE tree never touched; feature lives only on the integration branch
    assert _status(str(git_repo)).strip() == ""
    assert not (git_repo / "feature.txt").exists()
    integ = [b for b in _branches(str(git_repo))
             if b.startswith("dispatcher/") and b.endswith("/integration")]
    assert integ, out
    show = subprocess.run(["git", "show", f"{integ[0]}:feature.txt"],
                          cwd=str(git_repo), capture_output=True, text=True)
    assert "hello from agent" in show.stdout


def test_run_dry_run_rejects_and_leaves_no_branch(
        has_git, git_repo, tmp_path, mocked_run_env, monkeypatch, qapp, capsys):
    _cfg_at(monkeypatch, tmp_path)
    code = cli.main(["run", "task", "--project", str(git_repo), "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Решение: reject" in out
    assert not any(b.startswith("dispatcher/") for b in _branches(str(git_repo)))
    assert _status(str(git_repo)).strip() == ""


def test_run_rejects_non_git_project(tmp_path, mocked_run_env, monkeypatch, capsys):
    _cfg_at(monkeypatch, tmp_path)
    plain = tmp_path / "plain"
    plain.mkdir()
    code = cli.main(["run", "task", "--project", str(plain)])
    assert code == 2
    assert "git-репозитор" in capsys.readouterr().out


def test_run_no_providers(tmp_path, monkeypatch, capsys, has_git, git_repo):
    import app.cliagents as ca
    import app.config as cfgmod
    monkeypatch.setattr(ca, "quick_ready", lambda flavor, home=None: False)
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", tmp_path / "cfg" / "config.json")
    cfg = cfgmod.Config(cfgmod._default_config())
    for pid in list(cfg.providers):
        cfg.providers[pid]["enabled"] = False
    cfg.save()
    code = cli.main(["run", "task", "--project", str(git_repo)])
    assert code == 1
    assert "Нет активных провайдеров" in capsys.readouterr().out


def test_run_json_output(has_git, git_repo, tmp_path, mocked_run_env,
                         monkeypatch, qapp, capsys):
    _cfg_at(monkeypatch, tmp_path)
    code = cli.main(["run", "task", "--project", str(git_repo), "--json"])
    out = capsys.readouterr().out
    assert code == 0
    payload = json.loads(out[out.index("{"):])
    assert payload["result"]["status"] == "done"
    assert payload["decision"][0] == "approve"
