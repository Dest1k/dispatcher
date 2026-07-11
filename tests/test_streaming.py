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
