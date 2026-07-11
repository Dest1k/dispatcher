"""Shared pytest fixtures. No test in this suite performs real network calls."""
import os
import shutil
import subprocess

import pytest

# Qt must run headless in CI / containers.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def tmp_config(monkeypatch, tmp_path):
    """Redirect the config file to a temp location so tests never touch $HOME."""
    import app.config as cfgmod
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", tmp_path / "cfg" / "config.json")
    return cfgmod


@pytest.fixture(scope="session")
def qapp():
    """A single offscreen QApplication for UI tests."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


@pytest.fixture
def git_repo(tmp_path):
    """An initialized git repo with one commit, returns its path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    _git("config", "user.email", "t@t", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "README.md").write_text("# demo\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", "init", cwd=repo)
    yield repo
    shutil.rmtree(repo, ignore_errors=True)


@pytest.fixture
def has_git():
    if shutil.which("git") is None:
        pytest.skip("git not available")
