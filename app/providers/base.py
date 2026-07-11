"""Provider-neutral message/tool types and the agentic tool-use loop.

Every provider adapter converts these neutral types to/from its own wire
format. The `run_agent` loop is provider-agnostic: it asks the model what to
do, executes any tool calls locally, feeds results back, and repeats until the
model stops calling tools (or a limit / cancel is hit). Mid-run user steering
messages are injected between turns.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict


@dataclass
class Message:
    role: str                                  # system | user | assistant | tool
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str = ""
    name: str = ""
    raw: Any = None        # provider-native content, resent verbatim for exact round-trips


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens


@dataclass
class CompletionResult:
    text: str
    thinking: str
    tool_calls: list[ToolCall]
    usage: Usage
    raw_assistant: Any
    stop_reason: str


class Steering:
    """Thread-safe list of user steering messages, broadcast to every agent."""

    def __init__(self) -> None:
        self._items: list[str] = []
        self._lock = threading.Lock()

    def add(self, text: str) -> None:
        with self._lock:
            self._items.append(text)

    def drain_from(self, index: int) -> tuple[list[str], int]:
        with self._lock:
            new = self._items[index:]
            return new, len(self._items)


class BaseAdapter:
    """Adapters implement `complete()`."""

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def complete(self, system: str, messages: list[Message],
                 tools: list[ToolSpec]) -> CompletionResult:  # pragma: no cover
        raise NotImplementedError


# --- The agent loop ---------------------------------------------------------

EventCb = Callable[[str, str], None]        # (kind, payload) -> None
ToolExec = Callable[[str, dict], str]       # (name, args) -> result text


def run_agent(
    adapter: BaseAdapter,
    system: str,
    initial_messages: list[Message],
    tools: list[ToolSpec],
    tool_executor: ToolExec,
    steering: Steering,
    on_event: EventCb,
    max_iters: int,
    cancel: threading.Event,
    total_usage: Usage,
) -> str:
    """Run one agent to completion. Returns its final text (its self-report)."""
    messages = list(initial_messages)
    steer_index = 0
    final_text = ""

    for _ in range(max_iters):
        if cancel.is_set():
            on_event("status", "остановлено")
            return final_text or "(остановлено пользователем)"

        # Inject any new mid-run instructions before the next turn.
        new_steer, steer_index = steering.drain_from(steer_index)
        if new_steer:
            joined = "\n\n".join(new_steer)
            messages.append(Message(
                role="user",
                text=("[Дополнительные инструкции от пользователя — "
                      f"учти их немедленно]\n{joined}"),
            ))
            on_event("steering", joined)

        try:
            result = adapter.complete(system, messages, tools)
        except Exception as exc:  # network / API errors -> surface, stop this agent
            on_event("error", f"Ошибка обращения к модели: {exc}")
            return final_text or f"(ошибка: {exc})"

        total_usage.add(result.usage)
        on_event("usage", json.dumps(
            {"in": result.usage.input_tokens, "out": result.usage.output_tokens}))

        if result.thinking:
            on_event("thinking", result.thinking)
        if result.text:
            on_event("text", result.text)
            final_text = result.text

        messages.append(Message(
            role="assistant",
            text=result.text,
            tool_calls=result.tool_calls,
            raw=result.raw_assistant,
        ))

        if not result.tool_calls:
            return final_text

        for call in result.tool_calls:
            if cancel.is_set():
                return final_text or "(остановлено пользователем)"
            on_event("tool", json.dumps(
                {"name": call.name, "args": call.args}, ensure_ascii=False))
            if call.name == "finish":
                final_text = str(call.args.get("summary", "")) or final_text
                on_event("text", final_text)
                # still send a tool result so the transcript stays valid
                messages.append(Message(
                    role="tool", text="OK", tool_call_id=call.id, name=call.name))
                return final_text
            try:
                output = tool_executor(call.name, call.args)
            except Exception as exc:
                output = f"ERROR: {exc}"
            on_event("tool_result", output[:1500])
            messages.append(Message(
                role="tool", text=output, tool_call_id=call.id, name=call.name))

    on_event("status", "достигнут лимит шагов")
    return final_text or "(достигнут лимит шагов инструментов)"
