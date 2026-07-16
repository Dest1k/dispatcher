# Dispatcher — AI Engineering Command Center

A desktop + CLI control plane (Python, PySide6) that coordinates **multiple
specialized AI agents** over your repositories with isolation, technically
enforced file ownership, sandboxed commands, deterministic verification, an
explicit human approval gate — and an intelligence layer (capability registry,
explainable routing, agent reputation, project memory graph, context-integrity
ledger, AI council).

```
                         USER
                          │
                  DISPATCHER CORE
        (routing · council · memory · ledger · risk)
                          │
        ┌─────────────────┼─────────────────┐
   ARCHITECT           CODER            RED TEAM / REVIEW
   Claude Code CLI     Codex CLI        Grok CLI
   (Opus 4.8 · max)    (GPT-5.6-Sol ·   (Grok 4.5 · high)
                        ultra)
                          │
                 VALIDATION PIPELINE
          (integration → review → verification → risk)
                          │
                   HUMAN APPROVAL
                          │
                        MERGE
```

## Providers: your existing CLI sessions, no API keys required

Dispatcher's primary transport is **`cli_session`**: it drives the official,
already installed and already logged-in CLI clients as subprocesses — exactly
what happens when you run them by hand. It does **not** ask for API keys, does
not create or copy credentials, and never scrapes cookies. Billing is your
existing subscription.

| Agent | CLI | Default model / effort | Default role |
|---|---|---|---|
| Claude | Claude Code CLI (`claude`) | `claude-opus-4-8` / `max` | architect, refactoring, review |
| Codex | OpenAI Codex CLI (`codex`) | `gpt-5.6-sol` / `ultra` | implementation, debugging, tests |
| Grok | Grok CLI (`grok`) | `grok-4.5` / `high` | research, red team, alternatives |

Models and efforts are **per-participant choices**: available models are
discovered from each CLI's own local cache (`~/.codex/models_cache.json`,
`~/.grok/models_cache.json`, built-in registry for Claude) and editable in
Settings or config.

Direct APIs (Anthropic / OpenAI / xAI / any OpenAI-compatible endpoint,
including local vLLM) remain fully supported as separate provider profiles
with per-token billing — see `PROVIDERS.md`.

## Quick start

Requires Python 3.10+ and git. For CLI providers: the official CLIs installed
and logged in (`claude`, `codex login`, `grok login`).

```bash
pip install -e .
dispatcher doctor          # what is installed, logged in, which models exist
dispatcher run "..."       # full orchestration from the terminal (headless)
dispatcher                 # GUI (or: python run.py)
```

```
$ dispatcher doctor
Claude Code CLI (Anthropic)
  установлен:       да (…\npm\claude.CMD)
  версия:           2.1.211 (Claude Code)
  аутентификация:   да — сессия Claude Code (~/.claude/.credentials.json)
  модель:           claude-opus-4-8 (effort: max)
  доступные модели: claude-opus-4-8, opus, sonnet, haiku
  статус:           ГОТОВ
…
Готово к работе: 3 из 3
```

`dispatcher doctor --probe` performs one real round-trip through every ready
CLI (clearly labeled: it spends subscription quota).

## Headless orchestration (`dispatcher run`)

Runs the whole pipeline — plan → isolated worktrees → integrate → review →
verification → risk → publication decision — from the terminal, over the CLI
agents, with no GUI:

```bash
dispatcher run "Refactor the database layer" --mode adaptive
dispatcher run "..." --deliberate              # council agrees an approach first
dispatcher run "..." --project <path|id>  --providers claude_cli,codex_cli
dispatcher run "..." --dry-run --show-diff     # run everything, publish nothing
dispatcher run "..." --push                    # push the integration branch (never the target)
```

**Safe by default:** an approved run commits only the isolated
`dispatcher/<id>/integration` branch **locally**; `--push` is required to push
it (and is refused when verification blocked); a `partial`/failed verification
or a high-risk diff requires an explicit `--yes`; the target branch is never
written. `--mode adaptive` starts solo and **escalates on a failed
verification** along a configurable ladder (`escalation_ladder`, default
solo → pair, extendable to full_council), feeding the failure back into the
retry. `--deliberate` (and `--mode full_council`, the **complete reasoning
pipeline**) runs the council (architect → red-team → feasibility → synthesis)
to agree an approach **before** any file is touched, then injects that
approach into the implementers.

