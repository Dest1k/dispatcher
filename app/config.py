"""Configuration & persistence.

Settings and projects are stored as a single plain-JSON file under the user's
home directory. No encryption (by explicit user choice) — the file lives in the
user profile and is only readable by the OS account that owns it.
"""
from __future__ import annotations

import copy
import json
import os
import uuid
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / ".multi_ai_control_center"
CONFIG_PATH = CONFIG_DIR / "config.json"


# --- Default "fattest" presets for the three smartest models on the planet ---
def _default_config() -> dict[str, Any]:
    return {
        "providers": {
            "anthropic": {
                "id": "anthropic",
                "kind": "anthropic",
                "label": "Claude Opus 4.8 · Ultracode",
                "short": "Claude",
                "enabled": True,
                "api_key": "",
                "model": "claude-opus-4-8",
                "effort": "max",
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
                "label": "ChatGPT 5.6 · Sol Ultra",
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
                "label": "Grok 4.5 · Max",
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
        },
        "orchestration": {
            "mode": "lead",              # 'lead' = Claude раздаёт роли; 'auto' = каждый сам
            "lead_provider": "anthropic",
            "auto_push": True,
            "max_tool_iterations": 24,
            "commit_prefix": "",
        },
        "projects": [],
        "active_project": None,
    }


PROVIDER_ORDER = ["anthropic", "openai", "xai"]


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge saved config on top of defaults so new keys always appear."""
    out = copy.deepcopy(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class Config:
    """In-memory config with load/save helpers."""

    def __init__(self, data: dict[str, Any]):
        self.data = data

    # ---- persistence -------------------------------------------------
    @classmethod
    def load(cls) -> "Config":
        defaults = _default_config()
        if CONFIG_PATH.exists():
            try:
                saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                merged = _deep_merge(defaults, saved)
                # provider dicts: merge each so new fields (like effort_options) survive
                for pid, pdefault in defaults["providers"].items():
                    if pid in saved.get("providers", {}):
                        merged["providers"][pid] = _deep_merge(
                            pdefault, saved["providers"][pid]
                        )
                return cls(merged)
            except (json.JSONDecodeError, OSError):
                pass
        return cls(defaults)

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # ---- providers ---------------------------------------------------
    @property
    def providers(self) -> dict[str, dict]:
        return self.data["providers"]

    def ordered_providers(self) -> list[dict]:
        return [self.providers[pid] for pid in PROVIDER_ORDER if pid in self.providers]

    def active_providers(self) -> list[dict]:
        """Providers that are enabled AND have an API key (start of a run)."""
        return [
            p for p in self.ordered_providers()
            if p.get("enabled") and p.get("api_key", "").strip()
        ]

    def available_providers(self) -> list[dict]:
        """Providers that merely have an API key — can be toggled in/out live."""
        return [
            p for p in self.ordered_providers()
            if p.get("api_key", "").strip()
        ]

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
                self.projects[0]["id"] if self.projects else None
            )
        self.save()

    def add_chat_message(self, project_id: str, role: str, text: str,
                         meta: dict | None = None) -> dict:
        project = self.get_project(project_id)
        msg = {"role": role, "text": text, "meta": meta or {}}
        if project is not None:
            project.setdefault("chat", []).append(msg)
            self.save()
        return msg
