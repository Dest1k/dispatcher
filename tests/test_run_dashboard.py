"""Offscreen behavior of the run dashboard (pure view fed via public slots)."""
from app.ui.run_dashboard import PHASES, RunDashboard


def _dash(qapp):
    return RunDashboard()


def test_phase_progression(qapp):
    d = _dash(qapp)
    d.set_phase("executing")
    assert d.current_phase == "executing"
    # phases before the current are "reached", current is highlighted
    exec_style = d._phase_labels["executing"].styleSheet()
    plan_style = d._phase_labels["planning"].styleSheet()
    later_style = d._phase_labels["verifying"].styleSheet()
    assert "1f6feb" in exec_style                 # current = blue
    assert "238636" in plan_style                 # reached = green
    assert "21262d" in later_style                # not reached = grey
    d.set_phase("bogus")                          # ignored, no crash
    assert d.current_phase == "executing"


def test_set_plan_renders_overview_and_assignments(qapp):
    d = _dash(qapp)
    d.set_plan({"overview": "## Граф задач (по слоям)\nСлой 1",
                "assignments": [
                    {"title": "Схема", "role": "developer",
                     "objective": "создать модель", "files": ["model.py"]}]})
    text = d.plan_view.toPlainText()
    assert "Граф задач" in text
    assert "Схема" in text and "model.py" in text


def test_set_agent_upserts_rows(qapp):
    d = _dash(qapp)
    d.set_agent("a1", label="Claude", role="архитектор", status="ожидание")
    d.set_agent("a2", label="Codex", role="разработчик", status="работает")
    assert d.agents.rowCount() == 2
    # update the same agent's status in place (no new row)
    d.set_agent("a1", status="готово")
    assert d.agents.rowCount() == 2
    assert d.agents.item(0, 0).text() == "Claude"
    assert d.agents.item(0, 2).text() == "готово"


def test_set_integration_summary(qapp):
    d = _dash(qapp)
    d.set_integration({"changed_files": ["a.py", "b.py"], "conflicts": [],
                       "change_risk": {"level": "medium"}})
    t = d.integration_label.text()
    assert "файлов 2" in t and "конфликтов 0" in t and "средний" in t


def test_set_verification_status(qapp):
    d = _dash(qapp)
    d.set_verification({"status": "pass",
                        "checks": [{"name": "pytest", "status": "pass"},
                                   {"name": "ruff", "status": "pass"}]})
    t = d.verification_label.text()
    assert "pass" in t and "2/2" in t
    assert "3fb950" in d.verification_label.styleSheet()      # green for pass


def test_reset_clears(qapp):
    d = _dash(qapp)
    d.set_agent("a1", label="X")
    d.set_phase("verifying")
    d.reset()
    assert d.agents.rowCount() == 0
    assert d.current_phase == ""
    assert "план ещё не готов" in d.plan_view.toPlainText()


def test_phase_keys_are_ordered():
    keys = [k for k, _ in PHASES]
    assert keys == ["planning", "executing", "integrating", "verifying",
                    "awaiting", "done"]
