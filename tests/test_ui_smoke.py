"""Headless (offscreen) UI construction — no network, no real config writes."""
import pytest


@pytest.fixture
def window(qapp, tmp_config):
    from app.ui.main_window import MainWindow
    win = MainWindow()
    yield win
    win.close()


def test_window_builds_empty(window):
    assert window.project_name.text() == "Нет проекта"


def test_panels_follow_available_providers(window):
    win = window
    win.config.providers["anthropic"]["api_key"] = "sk-test"
    win.config.save()
    proj = win.config.add_project("Demo", "/tmp/demo-x", "o/r",
                                  "https://github.com/o/r.git", "main", "")
    win._refresh_projects()
    assert win.project_name.text() == "Demo"
    # available = has credentials: anthropic (key) + local (auth=none). Not openai/xai.
    assert set(win.agent_panels.keys()) == {"anthropic", "local"}


def test_settings_dialog_dynamic_tabs(qapp, tmp_config):
    from app.config import Config, _default_config
    from app.ui.settings_dialog import SettingsDialog
    cfg = Config(_default_config())
    dlg = SettingsDialog(cfg)
    # one tab per provider + the orchestration tab
    assert dlg.tabs.count() == len(cfg.ordered_providers()) + 1
    # safety knobs are exposed in the UI, not just JSON
    assert dlg.sandbox_mode.count() == 3
    assert dlg.execution_mode.count() == 4
    assert dlg.require_verification is not None
    # execution-time limits are editable knobs, not just JSON
    dlg.command_timeout.setValue(120)
    dlg.verify_timeout.setValue(600)
    dlg._save()
    assert cfg.orchestration["sandbox_mode"] == "restricted"
    assert cfg.orchestration["auto_push"] is False
    assert cfg.orchestration["command_timeout"] == 120
    assert cfg.orchestration["verify_timeout"] == 600
    dlg.close()


def test_agent_panel_shows_billing(qapp, tmp_config):
    from app.config import Config, _default_config
    from app.ui.agent_panel import AgentPanel
    cfg = Config(_default_config())
    grok = AgentPanel(cfg.providers["xai"])
    assert "SuperGrok" in grok.name_label.text()
    local = AgentPanel(cfg.providers["local"])
    assert "локально" in local.name_label.text()


def test_no_autoping_on_startup(window, monkeypatch):
    # Constructing the window must not have started a LimitsChecker (no paid ping).
    assert window._limits_checker is None


def test_panel_toggle_running_does_not_crash(window):
    """Toggling a panel while a run is live must route to the orchestrator's
    disable/hot-join controls without raising (regression: add_agent was
    missing and raised AttributeError)."""
    import threading

    from app.orchestrator import Orchestrator
    win = window
    win.config.providers["anthropic"]["api_key"] = "sk-test"
    win.config.save()
    win.config.add_project("Demo", "/tmp/demo-toggle", "o/r",
                           "https://github.com/o/r.git", "main", "")
    win._refresh_projects()

    project = {"id": "p1", "name": "Demo", "local_path": "/tmp/demo-toggle"}
    orc = Orchestrator(win.config, project, "task", store=win.run_store)
    orc.live_ids.add("anthropic")
    orc.agent_cancels["anthropic"] = threading.Event()
    win.orchestrator = orc
    win.running_project_id = "p1"

    panel = win.agent_panels["anthropic"]
    panel.set_live(True)
    win._on_panel_toggle("anthropic")            # disable a live agent
    assert "anthropic" in orc.disabled
    assert panel.live is False

    win._on_panel_toggle("anthropic")            # attempt hot-join -> declined
    assert panel.live is False                    # honestly not re-added


def test_runs_dialog_lists_runs(qapp, tmp_config, tmp_path):
    from app.config import Config, _default_config
    from app.persistence import RunStore
    from app.ui.runs_dialog import RunsDialog
    store = RunStore(tmp_path / "db.sqlite")
    cfg = Config(_default_config())
    project = {"id": "pX", "name": "Demo", "local_path": str(tmp_path)}
    rid = store.create_run("pX", "сделать что-то")
    store.add_usage(rid, "anthropic", 100, 50, 0.0011)
    dlg = RunsDialog(store, cfg, project)
    assert dlg.table.rowCount() == 1
    assert "сделать" in dlg.table.item(0, 0).text()
    dlg.close()
    store.close()
