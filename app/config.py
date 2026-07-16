"""Configuration, provider profiles & persistence.

Design points for the production redesign:
  * Provider profiles are a *dynamic* dict (not exactly three). Each profile
    declares transport, auth and billing_source so subscription-backed,
    direct-API and local backends are represented distinctly.
  * Secrets (api keys, github tokens) are never written to the plaintext config;
    they live in the OS secret store and only a reference is persisted.
    Legacy plaintext secrets are migrated on first save.
"""
from __future__ import annotations

import copy
import json
import time
import uuid
from pathlib import Path
from typing import Any

from .security import register_secret, secret_store

CONFIG_DIR = Path.home() / ".multi_ai_control_center"
CONFIG_PATH = CONFIG_DIR / "config.json"

# Transports (behind adapters). cli_session drives the official, already
# authenticated CLI clients (Claude Code / Codex / Grok) as subprocesses — see
# PROVIDERS.md. subscription_agent remains a capability-gated boundary for
# other official subscription backends without a local CLI.
TRANSPORT_ANTHROPIC = "anthropic_messages"
TRANSPORT_OPENAI = "openai_chat"
TRANSPORT_LOCAL = "openai_compatible_local"
TRANSPORT_CLI = "cli_session"
TRANSPORT_SUBSCRIPTION = "subscription_agent"

# Billing sources — kept distinct from API rate limits and financial budget.
BILLING_API = "api"
BILLING_SUBSCRIPTION = "subscription"
BILLING_LOCAL = "local"

# Secret-bearing fields, resolved from refs at load and stripped at save.
_PROVIDER_SECRET = "api_key"
_PROJECT_SECRET = "github_token"


def _seed_providers() -> dict[str, Any]:
    return {
        # --- official CLI sessions (subscription; no API keys involved) ----
        "claude_cli": {
            "id": "claude_cli",
            "kind": "cli",
            "cli_flavor": "claude",
            "transport": TRANSPORT_CLI,
            "auth": "cli_session",
            "billing_source": BILLING_SUBSCRIPTION,
            "subscription_tier": "Claude Code",
            "quota_source": "not_exposed",
            "label": "Claude Code CLI · Opus 4.8",
            "short": "Claude",
            "enabled": True,
            "api_key": "",
            "model": "claude-opus-4-8",
            "effort": "max",
            "base_url": "",
            "max_tokens": 0,
            "price_in": 0.0,
            "price_out": 0.0,
            "effort_options": ["low", "medium", "high", "xhigh", "max"],
            "cli_native": True,
            "cli_timeout": 1200,
            "accent": "#d97757",
            "strength": "архитектура, планирование, рефакторинг, ревью",
            "default_role": "architect",
        },
        "codex_cli": {
            "id": "codex_cli",
            "kind": "cli",
            "cli_flavor": "codex",
            "transport": TRANSPORT_CLI,
            "auth": "cli_session",
            "billing_source": BILLING_SUBSCRIPTION,
            "subscription_tier": "ChatGPT · Codex",
            "quota_source": "not_exposed",
            "label": "Codex CLI · GPT-5.6-Sol",
            "short": "Codex",
            "enabled": True,
            "api_key": "",
            "model": "gpt-5.6-sol",
            "effort": "ultra",
            "base_url": "",
            "max_tokens": 0,
            "price_in": 0.0,
            "price_out": 0.0,
            "effort_options": ["low", "medium", "high", "xhigh", "max", "ultra"],
            "cli_native": True,
            "cli_timeout": 1200,
            "accent": "#10a37f",
            "strength": "реализация, отладка, тесты, верификация",
            "default_role": "developer",
        },
        "grok_cli": {
            "id": "grok_cli",
            "kind": "cli",
            "cli_flavor": "grok",
            "transport": TRANSPORT_CLI,
            "auth": "cli_session",
            "billing_source": BILLING_SUBSCRIPTION,
            "subscription_tier": "SuperGrok",
            "quota_source": "not_exposed",
            "label": "Grok CLI · Grok 4.5",
            "short": "Grok",
            "enabled": True,
            "api_key": "",
            "model": "grok-4.5",
            "effort": "high",
            "base_url": "",
            "max_tokens": 0,
            "price_in": 0.0,
            "price_out": 0.0,
            "effort_options": ["low", "medium", "high"],
            "cli_native": True,
            "cli_timeout": 1200,
            "accent": "#6366f1",
            "strength": "исследование, red team, альтернативные решения",
            "default_role": "red_team",
        },
        # --- direct APIs (per-token billing) --------------------------------
        "anthropic": {
            "id": "anthropic",
            "kind": "anthropic",
            "transport": TRANSPORT_ANTHROPIC,
            "auth": "api_key",
            "billing_source": BILLING_API,
            "subscription_tier": "",
            "quota_source": "api_headers",
            "label": "Claude Opus 4.8",
            "short": "Claude",
            "enabled": True,
            "api_key": "",
            "model": "claude-opus-4-8",
            "effort": "high",
            "base_url": "https://api.anthropic.com/v1",
            "max_tokens": 16000,
            "price_in": 5.0,
            "price_out": 25.0,
            "effort_options": ["low", "medium", "high", "xhigh", "max"],
            "accent": "#d97757",
            "strength": "архитектура, сложная логика, ревью и интеграция результатов",
        },
        "openai": {
            "id": "openai",
            "kind": "openai",
            "transport": TRANSPORT_OPENAI,
            "auth": "api_key",
            "billing_source": BILLING_API,
            "subscription_tier": "",
            "quota_source": "api_headers",
            "label": "ChatGPT 5.6",
            "short": "ChatGPT",
            "enabled": True,
            "api_key": "",
            "model": "gpt-5.6",
            "effort": "high",
            "base_url": "https://api.openai.com/v1",
            "max_tokens": 16000,
            "price_in": 5.0,
            "price_out": 15.0,
            "effort_options": ["minimal", "low", "medium", "high"],
            "accent": "#10a37f",
            "strength": "реализация фич, алгоритмы, аккуратный код и тесты",
        },
        "xai": {
            "id": "xai",
            "kind": "openai",
            "transport": TRANSPORT_OPENAI,
            "auth": "api_key",
            "billing_source": BILLING_SUBSCRIPTION,
            "subscription_tier": "SuperGrok",
            "quota_source": "not_exposed",
            "label": "Grok 4.5 · SuperGrok",
            "short": "Grok",
            "enabled": True,
            "api_key": "",
            "model": "grok-4.5",
            "effort": "high",
            "base_url": "https://api.x.ai/v1",
            "max_tokens": 16000,
            "price_in": 5.0,
            "price_out": 15.0,
            "effort_options": ["low", "medium", "high"],
            "accent": "#6366f1",
            "strength": "инфраструктура, интеграции, документация и данные",
        },
        "local": {
            "id": "local",
            "kind": "openai",
            "transport": TRANSPORT_LOCAL,
            "auth": "none",
            "billing_source": BILLING_LOCAL,
            "subscription_tier": "",
            "quota_source": "local",
            "label": "Local model · vLLM",
            "short": "Local",
            "enabled": False,
            "api_key": "",
            "model": "your-local-model",
            "effort": "none",
            "base_url": "http://localhost:8000/v1",
            "max_tokens": 8000,
            "price_in": 0.0,
            "price_out": 0.0,
            "effort_options": ["none", "low", "medium", "high"],
            "accent": "#3fb950",
            "strength": "индексация репозитория, суммаризация, дешёвая предобработка",
        },
    }


