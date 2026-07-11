"""OpenAI-compatible adapter — drives both ChatGPT (OpenAI) and Grok (xAI).

xAI's API is OpenAI Chat Completions compatible, so one adapter serves both;
only the base URL, model id and token-limit field name differ. Reasoning depth
is controlled with the top-level `reasoning_effort` parameter.
"""
from __future__ import annotations

import json

import requests

from .base import BaseAdapter, CompletionResult, Message, ToolCall, ToolSpec, Usage
from .http import retrying_post

TIMEOUT = 600


class OpenAIAdapter(BaseAdapter):
    def _url(self) -> str:
        base = self.cfg.get("base_url", "https://api.openai.com/v1").rstrip("/")
        return f"{base}/chat/completions"

    def _to_messages(self, system: str, messages: list[Message]) -> list[dict]:
        out: list[dict] = []
        if system:
            out.append({"role": "system", "content": system})
        for m in messages:
            if m.role == "user":
                out.append({"role": "user", "content": m.text})
            elif m.role == "tool":
                out.append({
                    "role": "tool",
                    "tool_call_id": m.tool_call_id,
                    "content": m.text,
                })
            elif m.role == "assistant":
                if m.raw is not None:
                    out.append(m.raw)
                else:
                    msg: dict = {"role": "assistant", "content": m.text or None}
                    if m.tool_calls:
                        msg["tool_calls"] = [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.name,
                                    "arguments": json.dumps(tc.args, ensure_ascii=False),
                                },
                            }
                            for tc in m.tool_calls
                        ]
                    out.append(msg)
        return out

    def _build_body(self, system, messages, tools) -> dict:
        body: dict = {
            "model": self.cfg["model"],
            "messages": self._to_messages(system, messages),
            "tools": [
                {"type": "function",
                 "function": {"name": t.name, "description": t.description,
                              "parameters": t.parameters}}
                for t in tools
            ],
            "tool_choice": "auto",
        }
        max_tokens = int(self.cfg.get("max_tokens", 16000))
        if self.cfg.get("id") == "openai":
            body["max_completion_tokens"] = max_tokens
        else:
            body["max_tokens"] = max_tokens
        effort = self.cfg.get("effort")
        if effort and effort != "none":
            body["reasoning_effort"] = effort
        return body

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.cfg['api_key']}",
                "Content-Type": "application/json"}

    def complete(self, system: str, messages: list[Message],
                 tools: list[ToolSpec]) -> CompletionResult:
        body = self._build_body(system, messages, tools)
        resp = retrying_post(requests, self._url(), body, self._headers(), TIMEOUT)
        if resp.status_code != 200:
            raise RuntimeError(
                f"{self.cfg.get('short', 'OpenAI')} {resp.status_code}: {resp.text[:400]}")
        limits = _limits_from_headers(resp.headers)
        data = resp.json()

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message", {}) or {}
        text = message.get("content") or ""
        tool_calls: list[ToolCall] = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCall(
                id=tc.get("id", ""),
                name=fn.get("name", ""),
                args=args,
            ))
        usage_data = data.get("usage", {}) or {}
        # Some providers expose reasoning content separately.
        thinking = message.get("reasoning_content") or ""
        return CompletionResult(
            text=(text or "").strip(),
            thinking=(thinking or "").strip(),
            tool_calls=tool_calls,
            usage=Usage(
                input_tokens=usage_data.get("prompt_tokens", 0),
                output_tokens=usage_data.get("completion_tokens", 0),
            ),
            raw_assistant=message,
            stop_reason=choice.get("finish_reason", ""),
            limits=limits,
        )

    def stream_complete(self, system, messages, tools, on_delta, cancel=None):
        """SSE streaming: emits text deltas via on_delta, accumulates tool calls,
        and aborts promptly on cancel by closing the connection."""
        body = self._build_body(system, messages, tools)
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
        resp = requests.post(self._url(), json=body, headers=self._headers(),
                             timeout=TIMEOUT, stream=True)
        if resp.status_code != 200:
            raise RuntimeError(
                f"{self.cfg.get('short', 'OpenAI')} {resp.status_code}: {resp.text[:400]}")
        text_parts: list[str] = []
        tool_acc: dict[int, dict] = {}
        usage = Usage()
        finish = ""
        for line in resp.iter_lines(decode_unicode=True):
            if cancel is not None and cancel.is_set():
                resp.close()
                break
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            if obj.get("usage"):
                usage = Usage(obj["usage"].get("prompt_tokens", 0),
                              obj["usage"].get("completion_tokens", 0))
            choice = (obj.get("choices") or [{}])[0]
            delta = choice.get("delta", {}) or {}
            if delta.get("content"):
                text_parts.append(delta["content"])
                on_delta(delta["content"])
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                acc = tool_acc.setdefault(idx, {"id": "", "name": "", "args": ""})
                if tc.get("id"):
                    acc["id"] = tc["id"]
                fn = tc.get("function", {}) or {}
                if fn.get("name"):
                    acc["name"] = fn["name"]
                if fn.get("arguments"):
                    acc["args"] += fn["arguments"]
            if choice.get("finish_reason"):
                finish = choice["finish_reason"]
        tool_calls: list[ToolCall] = []
        for acc in tool_acc.values():
            if not acc["name"]:
                continue
            try:
                args = json.loads(acc["args"] or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCall(acc["id"], acc["name"], args))
        raw = {"role": "assistant", "content": "".join(text_parts) or None}
        if tool_calls:
            raw["tool_calls"] = [
                {"id": t.id, "type": "function",
                 "function": {"name": t.name, "arguments": json.dumps(t.args)}}
                for t in tool_calls
            ]
        return CompletionResult(
            text="".join(text_parts).strip(), thinking="", tool_calls=tool_calls,
            usage=usage, raw_assistant=raw, stop_reason=finish, limits=None)

    def _ping_headers(self):
        body = {
            "model": self.cfg["model"],
            "messages": [{"role": "user", "content": "ping"}],
        }
        if self.cfg.get("id") == "openai":
            body["max_completion_tokens"] = 16
        else:
            body["max_tokens"] = 16
        headers = {
            "Authorization": f"Bearer {self.cfg['api_key']}",
            "Content-Type": "application/json",
        }
        resp = requests.post(self._url(), json=body, headers=headers, timeout=60)
        if resp.status_code != 200:
            raise RuntimeError(f"{resp.status_code}: {resp.text[:200]}")
        return resp.headers

    def ping(self) -> str:
        self._ping_headers()
        return "ok"

    def fetch_limits(self) -> dict:
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
        "req_remaining": as_int("x-ratelimit-remaining-requests"),
        "req_limit": as_int("x-ratelimit-limit-requests"),
        "tok_remaining": as_int("x-ratelimit-remaining-tokens"),
        "tok_limit": as_int("x-ratelimit-limit-tokens"),
        "reset": (headers.get("x-ratelimit-reset-tokens")
                  or headers.get("x-ratelimit-reset-requests")),
    }
