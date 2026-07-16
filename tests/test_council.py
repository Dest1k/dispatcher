"""Council modes over fake adapters: casting, pipelines, honesty, cancel."""
import threading

import pytest

from app.council import Council, run_council
from app.providers.base import CompletionResult, Usage


class FakeAdapter:
    calls: list[dict] = []            # class-level transcript across instances
    fail_ids: set[str] = set()

    def __init__(self, cfg):
        self.cfg = cfg

    def complete(self, system, messages, tools):
        record = {"id": self.cfg["id"], "system": system,
                  "prompt": messages[-1].text}
        FakeAdapter.calls.append(record)
        if self.cfg["id"] in FakeAdapter.fail_ids:
            raise RuntimeError("CLI недоступен (эмуляция)")
        return CompletionResult(
            text=f"мнение {self.cfg['id']}", thinking="", tool_calls=[],
            usage=Usage(10, 5), raw_assistant=None, stop_reason="end")


@pytest.fixture(autouse=True)
def _reset_fake():
    FakeAdapter.calls = []
    FakeAdapter.fail_ids = set()


def _providers():
    return [
        {"id": "claude_cli", "label": "Claude", "short": "Claude",
         "model": "claude-opus-4-8", "default_role": "architect"},
        {"id": "codex_cli", "label": "Codex", "short": "Codex",
         "model": "gpt-5.6-sol", "default_role": "developer"},
        {"id": "grok_cli", "label": "Grok", "short": "Grok",
         "model": "grok-4.5", "default_role": "red_team"},
    ]


def _council(providers=None, **kw):
    providers = providers or _providers()
    return Council(providers, lead=providers[0],
                   adapter_factory=FakeAdapter, **kw)


def test_solo_single_call():
    result = _council().run("вопрос", mode="solo")
    assert len(result.opinions) == 1
    assert result.opinions[0].text.startswith("мнение")
    assert result.synthesis == ""             # ничего синтезировать
    assert len(FakeAdapter.calls) == 1


def test_pair_proposal_then_critique():
    result = _council().run("спроектируй кэш", mode="pair")
    assert [o.role for o in result.opinions] == ["architect", "reviewer"]
    # the critic sees the proposal text
    critique_call = FakeAdapter.calls[1]
    assert "мнение" in critique_call["prompt"]
    assert "Предложение" in critique_call["prompt"]
    assert len(FakeAdapter.calls) == 2


def test_council_parallel_plus_synthesis():
    result = _council().run("оцени архитектуру", mode="council")
    assert len(result.opinions) == 3
    assert result.synthesis == "мнение claude_cli"      # lead synthesizes
    assert result.synthesis_by == "Claude"
    # 3 opinions + 1 synthesis
    assert len(FakeAdapter.calls) == 4
    tin, tout = result.usage_total()
    assert (tin, tout) == (40, 20)


def test_full_council_pipeline_chains_stages():
    result = _council().run("Refactor authentication system",
                            mode="full_council")
    roles = [o.role for o in result.opinions]
    assert roles == ["architect", "red_team", "developer"]
    # роли розданы по способностям: Claude строит, Grok атакует, Codex оценивает
    assert [o.provider_id for o in result.opinions] == [
        "claude_cli", "grok_cli", "codex_cli"]
    red_prompt = FakeAdapter.calls[1]["prompt"]
    dev_prompt = FakeAdapter.calls[2]["prompt"]
    assert "Предложение архитектора" in red_prompt
    assert "Критика red team" in dev_prompt
    assert result.synthesis
    assert len(result.decision_path) >= 4


def test_failed_participant_reported_honestly():
    FakeAdapter.fail_ids = {"grok_cli"}
    result = _council().run("вопрос", mode="council")
    failed = [o for o in result.opinions if o.error]
    assert len(failed) == 1 and failed[0].provider_id == "grok_cli"
    md = result.to_markdown()
    assert "не ответил" in md
    assert "эмуляция" in md
    # synthesis still happens over the remaining opinions
    assert result.synthesis


def test_all_failed_synthesis_impossible():
    FakeAdapter.fail_ids = {"claude_cli", "codex_cli", "grok_cli"}
    result = _council().run("вопрос", mode="council")
    assert "синтез невозможен" in result.synthesis


def test_cancel_stops_pipeline():
    cancel = threading.Event()
    cancel.set()
    result = _council(cancel=cancel).run("вопрос", mode="full_council")
    assert result.cancelled
    assert all(o.error for o in result.opinions)


def test_unknown_mode_rejected():
    with pytest.raises(ValueError, match="режим"):
        _council().run("вопрос", mode="quartet")


def test_run_council_helper_and_markdown_sections():
    result = run_council("вопрос", _providers(), mode="council",
                         adapter_factory=FakeAdapter)
    md = result.to_markdown()
    assert "# Совет ИИ" in md
    assert "## Синтез" in md
    assert "## Ход совета" in md
    assert "Маршрутизация" in result.routing_notes


def test_context_included_in_prompts():
    _council(context="память: раньше решили X").run("вопрос", mode="solo")
    assert "память: раньше решили X" in FakeAdapter.calls[0]["prompt"]
