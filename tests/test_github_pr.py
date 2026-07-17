"""Draft-PR creation via the GitHub API (mocked) + orchestrator publish wiring."""
from app.github_pr import create_draft_pr


class FakeResp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeRequests:
    def __init__(self, resp=None, exc=None):
        self.resp = resp
        self.exc = exc
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        if self.exc:
            raise self.exc
        return self.resp


def test_create_draft_pr_success():
    rq = FakeRequests(FakeResp(201, {"html_url": "https://github.com/o/r/pull/7",
                                     "number": 7}))
    out = create_draft_pr("o/r", "dispatcher/x/integration", "main",
                          "Заголовок", "тело", "tok_123", requests_mod=rq)
    assert out["created"] is True
    assert out["url"].endswith("/pull/7") and out["number"] == 7
    call = rq.calls[0]
    assert call["url"] == "https://api.github.com/repos/o/r/pulls"
    assert call["json"]["draft"] is True
    assert call["json"]["head"] == "dispatcher/x/integration"
    assert call["json"]["base"] == "main"
    assert call["headers"]["Authorization"] == "Bearer tok_123"


def test_missing_inputs_declined_without_call():
    rq = FakeRequests()
    for args in [("", "h", "b", "t", "x", "tok"),        # no repo
                 ("o/r", "", "b", "t", "x", "tok"),       # no head
                 ("o/r", "h", "b", "t", "x", ""),         # no token
                 ("noslash", "h", "b", "t", "x", "tok")]:  # bad repo
        out = create_draft_pr(*args, requests_mod=rq)
        assert out["created"] is False
    assert rq.calls == []                                # never hit the API


def test_existing_pr_422_surfaced():
    rq = FakeRequests(FakeResp(422, {"message": "Validation Failed",
                                     "errors": [{"message": "A pull request already exists"}]}))
    out = create_draft_pr("o/r", "h", "main", "t", "b", "tok", requests_mod=rq)
    assert out["created"] is False
    assert "422" in out["message"] and "already exists" in out["message"]


def test_network_error_never_raises():
    rq = FakeRequests(exc=ConnectionError("dns fail"))
    out = create_draft_pr("o/r", "h", "main", "t", "b", "tok", requests_mod=rq)
    assert out["created"] is False and "сеть" in out["message"]


def test_token_redacted_in_error(monkeypatch):
    from app.security import register_secret
    register_secret("ghp_SECRETTOKEN123456")
    # a hypothetical error body echoing the token must be masked
    rq = FakeRequests(FakeResp(403, {"message": "bad ghp_SECRETTOKEN123456"}))
    out = create_draft_pr("o/r", "h", "main", "t", "b", "ghp_SECRETTOKEN123456",
                          requests_mod=rq)
    assert "ghp_SECRETTOKEN123456" not in out["message"]


# ---- orchestrator publish wiring -------------------------------------------

class _FakeRW:
    def __init__(self):
        self.integration_branch = "dispatcher/test/integration"
        self.run_id = "test"
        self.pushed = False
        self.cleaned = False

    def commit_integration(self, message):
        return "abc1234"

    def integration_head(self):
        return "abc1234567"

    def push_integration(self, github_url="", token=""):
        self.pushed = True
        return "ok"

    def cleanup(self, keep=None):
        self.cleaned = True


def _orc(tmp_path, monkeypatch, auto_pr):
    import app.cliagents as ca
    import app.config as cfgmod
    from app.orchestrator import Orchestrator
    from app.persistence import RunStore
    monkeypatch.setattr(ca, "quick_ready", lambda flavor, home=None: False)
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", tmp_path / "cfg" / "config.json")
    cfg = cfgmod.Config(cfgmod._default_config())
    for pid in list(cfg.providers):
        cfg.providers[pid]["enabled"] = pid == "anthropic"
    cfg.providers["anthropic"]["api_key"] = "k"
    cfg.orchestration["auto_draft_pr"] = auto_pr
    project = {"id": "p1", "name": "Demo", "local_path": str(tmp_path),
               "github_repo": "o/r", "github_url": "https://github.com/o/r.git",
               "branch": "main", "github_token": "tok_abc"}
    orc = Orchestrator(cfg, project, "task", store=RunStore(tmp_path / "db.sqlite"))
    orc.run_id = orc.store.create_run("p1", "task", "base")
    orc.rw = _FakeRW()
    orc._report = "# Заголовок\nтело отчёта"
    return orc


def test_publish_creates_draft_pr_when_enabled(tmp_path, monkeypatch, qapp):
    import app.github_pr as gh
    orc = _orc(tmp_path, monkeypatch, auto_pr=True)
    seen = {}

    def fake_create(repo, head, base, title, body, token, **kw):
        seen.update(repo=repo, head=head, base=base, token=token)
        return {"created": True, "url": "https://github.com/o/r/pull/9",
                "number": 9, "message": "ok"}

    monkeypatch.setattr(gh, "create_draft_pr", fake_create)
    captured = {}
    orc.run_finished.connect(lambda r: captured.setdefault("r", r))
    orc._publish(push=True, blocked=False)

    r = captured["r"]
    assert r["pushed"] is True and r.get("pr_created") is True
    assert r["pr_url"] == "https://github.com/o/r/pull/9"
    assert seen == {"repo": "o/r", "head": "dispatcher/test/integration",
                    "base": "main", "token": "tok_abc"}


def test_publish_no_pr_when_disabled(tmp_path, monkeypatch, qapp):
    import app.github_pr as gh
    orc = _orc(tmp_path, monkeypatch, auto_pr=False)
    called = {"n": 0}
    monkeypatch.setattr(gh, "create_draft_pr",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    captured = {}
    orc.run_finished.connect(lambda r: captured.setdefault("r", r))
    orc._publish(push=True, blocked=False)
    assert called["n"] == 0                       # not attempted
    assert captured["r"].get("pr_created") is None
    # the one-click compare URL is still provided
    assert "compare" in captured["r"]["pr_url"]


def test_publish_pr_failure_keeps_compare_url(tmp_path, monkeypatch, qapp):
    import app.github_pr as gh
    orc = _orc(tmp_path, monkeypatch, auto_pr=True)
    monkeypatch.setattr(gh, "create_draft_pr",
                        lambda *a, **k: {"created": False, "url": "",
                                         "message": "GitHub 403: forbidden"})
    captured = {}
    orc.run_finished.connect(lambda r: captured.setdefault("r", r))
    orc._publish(push=True, blocked=False)
    r = captured["r"]
    assert r.get("pr_created") is None
    assert "compare" in r["pr_url"]               # fell back
    assert "Авто-PR не создан" in r["message"]
