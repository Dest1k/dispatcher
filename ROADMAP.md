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

## Phase 2 — Persistent run engine ⬜
- SQLite persistence for projects, runs, tasks, events, artifacts, usage,
  verification, approvals, with schema migrations.
- Explicit run state machine + checkpoints after every transition.
- Restart discovery and safe resume of incomplete runs.

## Phase 3 — Isolated multi-agent orchestration ◐
Done: per-agent worktrees, enforced path scope, sequential patch integration,
independent cross-review, safe single-executor fallback, reassign-to-one on
disable. Planned: a validated task DAG scheduler (cycles/overlap/ budget checks);
multi-round council (independent proposals → critique → plan → implement →
cross-review → integrate → verify → synthesize); adaptive escalation.

## Phase 4 — Providers & auth ◐
Done: dynamic registry, transport/auth/billing model, direct-API + local
(vLLM) adapters, capability-gated `subscription_agent` boundary (unavailable).
Planned: official subscription-agent adapters where supported; Responses-API
adapters; streaming + provider-native cancellation; a versioned, source-dated
model capability catalog; Docker container-per-agent for the implementation step.

## Phase 5 — UI & publication ◐
Done: approval dialog with full diff + verification evidence; billing/limit
labels; integration-branch publish. Planned: a run dashboard (task graph,
artifacts, per-run budget), richer diff viewer, one-click draft PR (needs a
GitHub token/App), resume controls.

## Phase 6 — Docs, packaging, hardening 🚧
Done: README/ARCHITECTURE/SECURITY/PROVIDERS/MIGRATION/DEVELOPMENT/ROADMAP;
Windows PyInstaller spec. Planned: CI, broader tests, dead-code sweeps.

## Requires external credentials / provider support
- Official subscription-backed agent transports (Codex / Claude Agent SDK / xAI).
- Draft-PR creation (GitHub token or App installation).
- Strict network isolation in the default sandbox (needs Docker/WSL2 or a Linux
  network namespace).
