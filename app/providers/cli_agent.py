"""Adapter for official CLI coding agents (Claude Code / Codex / Grok).

Transport `cli_session`: Dispatcher spawns the *official, already
authenticated* CLI as a subprocess — exactly what happens when the user runs
it manually. No API keys are requested or stored; auth lives entirely inside
the CLI's own session. Billing is the user's subscription.

Two modes:

* `complete()` — a single reasoning turn (planning / council / review /
  research). The CLI runs headless with mutating tools disabled (Claude/Grok:
  auto-deny permission mode + disallowed edit tools; Codex: read-only
  sandbox), so it may *read* the project for context but cannot change it.
* `native_run()` — implementation mode inside an isolated agent worktree.
  The CLI edits files itself with its own tools (Claude/Grok: acceptEdits
  confined to the worktree; Codex: its official workspace-write sandbox).
  Dispatcher's safety boundary moves to patch collection: the resulting diff
  is validated against the agent's PathPolicy before integration.

Everything is parsed defensively: JSON first, raw text as a fallback. Token
usage is reported only when the CLI exposes it (Claude does; others show 0 —
subscription billing has no per-token price anyway).
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .base import BaseAdapter, CompletionResult, Message, ToolSpec, Usage

# Tools a reasoning-only call must not use. Unknown names are ignored by the
# CLIs, so one conservative list serves both Claude Code and Grok (which
# mirrors Claude Code's flags).
_READONLY_DISALLOWED = "Bash,Edit,Write,NotebookEdit,Task,Agent"

_POLL_S = 0.25


class CLIError(RuntimeError):
    pass


def _kill_tree(proc: subprocess.Popen) -> None:
    """Terminate the CLI and every child it spawned."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=30)
        else:
            import signal
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _flatten(system: str, messages: list[Message]) -> str:
    """Serialize a neutral conversation into one prompt for a stateless CLI
    invocation. Single user turns (the common case) pass through unchanged."""
    parts: list[str] = []
    if system.strip():
        parts.append(f"[Инструкции для этой задачи]\n{system.strip()}")
    turns = [m for m in messages if (m.text or "").strip()]
    if len(turns) == 1 and turns[0].role == "user" and not parts:
        return turns[0].text
    tags = {"user": "Пользователь", "assistant": "Твой предыдущий ответ",
            "tool": "Результат инструмента", "system": "Система"}
    for m in turns:
        parts.append(f"[{tags.get(m.role, m.role)}]\n{m.text.strip()}")
    parts.append("[Пользователь]\nПродолжи и дай финальный ответ.")
    return "\n\n".join(parts)


