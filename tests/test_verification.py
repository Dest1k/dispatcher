import os

import pytest

from app.verification import detect_commands, run_verification

POSIX = os.name != "nt"


def test_detect_python(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tests").mkdir()
    cmds = detect_commands(str(tmp_path))
    names = [c["name"] for c in cmds]
    assert "pytest" in names


def test_detect_node(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"test": "jest", "build": "tsc"}}')
    cmds = detect_commands(str(tmp_path))
    names = [c["name"] for c in cmds]
    assert "npm test" in names and "npm build" in names


def test_no_commands_is_unknown_and_blocks(tmp_path):
    res = run_verification(str(tmp_path), [])
    assert res.status == "unknown"
    assert res.blocks_publication()


@pytest.mark.skipif(not POSIX, reason="uses POSIX true/false")
def test_all_pass(tmp_path):
    res = run_verification(str(tmp_path), [
        {"name": "a", "command": "true"},
        {"name": "b", "command": "echo ok"},
    ])
    assert res.status == "pass"
    assert res.risk == "low"
    assert not res.blocks_publication()


@pytest.mark.skipif(not POSIX, reason="uses POSIX true/false")
def test_a_failure_blocks(tmp_path):
    res = run_verification(str(tmp_path), [
        {"name": "ok", "command": "true"},
        {"name": "bad", "command": "false"},
    ])
    assert res.status == "fail"
    assert res.blocks_publication()
    assert any(c.status == "fail" for c in res.checks)


@pytest.mark.skipif(not POSIX, reason="uses POSIX true/false")
def test_result_serializes(tmp_path):
    res = run_verification(str(tmp_path), [{"name": "a", "command": "true"}])
    d = res.to_dict()
    assert d["status"] == "pass" and d["checks"][0]["name"] == "a"
