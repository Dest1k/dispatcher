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
rejects prefix/glob zone overlaps (not just exact-path collisions), and mid-run
**drop**: disabling a live agent cancels only it and reassigns its work to
exactly one remaining agent. Planned: a validated task DAG scheduler
(cycles/overlap/budget checks); multi-round council (independent proposals →
critique → plan → implement → cross-review → integrate → verify → synthesize);
adaptive escalation; mid-run **hot-join** of a new agent (currently declined
honestly — a safe late-join needs a fresh isolated worktree with a disjoint
ownership zone and re-planning, so the model participates from the next task).

## Phase 4 — Providers & auth ◐
Done: dynamic registry, transport/auth/billing model, direct-API + local
(vLLM) adapters, capability-gated `subscription_agent` boundary (unavailable),
transient retry/backoff (Retry-After), per-run budget enforcement + forecast,
SSE streaming for **both** OpenAI-compatible and Anthropic adapters (text
deltas; tool-use/thinking round-trip; cancel closes the connection), versioned
source-dated model capability catalog. Planned: official subscription-agent
adapters where supported; Responses-API adapters; Docker container-per-agent for
the implementation step.

## Phase 5 — UI & publication ◐
Done: approval dialog with full diff + verification evidence; billing/limit
labels; integration-branch publish; one-click compare/PR URL; run history +
event timeline; run recovery; safety knobs + budget in settings; secret-store
status. Planned: a richer run dashboard (task graph, artifacts), richer diff
viewer, API-based draft PR (needs a GitHub token/App).

## Phase 6 — Docs, packaging, hardening 🚧
Done: README/ARCHITECTURE/SECURITY/PROVIDERS/MIGRATION/DEVELOPMENT/ROADMAP;
Windows PyInstaller spec; GitHub Actions CI (offscreen pytest); 100-test suite.
Planned: broader tests, dead-code sweeps.

## Requires external credentials / provider support
- Official subscription-backed agent transports (Codex / Claude Agent SDK / xAI).
- Draft-PR creation (GitHub token or App installation).
- Strict network isolation in the default sandbox (needs Docker/WSL2 or a Linux
  network namespace).
