# Implementation Status

Living tracker for the production redesign. Updated as phases land so a resumed
session can see exactly where to continue. `pytest -q` is the source of truth for
"working".

## Phase progress

| Phase | Scope | Status |
|---|---|---|
| 0 | Audit + committed pytest baseline | ✅ done |
| 1 | Critical safety (git, isolation, secrets, verification, honest UI) | ✅ done (core) |
| 2 | Persistent run engine (SQLite, state machine, checkpoints, resume) | ✅ done |
| 3 | Isolated multi-agent orchestration (worktrees, DAG, review, integrate) | ◐ isolation+integration+review+plan-validation+safe fallback+mid-run disable+plan red-team critique+standalone council+adaptive escalation (configurable ladder)+council deliberation+full_council reasoning pipeline+task-DAG executor done; mid-run hot-join planned |
| 4 | Provider/auth backends (transports, billing, local vLLM, **CLI sessions**) | ✅ core: registry+billing+local+API+retry/backoff+SSE+`cli_session` (claude/codex/grok через существующие логины, doctor, discovery моделей, native-режим, patch-boundary policy) |
| 4.5 | Intelligence layer (v3) | ✅ capabilities+routing+reputation+council+memory graph+ledger+risk |
| 5 | UI + publication (dashboard, diff, approvals, draft PR) | ◐ approval+diff+evidence+risk+billing labels+CLI-session forms+safety knobs+run-recovery done; run dashboard + 1-click PR planned |
| 6 | Docs, packaging, hardening | ✅ docs + packaging + CI (GitHub Actions) done; more tests ongoing |

## Non-negotiable acceptance criteria (§18)

| # | Criterion | Status |
|---|---|---|
| 1 | Two implementation agents never share a writable worktree | ✅ |
| 2 | File ownership technically enforced | ✅ |
| 3 | Planner failure can't trigger unsafe full parallelism | ✅ |
| 4 | Host shell execution not the default | ✅ restricted worker (env-scrubbed, temp HOME, no secrets/network) default; Docker **container-per-agent** (persistent) + per-command backends, live-verified on WSL2/Docker, fallback to restricted when absent |
| 5 | Provider secrets unavailable to worker commands | ✅ |
| 6 | Existing user changes can't be accidentally committed | ✅ |
| 7 | Integration doesn't use broad `git add -A` | ✅ |
| 8 | Existing branches not reset | ✅ |
| 9 | Auto-push disabled by default | ✅ |
| 10 | Direct target-branch publication not default | ✅ |
| 11 | Real verification stage before publication | ✅ |
| 12 | Failed/unknown verification blocks auto-publication | ✅ |
| 13 | Subscription vs direct-API backends separated | ✅ (CLI-session profiles ≠ API profiles) |
| 14 | No unofficial browser/cookie auth | ✅ official CLI sessions only; credentials never read/copied |
| 15 | Local OpenAI-compatible endpoints supported | ✅ |
| 16 | Provider list dynamic, not hardcoded to three | ✅ |
| 17 | Runs persist across restart | ✅ |
| 18 | Cancellation terminates process trees | ✅ |
| 19 | Retry/backoff for transient errors | ✅ |
| 20 | Rate limit / quota / budget separate in UI | ✅ |
| 21 | Secrets in OS secret store | ✅ (keyring w/ documented fallback) |
| 22 | Logs/reports redact credentials | ✅ |
| 23 | Full diff + verification evidence before approval | ✅ |
| 24 | Draft PR after approval | ✅ push integration branch + one-click "compare/PR" URL; API PR creation optional (token/App) |
| 25 | Reports stored consistently; no `.gitignore` contradiction | ✅ |
| 26 | UI doesn't claim raw chain-of-thought | ✅ |
| 27 | Tests cover git/sandbox/orchestration/persistence/security | ✅ git/sandbox/security/providers/persistence/resume/e2e/planning/mid-run/publish-redaction + CLI-agents/adapter/council/routing/reputation/memory/ledger/risk/headless-run/adaptive-escalation (251 tests) |
| 28 | README describes only real functionality | ✅ |
| 29 | User config migratable without losing projects | ✅ |
| 30 | Original repo recoverable after failed/cancelled run | ✅ |

