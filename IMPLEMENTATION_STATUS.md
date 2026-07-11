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
| 3 | Isolated multi-agent orchestration (worktrees, DAG, review, integrate) | ◐ isolation+integration+review+plan-validation (incl. prefix/glob overlap)+safe fallback+mid-run disable/redistribute done; multi-round council planned |
| 4 | Provider/auth backends (transports, billing source, local vLLM, subscription) | ◐ registry+billing+local+API+retry/backoff done; official subscription agents + streaming planned |
| 5 | UI + publication (dashboard, diff, approvals, draft PR) | ◐ approval+diff+evidence+billing labels+safety knobs+run-recovery done; run dashboard + 1-click PR planned |
| 6 | Docs, packaging, hardening | ✅ docs + packaging + CI (GitHub Actions) done; more tests ongoing |

## Non-negotiable acceptance criteria (§18)

| # | Criterion | Status |
|---|---|---|
| 1 | Two implementation agents never share a writable worktree | ✅ |
| 2 | File ownership technically enforced | ✅ |
| 3 | Planner failure can't trigger unsafe full parallelism | ✅ |
| 4 | Host shell execution not the default | ◐ restricted worker (env-scrubbed, temp HOME, no secrets/network) default; Docker/WSL2 backend = interface + fallback |
| 5 | Provider secrets unavailable to worker commands | ✅ |
| 6 | Existing user changes can't be accidentally committed | ✅ |
| 7 | Integration doesn't use broad `git add -A` | ✅ |
| 8 | Existing branches not reset | ✅ |
| 9 | Auto-push disabled by default | ✅ |
| 10 | Direct target-branch publication not default | ✅ |
| 11 | Real verification stage before publication | ✅ |
| 12 | Failed/unknown verification blocks auto-publication | ✅ |
| 13 | Subscription vs direct-API backends separated | ✅ |
| 14 | No unofficial browser/cookie auth | ✅ (by design; documented) |
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
| 27 | Tests cover git/sandbox/orchestration/persistence/security | ✅ git/sandbox/security/providers/persistence/resume/e2e/planning/mid-run/publish-redaction (94 tests) |
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

## Where to continue next (large / credential-gated)

1. Multi-round council (§3.7): independent proposals → critique → cross-review
   across agents (the isolation/integration/verify/review primitives exist).
2. Adaptive escalation: start solo, escalate to pair/council on verification
   failure (currently `adaptive` ≈ pair).
3. Docker container-per-agent for the *implementation* step (commands already
   sandbox in Docker); official subscription-agent transports (Codex / Claude
   Agent SDK / xAI) behind the capability-gated boundary — need official auth.
4. Anthropic SSE streaming (thinking-block round-trip needs care); a richer run
   dashboard / task-graph view; API-based draft PR (needs a GitHub token/App).
