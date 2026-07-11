"""Retry/backoff for transient provider failures.

Retries connection errors and transient HTTP statuses (408/409/429/5xx/529) with
exponential backoff + jitter, honoring `Retry-After`. Permanent errors (4xx
other than 408/409/429) are returned immediately for the caller to raise.

The `requests` module is passed in by the caller so unit tests that monkeypatch
an adapter module's `requests.post` still intercept the call.
"""
from __future__ import annotations

import random
import time

TRANSIENT_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def _backoff(attempt: int, base: float, cap: float, retry_after: float | None) -> float:
    if retry_after is not None:
        return min(retry_after, cap)
    return min(cap, base * (2 ** attempt)) + random.uniform(0, base)


def retrying_post(requests, url: str, payload: dict, headers: dict, timeout: int,
                  max_retries: int = 3, base: float = 0.5, cap: float = 20.0,
                  sleep=None):
    """POST with retry. Returns the final Response (which may be non-2xx on the
    last attempt); raises the last connection error if all attempts fail."""
    sleep = sleep or time.sleep
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        except Exception as exc:  # connection/timeout errors are transient
            last_exc = exc
            if attempt >= max_retries:
                raise
            sleep(_backoff(attempt, base, cap, None))
            continue
        if resp.status_code in TRANSIENT_STATUS and attempt < max_retries:
            ra = _parse_retry_after(resp.headers.get("retry-after")
                                    if hasattr(resp, "headers") else None)
            sleep(_backoff(attempt, base, cap, ra))
            continue
        return resp
    if last_exc:
        raise last_exc
    return resp  # pragma: no cover
