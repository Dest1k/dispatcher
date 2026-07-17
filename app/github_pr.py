"""Create a draft pull request via the GitHub REST API.

After an approved run has pushed the integration branch, Dispatcher can open a
**draft** PR from that branch into the project's target branch. This is an
outward-facing action, so it is opt-in (`orchestration.auto_draft_pr`) and only
runs when the user actually pushed and supplied a GitHub token + repo. The
function never raises and never merges anything — the human still reviews and
un-drafts the PR. Falls back cleanly to the existing one-click compare URL.

The `requests` module is injected so tests intercept the call without network.
"""
from __future__ import annotations

from .security import redact

_API = "https://api.github.com"


def create_draft_pr(repo: str, head: str, base: str, title: str, body: str,
                    token: str, requests_mod=None, timeout: int = 30) -> dict:
    """Open a draft PR `head` → `base` in `repo` (owner/name).

    Returns a dict: {created: bool, url: str, number: int|None, message: str}.
    Never raises. Missing inputs or any API error → created=False with a
    redacted, human-readable message (the compare URL remains available).
    """
    if not (repo and "/" in repo and head and base and token):
        return {"created": False, "url": "", "number": None,
                "message": "нет токена, репозитория или веток — PR не создаём"}
    if requests_mod is None:
        import requests as requests_mod
    url = f"{_API}/repos/{repo}/pulls"
    payload = {"title": (title or "Работа консилиума")[:250],
               "head": head, "base": base,
               "body": (body or "")[:60000], "draft": True}
    headers = {"Authorization": f"Bearer {token}",
               "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    try:
        resp = requests_mod.post(url, json=payload, headers=headers, timeout=timeout)
    except Exception as exc:  # network / DNS / TLS — never propagate
        return {"created": False, "url": "", "number": None,
                "message": redact(f"сеть: {exc}")[:300]}
    status = getattr(resp, "status_code", 0)
    if status == 201:
        try:
            data = resp.json()
        except Exception:
            data = {}
        return {"created": True, "url": data.get("html_url", ""),
                "number": data.get("number"), "message": "черновой PR создан"}
    # Common: 422 when a PR for this branch already exists, or 403 perms.
    detail = ""
    try:
        payload_err = resp.json()
        detail = payload_err.get("message", "")
        errors = payload_err.get("errors") or []
        if errors and isinstance(errors[0], dict):
            detail += " · " + str(errors[0].get("message", ""))[:120]
    except Exception:
        try:
            detail = (resp.text or "")[:200]
        except Exception:
            detail = ""
    return {"created": False, "url": "", "number": None,
            "message": redact(f"GitHub {status}: {detail}".strip())[:300]}
