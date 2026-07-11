from datetime import date

from app.catalog import describe, get


def test_known_model_has_provenance():
    entry = get("claude-opus-4-8")
    assert entry is not None
    assert entry.context_window == 1_000_000
    assert "Anthropic" in entry.source
    assert "проверено" in describe("claude-opus-4-8")


def test_unknown_model_reports_no_data_not_guess():
    assert get("gpt-5.6") is None
    assert "нет данных" in describe("gpt-5.6")


def test_staleness_flag():
    entry = get("claude-opus-4-8")
    # far-future "today" makes the entry stale
    assert entry.is_stale(date(2030, 1, 1))
    assert "устарели" in describe("claude-opus-4-8", today=date(2030, 1, 1))
    # near the verification date it is fresh
    assert not entry.is_stale(date(2026, 7, 1))
