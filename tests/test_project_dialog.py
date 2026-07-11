"""Verify-commands editor: parsing and dialog round-trip (offscreen)."""
from app.ui.project_dialog import format_verify_commands, parse_verify_commands


def test_parse_plain_lines():
    cmds = parse_verify_commands("python -m pytest -q\nruff check .")
    assert cmds == [
        {"name": "python -m pytest -q", "command": "python -m pytest -q"},
        {"name": "ruff check .", "command": "ruff check ."},
    ]


def test_parse_labelled_and_ignores_blanks_and_comments():
    text = "\n тесты :: python -m pytest -q \n\n# comment\nlint :: ruff check .\n"
    cmds = parse_verify_commands(text)
    assert cmds == [
        {"name": "тесты", "command": "python -m pytest -q"},
        {"name": "lint", "command": "ruff check ."},
    ]


def test_round_trip_format_parse():
    original = [
        {"name": "тесты", "command": "python -m pytest -q"},
        {"name": "go test ./...", "command": "go test ./..."},
    ]
    assert parse_verify_commands(format_verify_commands(original)) == original


def test_empty_means_autodetect():
    assert parse_verify_commands("   \n\n# only comments\n") == []


def test_dialog_persists_verify_commands(qapp, tmp_config):
    from app.config import Config, _default_config
    from app.ui.project_dialog import ProjectDialog

    cfg = Config(_default_config())
    dlg = ProjectDialog(cfg)
    dlg.name.setText("Demo")
    dlg.local_path.setText("/tmp/demo-verify")
    dlg.verify.setPlainText("тесты :: python -m pytest -q\nruff check .")
    dlg._save()
    project = cfg.get_project(dlg.saved_id)
    assert project["verify_commands"] == [
        {"name": "тесты", "command": "python -m pytest -q"},
        {"name": "ruff check .", "command": "ruff check ."},
    ]
    dlg.close()
