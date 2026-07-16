"""CLIAdapter: command construction, output parsing, timeouts, cancellation.

subprocess.Popen is faked — no real CLI is ever spawned.
"""
import json
import threading
import time
from pathlib import Path

import pytest

import app.providers.cli_agent as cliagent
from app.providers.base import Message
from app.providers.cli_agent import CLIAdapter, CLIError


class FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0, delay=0.0,
                 last_message=""):
        self.stdout_text = stdout
        self.stderr_text = stderr
        self.returncode = returncode
        self.delay = delay
        self.last_message = last_message
        self.pid = 999999
        self.stdin_received = None
        self.cmd = None

    def communicate(self, input=None):
        self.stdin_received = input
        if self.delay:
            time.sleep(self.delay)
        if self.last_message and self.cmd and "--output-last-message" in self.cmd:
            target = self.cmd[self.cmd.index("--output-last-message") + 1]
            Path(target).write_text(self.last_message, encoding="utf-8")
        return self.stdout_text, self.stderr_text

    def kill(self):
        self.returncode = -9


def _install(monkeypatch, proc: FakeProc):
    captured = {}

    def fake_popen(cmd, cwd=None, **kwargs):
        proc.cmd = list(cmd)
        captured["cmd"] = list(cmd)
        captured["cwd"] = cwd
        return proc

    monkeypatch.setattr(cliagent.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(cliagent, "_kill_tree", lambda p: p.kill())
    return captured


def _cfg(flavor, **over):
    cfg = {"id": f"{flavor}_cli", "kind": "cli", "cli_flavor": flavor,
           "cli_binary": f"/fake/{flavor}", "model": "test-model",
           "effort": "high", "cli_timeout": 60}
    cfg.update(over)
    return cfg


# ---- claude ---------------------------------------------------------------

def test_claude_reasoning_command_and_parse(monkeypatch, tmp_path):
    out = json.dumps({"type": "result", "result": "Ответ готов", "is_error": False,
                      "usage": {"input_tokens": 11, "output_tokens": 7},
                      "total_cost_usd": 0.0})
    captured = _install(monkeypatch, FakeProc(stdout=out))
    adapter = CLIAdapter(_cfg("claude", model="claude-opus-4-8", effort="max",
                              workdir=str(tmp_path)))
    res = adapter.complete("Системные правила",
                           [Message("user", text="вопрос")], [])
    cmd = captured["cmd"]
    assert cmd[0] == "/fake/claude"
    assert "-p" in cmd and "--output-format" in cmd and "json" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-opus-4-8"
    assert cmd[cmd.index("--effort") + 1] == "max"
    assert cmd[cmd.index("--append-system-prompt") + 1] == "Системные правила"
    assert "--disallowedTools" in cmd          # reasoning mode: no edits
    assert res.text == "Ответ готов"
    assert res.usage.input_tokens == 11 and res.usage.output_tokens == 7
    assert res.tool_calls == []


def test_claude_error_result_raises(monkeypatch, tmp_path):
    out = json.dumps({"type": "result", "result": "квота исчерпана",
                      "is_error": True, "subtype": "error_during_execution"})
    _install(monkeypatch, FakeProc(stdout=out))
    adapter = CLIAdapter(_cfg("claude", workdir=str(tmp_path)))
    with pytest.raises(CLIError, match="квота"):
        adapter.complete("", [Message("user", text="q")], [])


def test_nonzero_exit_raises_with_stderr(monkeypatch, tmp_path):
    _install(monkeypatch, FakeProc(stderr="fatal: boom", returncode=3))
    adapter = CLIAdapter(_cfg("claude", workdir=str(tmp_path)))
    with pytest.raises(CLIError, match="boom"):
        adapter.complete("", [Message("user", text="q")], [])


# ---- codex ------------------------------------------------------------------

def test_codex_reasoning_readonly_and_last_message(monkeypatch, tmp_path):
    captured = _install(monkeypatch, FakeProc(stdout="progress noise",
                                              last_message="Финальный ответ"))
    adapter = CLIAdapter(_cfg("codex", model="gpt-5.6-sol", effort="ultra",
                              workdir=str(tmp_path)))
    res = adapter.complete("Правила", [Message("user", text="вопрос")], [])
    cmd = captured["cmd"]
    assert cmd[:2] == ["/fake/codex", "exec"]
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    assert "--skip-git-repo-check" in cmd
    assert cmd[cmd.index("-m") + 1] == "gpt-5.6-sol"
    assert 'model_reasoning_effort="ultra"' in cmd
    assert cmd[-1] == "-"                       # prompt via stdin
    assert res.text == "Финальный ответ"


def test_codex_stdin_contains_system_and_prompt(monkeypatch, tmp_path):
    proc = FakeProc(stdout="", last_message="ok")
    _install(monkeypatch, proc)
    adapter = CLIAdapter(_cfg("codex", workdir=str(tmp_path)))
    adapter.complete("Секция правил", [Message("user", text="сам вопрос")], [])
    assert "Секция правил" in proc.stdin_received
    assert "сам вопрос" in proc.stdin_received


# ---- grok --------------------------------------------------------------------

def test_grok_reasoning_prompt_file_and_json(monkeypatch, tmp_path):
    out = json.dumps({"result": "мнение grok",
                      "usage": {"input_tokens": 2, "output_tokens": 3}})
    captured = _install(monkeypatch, FakeProc(stdout=out))
    adapter = CLIAdapter(_cfg("grok", model="grok-4.5", effort="high",
                              workdir=str(tmp_path)))

    prompt_content = {}
    real_popen = cliagent.subprocess.Popen

    def spying_popen(cmd, cwd=None, **kwargs):
        # the prompt file must exist and hold the prompt at spawn time
        pf = cmd[cmd.index("--prompt-file") + 1]
        prompt_content["text"] = Path(pf).read_text(encoding="utf-8")
        return real_popen(cmd, cwd=cwd, **kwargs)

    monkeypatch.setattr(cliagent.subprocess, "Popen", spying_popen)
    res = adapter.complete("правила grok", [Message("user", text="вопрос grok")], [])
    cmd = captured["cmd"]
    assert cmd[0] == "/fake/grok"
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert cmd[cmd.index("--model") + 1] == "grok-4.5"
    assert cmd[cmd.index("--reasoning-effort") + 1] == "high"
    assert cmd[cmd.index("--rules") + 1] == "правила grok"
    assert prompt_content["text"] == "вопрос grok"
    assert res.text == "мнение grok"
    assert res.usage.output_tokens == 3


def test_grok_plain_text_fallback(monkeypatch, tmp_path):
    _install(monkeypatch, FakeProc(stdout="просто текст без JSON"))
    adapter = CLIAdapter(_cfg("grok", workdir=str(tmp_path)))
    res = adapter.complete("", [Message("user", text="q")], [])
    assert res.text == "просто текст без JSON"
    assert res.usage.input_tokens == 0


# ---- shared behavior -----------------------------------------------------------

def test_flatten_multi_turn():
    msgs = [Message("user", text="первый"),
            Message("assistant", text="ответ"),
            Message("user", text="уточнение")]
    text = cliagent._flatten("", msgs)
    assert "первый" in text and "ответ" in text and "уточнение" in text
    assert "[Пользователь]" in text and "[Твой предыдущий ответ]" in text


def test_timeout_kills_and_raises(monkeypatch, tmp_path):
    proc = FakeProc(stdout="never", delay=5.0)
    _install(monkeypatch, proc)
    adapter = CLIAdapter(_cfg("claude", workdir=str(tmp_path)))
    with pytest.raises(CLIError, match="таймаут"):
        adapter._execute(["/fake/claude"], "", str(tmp_path), timeout=1)
    assert proc.returncode == -9               # killed


def test_cancel_kills_and_raises(monkeypatch, tmp_path):
    proc = FakeProc(stdout="never", delay=5.0)
    _install(monkeypatch, proc)
    adapter = CLIAdapter(_cfg("claude", workdir=str(tmp_path)))
    cancel = threading.Event()
    threading.Timer(0.4, cancel.set).start()
    with pytest.raises(CLIError, match="отмен"):
        adapter._execute(["/fake/claude"], "", str(tmp_path),
                         cancel=cancel, timeout=30)
    assert proc.returncode == -9


def test_missing_binary_is_actionable(monkeypatch, tmp_path):
    import app.cliagents as ca
    monkeypatch.setattr(ca, "find_binary", lambda flavor, home=None: "")
    adapter = CLIAdapter({"id": "x", "kind": "cli", "cli_flavor": "claude",
                          "workdir": str(tmp_path)})
    with pytest.raises(CLIError, match="doctor"):
        adapter.complete("", [Message("user", text="q")], [])


# ---- native mode -----------------------------------------------------------------

def test_native_run_claude_accept_edits(monkeypatch, tmp_path):
    out = json.dumps({"result": "готово: файл создан",
                      "usage": {"input_tokens": 100, "output_tokens": 50}})
    captured = _install(monkeypatch, FakeProc(stdout=out))
    adapter = CLIAdapter(_cfg("claude", model="opus", effort="max"))
    events = []
    final = adapter.native_run("сделай задачу", str(tmp_path),
                               lambda k, p: events.append((k, p)))
    cmd = captured["cmd"]
    assert cmd[cmd.index("--permission-mode") + 1] == "acceptEdits"
    assert captured["cwd"] == str(tmp_path)
    assert final == "готово: файл создан"
    usage_events = [p for k, p in events if k == "usage"]
    assert usage_events and json.loads(usage_events[0])["in"] == 100


def test_native_run_codex_workspace_write(monkeypatch, tmp_path):
    captured = _install(monkeypatch,
                        FakeProc(stdout="", last_message="итоговый отчёт"))
    adapter = CLIAdapter(_cfg("codex"))
    final = adapter.native_run("задача", str(tmp_path), lambda k, p: None)
    cmd = captured["cmd"]
    assert cmd[cmd.index("--sandbox") + 1] == "workspace-write"
    assert cmd[cmd.index("--cd") + 1] == str(tmp_path)
    assert final == "итоговый отчёт"