class CLIAdapter(BaseAdapter):
    """cfg keys used: cli_flavor, model, effort, cli_timeout, workdir,
    cli_binary (optional override, mainly for tests)."""

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.flavor = cfg.get("cli_flavor", "")
        self.cancel: threading.Event | None = None   # optional external cancel

    # ---- plumbing ----------------------------------------------------
    def _binary(self) -> str:
        binary = self.cfg.get("cli_binary", "")
        if not binary:
            from ..cliagents import find_binary
            binary = find_binary(self.flavor)
        if not binary:
            raise CLIError(
                f"CLI «{self.flavor}» не найден — установи его или проверь PATH "
                "(см. dispatcher doctor)")
        return binary

    def _workdir(self) -> str:
        wd = self.cfg.get("workdir", "")
        if wd and Path(wd).is_dir():
            return str(wd)
        return str(Path.cwd())

    def _timeout(self) -> int:
        try:
            return max(30, int(self.cfg.get("cli_timeout", 1200)))
        except (TypeError, ValueError):
            return 1200

    def _execute(self, cmd: list[str], stdin_text: str, cwd: str,
                 cancel: threading.Event | None = None,
                 timeout: int | None = None) -> tuple[str, str]:
        """Run the CLI, honoring cancellation; returns (stdout, stderr)."""
        timeout = timeout or self._timeout()
        cancel = cancel or self.cancel
        try:
            proc = subprocess.Popen(
                cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                errors="replace")
        except OSError as exc:
            raise CLIError(f"не удалось запустить {cmd[0]}: {exc}") from exc

        result: dict = {}

        def _communicate() -> None:
            try:
                result["out"], result["err"] = proc.communicate(stdin_text)
            except Exception as exc:            # pragma: no cover - defensive
                result["exc"] = exc

        thread = threading.Thread(target=_communicate, daemon=True)
        thread.start()
        deadline = time.monotonic() + timeout
        while thread.is_alive():
            if cancel is not None and cancel.is_set():
                _kill_tree(proc)
                thread.join(10)
                raise CLIError("вызов CLI отменён")
            if time.monotonic() > deadline:
                _kill_tree(proc)
                thread.join(10)
                raise CLIError(f"CLI не ответил за {timeout} с (таймаут)")
            thread.join(_POLL_S)
        if "exc" in result:
            raise CLIError(f"сбой обмена с CLI: {result['exc']}")
        out, err = result.get("out", ""), result.get("err", "")
        if proc.returncode != 0:
            tail = (err or out or "").strip()[-800:]
            raise CLIError(
                f"{Path(cmd[0]).name} завершился с кодом {proc.returncode}: {tail}")
        return out, err

    # ---- command builders (reasoning mode) ---------------------------
    def _cmd_reasoning(self, system: str, workdir: str,
                       last_message_file: str, prompt_file: str) -> list[str]:
        model = (self.cfg.get("model") or "").strip()
        effort = (self.cfg.get("effort") or "").strip()
        binary = self._binary()
        if self.flavor == "claude":
            cmd = [binary, "-p", "--output-format", "json",
                   "--disallowedTools", _READONLY_DISALLOWED]
            if model:
                cmd += ["--model", model]
            if effort:
                cmd += ["--effort", effort]
            if system.strip():
                cmd += ["--append-system-prompt", system.strip()[:8000]]
            return cmd
        if self.flavor == "codex":
            cmd = [binary, "exec", "--sandbox", "read-only",
                   "--skip-git-repo-check", "--cd", workdir, "--color", "never",
                   "--output-last-message", last_message_file]
            if model:
                cmd += ["-m", model]
            if effort:
                cmd += ["-c", f'model_reasoning_effort="{effort}"']
            cmd += ["-"]                       # prompt from stdin
            return cmd
        if self.flavor == "grok":
            cmd = [binary, "--prompt-file", prompt_file,
                   "--output-format", "json", "--permission-mode", "dontAsk",
                   "--cwd", workdir]
            if model:
                cmd += ["--model", model]
            if effort:
                cmd += ["--reasoning-effort", effort]
            if system.strip():
                cmd += ["--rules", system.strip()[:8000]]
            return cmd
        raise CLIError(f"неизвестный CLI-провайдер: {self.flavor}")

    # ---- output parsers ----------------------------------------------
    @staticmethod
    def _parse_json_result(stdout: str) -> tuple[str, Usage, bool, str]:
        """(text, usage, is_error, detail) from claude/grok-style JSON output.

        Tries the whole stdout as JSON, then the last JSON-looking line
        (streaming formats), then falls back to raw text.
        """
        candidates = []
        s = stdout.strip()
        if s:
            candidates.append(s)
            lines = [ln for ln in s.splitlines() if ln.strip().startswith("{")]
            candidates.extend(reversed(lines))
        for cand in candidates:
            try:
                obj = json.loads(cand)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            text = obj.get("result")
            if text is None:
                text = obj.get("text", obj.get("content", ""))
            if isinstance(text, list):        # content blocks
                text = "\n".join(str(b.get("text", "")) if isinstance(b, dict)
                                 else str(b) for b in text)
            usage_d = obj.get("usage") or {}
            usage = Usage(int(usage_d.get("input_tokens", 0) or 0),
                          int(usage_d.get("output_tokens", 0) or 0))
            is_error = bool(obj.get("is_error"))
            detail = str(obj.get("subtype", "") or "")
            if text is not None:
                return str(text), usage, is_error, detail
        return stdout.strip(), Usage(), False, "raw"

    # ---- BaseAdapter API ----------------------------------------------
    def complete(self, system: str, messages: list[Message],
                 tools: list[ToolSpec]) -> CompletionResult:
        """One reasoning turn. `tools` are ignored: a CLI agent uses its own
        native tools; Dispatcher's JSON tool protocol is for API adapters."""
        # The system text travels via the CLI's own flag (claude:
        # --append-system-prompt, grok: --rules); it must not be duplicated
        # inside the prompt body. codex has no such flag — prepended below.
        prompt = _flatten("", messages)
        workdir = self._workdir()
        with tempfile.TemporaryDirectory(prefix="dispatcher-cli-") as tmp:
            last_file = str(Path(tmp) / "last_message.txt")
            prompt_file = str(Path(tmp) / "prompt.txt")
            Path(prompt_file).write_text(prompt, encoding="utf-8", newline="\n")
            if self.flavor == "codex" and system.strip():
                # codex has no system-prompt flag; prepend it to the prompt
                stdin_text = f"[Инструкции для этой задачи]\n{system.strip()}\n\n{prompt}"
            else:
                stdin_text = prompt
            cmd = self._cmd_reasoning(system, workdir, last_file, prompt_file)
            out, err = self._execute(cmd, stdin_text if self.flavor != "grok" else "",
                                     workdir)
            if self.flavor == "codex":
                text = ""
                try:
                    text = Path(last_file).read_text(encoding="utf-8").strip()
                except OSError:
                    pass
                if not text:
                    text = out.strip() or err.strip()
                return CompletionResult(text=text, thinking="", tool_calls=[],
                                        usage=Usage(), raw_assistant=None,
                                        stop_reason="end_turn")
            text, usage, is_error, detail = self._parse_json_result(out)
            if is_error:
                raise CLIError(f"{self.flavor} вернул ошибку ({detail}): "
                               f"{text[:500]}")
            return CompletionResult(text=text, thinking="", tool_calls=[],
                                    usage=usage, raw_assistant=None,
                                    stop_reason="end_turn")

    def ping(self) -> str:
        """Readiness check without spending subscription quota: the binary
        must exist and the CLI's own session files must show a login. A real
        end-to-end call is `dispatcher doctor --probe`."""
        from ..cliagents import detect
        st = detect(self.flavor, run_commands=False)
        if not st.installed:
            raise CLIError(f"CLI «{self.flavor}» не установлен")
        if st.authenticated is not True:
            raise CLIError(f"вход в {self.flavor} не выполнен ({st.auth_source})")
        return f"сессия найдена: {st.auth_source}"

    # ---- native implementation mode ------------------------------------
    def _cmd_native(self, workdir: str, last_message_file: str,
                    prompt_file: str) -> list[str]:
        model = (self.cfg.get("model") or "").strip()
        effort = (self.cfg.get("effort") or "").strip()
        binary = self._binary()
        if self.flavor == "claude":
            cmd = [binary, "-p", "--output-format", "json",
                   "--permission-mode", "acceptEdits"]
            if model:
                cmd += ["--model", model]
            if effort:
                cmd += ["--effort", effort]
            return cmd
        if self.flavor == "codex":
            cmd = [binary, "exec", "--sandbox", "workspace-write",
                   "--skip-git-repo-check", "--cd", workdir, "--color", "never",
                   "--output-last-message", last_message_file]
            if model:
                cmd += ["-m", model]
            if effort:
                cmd += ["-c", f'model_reasoning_effort="{effort}"']
            cmd += ["-"]
            return cmd
        if self.flavor == "grok":
            cmd = [binary, "--prompt-file", prompt_file,
                   "--output-format", "json", "--permission-mode", "acceptEdits",
                   "--cwd", workdir]
            if model:
                cmd += ["--model", model]
            if effort:
                cmd += ["--reasoning-effort", effort]
            return cmd
        raise CLIError(f"неизвестный CLI-провайдер: {self.flavor}")

    def native_run(self, prompt: str, workdir: str, on_event,
                   cancel: threading.Event | None = None,
                   timeout: int | None = None) -> str:
        """Let the official CLI implement the task inside `workdir` (an
        isolated worktree) using its own tools. Returns its final report."""
        on_event("status", f"официальный CLI ({self.flavor}) работает в "
                           "изолированной копии…")
        with tempfile.TemporaryDirectory(prefix="dispatcher-cli-") as tmp:
            last_file = str(Path(tmp) / "last_message.txt")
            prompt_file = str(Path(tmp) / "prompt.txt")
            Path(prompt_file).write_text(prompt, encoding="utf-8", newline="\n")
            cmd = self._cmd_native(workdir, last_file, prompt_file)
            stdin_text = "" if self.flavor == "grok" else prompt
            out, err = self._execute(cmd, stdin_text, workdir,
                                     cancel=cancel, timeout=timeout)
            if self.flavor == "codex":
                try:
                    text = Path(last_file).read_text(encoding="utf-8").strip()
                except OSError:
                    text = ""
                final = text or out.strip() or err.strip()
                on_event("usage", json.dumps({"in": 0, "out": 0}))
                return final
            text, usage, is_error, detail = self._parse_json_result(out)
            on_event("usage", json.dumps(
                {"in": usage.input_tokens, "out": usage.output_tokens}))
            if is_error:
                raise CLIError(f"{self.flavor} вернул ошибку ({detail}): "
                               f"{text[:500]}")
            return text
