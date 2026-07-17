"""Official CLI coding agents: discovery, auth status, models, readiness.

Dispatcher's `cli_session` transport drives the *official, already installed
and already logged-in* CLI clients (Claude Code, OpenAI Codex, Grok Build) as
subprocesses — exactly the way the user runs them manually. This module answers
"what is actually available on this machine?" without inventing anything:

  * installation — the binary resolved on PATH (plus known install dirs);
  * version      — the CLI's own `--version` output;
  * auth         — evidence from the CLI's official session files (and for
                   codex its offline `login status` command); never cookies,
                   never scraped tokens, never copied credentials;
  * models       — read from the CLI's *local* model caches where they exist
                   (`~/.codex/models_cache.json`, `~/.grok/models_cache.json`),
                   otherwise a small static registry with provenance.

No function here performs a network call. A real end-to-end invocation is a
separate, explicitly requested action (`dispatcher doctor --probe`).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

FLAVORS = ("claude", "codex", "grok")

# Static knowledge with provenance — used only where a CLI has no local model
# cache to read. Kept deliberately small and honest.
_KNOWN = {
    "claude": {
        "title": "Claude Code CLI (Anthropic)",
        "models": ["claude-opus-4-8", "opus", "sonnet", "haiku"],
        "efforts": ["low", "medium", "high", "xhigh", "max", "ultracode"],
        "default_model": "claude-opus-4-8",
        "default_effort": "ultracode",
        "verified": "2026-07-18 · claude 2.1.211 (--effort ultracode = xhigh + workflow-оркестрация)",
    },
    "codex": {
        "title": "Codex CLI (OpenAI)",
        "models": ["gpt-5.6-sol"],
        "efforts": ["low", "medium", "high", "xhigh", "max", "ultra"],
        "default_model": "gpt-5.6-sol",
        "default_effort": "ultra",
        "verified": "2026-07-17 · codex-cli 0.144.5 models_cache",
    },
    "grok": {
        "title": "Grok CLI (xAI)",
        "models": ["grok-4.5"],
        "efforts": ["low", "medium", "high"],
        "default_model": "grok-4.5",
        "default_effort": "high",
        "verified": "2026-07-17 · grok 0.2.101 models_cache",
    },
}


@dataclass
class CLIStatus:
    flavor: str
    title: str = ""
    binary: str = ""                 # resolved absolute path, "" if not found
    installed: bool = False
    version: str = ""
    authenticated: bool | None = None   # None = could not determine
    auth_source: str = ""               # human-readable evidence
    models: list[str] = field(default_factory=list)
    efforts: dict[str, list[str]] = field(default_factory=dict)  # model -> efforts
    default_model: str = ""
    default_effort: str = ""
    models_source: str = ""
    detail: str = ""

    @property
    def ready(self) -> bool:
        return self.installed and self.authenticated is True

    def effort_options(self, model: str = "") -> list[str]:
        return (self.efforts.get(model or self.default_model)
                or self.efforts.get("*")
                or _KNOWN.get(self.flavor, {}).get("efforts", []))

    def to_dict(self) -> dict:
        return {
            "flavor": self.flavor, "title": self.title, "binary": self.binary,
            "installed": self.installed, "version": self.version,
            "authenticated": self.authenticated, "auth_source": self.auth_source,
            "models": self.models, "efforts": self.efforts,
            "default_model": self.default_model,
            "default_effort": self.default_effort,
            "models_source": self.models_source,
            "ready": self.ready, "detail": self.detail,
        }


# --- low-level helpers (monkeypatchable in tests) ---------------------------

def _which(name: str) -> str | None:
    return shutil.which(name)


def _home() -> Path:
    return Path.home()


def _run_version(binary: str, timeout: int = 30) -> str:
    """Ask a CLI for its version. UTF-8 explicitly: on Windows the locale
    codec would garble output."""
    try:
        proc = subprocess.run([binary, "--version"], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    out = (proc.stdout or proc.stderr or "").strip()
    return out.splitlines()[0].strip() if out else ""


def _run_codex_login_status(binary: str, timeout: int = 30) -> tuple[bool | None, str]:
    """`codex login status` is offline and definitive."""
    try:
        proc = subprocess.run([binary, "login", "status"], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None, ""
    text = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode == 0 and text:
        logged = "not logged" not in text.lower()
        return logged, text.splitlines()[0].strip()
    return (False, text.splitlines()[0].strip() if text else "")


# --- binary resolution -------------------------------------------------------

def find_binary(flavor: str, home: Path | None = None) -> str:
    """Resolve the CLI binary: PATH first, then known install locations."""
    hit = _which(flavor)
    if hit:
        return str(Path(hit))
    home = home or _home()
    candidates = {
        "claude": [home / ".local" / "bin" / "claude",
                   home / "AppData" / "Roaming" / "npm" / "claude.cmd"],
        "codex": [home / ".local" / "bin" / "codex",
                  home / "AppData" / "Roaming" / "npm" / "codex.cmd"],
        "grok": [home / ".grok" / "bin" / "grok",
                 home / ".grok" / "bin" / "grok.exe"],
    }.get(flavor, [])
    for cand in candidates:
        if cand.exists():
            return str(cand)
    return ""


# --- auth evidence (official session files only) -----------------------------

def auth_evidence(flavor: str, home: Path | None = None) -> tuple[bool | None, str]:
    """Look for the CLI's own session artifacts. Returns (authenticated, source).

    We only *observe* that the official client keeps a live session; we never
    read, copy or reuse the credential contents.
    """
    home = home or _home()
    if flavor == "claude":
        cred = home / ".claude" / ".credentials.json"
        if cred.exists():
            return True, "сессия Claude Code (~/.claude/.credentials.json)"
        cfg = home / ".claude.json"
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            if data.get("oauthAccount"):
                return True, "OAuth-аккаунт в ~/.claude.json"
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
        return None, "файлы сессии Claude Code не найдены"
    if flavor == "codex":
        auth = home / ".codex" / "auth.json"
        if auth.exists():
            return True, "сессия Codex CLI (~/.codex/auth.json)"
        return None, "файл сессии Codex CLI не найден"
    if flavor == "grok":
        auth = home / ".grok" / "auth.json"
        if auth.exists():
            return True, "сессия Grok CLI (~/.grok/auth.json)"
        return None, "файл сессии Grok CLI не найден"
    return None, "неизвестный CLI"


# --- model discovery from the CLIs' local caches ------------------------------

def _codex_models(home: Path) -> tuple[list[str], dict[str, list[str]], str]:
    cache = home / ".codex" / "models_cache.json"
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        models, efforts = [], {}
        for m in data.get("models", []):
            slug = m.get("slug", "")
            if not slug or m.get("visibility") == "hidden":
                continue
            models.append(slug)
            levels = [lv.get("effort", "") for lv in
                      m.get("supported_reasoning_levels", []) if lv.get("effort")]
            if levels:
                efforts[slug] = levels
        if models:
            return models, efforts, f"локальный кэш Codex ({cache.name})"
    except (OSError, json.JSONDecodeError):
        pass
    return [], {}, ""


def _grok_models(home: Path) -> tuple[list[str], dict[str, list[str]], str]:
    cache = home / ".grok" / "models_cache.json"
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        models, efforts = [], {}
        for mid, entry in (data.get("models") or {}).items():
            info = entry.get("info") or {}
            if info.get("hidden"):
                continue
            models.append(mid)
            levels = [e.get("value", "") for e in
                      (info.get("reasoning_efforts") or []) if e.get("value")]
            if levels:
                efforts[mid] = levels
        if models:
            return models, efforts, f"локальный кэш Grok ({cache.name})"
    except (OSError, json.JSONDecodeError):
        pass
    return [], {}, ""


def discover_models(flavor: str, home: Path | None = None) \
        -> tuple[list[str], dict[str, list[str]], str]:
    """(models, efforts-per-model, source). Falls back to the static registry."""
    home = home or _home()
    if flavor == "codex":
        models, efforts, src = _codex_models(home)
        if models:
            return models, efforts, src
    if flavor == "grok":
        models, efforts, src = _grok_models(home)
        if models:
            return models, efforts, src
    known = _KNOWN.get(flavor, {})
    return (list(known.get("models", [])),
            {"*": list(known.get("efforts", []))},
            f"встроенный реестр (проверено {known.get('verified', '—')})")


# --- full detection -----------------------------------------------------------

def detect(flavor: str, home: Path | None = None,
           run_commands: bool = True) -> CLIStatus:
    """Full status for one CLI. `run_commands=False` skips subprocess calls
    (version / codex login status) for cheap contexts and tests."""
    known = _KNOWN.get(flavor, {})
    st = CLIStatus(flavor=flavor, title=known.get("title", flavor))
    st.binary = find_binary(flavor, home)
    st.installed = bool(st.binary)
    if not st.installed:
        st.detail = "CLI не установлен (не найден в PATH и известных каталогах)"
        return st
    if run_commands:
        st.version = _run_version(st.binary)
    st.authenticated, st.auth_source = auth_evidence(flavor, home)
    if flavor == "codex" and run_commands:
        logged, src = _run_codex_login_status(st.binary)
        if logged is not None:
            st.authenticated = logged
            st.auth_source = f"codex login status: {src}" if src else st.auth_source
    st.models, st.efforts, st.models_source = discover_models(flavor, home)
    st.default_model = (known.get("default_model")
                        if known.get("default_model") in st.models or not st.models
                        else st.models[0]) or ""
    st.default_effort = known.get("default_effort", "")
    if st.default_effort and st.default_effort not in st.effort_options():
        opts = st.effort_options()
        st.default_effort = opts[-1] if opts else ""
    if not st.ready:
        st.detail = (st.detail or
                     "вход не выполнен — запусти CLI и авторизуйся"
                     if st.authenticated is not True else "")
    return st


def detect_all(home: Path | None = None, run_commands: bool = True) -> list[CLIStatus]:
    return [detect(f, home=home, run_commands=run_commands) for f in FLAVORS]


# --- cheap cached readiness (used by config availability checks) -------------

_CACHE: dict[str, tuple[float, bool]] = {}
_CACHE_TTL = 60.0


def quick_ready(flavor: str, home: Path | None = None) -> bool:
    """Binary present + session evidence present. Cached; no subprocesses."""
    now = time.monotonic()
    hit = _CACHE.get(flavor)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    ok = bool(find_binary(flavor, home)) and auth_evidence(flavor, home)[0] is True
    _CACHE[flavor] = (now, ok)
    return ok


def clear_cache() -> None:
    _CACHE.clear()
