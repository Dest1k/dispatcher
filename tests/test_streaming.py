import json
import threading

from app.config import _default_config
from app.providers import make_adapter


class _StreamResp:
    def __init__(self, lines, status=200):
        self.status_code = status
        self._lines = lines
        self.text = ""
        self.closed = False

    def iter_lines(self, decode_unicode=True):
        for ln in self._lines:
            yield ln

    def close(self):
        self.closed = True


def _sse(obj):
    return "data: " + json.dumps(obj)


def test_openai_stream_accumulates_text_and_tool_calls(monkeypatch):
    import app.providers.openai_compat as oai
    lines = [
        _sse({"choices": [{"delta": {"content": "Hel"}}]}),
        _sse({"choices": [{"delta": {"content": "lo"}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c1", "function": {"name": "write_file",
                                                  "arguments": "{\"pa"}}]}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": "th\": \"a.txt\"}"}}]}}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}],
              "usage": {"prompt_tokens": 5, "completion_tokens": 3}}),
        "data: [DONE]",
    ]
    monkeypatch.setattr(oai.requests, "post",
                        lambda *a, **k: _StreamResp(lines))
    adapter = make_adapter(_default_config()["providers"]["openai"])
    deltas = []
    result = adapter.stream_complete("sys", [], [], deltas.append, threading.Event())
    assert result.text == "Hello"
    assert deltas == ["Hel", "lo"]
    assert result.tool_calls[0].name == "write_file"
    assert result.tool_calls[0].args == {"path": "a.txt"}
    assert result.usage.input_tokens == 5 and result.usage.output_tokens == 3
    # raw_assistant round-trips as a valid OpenAI assistant message with tool_calls
    assert result.raw_assistant["tool_calls"][0]["function"]["name"] == "write_file"


def test_stream_cancel_closes_connection(monkeypatch):
    import app.providers.openai_compat as oai
    cancel = threading.Event()
    cancel.set()
    resp = _StreamResp([_sse({"choices": [{"delta": {"content": "x"}}]})])
    monkeypatch.setattr(oai.requests, "post", lambda *a, **k: resp)
    adapter = make_adapter(_default_config()["providers"]["openai"])
    adapter.stream_complete("sys", [], [], lambda c: None, cancel)
    assert resp.closed


def _an(obj):
    # Anthropic SSE payloads arrive as data: lines with a typed JSON object.
    return "data: " + json.dumps(obj)


def test_anthropic_stream_accumulates_text_thinking_and_tools(monkeypatch):
    import app.providers.anthropic as anth
    lines = [
        _an({"type": "message_start", "message": {"usage": {"input_tokens": 7}}}),
        _an({"type": "content_block_start", "index": 0,
             "content_block": {"type": "thinking", "thinking": ""}}),
        _an({"type": "content_block_delta", "index": 0,
             "delta": {"type": "thinking_delta", "thinking": "думаю"}}),
        _an({"type": "content_block_delta", "index": 0,
             "delta": {"type": "signature_delta", "signature": "sig"}}),
        _an({"type": "content_block_stop", "index": 0}),
        _an({"type": "content_block_start", "index": 1,
             "content_block": {"type": "text", "text": ""}}),
        _an({"type": "content_block_delta", "index": 1,
             "delta": {"type": "text_delta", "text": "При"}}),
        _an({"type": "content_block_delta", "index": 1,
             "delta": {"type": "text_delta", "text": "вет"}}),
        _an({"type": "content_block_stop", "index": 1}),
        _an({"type": "content_block_start", "index": 2,
             "content_block": {"type": "tool_use", "id": "t1", "name": "write_file",
                               "input": {}}}),
        _an({"type": "content_block_delta", "index": 2,
             "delta": {"type": "input_json_delta", "partial_json": "{\"path\":"}}),
        _an({"type": "content_block_delta", "index": 2,
             "delta": {"type": "input_json_delta", "partial_json": " \"a.txt\"}"}}),
        _an({"type": "content_block_stop", "index": 2}),
        _an({"type": "message_delta", "delta": {"stop_reason": "tool_use"},
             "usage": {"output_tokens": 4}}),
        _an({"type": "message_stop"}),
    ]
    monkeypatch.setattr(anth.requests, "post", lambda *a, **k: _StreamResp(lines))
    adapter = make_adapter(_default_config()["providers"]["anthropic"])
    deltas = []
    result = adapter.stream_complete("sys", [], [], deltas.append, threading.Event())

    assert deltas == ["При", "вет"]                    # only text is streamed
    assert result.text == "Привет"
    assert result.thinking == "думаю"                   # captured, not streamed
    assert result.usage.input_tokens == 7 and result.usage.output_tokens == 4
    assert result.stop_reason == "tool_use"
    # tool call assembled from streamed partial JSON
    assert result.tool_calls[0].name == "write_file"
    assert result.tool_calls[0].args == {"path": "a.txt"}
    # raw_assistant round-trips ordered blocks (thinking, text, tool_use) with
    # the tool_use input resolved and the thinking signature preserved
    types = [b["type"] for b in result.raw_assistant]
    assert types == ["thinking", "text", "tool_use"]
    assert result.raw_assistant[0]["signature"] == "sig"
    assert result.raw_assistant[2]["input"] == {"path": "a.txt"}


def test_anthropic_stream_cancel_closes_connection(monkeypatch):
    import app.providers.anthropic as anth
    cancel = threading.Event()
    cancel.set()
    resp = _StreamResp([_an({"type": "content_block_delta", "index": 0,
                             "delta": {"type": "text_delta", "text": "x"}})])
    monkeypatch.setattr(anth.requests, "post", lambda *a, **k: resp)
    adapter = make_adapter(_default_config()["providers"]["anthropic"])
    adapter.stream_complete("sys", [], [], lambda c: None, cancel)
    assert resp.closed
