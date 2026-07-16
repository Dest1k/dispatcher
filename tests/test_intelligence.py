"""Capability registry, explainable routing, reputation."""
from app import capabilities
from app.reputation import ReputationStore
from app.routing import classify_task, route


def _providers():
    return [
        {"id": "claude_cli", "label": "Claude Code CLI · Opus 4.8",
         "short": "Claude", "model": "claude-opus-4-8"},
        {"id": "codex_cli", "label": "Codex CLI · GPT-5.6-Sol",
         "short": "Codex", "model": "gpt-5.6-sol"},
        {"id": "grok_cli", "label": "Grok CLI · Grok 4.5",
         "short": "Grok", "model": "grok-4.5"},
    ]


# ---- capabilities -----------------------------------------------------------

def test_profile_prefix_matching():
    assert capabilities.profile_for("claude-opus-4-8").family == "claude-opus"
    assert capabilities.profile_for("claude-sonnet-x").family == "claude"
    assert capabilities.profile_for("gpt-5.6-sol").family == "gpt-5.6-sol"
    assert capabilities.profile_for("grok-4.5").family == "grok"
    assert capabilities.profile_for("mystery-9000") is None


def test_unknown_model_is_neutral_and_honest():
    s, src = capabilities.score("mystery-9000", "coding")
    assert s == 0.5
    assert "нет данных" in src
    assert "нейтральный" in capabilities.describe("mystery-9000")


def test_role_fit_matches_product_vision():
    arch_claude, _ = capabilities.role_score("claude-opus-4-8", "architect")
    arch_codex, _ = capabilities.role_score("gpt-5.6-sol", "architect")
    dev_claude, _ = capabilities.role_score("claude-opus-4-8", "developer")
    dev_codex, _ = capabilities.role_score("gpt-5.6-sol", "developer")
    red_grok, _ = capabilities.role_score("grok-4.5", "red_team")
    red_codex, _ = capabilities.role_score("gpt-5.6-sol", "red_team")
    assert arch_claude > arch_codex        # Claude = architect
    assert dev_codex > dev_claude          # Codex = developer
    assert red_grok > red_codex            # Grok = red team


# ---- task classification ------------------------------------------------------

def test_classify_task_bilingual():
    kinds, emphasis = classify_task("Refactor authentication system")
    assert "рефакторинг" in kinds and "безопасность" in kinds
    assert emphasis.get("security_review", 0) > 0
    kinds_ru, _ = classify_task("исправь баг в тестах")
    assert "исправление бага" in kinds_ru and "тестирование" in kinds_ru
    kinds_none, emphasis_none = classify_task("сделай что-нибудь хорошее")
    assert kinds_none == [] and emphasis_none == {}


# ---- routing --------------------------------------------------------------------

def test_route_assigns_expected_roles_distinctly():
    decision = route("Refactor authentication system", _providers())
    by_role = {a.role: a.provider_id for a in decision.assignments}
    assert by_role["architect"] == "claude_cli"
    assert by_role["developer"] == "codex_cli"
    assert by_role["reviewer"] == "grok_cli"     # distinctness keeps roles independent
    assert len({a.provider_id for a in decision.assignments}) == 3
    for a in decision.assignments:
        assert "способности" in a.explanation


def test_route_notes_when_best_is_busy():
    # Two providers, three roles: the third role must reuse one, with a note.
    decision = route("сделай фичу", _providers()[:2])
    assert len(decision.assignments) == 3
    reused = [n for n in decision.notes if "занят" in n or "повторно" in n]
    assert reused


def test_route_single_provider_takes_all():
    decision = route("задача", _providers()[:1])
    assert {a.provider_id for a in decision.assignments} == {"claude_cli"}
    assert any("один провайдер" in n for n in decision.notes)


def test_route_uses_reputation(tmp_path):
    rep = ReputationStore(tmp_path / "rep.json")
    for _ in range(8):
        rep.record_task("codex_cli", False)
        rep.record_verification("codex_cli", False)
    decision = route("почини баг", _providers(), reputation=rep)
    dev = decision.by_role("developer")
    # codex is the capability favorite for developer, but its trashed
    # reputation must show up in the explanation (and lower the score).
    assert "репутация" in dev.explanation or dev.provider_id != "codex_cli"


def test_routing_decision_serializable_and_describable():
    decision = route("Refactor authentication system", _providers())
    d = decision.to_dict()
    assert d["assignments"] and d["kinds"]
    text = decision.describe()
    assert "Маршрутизация" in text and "архитектор" in text


# ---- reputation -------------------------------------------------------------------

def test_reputation_neutral_without_history(tmp_path):
    rep = ReputationStore(tmp_path / "rep.json")
    assert rep.multiplier("anyone") == 1.0
    assert "истории нет" in rep.explain("anyone")


def test_reputation_rises_and_falls_bounded(tmp_path):
    rep = ReputationStore(tmp_path / "rep.json")
    for _ in range(10):
        rep.record_task("good", True)
        rep.record_verification("good", True)
        rep.record_review("good", True)
        rep.record_task("bad", False)
        rep.record_verification("bad", False)
        rep.record_review("bad", False)
    rep.record_rollback("bad")
    assert 1.0 < rep.multiplier("good") <= 1.15
    assert 0.85 <= rep.multiplier("bad") < 1.0


def test_reputation_persists(tmp_path):
    path = tmp_path / "rep.json"
    ReputationStore(path).record_task("x", True)
    again = ReputationStore(path)
    assert again.snapshot()["x"]["tasks_ok"] == 1
    assert "задачи 1✓" in again.explain("x")
