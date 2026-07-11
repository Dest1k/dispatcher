"""Secret storage backed by the OS secret store (keyring) with a documented,
explicitly-insecure fallback.

Secrets (API keys, GitHub tokens) are never written to the plaintext config.
Instead the config stores a *reference*; the real value lives in the OS
credential store (Windows Credential Manager / macOS Keychain / Linux Secret
Service via `keyring`). If `keyring` or a working backend is unavailable, a
fallback file under the config dir is used and `is_secure()` returns False so
the UI/docs can warn.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

SERVICE = "dispatcher"


def _config_dir() -> Path:
    # imported lazily to avoid a circular import with app.config
    from ..config import CONFIG_DIR
    return CONFIG_DIR


class SecretStore:
    def __init__(self) -> None:
        self._keyring = None
        self._secure = False
        try:
            import keyring  # type: ignore
            # Reject the "fail" / "null" backends that raise or silently drop.
            # The fail backend's class is `keyring.backends.fail.Keyring`, so the
            # class name alone ("Keyring") is not enough — inspect the module too.
            backend = keyring.get_keyring()
            cls = backend.__class__
            qualified = f"{cls.__module__}.{cls.__name__}".lower()
            if not any(bad in qualified for bad in ("fail", "null")):
                self._keyring = keyring
                self._secure = True
        except Exception:
            self._keyring = None
        self._fallback_override: Path | None = None

    def _downgrade_to_file(self) -> None:
        """A keyring backend that passed the probe but fails at runtime forces a
        graceful downgrade to the insecure file store (criterion §21 fallback)."""
        self._keyring = None
        self._secure = False

    def _fb_path(self) -> Path:
        # Resolved lazily so a test that redirects CONFIG_DIR is honored.
        return self._fallback_override or (_config_dir() / "secrets.json")

    def is_secure(self) -> bool:
        return self._secure

    def backend_name(self) -> str:
        if self._secure and self._keyring is not None:
            try:
                return self._keyring.get_keyring().__class__.__name__
            except Exception:
                return "keyring"
        return "insecure-file-fallback"

    # ---- fallback file ----------------------------------------------
    def _load_fallback(self) -> dict:
        if self._fb_path().exists():
            try:
                return json.loads(self._fb_path().read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {}
        return {}

    def _save_fallback(self, data: dict) -> None:
        self._fb_path().parent.mkdir(parents=True, exist_ok=True)
        self._fb_path().write_text(json.dumps(data), encoding="utf-8")
        try:
            os.chmod(self._fb_path(), 0o600)
        except OSError:
            pass

    # ---- public API -------------------------------------------------
    def set(self, account: str, value: str) -> str:
        """Store a secret and return a reference string."""
        if self._secure and self._keyring is not None:
            try:
                self._keyring.set_password(SERVICE, account, value)
                return f"keyring:{account}"
            except Exception:
                # Backend passed the probe but fails on write (e.g. no Secret
                # Service running). Degrade to the file store instead of
                # crashing config.save().
                self._downgrade_to_file()
        data = self._load_fallback()
        data[account] = value
        self._save_fallback(data)
        return f"file:{account}"

    def get(self, ref: str | None) -> str | None:
        if not ref:
            return None
        scheme, _, account = ref.partition(":")
        if scheme == "keyring" and self._keyring is not None:
            try:
                return self._keyring.get_password(SERVICE, account)
            except Exception:
                return None
        if scheme == "file":
            return self._load_fallback().get(account)
        return None

    def delete(self, ref: str | None) -> None:
        if not ref:
            return
        scheme, _, account = ref.partition(":")
        if scheme == "keyring" and self._keyring is not None:
            try:
                self._keyring.delete_password(SERVICE, account)
            except Exception:
                pass
        elif scheme == "file":
            data = self._load_fallback()
            data.pop(account, None)
            self._save_fallback(data)


# Process-wide store.
secret_store = SecretStore()
