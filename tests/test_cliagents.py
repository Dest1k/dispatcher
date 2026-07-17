"""CLI discovery: binaries, session evidence, local model caches.

No real subprocesses and no network — everything is driven off a fake HOME.
"""
import json

import app.cliagents as ca


def _home(tmp_path):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return home


def test_not_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(ca, "_which", lambda name: None)
    st = ca.detect("claude", home=_home(tmp_path), run_commands=False)
    assert not st.installed
    assert not st.ready
    assert "не установлен" in st.detail


def test_claude_ready_via_session_file(monkeypatch, tmp_path):
    home = _home(tmp_path)
    (home / ".claude").mkdir()
    (home / ".claude" / ".credentials.json").write_text("{}")
    monkeypatch.setattr(ca, "_which", lambda name: str(tmp_path / "claude.cmd"))
    st = ca.detect("claude", home=home, run_commands=False)
    assert st.installed and st.authenticated is True and st.ready
    assert st.default_model == "claude-opus-4-8"
    assert st.default_effort == "ultracode"
    assert "max" in st.effort_options()
    assert "ultracode" in st.effort_options()


def test_claude_auth_via_oauth_account(monkeypatch, tmp_path):
    home = _home(tmp_path)
    (home / ".claude.json").write_text(json.dumps({"oauthAccount": {"id": "x"}}))
    ok, source = ca.auth_evidence("claude", home=home)
    assert ok is True
    assert "oauth" in source.lower()


def test_installed_but_not_logged_in(monkeypatch, tmp_path):
    home = _home(tmp_path)
    monkeypatch.setattr(ca, "_which", lambda name: str(tmp_path / "grok.exe"))
    st = ca.detect("grok", home=home, run_commands=False)
    assert st.installed and st.authenticated is None and not st.ready
    assert "вход не выполнен" in st.detail


def test_codex_models_from_local_cache(monkeypatch, tmp_path):
    home = _home(tmp_path)
    (home / ".codex").mkdir()
    (home / ".codex" / "auth.json").write_text("{}")
    cache = {"models": [
        {"slug": "gpt-5.6-sol", "supported_reasoning_levels": [
            {"effort": "low"}, {"effort": "high"}, {"effort": "ultra"}]},
        {"slug": "secret-model", "visibility": "hidden"},
    ]}
    (home / ".codex" / "models_cache.json").write_text(json.dumps(cache))
    monkeypatch.setattr(ca, "_which", lambda name: str(tmp_path / "codex.exe"))
    st = ca.detect("codex", home=home, run_commands=False)
    assert st.ready
    assert st.models == ["gpt-5.6-sol"]           # hidden models excluded
    assert st.efforts["gpt-5.6-sol"] == ["low", "high", "ultra"]
    assert "кэш" in st.models_source.lower()
    assert st.default_model == "gpt-5.6-sol"


def test_grok_models_from_local_cache(monkeypatch, tmp_path):
    home = _home(tmp_path)
    (home / ".grok").mkdir()
    (home / ".grok" / "auth.json").write_text("{}")
    cache = {"models": {
        "grok-4.5": {"info": {"reasoning_efforts": [
            {"value": "high"}, {"value": "medium"}, {"value": "low"}]}},
        "grok-hidden": {"info": {"hidden": True}},
        "grok-composer-2.5-fast": {"info": {}},
    }}
    (home / ".grok" / "models_cache.json").write_text(json.dumps(cache))
    monkeypatch.setattr(ca, "_which", lambda name: str(tmp_path / "grok.exe"))
    st = ca.detect("grok", home=home, run_commands=False)
    assert st.ready
    assert "grok-4.5" in st.models and "grok-composer-2.5-fast" in st.models
    assert "grok-hidden" not in st.models
    assert st.efforts["grok-4.5"] == ["high", "medium", "low"]
    assert st.default_model == "grok-4.5"


def test_default_model_falls_back_to_first_discovered(monkeypatch, tmp_path):
    home = _home(tmp_path)
    (home / ".grok").mkdir()
    (home / ".grok" / "auth.json").write_text("{}")
    (home / ".grok" / "models_cache.json").write_text(json.dumps(
        {"models": {"grok-9-experimental": {"info": {}}}}))
    monkeypatch.setattr(ca, "_which", lambda name: str(tmp_path / "grok.exe"))
    st = ca.detect("grok", home=home, run_commands=False)
    assert st.default_model == "grok-9-experimental"


def test_corrupt_cache_falls_back_to_registry(monkeypatch, tmp_path):
    home = _home(tmp_path)
    (home / ".codex").mkdir()
    (home / ".codex" / "models_cache.json").write_text("{broken json")
    models, efforts, source = ca.discover_models("codex", home=home)
    assert models == ["gpt-5.6-sol"]
    assert "реестр" in source


def test_quick_ready_is_cached(monkeypatch):
    ca.clear_cache()
    calls = {"n": 0}

    def fake_find(flavor, home=None):
        calls["n"] += 1
        return "somewhere"

    monkeypatch.setattr(ca, "find_binary", fake_find)
    monkeypatch.setattr(ca, "auth_evidence", lambda f, home=None: (True, "ok"))
    assert ca.quick_ready("claude") is True
    assert ca.quick_ready("claude") is True
    assert calls["n"] == 1                       # second call served from cache
    ca.clear_cache()


def test_find_binary_known_location(monkeypatch, tmp_path):
    home = _home(tmp_path)
    grok_bin = home / ".grok" / "bin"
    grok_bin.mkdir(parents=True)
    (grok_bin / "grok").write_text("")
    monkeypatch.setattr(ca, "_which", lambda name: None)
    assert ca.find_binary("grok", home=home).endswith("grok")


def test_status_to_dict_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(ca, "_which", lambda name: None)
    st = ca.detect("codex", home=_home(tmp_path), run_commands=False)
    d = st.to_dict()
    assert d["flavor"] == "codex" and d["ready"] is False
    assert isinstance(d["models"], list)
