"""Configurable adaptive escalation ladder + full_council auto-deliberation.

`_attempt_plan` / `_team_for_mode` are pure team-selection logic — tested by
constructing an Orchestrator without starting the QThread (no network).
"""


def _orc(tmp_path, monkeypatch, active_ids, execution_mode="adaptive",
         ladder=None):
    import app.cliagents as ca
    import app.config as cfgmod
    from app.orchestrator import Orchestrator
    from app.persistence import RunStore

    monkeypatch.setattr(ca, "quick_ready", lambda flavor, home=None: False)
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", tmp_path / "cfg" / "config.json")
    cfg = cfgmod.Config(cfgmod._default_config())
    for pid in list(cfg.providers):
        cfg.providers[pid]["enabled"] = pid in active_ids
        if pid in active_ids and cfg.providers[pid]["auth"] == "api_key":
            cfg.providers[pid]["api_key"] = "k"
    cfg.orchestration["execution_mode"] = execution_mode
    cfg.orchestration["lead_provider"] = "anthropic"
    if ladder is not None:
        cfg.orchestration["escalation_ladder"] = ladder
    project = {"id": "p1", "name": "Demo", "local_path": str(tmp_path)}
    return Orchestrator(cfg, project, "task", store=RunStore(tmp_path / "db.sqlite"))


def _sig(attempts):
    return [([p["id"] for p in impl], rev["id"] if rev else None)
            for impl, rev in attempts]


def test_default_ladder_solo_then_pair(tmp_path, monkeypatch):
    orc = _orc(tmp_path, monkeypatch, ("anthropic", "openai", "xai"))
    attempts = orc._attempt_plan()
    assert _sig(attempts) == [(["anthropic"], None),
                              (["anthropic"], "openai")]


def test_full_ladder_grows_solo_pair_full_council(tmp_path, monkeypatch):
    orc = _orc(tmp_path, monkeypatch, ("anthropic", "openai", "xai"),
               ladder=["solo", "pair", "full_council"])
    attempts = orc._attempt_plan()
    sigs = _sig(attempts)
    assert sigs[0] == (["anthropic"], None)          # solo
    assert sigs[1] == (["anthropic"], "openai")      # pair
    # full_council: all three implement, lead reviews
    assert set(sigs[2][0]) == {"anthropic", "openai", "xai"}
    assert sigs[2][1] == "anthropic"


def test_ladder_skips_steps_needing_more_providers(tmp_path, monkeypatch):
    # only two providers: full_council collapses; dedupe keeps distinct teams
    orc = _orc(tmp_path, monkeypatch, ("anthropic", "openai"),
               ladder=["solo", "pair", "full_council"])
    sigs = _sig(orc._attempt_plan())
    assert sigs[0] == (["anthropic"], None)
    assert sigs[1] == (["anthropic"], "openai")
    # full_council with 2 providers = both implement + lead review (distinct)
    assert set(sigs[2][0]) == {"anthropic", "openai"}


def test_ladder_dedupes_repeats(tmp_path, monkeypatch):
    orc = _orc(tmp_path, monkeypatch, ("anthropic", "openai"),
               ladder=["solo", "solo", "pair", "pair"])
    assert _sig(orc._attempt_plan()) == [(["anthropic"], None),
                                         (["anthropic"], "openai")]


def test_team_for_mode_needs_enough_providers(tmp_path, monkeypatch):
    orc = _orc(tmp_path, monkeypatch, ("anthropic",))
    assert orc._team_for_mode("pair") is None
    assert orc._team_for_mode("full_council") is None
    assert orc._team_for_mode("solo") is not None


def test_non_adaptive_single_attempt_unchanged(tmp_path, monkeypatch):
    orc = _orc(tmp_path, monkeypatch, ("anthropic", "openai"),
               execution_mode="pair")
    assert len(orc._attempt_plan()) == 1


def test_single_provider_no_escalation(tmp_path, monkeypatch):
    orc = _orc(tmp_path, monkeypatch, ("anthropic",))
    assert len(orc._attempt_plan()) == 1


# a light guard that the config default is present and sane
def test_config_default_ladder(tmp_path, monkeypatch):
    import app.config as cfgmod
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", tmp_path / "cfg" / "config.json")
    cfg = cfgmod.Config(cfgmod._default_config())
    assert cfg.orchestration["escalation_ladder"] == ["solo", "pair"]
