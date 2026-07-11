"""Mid-run participant controls: disabling an agent redistributes its work
to exactly ONE remaining agent (not a broadcast), and steering/cancel wiring
behaves. Constructed without starting the QThread, so no network is touched.
"""
import os


POSIX = os.name != "nt"


def _orc(tmp_path, monkeypatch, active_ids=("anthropic", "openai", "xai")):
    import app.config as cfgmod
    from app.orchestrator import Orchestrator
    from app.persistence import RunStore

    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", tmp_path / "cfg" / "config.json")
    cfg = cfgmod.Config(cfgmod._default_config())
    for pid in ("anthropic", "openai", "xai", "local"):
        cfg.providers[pid]["enabled"] = pid in active_ids
        if pid in active_ids and cfg.providers[pid]["auth"] == "api_key":
            cfg.providers[pid]["api_key"] = "k"
    project = {"id": "p1", "name": "Demo", "local_path": str(tmp_path)}
    store = RunStore(tmp_path / "db.sqlite")
    return Orchestrator(cfg, project, "инструкция", store=store)


def _seed_running(orc, pids):
    """Simulate the state _execute sets up while agents are working."""
    import threading
    for pid in pids:
        orc.live_ids.add(pid)
        orc.agent_cancels[pid] = threading.Event()
        orc.assignments[pid] = {
            "objective": f"работа {pid}", "files": [f"{pid}.py"], "role": pid}


def test_disable_agent_cancels_and_hands_to_single_taker(tmp_path, monkeypatch):
    orc = _orc(tmp_path, monkeypatch)
    _seed_running(orc, ["anthropic", "openai", "xai"])

    before = orc.steering.drain_from(0)[1]
    orc.disable_agent("openai")

    # disabled bookkeeping
    assert "openai" in orc.disabled
    assert "openai" not in orc.live_ids
    assert orc.agent_cancels["openai"].is_set()
    # the other two are still live and NOT cancelled
    assert orc.live_ids == {"anthropic", "xai"}
    assert not orc.agent_cancels["anthropic"].is_set()
    assert not orc.agent_cancels["xai"].is_set()

    new_items, _ = orc.steering.drain_from(before)
    assert len(new_items) == 1, "exactly one redistribution message, not a broadcast"
    msg = new_items[0]
    # the disabled agent (ChatGPT) is named as handed-off; exactly ONE of the
    # remaining agents is named as the single taker (not a broadcast to both)
    assert "ChatGPT" in msg
    remaining_takers = [s for s in ("Claude", "Grok") if s in msg]
    assert len(remaining_takers) == 1
    assert "ТОЛЬКО" in msg and "не дубл" in msg.lower()


def test_disable_last_active_agent_has_no_taker(tmp_path, monkeypatch):
    orc = _orc(tmp_path, monkeypatch)
    _seed_running(orc, ["anthropic"])

    before = orc.steering.drain_from(0)[1]
    orc.disable_agent("anthropic")

    assert orc.live_ids == set()
    assert orc.agent_cancels["anthropic"].is_set()
    # no taker exists, so no redistribution steering is queued
    new_items, _ = orc.steering.drain_from(before)
    assert new_items == []


def test_disable_is_idempotent_and_ignores_unknown(tmp_path, monkeypatch):
    orc = _orc(tmp_path, monkeypatch)
    _seed_running(orc, ["anthropic", "xai"])

    orc.disable_agent("anthropic")
    live_after_first = set(orc.live_ids)
    before = orc.steering.drain_from(0)[1]
    # disabling again, or a provider that isn't live, is a no-op
    orc.disable_agent("anthropic")
    orc.disable_agent("not-a-provider")
    assert orc.live_ids == live_after_first
    assert orc.steering.drain_from(before)[0] == []


def test_cancel_sets_every_agent_event(tmp_path, monkeypatch):
    orc = _orc(tmp_path, monkeypatch)
    _seed_running(orc, ["anthropic", "openai", "xai"])
    orc.cancel()
    assert orc.cancel_event.is_set()
    assert all(ev.is_set() for ev in orc.agent_cancels.values())


def test_add_agent_already_live_returns_true(tmp_path, monkeypatch):
    orc = _orc(tmp_path, monkeypatch)
    _seed_running(orc, ["anthropic"])
    assert orc.add_agent({"id": "anthropic", "short": "Claude"}) is True


def test_add_agent_hotjoin_declines_honestly(tmp_path, monkeypatch):
    # A model that isn't live can't be safely hot-joined mid-run: the call must
    # decline (return False) rather than crash the caller or silently pretend.
    orc = _orc(tmp_path, monkeypatch)
    _seed_running(orc, ["anthropic"])
    logs = []
    orc.log.connect(logs.append)
    assert orc.add_agent({"id": "xai", "short": "Grok"}) is False
    assert "xai" not in orc.live_ids
    assert any("Grok" in m for m in logs)
