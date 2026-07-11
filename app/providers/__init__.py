"""Model provider adapters (Anthropic, OpenAI-compatible)."""
from __future__ import annotations

from .base import Message, ToolCall, ToolSpec, Usage, CompletionResult, Steering, run_agent
from .anthropic import AnthropicAdapter
from .openai_compat import OpenAIAdapter


def make_adapter(provider_cfg: dict):
    """Build the right adapter for a provider config dict."""
    kind = provider_cfg.get("kind", "openai")
    if kind == "anthropic":
        return AnthropicAdapter(provider_cfg)
    return OpenAIAdapter(provider_cfg)


__all__ = [
    "Message", "ToolCall", "ToolSpec", "Usage", "CompletionResult",
    "Steering", "run_agent", "AnthropicAdapter", "OpenAIAdapter", "make_adapter",
]
