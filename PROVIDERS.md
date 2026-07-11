# Providers, Transports & Billing

Dispatcher separates **how** it talks to a model (transport + auth) from **who
pays** (billing source). The provider list is dynamic; the four seeded profiles
are examples, not a fixed set.

## Transports

| Transport | Meaning | Adapter |
|---|---|---|
| `anthropic_messages` | Anthropic Messages REST API | `app/providers/anthropic.py` |
| `openai_chat` | OpenAI Chat Completions (also xAI, OpenAI-compatible) | `app/providers/openai_compat.py` |
| `openai_compatible_local` | A local OpenAI-compatible server (vLLM, etc.) | `app/providers/openai_compat.py` |
| `subscription_agent` | Official subscription-backed coding agents | **capability-gated, currently unavailable** |

The orchestration core does **not** branch on provider name; it uses these
transports via adapters. Request shapes (effort, reasoning params, token fields)
should be verified against current official provider docs before extending.

## Billing sources

| billing_source | Meaning | Quota visibility |
|---|---|---|
| `api` | Direct API, per-token billing | API rate-limit headers (per response) |
| `subscription` | Subscription pool (e.g. **Grok · SuperGrok**) | Usually **not exposed** by the provider's API |
| `local` | Local model (vLLM) | No billing; no remote quota |

**Grok / SuperGrok:** the account is on the SuperGrok subscription tier. The
programmatic path still uses an xAI API key (`transport: openai_chat`), but
`billing_source` is `subscription` and `quota_source` is `not_exposed`. Dispatcher
therefore shows `Grok · SuperGrok` and does **not** fabricate a remaining-quota
number. Effort is set to the highest xAI reasoning level (`high`).

## Rate limits vs quotas vs budgets

These are distinct and shown separately:

- **API rate limit** — requests/tokens remaining in the current short window,
  parsed from real response headers (`x-ratelimit-*`, `anthropic-ratelimit-*`).
- **Subscription quota** — only shown when a provider exposes it; otherwise
  "not exposed by provider".
- **Financial budget** — your configured per-run/per-agent spend caps and the
  measured token cost (roadmap: hard budget enforcement).

There is **no** automatic paid ping on startup; refreshing limits is a manual,
clearly-labeled billable request.

## Local vLLM

Set the local profile's `base_url` (default `http://localhost:8000/v1`), `model`
to your served model name, leave `auth: none`. Start vLLM, e.g.:

```bash
python -m vllm.entrypoints.openai.api_server --model <hf-model> --port 8000
```

Local models are ideal for cheap preprocessing (repo indexing, summaries,
first-pass review); reserve paid cloud agents for work that needs them.

## Subscription-backed coding agents

Codex (ChatGPT), Claude Code / Claude Agent SDK, and xAI build agents are real
products, but a supported *programmatic, headless* integration for each must be
verified against current official docs and typically needs an official auth
flow. Dispatcher keeps a clean `subscription_agent` boundary and marks these
**unavailable** rather than shipping an unofficial cookie/session hack. A direct
API key and a subscription agent for the same model remain **separate backends**.

## Model catalog

Model IDs, context windows, effort options and prices change over time and are
**user-editable** in Settings. Dispatcher does not hardcode "maximum" presets as
permanent facts; defaults are conservative and configurable.

`app/catalog.py` is a small **versioned capability catalog**: for a model it can
actually source, it records the context window, pricing, effort options, **the
date it was verified**, and the **source**, and marks the entry **stale** past a
threshold. Models it cannot verify (e.g. future IDs) return **"no catalog data —
verify with the provider"** in Settings rather than a fabricated number. This
keeps guessed values from being presented as current facts.