Legend: ✅ done · ◐ partial · ⬜ not started · 🚧 in progress

## Landed (in addition to the criteria above)

Retry/backoff with Retry-After (criterion 19); per-run budget enforcement +
forecast (§8); SSE streaming for OpenAI-compatible agents (gated); persisted
structured event timeline + run-history view; report/event redaction; secret-
store status; versioned model catalog with provenance/staleness (§5.2); plan
validation with safe fallback (§6); CI.

Session-3 additions: configurable per-command & per-verification timeouts
(§3.4, editable in Settings); per-project verification-commands editor (UI wired
end-to-end); plan-validation now detects prefix/glob zone overlaps that share a
file under PathPolicy semantics (not just exact-path collisions), so overlapping
plans trigger the safe single-executor fallback; push-failure messages redact
the tokenized remote URL (git echoes it on 4xx) before display/persistence
(§22); unit coverage for mid-run disable/redistribute/cancel controls.

Session-3 bug fixes (all with regression tests): OpenAI-compatible requests no
longer send `tool_choice` on tool-less planning/review/report calls (would 400);
streamed assistant text is no longer double-rendered (delta + text) and streamed
deltas are now redacted; `Orchestrator.add_agent` added so the mid-run panel
hot-join toggle no longer raises AttributeError (declines late-join honestly).
Test suite 77 → 100.

## Session-4 (v3 «AI Multi-Agent Control Center»)

- **`cli_session` transport**: official Claude Code (`claude` 2.1.211), Codex
  (`codex` 0.144.5) and Grok (`grok` 0.2.101) CLIs driven through the user's
  *existing logins*. No API keys requested/stored; credentials never read.
  Reasoning mode (read-only tools) + native worktree implementation mode;
  defensive output parsing; per-call timeout; process-tree cancel.
- **`dispatcher` console CLI**: `doctor` (install/auth/models/readiness, with
  `--probe` live round-trip), `council`, `route`, `capabilities`,
  `reputation`, `memory`, `gui`. Entry point switched to `app.cli:main`.
- **Discovery**: models + per-model reasoning levels read from the CLIs' own
  local caches; defaults per spec (Opus 4.8/max, GPT-5.6-Sol/ultra,
  Grok 4.5/high); per-participant model/effort choice in Settings.
- **Intelligence layer**: capability registry (12 axes + provenance),
  explainable routing with distinct council roles, measured reputation
  (bounded ×0.85–1.15), AI council (solo/pair/council/full_council with
  architect→red-team→feasibility→synthesis), project memory graph (SQLite,
  reasoned edges, prompt digest), context-integrity ledger (evidence-backed
  claims in reports), deterministic change-risk scoring in approval payloads.
- **Safety extension**: CLI-native patches validated against PathPolicy at the
  integration boundary (out-of-zone/secret-path writes reject the patch whole).
- **Fixes**: Windows patch corruption (`write_text` CRLF translation broke
  `git apply` context matching); wheel packaging now includes subpackages.
- **Headless `dispatcher run`**: the full orchestration pipeline (plan →
  isolated worktrees → integrate → review → verification → risk → publish)
  from the terminal, driven synchronously (no GUI), with a safe
  publication-decision policy (`decide_publication`): approved runs commit
  only the isolated integration branch locally; `--push` required to push and
  refused when verification blocked; `partial`/failed verification or
  high-risk diff needs `--yes`; `--dry-run` runs everything and publishes
  nothing; target branch never written.
- **Adaptive escalation**: `execution_mode: adaptive` runs a solo attempt and,
  on a failed verification, escalates to a pair in fresh isolated worktrees,
  feeding the failure summary back as steering, then retries once.
