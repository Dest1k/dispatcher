"""OpenAI-compatible adapter — drives both ChatGPT (OpenAI) and Grok (xAI).

xAI's API is OpenAI Chat Completions compatible, so one adapter serves both;
only the base URL, model id and token-limit field name differ. Reasoning depth
is controlled with the top-level `reasoning_effort` parameter.
"""
from __future__ import annotations

import json

import requests

from .base import BaseAdapter, CompletionResult, Message, ToolCall, ToolSpec, Usage

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

    def complete(self, system: str, messages: list[Message],
                 tools: list[ToolSpec]) -> CompletionResult:
        body: dict = {
            "model": self.cfg["model"],
            "messages": self._to_messages(system, messages),
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ],
            "tool_choice": "auto",
        }
        max_tokens = int(self.cfg.get("max_tokens", 16000))
        # Newer OpenAI reasoning models require max_completion_tokens; xAI uses max_tokens.
        if self.cfg.get("id") == "openai":
            body["max_completion_tokens"] = max_tokens
        else:
            body["max_tokens"] = max_tokens
        effort = self.cfg.get("effort")
        if effort and effort != "none":
            body["reasoning_effort"] = effort

        headers = {
            "Authorization": f"Bearer {self.cfg['api_key']}",
            "Content-Type": "application/json",
        }
        resp = requests.post(self._url(), json=body, headers=headers, timeout=TIMEOUT)
        if resp.status_code != 200:
            raise RuntimeError(
                f"{self.cfg.get('short', 'OpenAI')} {resp.status_code}: {resp.text[:400]}")
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
        )

    def ping(self) -> str:
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
        return "ok"
