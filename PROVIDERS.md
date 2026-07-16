# Providers, Transports & Billing

Dispatcher separates **how** it talks to a model (transport + auth) from **who
pays** (billing source). The provider list is dynamic; the seeded profiles are
examples, not a fixed set.

## Transports

| Transport | Meaning | Adapter |
|---|---|---|
| `cli_session` | **Official installed CLI with the user's existing login** (Claude Code / Codex / Grok) run as a subprocess | `app/providers/cli_agent.py` |
| `anthropic_messages` | Anthropic Messages REST API | `app/providers/anthropic.py` |
| `openai_chat` | OpenAI Chat Completions (also xAI, OpenAI-compatible) | `app/providers/openai_compat.py` |
| `openai_compatible_local` | A local OpenAI-compatible server (vLLM, etc.) | `app/providers/openai_compat.py` |
| `subscription_agent` | Other official subscription backends without a local CLI | capability-gated, unavailable |

The orchestration core does **not** branch on provider name; it uses these
transports via adapters (`make_adapter`).

## The `cli_session` transport

**Authentication policy (strict):** Dispatcher uses the CLI's own session —
the one created when *you* ran `claude` / `codex login` / `grok login`. It
never asks for API keys for these profiles, never reads, copies or stores the
credential files, and never automates a web login. If the session is missing,
`dispatcher doctor` says so and tells you to log in with the official client.

Two modes per provider:

1. **Reasoning calls** (planning, council, review, research, synthesis):
   one-shot headless invocation with mutating tools disabled —
   - Claude: `claude -p --output-format json --model … --effort …
     --disallowedTools Bash,Edit,Write,… --append-system-prompt …`
   - Codex: `codex exec --sandbox read-only --skip-git-repo-check --cd …`
   - Grok: `grok --prompt-file … --output-format json --permission-mode
     dontAsk --rules …`
   The CLI may *read* the project for context but cannot change anything.
2. **Native implementation mode** (`cli_native: true`, the default): the CLI
   implements the task inside the agent's **isolated worktree** with its own
   tools — Claude/Grok under `acceptEdits` (file edits confined to the
   worktree; shell commands auto-denied), Codex under its official
   `workspace-write` sandbox. The resulting patch is validated against the
   agent's `PathPolicy` at the integration boundary: any write outside the
   agent's zone (or into secret-file patterns) rejects the patch whole.

Output parsing is defensive (JSON → last JSON line → raw text); non-zero exit
codes surface the CLI's stderr; calls run under a configurable per-call
timeout (`cli_timeout`, default 1200 s) with process-tree kill on cancel.

## Models and efforts per participant

Every participant chooses its model and effort independently. Availability is
**discovered**, not guessed:

- Codex: `~/.codex/models_cache.json` (slugs + supported reasoning levels,
  e.g. `gpt-5.6-sol` with low…`ultra`);
- Grok: `~/.grok/models_cache.json` (e.g. `grok-4.5` with low/medium/high);
- Claude: built-in registry with provenance (`claude-opus-4-8`, `opus`,
  `sonnet`, `haiku`; efforts low…`max`).

Defaults follow the product spec: **Opus 4.8 / max**, **GPT-5.6-Sol / ultra**,
**Grok 4.5 / high**. `dispatcher doctor` shows what your machine actually has.

## Billing sources

| billing_source | Meaning | Quota visibility |
|---|---|---|
| `subscription` | CLI session (Claude Code / ChatGPT·Codex / SuperGrok) or subscription-pool API | **Not exposed** by providers — Dispatcher never invents a remaining-quota number |
| `api` | Direct API, per-token billing | API rate-limit headers (per response) |
| `local` | Local model (vLLM) | No billing; no remote quota |

Token usage for CLI providers is shown only when the CLI reports it (Claude's
JSON output does; Codex/Grok headless output does not → shown as 0, cost
"included in subscription"). Rate limits, subscription quotas and financial
budgets remain three distinct concepts in the UI.

## Local vLLM

Set the local profile's `base_url` (default `http://localhost:8000/v1`),
`model` to your served model name, leave `auth: none`:

```bash
python -m vllm.entrypoints.openai.api_server --model <hf-model> --port 8000
```

## Model catalog & capability registry

`app/catalog.py` records verified facts (context window, prices, effort
options) **with provenance and staleness**; unknown models honestly return
"no catalog data". `app/capabilities.py` records what each model family is
*good at* (12 skill axes) as expert priors, corrected over time by the
measured reputation system — see README «Intelligence layer».
