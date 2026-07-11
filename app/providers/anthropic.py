"""Anthropic (Claude) adapter via the raw Messages REST API.

Raw HTTP is used deliberately: this app drives three different providers with
bleeding-edge reasoning/effort parameters and needs identical control over each
request body. The request shapes follow the official Anthropic Messages API
(x-api-key + anthropic-version headers, `tools`, adaptive thinking, effort).
"""
from __future__ import annotations

import json

import requests

from .base import BaseAdapter, CompletionResult, Message, ToolCall, ToolSpec, Usage
from .http import retrying_post

ANTHROPIC_VERSION = "2023-06-01"
TIMEOUT = 600


class AnthropicAdapter(BaseAdapter):
    def _url(self) -> str:
        base = self.cfg.get("base_url", "https://api.anthropic.com/v1").rstrip("/")
        return f"{base}/messages"

    def _to_messages(self, messages: list[Message]) -> list[dict]:
        out: list[dict] = []
        pending_tool_results: list[dict] = []

        def flush() -> None:
            if pending_tool_results:
                out.append({"role": "user", "content": list(pending_tool_results)})
                pending_tool_results.clear()

        for m in messages:
            if m.role == "tool":
                pending_tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": m.tool_call_id,
                    "content": m.text,
                })
                continue
            flush()
            if m.role == "user":
                out.append({"role": "user", "content": [
                    {"type": "text", "text": m.text}]})
            elif m.role == "assistant":
                if m.raw is not None:
                    out.append({"role": "assistant", "content": m.raw})
                else:
                    content: list[dict] = []
                    if m.text:
                        content.append({"type": "text", "text": m.text})
                    for tc in m.tool_calls:
                        content.append({
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.args,
                        })
                    out.append({"role": "assistant", "content": content})
        flush()
        return out

    def complete(self, system: str, messages: list[Message],
                 tools: list[ToolSpec]) -> CompletionResult:
        # Adaptive thinking + effort (set in _body) = the "fattest" configuration.
        body = self._body(system, messages, tools)
        resp = retrying_post(requests, self._url(), body, self._headers(), TIMEOUT)
        if resp.status_code != 200:
            raise RuntimeError(f"Anthropic {resp.status_code}: {resp.text[:400]}")
        limits = _limits_from_headers(resp.headers)
        data = resp.json()

        content = data.get("content", [])
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in content:
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block.get("text", ""))
            elif btype == "thinking":
                thinking_parts.append(block.get("thinking", ""))
            elif btype == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.get("id", ""),
                    name=block.get("name", ""),
                    args=block.get("input", {}) or {},
                ))
        usage_data = data.get("usage", {})
        return CompletionResult(
            text="".join(text_parts).strip(),
            thinking="".join(thinking_parts).strip(),
            tool_calls=tool_calls,
            usage=Usage(
                input_tokens=usage_data.get("input_tokens", 0),
                output_tokens=usage_data.get("output_tokens", 0),
            ),
            raw_assistant=content,
            stop_reason=data.get("stop_reason", ""),
            limits=limits,
        )

    def _headers(self) -> dict:
        return {
            "x-api-key": self.cfg["api_key"],
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    def _body(self, system, messages, tools) -> dict:
        body: dict = {
            "model": self.cfg["model"],
            "max_tokens": int(self.cfg.get("max_tokens", 16000)),
            "messages": self._to_messages(messages),
            "tools": [
                {"name": t.name, "description": t.description,
                 "input_schema": t.parameters}
                for t in tools
            ],
        }
        if system:
            body["system"] = system
        effort = self.cfg.get("effort")
        if effort and effort != "none":
            body["thinking"] = {"type": "adaptive", "display": "summarized"}
            body["output_config"] = {"effort": effort}
        return body

    def stream_complete(self, system, messages, tools, on_delta, cancel=None):
        """SSE streaming for the Messages API.

        Accumulates content blocks (text / thinking / tool_use) by index into a
        provider-native assistant content list so tool-use turns round-trip
        exactly on the next request, streams *text* deltas via on_delta, and
        aborts promptly on cancel by closing the connection.
        """
        body = self._body(system, messages, tools)
        body["stream"] = True
        resp = requests.post(self._url(), json=body, headers=self._headers(),
                             timeout=TIMEOUT, stream=True)
        if resp.status_code != 200:
            raise RuntimeError(f"Anthropic {resp.status_code}: {resp.text[:400]}")
        blocks: dict[int, dict] = {}           # index -> {"raw": {...}, "json": str}
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        usage = Usage()
        stop_reason = ""
        for line in resp.iter_lines(decode_unicode=True):
            if cancel is not None and cancel.is_set():
                resp.close()
                break
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data:
                continue
            try:
                evt = json.loads(data)
            except json.JSONDecodeError:
                continue
            etype = evt.get("type")
            if etype == "message_start":
                u = (evt.get("message") or {}).get("usage") or {}
                usage.input_tokens = u.get("input_tokens", 0) or 0
            elif etype == "content_block_start":
                idx = evt.get("index", 0)
                blocks[idx] = {"raw": dict(evt.get("content_block") or {}), "json": ""}
            elif etype == "content_block_delta":
                idx = evt.get("index", 0)
                b = blocks.setdefault(idx, {"raw": {"type": "text", "text": ""},
                                            "json": ""})
                delta = evt.get("delta") or {}
                dt = delta.get("type")
                if dt == "text_delta":
                    t = delta.get("text", "")
                    text_parts.append(t)
                    b["raw"]["text"] = b["raw"].get("text", "") + t
                    on_delta(t)
                elif dt == "thinking_delta":
                    th = delta.get("thinking", "")
                    thinking_parts.append(th)
                    b["raw"]["thinking"] = b["raw"].get("thinking", "") + th
                elif dt == "signature_delta":
                    b["raw"]["signature"] = (b["raw"].get("signature", "")
                                             + delta.get("signature", ""))
                elif dt == "input_json_delta":
                    b["json"] += delta.get("partial_json", "")
            elif etype == "content_block_stop":
                b = blocks.get(evt.get("index", 0))
                if b and b["raw"].get("type") == "tool_use":
                    try:
                        b["raw"]["input"] = json.loads(b["json"] or "{}")
                    except json.JSONDecodeError:
                        b["raw"]["input"] = {}
            elif etype == "message_delta":
                stop_reason = (evt.get("delta") or {}).get("stop_reason") or stop_reason
                u = evt.get("usage") or {}
                if u.get("output_tokens") is not None:
                    usage.output_tokens = u["output_tokens"]
        raw_content: list[dict] = []
        tool_calls: list[ToolCall] = []
        for idx in sorted(blocks):
            raw = blocks[idx]["raw"]
            raw_content.append(raw)
            if raw.get("type") == "tool_use":
                tool_calls.append(ToolCall(id=raw.get("id", ""),
                                           name=raw.get("name", ""),
                                           args=raw.get("input", {}) or {}))
        return CompletionResult(
            text="".join(text_parts).strip(),
            thinking="".join(thinking_parts).strip(),
            tool_calls=tool_calls,
            usage=usage,
            raw_assistant=raw_content,
            stop_reason=stop_reason,
            limits=None,
        )

    def _ping_headers(self):
        body = {
            "model": self.cfg["model"],
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "ping"}],
        }
        resp = requests.post(self._url(), json=body, headers=self._headers(),
                             timeout=60)
        if resp.status_code != 200:
            raise RuntimeError(f"{resp.status_code}: {resp.text[:200]}")
        return resp.headers

    def ping(self) -> str:
        """Cheap request used by the connection test."""
        self._ping_headers()
        return "ok"

    def fetch_limits(self) -> dict:
        """Return remaining rate-limit window for this key/model."""
        return _limits_from_headers(self._ping_headers())


def _limits_from_headers(headers) -> dict:
    def as_int(name):
        val = headers.get(name)
        if val is None:
            return None
        try:
            return int(float(val))
        except (TypeError, ValueError):
            return None

    return {
        "req_remaining": as_int("anthropic-ratelimit-requests-remaining"),
        "req_limit": as_int("anthropic-ratelimit-requests-limit"),
        "tok_remaining": (as_int("anthropic-ratelimit-tokens-remaining")
                          or as_int("anthropic-ratelimit-input-tokens-remaining")),
        "tok_limit": (as_int("anthropic-ratelimit-tokens-limit")
                      or as_int("anthropic-ratelimit-input-tokens-limit")),
        "reset": (headers.get("anthropic-ratelimit-tokens-reset")
                  or headers.get("anthropic-ratelimit-requests-reset")),
    }
