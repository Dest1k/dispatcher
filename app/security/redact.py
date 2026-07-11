"""Credential redaction for logs, reports, UI events, and error messages.

Two layers:
  * pattern-based masking of common key/token shapes (sk-..., ghp_..., xai-...);
  * an explicit registry of live secret values (API keys, tokens) so their exact
    value is masked everywhere even if the shape is unusual.

Redaction is best-effort defense-in-depth, not a substitute for never putting
secrets on a command line in the first place.
"""
from __future__ import annotations

import re
import threading

_MASK = "***REDACTED***"

# Common credential shapes. Kept deliberately broad.
_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"),           # OpenAI / Anthropic style
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"\bxai-[A-Za-z0-9_\-]{16,}\b"),          # xAI
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),       # GitHub tokens
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                 # AWS access key id
    re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}\b"),          # Google API key
    # Authorization: Bearer <token> / x-api-key headers, and token-in-URL.
    re.compile(r"(?i)(authorization:\s*bearer\s+)[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)(x-api-key:\s*)[A-Za-z0-9._\-]+"),
    re.compile(r"(x-access-token:)[^@\s/]+"),
]


class Redactor:
    """Holds explicitly-registered secret values to mask by exact match."""

    def __init__(self) -> None:
        self._secrets: set[str] = set()
        self._lock = threading.Lock()

    def register(self, value: str | None) -> None:
        if value and len(value) >= 6:
            with self._lock:
                self._secrets.add(value)

    def clear(self) -> None:
        with self._lock:
            self._secrets.clear()

    def redact(self, text: str) -> str:
        if not text:
            return text
        with self._lock:
            secrets = sorted(self._secrets, key=len, reverse=True)
        for secret in secrets:
            if secret in text:
                text = text.replace(secret, _MASK)
        for pattern in _PATTERNS:
            if pattern.groups:
                text = pattern.sub(lambda m: m.group(1) + _MASK, text)
            else:
                text = pattern.sub(_MASK, text)
        return text


# Process-wide default redactor.
_default = Redactor()


def register_secret(value: str | None) -> None:
    _default.register(value)


def redact(text: str) -> str:
    return _default.redact(text)
