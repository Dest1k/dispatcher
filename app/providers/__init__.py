"""Model provider adapters (Anthropic, OpenAI-compatible, official CLIs)."""
from __future__ import annotations

from .base import Message, ToolCall, ToolSpec, Usage, CompletionResult, Steering, run_agent
from .anthropic import AnthropicAdapter
from .openai_compat import OpenAIAdapter
from .cli_agent import CLIAdapter, CLIError


def make_adapter(provider_cfg: dict):
    """Build the right adapter for a provider config dict."""
    kind = provider_cfg.get("kind", "openai")
    if kind == "anthropic":
        return AnthropicAdapter(provider_cfg)
    if kind == "cli":
        return CLIAdapter(provider_cfg)
    return OpenAIAdapter(provider_cfg)


__all__ = [
    "Message", "ToolCall", "ToolSpec", "Usage", "CompletionResult",
    "Steering", "run_agent", "AnthropicAdapter", "OpenAIAdapter",
    "CLIAdapter", "CLIError", "make_adapter",
]
