"""The `dispatcher` console entry: doctor / route / memory / capabilities."""
import json

import pytest

import app.cli as cli
import app.cliagents as ca
from app.cliagents import CLIStatus


def _statuses(ready=True):
    out = []
    for flavor, model in (("claude", "claude-opus-4-8"),
                          ("codex", "gpt-5.6-sol"), ("grok", "grok-4.5")):
        st = CLIStatus(flavor=flavor, title=f"{flavor} CLI")
        st.binary = f"/bin/{flavor}" if ready else ""
        st.installed = ready
        st.version = "1.0" if ready else ""
        st.authenticated = True if ready else None
        st.auth_source = "сессия найдена" if ready else "не найдено"
        st.models = [model]
        st.efforts = {model: ["low", "high"]}
        st.default_model = model
        st.default_effort = "high"
        st.models_source = "тестовый реестр"
        out.append(st)
    return out


@pytest.fixture
def isolated_config(tmp_config, monkeypatch):
    """Config + reputation + memory all under tmp; CLI detection faked ready."""
    monkeypatch.setattr(ca, "quick_ready", lambda flavor, home=None: True)
    return tmp_config


def test_doctor_json_ready(isolated_config, monkeypatch, capsys):
    monkeypatch.setattr(ca, "detect_all",
                        lambda home=None, run_commands=True: _statuses(True))
    code = cli.main(["doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ready"] == 3
    entry = payload["providers"][0]
    assert entry["flavor"] == "claude"
    assert entry["configured_model"] == "claude-opus-4-8"
    assert entry["configured_effort"] == "max"       # user-specified default


def test_doctor_text_not_ready_exit_1(isolated_config, monkeypatch, capsys):
    monkeypatch.setattr(ca, "detect_all",
                        lambda home=None, run_commands=True: _statuses(False))
    code = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert code == 1
    assert "Диагностика" in out
    assert "НЕ ГОТОВ" in out
    assert "Ни один официальный CLI не готов" in out


def test_doctor_text_ready(isolated_config, monkeypatch, capsys):
    monkeypatch.setattr(ca, "detect_all",
                        lambda home=None, run_commands=True: _statuses(True))
    code = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert code == 0
    assert "ГОТОВ" in out and "Готово к работе: 3 из 3" in out
    assert "доступные модели" in out


def test_route_json(isolated_config, capsys):
    code = cli.main(["route", "Refactor authentication system", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    roles = {a["role"]: a["provider"] for a in payload["assignments"]}
    assert roles["architect"] == "claude_cli"
    assert roles["developer"] == "codex_cli"
    assert all(a["explanation"] for a in payload["assignments"])


def test_route_no_providers(isolated_config, monkeypatch, capsys):
    monkeypatch.setattr(ca, "quick_ready", lambda flavor, home=None: False)
    code = cli.main(["route", "задача"])
    assert code == 1
    assert "Нет активных провайдеров" in capsys.readouterr().out


def test_capabilities_text(isolated_config, capsys):
    code = cli.main(["capabilities"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Реестр способностей" in out
    assert "claude-opus-4-8" in out


def test_reputation_empty_then_filled(isolated_config, capsys):
    assert cli.main(["reputation"]) == 0
    assert "Истории пока нет" in capsys.readouterr().out
    from app.reputation import ReputationStore
    ReputationStore().record_task("codex_cli", True)
    assert cli.main(["reputation"]) == 0
    assert "задачи 1✓" in capsys.readouterr().out


def test_memory_add_list_show_pack(isolated_config, capsys, tmp_path):
    proj = str(tmp_path / "proj")
    assert cli.main(["memory", "add", "lesson", "Не пушить в пятницу",
                     "--body", "боевое наблюдение", "--project", proj]) == 0
    capsys.readouterr()

    assert cli.main(["memory", "list", "--project", proj]) == 0
    out = capsys.readouterr().out
    assert "Не пушить в пятницу" in out and "[lesson/active]" in out
    node_id = out.split()[0]

    assert cli.main(["memory", "show", node_id, "--project", proj]) == 0
    assert "боевое наблюдение" in capsys.readouterr().out

    assert cli.main(["memory", "pack", "--project", proj]) == 0
    assert "Память проекта" in capsys.readouterr().out


def test_memory_rejects_bad_kind(isolated_config, capsys, tmp_path):
    code = cli.main(["memory", "add", "vibe", "x",
                     "--project", str(tmp_path)])
    assert code == 2
    assert "Тип должен быть" in capsys.readouterr().out