- **Council deliberation phase** (`deliberate`): before any file is touched the
  council agrees an approach — architect proposal → red-team attack →
  feasibility → synthesis (reasoning-only, no writes) — which is injected into
  the planner and every implementer's context, recorded in the memory graph as
  an `approach` node, and shown in the report. `deliberation_ready` signal,
  `--deliberate` flag on `dispatcher run`, a Settings checkbox.
- **Configurable escalation ladder** (`escalation_ladder`, default solo → pair):
  adaptive mode climbs it on verification failure; steps needing more providers
  than are active are skipped and repeats deduped. **`full_council`** is now the
  vision's *complete reasoning pipeline* — it auto-deliberates before the
  parallel implementation.
- **Project-memory browser** (`ui/memory_dialog.py`, «🧠 Память проекта»):
  read-only window over the memory graph — filter by kind, search, inspect a
  node with its reasoned links (why/what failed/what was rejected).
- **Task planning engine** (`app/planner.py`, `dispatcher plan`): the vision's
  TASK PLANNING ENGINE — a phased plan (analyze → design → implement → test →
  **security** → review [→ release]) with per-phase agent routing, security/
  review actively kept off the implementer (VERIFICATION LOOP «no self-review»),
  and best-effort per-phase detail from the lead. Read-only; recorded in memory.
- **Task-DAG executor** (`app/dag.py` + orchestrator, `dag_execution`/`--dag`):
  the lead builds a dependency graph; `validate_dag` rejects cycles, unknown
  deps/providers and concurrent-zone overlaps (PathPolicy-aware); layers run in
  ordered rounds — independent tasks parallel in isolated worktrees, dependent
  tasks in later layers built on the previous layers' integrated commit
  (incremental commit per layer). Opt-in; default path unchanged.
- **Orchestrator decoupled from Qt** (`app/eventbus.py`): the orchestrator uses
  PySide6's `QThread`/`Signal` when present (GUI keeps Qt's queued cross-thread
  delivery) and a Qt-free shim (Signal descriptor + threading.Thread-backed
  QThread) otherwise, so the engine imports and runs **without PySide6** on a
  headless server. `dispatcher run` makes Qt optional too. Verified by importing
  the orchestrator with PySide6 blocked.
- **Docker container-per-agent** (`app/sandbox.py` `DockerAgentSandbox`,
  `sandbox_mode: docker`): one long-lived container per agent worktree, reused
  across the agent's commands (state persists), started lazily and removed on
  close or on a command timeout/cancel; mounts only the worktree, `--network
  none` by default, no host-env/secret passthrough, no docker socket, CPU/mem/
  pids caps. `docker_command` keeps the stateless one-container-per-command
  mode. Live-verified on Docker 29.6/WSL2 (cross-command persistence + no host
  secrets inside).
- Live validation on this machine: `doctor` 3/3 ready; `--probe` round-trips
  pong via claude (3.2 s), codex (11.4 s), grok (3.5 s); a live
  `full_council` ran the whole architect→red-team→feasibility→synthesis
  pipeline and **found two real config.py defects** (non-atomic save, silent
  reset of corrupt config) — both fixed with regression tests; a live headless
  `dispatcher run` over the real Claude CLI created a file in an isolated
  worktree and the dry-run left the source repo completely untouched.
- Test suite 100 → 251 (still no network, no real CLI spawns in tests; the
  new run/escalation e2e tests use real git with mocked providers/verification).

## Where to continue next

1. Docker container-per-agent for the *implementation* step (commands already
   sandbox in Docker per-command).
2. Richer run dashboard / task-graph view; API-based draft PR (needs a GitHub
   token/App).
3. Mid-run hot-join of a new agent (currently declined honestly — needs a fresh
   isolated worktree with a disjoint zone + re-planning).