def _default_config() -> dict[str, Any]:
    return {
        "providers": _seed_providers(),
        "provider_order": ["claude_cli", "codex_cli", "grok_cli",
                           "anthropic", "openai", "xai", "local"],
        "orchestration": {
            "mode": "lead",
            "lead_provider": "claude_cli",
            "execution_mode": "pair",          # solo | pair | adaptive | full_council
            "auto_push": False,                # SAFETY: never push automatically
            "publish_default": "integration_branch",  # never the target branch
            "require_verification": True,
            "council_planning": False,         # red-team plan critique (opt-in)
            "deliberate": False,               # council agrees an approach first
            "escalation_ladder": ["solo", "pair"],  # adaptive-mode retry ladder
            "max_tool_iterations": 14,          # safer adaptive default
            "budget_usd": 0.0,                   # 0 = no hard cap
            "budget_warn_ratio": 0.8,
            "stream": False,                     # SSE streaming for OpenAI-compatible agents
            "commit_prefix": "",
            "sandbox_mode": "restricted",       # restricted | docker | unsafe_local
            "allow_network": False,
            "command_timeout": 300,             # per shell command, seconds
            "verify_timeout": 900,              # whole verification stage, seconds
        },
        "projects": [],
        "active_project": None,
    }


# Seed order used only for building defaults; the live order is dynamic.
PROVIDER_ORDER = ["claude_cli", "codex_cli", "grok_cli",
                  "anthropic", "openai", "xai", "local"]


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class Config:
    def __init__(self, data: dict[str, Any]):
        self.data = data
        self.migrated_secrets = False

    # ---- persistence -------------------------------------------------
    @classmethod
    def load(cls) -> "Config":
        defaults = _default_config()
        if CONFIG_PATH.exists():
            try:
                saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                merged = _deep_merge(defaults, saved)
                seed = _seed_providers()
                for pid, pdefault in seed.items():
                    if pid in saved.get("providers", {}):
                        merged["providers"][pid] = _deep_merge(
                            pdefault, saved["providers"][pid])
                cfg = cls(merged)
                cfg._resolve_secrets()
                return cfg
            except json.JSONDecodeError:
                # A corrupt config must not be silently overwritten by the
                # next save: preserve it for recovery, then start clean.
                # (Defect found & verified by the AI council, 2026-07-17.)
                try:
                    backup = CONFIG_PATH.with_name(
                        f"config.json.corrupt-{int(time.time())}")
                    CONFIG_PATH.replace(backup)
                except OSError:
                    pass
            except OSError:
                pass
        return cls(defaults)

    def _resolve_secrets(self) -> None:
        """Pull secret values out of the OS store into memory; migrate legacy."""
        for provider in self.providers.values():
            self._resolve_field(provider, _PROVIDER_SECRET,
                                 f"provider:{provider.get('id')}:{_PROVIDER_SECRET}")
        for project in self.projects:
            self._resolve_field(project, _PROJECT_SECRET,
                                 f"project:{project.get('id')}:{_PROJECT_SECRET}")

    def _resolve_field(self, obj: dict, field: str, account: str) -> None:
        ref = obj.get(f"{field}_ref")
        legacy = obj.get(field)
        if ref:
            value = secret_store.get(ref) or ""
            obj[field] = value
            register_secret(value)
        elif legacy:
            # Legacy plaintext secret in an old config -> migrate on next save.
            self.migrated_secrets = True
            register_secret(legacy)

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        # Persist a copy with secrets replaced by references.
        persisted = copy.deepcopy(self.data)
        for pid, provider in self.providers.items():
            self._persist_field(provider, persisted["providers"][pid],
                                 _PROVIDER_SECRET,
                                 f"provider:{pid}:{_PROVIDER_SECRET}")
        for i, project in enumerate(self.projects):
            self._persist_field(project, persisted["projects"][i],
                                 _PROJECT_SECRET,
                                 f"project:{project['id']}:{_PROJECT_SECRET}")
        # Atomic write: temp file + replace, so a crash mid-save can't leave a
        # truncated config. (Defect found & verified by the AI council.)
        tmp = CONFIG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(persisted, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(CONFIG_PATH)

    def _persist_field(self, live: dict, persisted: dict, field: str,
                        account: str) -> None:
        value = live.get(field, "")
        persisted.pop(field, None)          # never write the plaintext secret
        if value:
            ref = secret_store.set(account, value)
            live[f"{field}_ref"] = ref
            persisted[f"{field}_ref"] = ref
            register_secret(value)
        else:
            secret_store.delete(live.get(f"{field}_ref"))
            live.pop(f"{field}_ref", None)
            persisted.pop(f"{field}_ref", None)

    # ---- providers ---------------------------------------------------
    @property
    def providers(self) -> dict[str, dict]:
        return self.data["providers"]

    def provider_order(self) -> list[str]:
        order = self.data.get("provider_order") or []
        # keep any providers not listed in the order at the end
        return order + [pid for pid in self.providers if pid not in order]

    def ordered_providers(self) -> list[dict]:
        return [self.providers[pid] for pid in self.provider_order()
                if pid in self.providers]

    def active_providers(self) -> list[dict]:
        return [p for p in self.ordered_providers() if _is_active(p)]

    def available_providers(self) -> list[dict]:
        return [p for p in self.ordered_providers() if _has_credentials(p)]

    def add_provider(self, profile: dict) -> None:
        pid = profile["id"]
        self.providers[pid] = profile
        order = self.data.setdefault("provider_order", [])
        if pid not in order:
            order.append(pid)
        self.save()

    def remove_provider(self, pid: str) -> None:
        self.providers.pop(pid, None)
        if pid in self.data.get("provider_order", []):
            self.data["provider_order"].remove(pid)
        self.save()

    @property
    def orchestration(self) -> dict:
        return self.data["orchestration"]

    # ---- projects ----------------------------------------------------
    @property
    def projects(self) -> list[dict]:
        return self.data["projects"]

    def get_project(self, project_id: str) -> dict | None:
        return next((p for p in self.projects if p["id"] == project_id), None)

    def add_project(self, name: str, local_path: str, github_repo: str,
                    github_url: str, branch: str, github_token: str) -> dict:
        project = {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "local_path": local_path,
            "github_repo": github_repo,
            "github_url": github_url,
            "branch": branch or "main",
            "github_token": github_token,
            "verify_commands": [],
            "chat": [],
            "runs": [],
        }
        self.projects.append(project)
        self.data["active_project"] = project["id"]
        self.save()
        return project

    def update_project(self, project_id: str, **fields) -> None:
        project = self.get_project(project_id)
        if project:
            project.update(fields)
            self.save()

    def delete_project(self, project_id: str) -> None:
        self.data["projects"] = [p for p in self.projects if p["id"] != project_id]
        if self.data.get("active_project") == project_id:
            self.data["active_project"] = (
                self.projects[0]["id"] if self.projects else None)
        self.save()

    def add_chat_message(self, project_id: str, role: str, text: str,
                         meta: dict | None = None) -> dict:
        project = self.get_project(project_id)
        msg = {"role": role, "text": text, "meta": meta or {}}
        if project is not None:
            project.setdefault("chat", []).append(msg)
            self.save()
        return msg


def _has_credentials(p: dict) -> bool:
    if p.get("auth") == "cli_session":
        # An official CLI counts as credentialed when it is installed and its
        # own session files show a completed login. No keys are stored.
        from . import cliagents
        return cliagents.quick_ready(p.get("cli_flavor", ""))
    if p.get("auth") == "none":
        return bool(p.get("base_url"))
    return bool(p.get("api_key", "").strip())


def _is_active(p: dict) -> bool:
    return bool(p.get("enabled")) and _has_credentials(p)