## The AI Council

```bash
dispatcher council "стоит ли переходить на event sourcing?"                 # independent opinions + synthesis
dispatcher council "..." --mode solo|pair|council|full_council
dispatcher route  "Refactor authentication system"                          # explainable role routing
dispatcher capabilities                                                     # what each model is good at
dispatcher reputation                                                       # measured outcomes per agent
dispatcher memory list|show|add|pack                                        # project memory graph
```

`full_council` runs the reasoning pipeline: **architect proposal → red-team
attack → implementation feasibility → lead synthesis**, with role casting by
the routing engine and a decision trace in the result. Council answers are
stored in the project memory graph and in `~/.multi_ai_control_center/reports/`.

## Intelligence layer

- **Capability registry** (`app/capabilities.py`) — 12 skill axes per model
  family (coding, architecture, debugging, research, red_team, …), recorded as
  expert priors with provenance; unknown models honestly get a neutral profile.
- **Adaptive routing** (`app/routing.py`) — task classification (RU/EN) +
  role-weighted capability scores × bounded reputation multiplier; every
  assignment carries a human-readable explanation; council roles stay distinct.
- **Reputation** (`app/reputation.py`) — measured per-provider outcomes
  (tasks, verifications, human approvals, rollbacks) with Laplace smoothing,
  bounded to ×[0.85..1.15] so priors are tuned, never overridden.
- **Project memory graph** (`app/memory_graph.py`) — SQLite graph of decisions
  / bugs / solutions / approaches / lessons with *reasoned* edges
  (`solved_by`, `rejected_for`, …). Run outcomes are recorded automatically;
  a compact digest is injected into agent prompts.
- **Context-integrity ledger** (`app/ledger.py`) — evidence (files read/
  written, commands, tests, provider calls, permissions) recorded per run;
  reports contain a machine-generated evidence section, and claim checks block
  wording like "inspected the repository" without backing evidence.
- **Change risk scoring** (`app/risk.py`) — deterministic, explainable factors
  (sensitive paths, diff size, deletions, code-without-tests) shown in the
  report and the approval dialog.

## Safety model

| Concern | What Dispatcher does |
|---|---|
| Shared writable repo | Each agent works in its **own git worktree** from a recorded base commit. |
| File ownership | `PathPolicy` enforces allowed/deny/read-only paths. API agents are blocked at write time; CLI-native agents are validated at the **integration boundary** — a patch touching files outside the agent's zone is rejected whole. |
| Command execution | Sandboxed (no host env/secrets, throwaway HOME, kill-on-timeout; optional Docker). CLI-native mode uses the official CLI's own guardrails confined to the worktree (Claude/Grok `acceptEdits`, Codex `workspace-write`). |
| Git safety | Source tree never modified; dirty tree blocks the run; integration via explicit staged patches into a dedicated branch; no `git add -A`, no reset, no force push; `auto_push` off by default. |
| Verification | Real commands with structured results; fail/unknown/cancelled **blocks** publication. |
| Human approval | Full diff + verification evidence + **risk assessment** + ledger before anything is committed. |
| Secrets | API keys (only for direct-API profiles) live in the OS keyring; logs redacted. CLI sessions stay inside the official clients — Dispatcher never reads or copies them. |

## Documentation

- `ARCHITECTURE.md` — layers, run flow, intelligence modules.
- `PROVIDERS.md` — transports (incl. `cli_session`), billing, models/efforts.
- `SECURITY.md` — threat model; what is enforced vs best-effort.
- `AUDIT_REPORT.md` / `FINAL_REPORT.md` — аудит и итоговый отчёт (RU).
- `ROADMAP.md` / `IMPLEMENTATION_STATUS.md` — phases and current status.

## Tests

No test performs real network calls or spawns real CLIs — subprocesses and
HTTP are mocked; detection is driven off fake home dirs.

```bash
pip install pytest
python -m pytest -q
```

## Known limitations

- A single CLI invocation can't accept mid-run steering; steering queued
  before start is included, later steering applies from the next task.
- Subscription quotas are not exposed by the CLIs; Dispatcher shows honest
  "not exposed" instead of inventing numbers. Token usage is reported only
  where the CLI provides it (Claude does; Codex/Grok show 0).
- Headless `dispatcher run` (full orchestration without GUI) is not yet
  wired; the council/routing/doctor commands are headless today.
