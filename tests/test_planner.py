"""Task planning engine: phased plan, role routing, security phase, enrichment."""
import json

import pytest

from app.planner import generate_plan
from app.providers.base import CompletionResult, Usage


def _providers():
    return [
        {"id": "claude_cli", "label": "Claude", "short": "Claude",
         "model": "claude-opus-4-8"},
        {"id": "codex_cli", "label": "Codex", "short": "Codex",
         "model": "gpt-5.6-sol"},
        {"id": "grok_cli", "label": "Grok", "short": "Grok",
         "model": "grok-4.5"},
    ]


class _FailAdapter:
    def __init__(self, cfg):
        pass

    def complete(self, system, messages, tools):
        raise RuntimeError("no model in test")


class _EnrichAdapter:
    def __init__(self, cfg):
        pass

    def complete(self, system, messages, tools):
        payload = {"analyze": "изучить auth-модуль", "design": "выбрать JWT",
                   "implement": "переписать логин", "test": "тесты на токены",
                   "security": "проверить утечки секретов", "review": "финал"}
        return CompletionResult(text=json.dumps(payload), thinking="",
                                tool_calls=[], usage=Usage(3, 3),
                                raw_assistant=None, stop_reason="end")


def test_plan_has_ordered_phases_including_security():
    plan = generate_plan("Refactor authentication system", _providers(),
                         enrich=False)
    phases = [s.phase for s in plan.steps]
    assert phases == ["analyze", "design", "implement", "test",
                      "security", "review"]
    # every step has an assigned provider + a detail
    assert all(s.provider_id and s.detail for s in plan.steps)


def test_release_phase_only_with_remote():
    without = generate_plan("t", _providers(), enrich=False, has_remote=False)
    with_r = generate_plan("t", _providers(), enrich=False, has_remote=True)
    assert "release" not in [s.phase for s in without.steps]
    assert "release" in [s.phase for s in with_r.steps]


def test_roles_routed_by_capability():
    plan = generate_plan("Refactor authentication system", _providers(),
                         enrich=False)
    by_phase = {s.phase: s.provider_id for s in plan.steps}
    # implement → developer → Codex; security → red_team → Grok; design →
    # architect → Claude (per the capability registry)
    assert by_phase["implement"] == "codex_cli"
    assert by_phase["security"] == "grok_cli"
    assert by_phase["design"] == "claude_cli"


def test_security_and_review_independent_from_implementer():
    plan = generate_plan("сделай фичу", _providers(), enrich=False)
    impl = next(s.provider_id for s in plan.steps if s.phase == "implement")
    sec = next(s.provider_id for s in plan.steps if s.phase == "security")
    rev = next(s.provider_id for s in plan.steps if s.phase == "review")
    assert sec != impl and rev != impl        # no self-review with 3 providers


def test_enrichment_uses_model_details():
    plan = generate_plan("Refactor authentication system", _providers(),
                         adapter_factory=_EnrichAdapter, enrich=True)
    analyze = next(s for s in plan.steps if s.phase == "analyze")
    assert analyze.detail == "изучить auth-модуль"
    assert plan.enriched_by == "Claude"


def test_enrichment_failure_falls_back_to_template():
    plan = generate_plan("task", _providers(), adapter_factory=_FailAdapter,
                         enrich=True)
    assert plan.enriched_by == ""
    analyze = next(s for s in plan.steps if s.phase == "analyze")
    assert "Проанализировать" in analyze.detail     # template default
    # a plan without enrichment still fully routes
    assert len(plan.steps) == 6


def test_no_providers_is_honest():
    plan = generate_plan("task", [], enrich=False)
    assert plan.steps == []
    assert any("нет активных" in n for n in plan.notes)


def test_single_provider_notes_limited_independence():
    plan = generate_plan("task", _providers()[:1], enrich=False)
    # one provider does everything; markdown still renders
    assert all(s.provider_id == "claude_cli" for s in plan.steps)
    md = plan.to_markdown()
    assert "План выполнения" in md and "безопасность" in md


def test_plan_markdown_and_dict():
    plan = generate_plan("Refactor auth", _providers(), enrich=False)
    md = plan.to_markdown()
    assert "1. **анализ**" in md and "исполнитель:" in md
    d = plan.to_dict()
    assert len(d["steps"]) == 6 and d["task"] == "Refactor auth"


# ---- the `dispatcher plan` command -----------------------------------------

@pytest.fixture
def isolated(tmp_config, monkeypatch):
    import app.cliagents as ca
    monkeypatch.setattr(ca, "quick_ready", lambda flavor, home=None: True)
    monkeypatch.setattr("app.memory_graph._default_path",
                        lambda: tmp_config.CONFIG_DIR / "mem.db")
    return tmp_config


def test_cmd_plan_json_and_memory(isolated, monkeypatch, capsys, tmp_path):
    import app.cli as cli
    import app.planner as planner
    # avoid real CLI calls during enrichment
    monkeypatch.setattr(planner, "make_adapter", lambda cfg: _FailAdapter(cfg))
    code = cli.main(["plan", "Refactor authentication system",
                     "--project", str(tmp_path), "--json"])
    out = capsys.readouterr().out
    assert code == 0
    payload = json.loads(out[out.index("{"):])
    phases = [s["phase"] for s in payload["steps"]]
    assert "security" in phases and "implement" in phases
    # plan recorded in memory under the external-project id (resolved path)
    from pathlib import Path

    from app.memory_graph import MemoryGraph
    project_id = f"external:{Path(str(tmp_path)).resolve()}"
    mem = MemoryGraph(isolated.CONFIG_DIR / "mem.db")
    assert mem.recent(project_id, kinds=("task",))
    mem.close()


def test_cmd_plan_no_enrich_text(isolated, capsys, tmp_path):
    import app.cli as cli
    code = cli.main(["plan", "task", "--project", str(tmp_path), "--no-enrich"])
    out = capsys.readouterr().out
    assert code == 0
    assert "План выполнения" in out and "безопасность" in out
