import os

import pytest

from app.security import PathPolicy, PathViolation, Redactor, secret_store
from app.security.secrets import SecretStore


# ---- redaction -------------------------------------------------------
def test_redact_common_shapes():
    r = Redactor()
    assert "sk-" not in r.redact("key sk-abcdef0123456789ABCDEF here")
    assert "ghp_" not in r.redact("token ghp_" + "a" * 30)
    assert "xai-" not in r.redact("xai-" + "b" * 20)


def test_redact_registered_secret():
    r = Redactor()
    r.register("supersecretvalue123")
    out = r.redact("the token is supersecretvalue123 ok")
    assert "supersecretvalue123" not in out and "REDACTED" in out


def test_redact_token_in_url_and_headers():
    r = Redactor()
    assert "tok123456" not in r.redact("https://x-access-token:tok123456@github.com/o/r")
    assert "abcDEF12345" not in r.redact("Authorization: Bearer abcDEF12345")


# ---- path policy -----------------------------------------------------
def test_path_escape_blocked(tmp_path):
    pol = PathPolicy(str(tmp_path))
    with pytest.raises(PathViolation):
        pol.resolve_read("../../etc/passwd")


def test_denied_and_readonly(tmp_path):
    pol = PathPolicy(str(tmp_path), denied=[".env", "secrets/*"],
                     read_only=["pyproject.toml"])
    (tmp_path / "pyproject.toml").write_text("x")
    with pytest.raises(PathViolation):
        pol.resolve_read(".env")
    with pytest.raises(PathViolation):
        pol.resolve_write("pyproject.toml")
    # read of a read-only file is fine
    assert pol.resolve_read("pyproject.toml").name == "pyproject.toml"


def test_allowed_scope(tmp_path):
    pol = PathPolicy(str(tmp_path), allowed=["app/**", "tests/**"])
    assert pol.can_write("app/x.py")
    assert not pol.can_write("other/x.py")


@pytest.mark.skipif(os.name == "nt", reason="symlink perms differ on Windows CI")
def test_symlink_escape_blocked(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    root = tmp_path / "root"
    root.mkdir()
    link = root / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks not permitted")
    pol = PathPolicy(str(root))
    with pytest.raises(PathViolation):
        pol.resolve_read("link.txt")


# ---- secret store (forced fallback) ---------------------------------
def _fallback_store(tmp_path):
    store = SecretStore()
    store._secure = False
    store._keyring = None
    store._fallback_override = tmp_path / "secrets.json"
    return store


def test_secret_store_roundtrip(tmp_path):
    store = _fallback_store(tmp_path)
    ref = store.set("provider:test:api_key", "topsecret")
    assert ref.startswith("file:")
    assert store.get(ref) == "topsecret"
    store.delete(ref)
    assert store.get(ref) is None


def test_secret_store_is_secure_flag():
    # The real store reports whether an OS keychain backend is active.
    assert isinstance(secret_store.is_secure(), bool)
