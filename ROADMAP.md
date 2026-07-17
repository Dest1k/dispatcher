# Roadmap

Status legend: ✅ done · ◐ partial · ⬜ planned. See `IMPLEMENTATION_STATUS.md`
for the acceptance-criteria matrix.

## Phase 0 — Audit & baseline ✅
Committed pytest baseline; documented the prototype's unsafe patterns.

## Phase 1 — Critical safety ✅ (core)
Isolated worktrees; enforced file ownership; command sandbox with scrubbed env
and process-tree kill; verification gate; human approval before publish;
`auto_push=off`; no `git add -A`/branch-reset/force-push; secrets in OS keychain;
redaction; dynamic provider/billing model; honest UI (reasoning summary label,
separate rate-limit/quota/budget, no autoping); reports stored outside the repo.

## Phase 2 — Persistent run engine ✅
SQLite persistence for runs, events and usage with a schema version; an explicit
run state machine (`app/domain.py`) with checkpoints after every transition;
restart reconciliation and safe resume of runs parked at the approval gate.
Covered by `test_persistence.py` / `test_resume.py`.

## Phase 3 — Isolated multi-agent orchestration ◐
Done: per-agent worktrees, enforced path scope, sequential patch integration,
independent cross-review, safe single-executor fallback, plan validation that
rejects prefix/glob zone overlaps (not just exact-path collisions), mid-run
**drop**, opt-in red-team critique of the plan (`council_planning`), the
standalone multi-round council (`dispatcher council`: proposal → attack →
feasibility → synthesis), **adaptive escalation** (solo attempt → escalate to
a pair in fresh worktrees on verification failure, feeding the failure back),
and a **council deliberation phase** (`deliberate`) that agrees an approach —
architect → red-team → feasibility → synthesis — *before* implementation and
injects it into the planner and implementers; a **configurable escalation
ladder** (`escalation_ladder`, default solo → pair, extendable to
full_council; steps needing more providers are skipped and repeats deduped);
`full_council` as the vision's **complete reasoning pipeline** (it
auto-deliberates before the parallel implementation); and a **task-DAG
executor** (`dag_execution`) that runs a validated dependency graph in ordered
layers — independent tasks parallel in isolated worktrees, dependent tasks
building on prior layers' integrated commit. Planned: mid-run **hot-join** of a
new agent (currently declined honestly).

## Phase 4 — Providers & auth ✅ (core)
Done: dynamic registry, transport/auth/billing model, direct-API + local
(vLLM) adapters, transient retry/backoff (Retry-After), per-run budget
enforcement + forecast, SSE streaming for both OpenAI-compatible and Anthropic
adapters, versioned source-dated model catalog — **and the `cli_session`
transport**: official Claude Code / Codex / Grok CLIs driven through the
user's existing logins (no API keys), with CLI discovery (`dispatcher
doctor`), model/effort discovery from the CLIs' local caches, reasoning +
native-worktree modes, patch-boundary policy enforcement, timeouts and
process-tree cancel. Planned: Responses-API adapters; Docker
container-per-agent for the implementation step.

## Phase 4.5 — Intelligence layer ✅ (v3)
Capability registry (12 axes, provenance) → explainable routing (RU/EN task
classification, role scores, distinct council roles) → measured reputation
(bounded multiplier) → AI council (solo/pair/council/full_council) → project
memory graph (reasoned edges, prompt digest) → context-integrity ledger
(evidence-backed claims) → deterministic change-risk scoring in the approval
flow.

## Phase 5 — UI & publication ◐
Done: approval dialog with full diff + verification evidence (+ risk); billing
labels incl. CLI subscriptions; CLI-session provider forms (discovered models,
per-model efforts, session status, no key fields); integration-branch publish;
one-click compare/PR URL; run history + event timeline; run recovery; safety
knobs + budget in settings; **headless `dispatcher run`** (full pipeline from
the terminal with a safe publication-decision policy); a **project-memory
browser** (filter/search decisions, bugs, lessons, approaches with their
reasoned links). Planned: run dashboard (task graph), richer diff viewer,
API-based draft PR (needs a GitHub token/App).

## Phase 6 — Docs, packaging, hardening 🚧
Done: README/ARCHITECTURE/SECURITY/PROVIDERS/AUDIT_REPORT/FINAL_REPORT;
Windows PyInstaller spec; GitHub Actions CI (offscreen pytest); 180-test suite
(no network, no real CLI spawns). Planned: broader tests, dead-code sweeps.

## Requires external credentials / provider support
- Draft-PR creation (GitHub token or App installation).
- Strict network isolation in the default sandbox (needs Docker/WSL2 or a Linux
  network namespace).
- Headless `dispatcher run` (full orchestration without GUI) — needs the
  orchestrator decoupled from QThread.
