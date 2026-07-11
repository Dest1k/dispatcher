"""Baseline coverage of the provider-neutral engine, tools, git and config.

Records the working behavior established in Phase 0. Uses fake adapters only —
no network.
"""
import json
import threading

import pytest

from app import git_service
from app.config import Config, _default_config
from app.providers import Usage, make_adapter
from app.providers.base import CompletionResult, Message, Steering, ToolCall, run_agent
from app.tools import TOOL_SPECS, ProjectTools


# ---- tools -----------------------------------------------------------
def test_tools_write_read_edit(tmp_path):
    tools = ProjectTools(str(tmp_path))
    tools.execute("write_file", {"path": "hello.py", "content": "print(1)\n"})
    assert (tmp_path / "hello.py").exists()
    assert "print(1)" in tools.execute("read_file", {"path": "hello.py"})
    tools.execute("edit_file", {"path": "hello.py", "find": "print(1)", "replace": "print(42)"})
    assert "print(42)" in (tmp_path / "hello.py").read_text()


def test_tools_run_command(tmp_path):
    out = ProjectTools(str(tmp_path)).execute("run_command", {"command": "echo integration"})
    assert "integration" in out and "exit=0" in out


def test_tools_path_escape_blocked(tmp_path):
    out = ProjectTools(str(tmp_path)).execute("read_file", {"path": "../../../etc/passwd"})
    assert out.startswith("ERROR")


# ---- agent loop ------------------------------------------------------
class FakeAdapter:
    def __init__(self, script):
        self.script = script
        self.turn = 0

    def complete(self, system, messages, tools):
        step = self.script[self.turn]
        self.turn += 1
        return CompletionResult(
            text=step.get("text", ""), thinking="",
            tool_calls=step.get("tool_calls", []),
            usage=Usage(100, 50), raw_assistant=None,
            stop_reason="tool_use" if step.get("tool_calls") else "end_turn")


def test_agent_loop_writes_and_finishes(tmp_path):
    tools = ProjectTools(str(tmp_path))
    script = [
        {"tool_calls": [ToolCall("t1", "write_file", {"path": "out.txt", "content": "done"})]},
        {"tool_calls": [ToolCall("t2", "finish", {"summary": "готово"})]},
    ]
    usage = Usage()
    final = run_agent(FakeAdapter(script), "sys", [Message("user", text="task")],
                      TOOL_SPECS, tools.execute, Steering(), lambda k, p: None,
                      24, threading.Event(), usage)
    assert (tmp_path / "out.txt").read_text() == "done"
    assert "готово" in final
    assert usage.input_tokens == 200 and usage.output_tokens == 100


def test_steering_reaches_model(tmp_path):
    seen = {}
    steering = Steering()
    steering.add("используй steered.txt")

    class SteerAdapter:
        def __init__(self):
            self.turn = 0

        def complete(self, system, messages, tools):
            seen["has"] = any("steered.txt" in m.text for m in messages)
            self.turn += 1
            calls = [ToolCall("s1", "finish", {"summary": "ок"})] if self.turn == 1 else []
            return CompletionResult("ок", "", calls, Usage(1, 1), None, "end_turn")

    run_agent(SteerAdapter(), "sys", [Message("user", text="t")], TOOL_SPECS,
              ProjectTools(str(tmp_path)).execute, steering, lambda k, p: None,
              24, threading.Event(), Usage())
    assert seen.get("has")


def test_cancel_stops_before_call(tmp_path):
    cancel = threading.Event()
    cancel.set()

    class Never:
        def complete(self, *a):
            raise AssertionError("must not be called")

    final = run_agent(Never(), "s", [Message("user", text="t")], TOOL_SPECS,
                      ProjectTools(str(tmp_path)).execute, Steering(),
                      lambda k, p: None, 24, cancel, Usage())
    assert "остановлено" in final


# ---- provider message conversion ------------------------------------
def test_anthropic_conversion():
    anth = make_adapter(_default_config()["providers"]["anthropic"])
    msgs = [
        Message("user", text="X"),
        Message("assistant", text="", tool_calls=[ToolCall("a1", "read_file", {"path": "x"})]),
        Message("tool", text="c", tool_call_id="a1", name="read_file"),
    ]
    conv = anth._to_messages(msgs)
    assert conv[1]["content"][0]["type"] == "tool_use"
    assert conv[2]["role"] == "user" and conv[2]["content"][0]["type"] == "tool_result"


def test_openai_conversion():
    oai = make_adapter(_default_config()["providers"]["openai"])
    msgs = [
        Message("user", text="X"),
        Message("assistant", text="", tool_calls=[ToolCall("a1", "read_file", {"path": "x"})]),
        Message("tool", text="c", tool_call_id="a1", name="read_file"),
    ]
    conv = oai._to_messages("sys", msgs)
    assert conv[0]["role"] == "system"
    assert "tool_calls" in conv[2]
    assert conv[3]["role"] == "tool" and conv[3]["tool_call_id"] == "a1"


def test_openai_body_omits_tool_choice_when_no_tools():
    from app.providers.base import ToolSpec
    oai = make_adapter(_default_config()["providers"]["openai"])
    # tool-less call (planning / review / report): no tools, no tool_choice —
    # OpenAI-compatible APIs reject tool_choice when no tools are supplied.
    body = oai._build_body("sys", [Message("user", text="hi")], [])
    assert "tools" not in body and "tool_choice" not in body
    # with tools, both are present
    spec = ToolSpec("read_file", "read", {"type": "object", "properties": {}})
    body2 = oai._build_body("sys", [Message("user", text="hi")], [spec])
    assert body2["tool_choice"] == "auto"
    assert body2["tools"][0]["function"]["name"] == "read_file"


def test_openai_token_field_by_provider():
    # OpenAI uses max_completion_tokens; xAI (and other compatibles) max_tokens.
    oai = make_adapter(_default_config()["providers"]["openai"])
    xai = make_adapter(_default_config()["providers"]["xai"])
    assert "max_completion_tokens" in oai._build_body("s", [], [])
    assert "max_tokens" in xai._build_body("s", [], [])


# ---- git (read-only helpers; mutation is covered by test_workspace) --
def test_git_readonly_helpers(has_git, git_repo):
    assert git_service.has_repo(str(git_repo))
    assert git_service.current_branch(str(git_repo)) in ("main", "master")
    (git_repo / "b.txt").write_text("more")
    assert "b.txt" in git_service.status(str(git_repo))


# ---- config ----------------------------------------------------------
def test_config_defaults(tmp_config):
    cfg = Config(_default_config())
    # Dynamic provider list (not hardcoded to three): 4 seeded profiles.
    assert len(cfg.ordered_providers()) == 4
    # No API keys yet; the local (auth=none) profile counts as available.
    assert len(cfg.active_providers()) == 0
    assert {p["id"] for p in cfg.available_providers()} == {"local"}
    cfg.providers["anthropic"]["api_key"] = "k"
    assert {p["id"] for p in cfg.available_providers()} == {"anthropic", "local"}
    assert [p["id"] for p in cfg.active_providers()] == ["anthropic"]


def test_provider_billing_model(tmp_config):
    cfg = Config(_default_config())
    assert cfg.providers["xai"]["billing_source"] == "subscription"
    assert cfg.providers["xai"]["subscription_tier"] == "SuperGrok"
    assert cfg.providers["local"]["billing_source"] == "local"
    assert cfg.providers["anthropic"]["billing_source"] == "api"
    assert cfg.orchestration["auto_push"] is False
