"""A failed authenticated push must not leak the GitHub token into the run
result or the persisted run state. git echoes the tokenized remote URL on a
push error; _publish must redact it (criterion §22).
"""


class _FakeRW:
    """Stands in for RunWorkspaces: commit succeeds, push fails with a git-style
    error that embeds the authenticated remote URL (token in cleartext)."""

    def __init__(self, token):
        self.integration_branch = "dispatcher/test/integration"
        self.run_id = "test"
        self._token = token
        self.cleaned = False

    def commit_integration(self, message):
        return "abc1234"

    def push_integration(self, github_url="", token=""):
        from app.workspace import WorkspaceError
        raise WorkspaceError(
            "fatal: unable to access "
            f"'https://x-access-token:{self._token}@github.com/o/r.git/': "
            "The requested URL returned error: 403")

    def cleanup(self, keep=None):
        self.cleaned = True


def _orc(tmp_path, monkeypatch, token):
    import app.config as cfgmod
    from app.orchestrator import Orchestrator
    from app.persistence import RunStore

    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", tmp_path / "cfg" / "config.json")
    cfg = cfgmod.Config(cfgmod._default_config())
    for pid in list(cfg.providers):
        cfg.providers[pid]["enabled"] = pid == "anthropic"
    cfg.providers["anthropic"]["api_key"] = "k"
    project = {"id": "p1", "name": "Demo", "local_path": str(tmp_path),
               "github_repo": "o/r", "github_url": "https://github.com/o/r.git",
               "branch": "main", "github_token": token}
    store = RunStore(tmp_path / "db.sqlite")
    orc = Orchestrator(cfg, project, "task", store=store)
    orc.run_id = store.create_run("p1", "task", "base")
    orc.rw = _FakeRW(token)
    orc._report = "# Заголовок\nтекст"
    return orc, store


def test_push_error_does_not_leak_token(tmp_path, monkeypatch, qapp):
    token = "github_pat_SECRETVALUE_1234567890abcdef"
    orc, store = _orc(tmp_path, monkeypatch, token)

    captured = {}
    orc.run_finished.connect(lambda r: captured.setdefault("result", r))
    orc._publish(push=True, blocked=False)

    result = captured["result"]
    # partial (push failed) but the token must be nowhere in the surfaced message
    assert result["status"] == "partial"
    assert token not in result["message"]
    assert "REDACTED" in result["message"]

    # and not in the persisted run state either
    run = store.get(orc.run_id)
    assert token not in (run.message or "")
    store.close()
