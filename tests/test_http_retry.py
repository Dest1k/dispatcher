import pytest

from app.providers.http import retrying_post

NOSLEEP = lambda s: None


class _Resp:
    def __init__(self, status, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self.text = ""


class _Requests:
    def __init__(self, statuses):
        self.statuses = statuses
        self.calls = 0

    def post(self, url, json=None, headers=None, timeout=None):
        s = self.statuses[min(self.calls, len(self.statuses) - 1)]
        self.calls += 1
        return _Resp(s)


def test_retries_transient_then_succeeds():
    r = _Requests([429, 503, 200])
    resp = retrying_post(r, "u", {}, {}, 10, sleep=NOSLEEP)
    assert resp.status_code == 200 and r.calls == 3


def test_gives_up_returns_last_transient():
    r = _Requests([500, 500, 500, 500])
    resp = retrying_post(r, "u", {}, {}, 10, sleep=NOSLEEP)
    assert resp.status_code == 500 and r.calls == 4  # 1 + 3 retries


def test_permanent_error_not_retried():
    r = _Requests([400, 200])
    resp = retrying_post(r, "u", {}, {}, 10, sleep=NOSLEEP)
    assert resp.status_code == 400 and r.calls == 1


def test_connection_error_retried_then_raised():
    class Boom:
        def __init__(self):
            self.calls = 0

        def post(self, *a, **k):
            self.calls += 1
            raise ConnectionError("net down")

    b = Boom()
    with pytest.raises(ConnectionError):
        retrying_post(b, "u", {}, {}, 10, sleep=NOSLEEP)
    assert b.calls == 4


def test_retry_after_header_respected(monkeypatch):
    slept = []
    r = _Requests([429, 200])
    r.statuses = [429, 200]

    class _RespRA(_Resp):
        pass

    # first call returns 429 with Retry-After, second 200
    calls = {"n": 0}

    class RA:
        def post(self, *a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                return _Resp(429, {"retry-after": "2"})
            return _Resp(200)

    retrying_post(RA(), "u", {}, {}, 10, sleep=lambda s: slept.append(s))
    assert slept and abs(slept[0] - 2) < 0.001
