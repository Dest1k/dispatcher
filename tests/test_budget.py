import json


from app.budget import BudgetGuard


def test_guard_no_cap_never_exceeds():
    g = BudgetGuard(0.0)
    g.add(1000)
    assert not g.exceeded()
    assert g.remaining() == float("inf")


def test_guard_exceeds_at_cap():
    g = BudgetGuard(1.0)
    g.add(0.4)
    assert not g.exceeded()
    g.add(0.7)
    assert g.exceeded()
    assert g.remaining() == 0.0


def test_guard_warns_once():
    g = BudgetGuard(1.0, warn_ratio=0.8)
    g.add(0.5)
    assert not g.should_warn()
    g.add(0.4)          # total 0.9 >= 0.8
    assert g.should_warn()
    assert not g.should_warn()   # only once


def test_orchestrator_budget_stop(qapp, tmp_config):
    from app.config import Config, _default_config
    from app.orchestrator import Orchestrator
    cfg = Config(_default_config())
    cfg.providers["anthropic"]["api_key"] = "k"
    cfg.orchestration["budget_usd"] = 0.001      # tiny cap
    project = {"id": "p", "name": "d", "local_path": ".", "verify_commands": []}
    orc = Orchestrator(cfg, project, "task")
    cb = orc._emit_agent("anthropic")
    # one turn of 1M input tokens at $5/1M = $5 -> way over the $0.001 cap
    cb("usage", json.dumps({"in": 1_000_000, "out": 0}))
    assert orc.budget_event.is_set()
    orc.store.close()
