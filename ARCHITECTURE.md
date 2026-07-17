# Architecture

Dispatcher is a PySide6 desktop app + `dispatcher` console CLI with a
background orchestration engine and an intelligence layer.

## Layers

```
app/
  config.py            projects + dynamic provider profiles; secret-ref persistence
  cliagents.py         discovery of official CLIs: install/auth/models/readiness
  planner.py           phased task-planning engine (analyze→…→security→review)
  cli.py               `dispatcher` entry: doctor, run (headless orchestration),
                       plan, council, route, capabilities, reputation, memory, gui
  security/            redaction, path-ownership policy, OS secret store
  providers/           provider-neutral messages/tools + adapters:
                       anthropic (REST), openai_compat (REST/SSE),
                       cli_agent (official CLI sessions: claude/codex/grok)
  capabilities.py      model capability registry (12 skill axes, provenance)
  routing.py           explainable task→role→provider routing
  reputation.py        measured per-agent outcomes -> bounded multiplier
  council.py           AI council: solo | pair | council | full_council
  memory_graph.py      per-project knowledge graph (SQLite): decisions/bugs/
                       lessons + reasoned edges; context_pack() for prompts
  ledger.py            context-integrity ledger: evidence-backed claims
  risk.py              deterministic change-risk scoring for the diff
  tools.py             filesystem/command tools, PathPolicy-enforced, sandboxed
  sandbox.py           command isolation backends (restricted / docker / unsafe_local)
  workspace.py         per-agent git worktrees + safe integration + patch_paths
  verification.py      detect + run project checks -> structured pass/fail/…
  orchestrator.py      the Run controller (QThread)
  persistence.py       SQLite run store (state machine, events, usage, recovery)
  ui/                  main window, chat, agent panels, settings, approval dialog
```

## Run flow

```
task
  │  (Orchestrator.run on a QThread)
  ▼
prepare workspaces ── refuse if source tree dirty ── record base commit
  │        ledger: repo / branch / commit / permissions
  ▼
memory_graph.context_pack(project, task) ──► injected into prompts
  ▼
optional deliberation (deliberate): council agrees an approach BEFORE writes —
  architect proposal → red-team attack → feasibility → synthesis (reasoning-only)
  → approach injected into planning + implementer context, recorded in memory
  ▼
select team (solo | pair | adaptive | council/full_council) ──► lead plans (JSON zones)
  │            optional: red-team critique of the plan (council_planning)
  │            plan validation (zone overlaps) ── safe fallback: single executor
  ▼            ┌─ adaptive: solo attempt; on verification FAIL, escalate to a
  │            │  pair in fresh worktrees, feeding the failure back, then retry
per implementer: own git worktree + PathPolicy + sandbox
  │   API providers → run_agent tool loop (write/edit/read/run_command/finish)
  │   CLI providers → native_run: the official CLI edits inside the worktree
  ▼
collect per-agent patch ── validate patch paths vs PathPolicy (CLI-native
  │                        enforcement boundary; violations reject the patch)
  ▼
integration worktree ── apply patches sequentially (git apply --index) ── conflicts
  ▼
risk.assess_change(diff) ──► explainable risk factors
  ▼
optional cross-review (CLI reviewers read the integration tree read-only)
  ▼
verification (real commands in sandbox) → structured result → ledger: tests
  │        reputation.record_verification(per implementer)
  ▼
report = agents + review + integration + RISK + verification + LEDGER EVIDENCE
  ▼
AWAIT human approval ◄── blocks the run
  │ approve → commit integration branch (+push only that branch if asked)
  │           reputation: approved; memory: decision node
  │ reject  → discard worktrees; reputation: rejected; memory: lesson node
```

## Intelligence data flow

```
capabilities (priors, provenance)
      │            reputation (measured, bounded ×0.85..1.15)
      └────────┬───┘
               ▼
routing.route(task) ── explainable RoleAssignments ── council casting
               ▼
council: architect → red_team → developer → synthesis (full_council)
               ▼
memory_graph: task/decision/lesson/bug nodes + reasoned edges
ledger: evidence per run → report section + claim checks
```

## Key objects

- **Provider profile** (`config.providers[...]`): `kind` (`anthropic` |
  `openai` | `cli`), `transport`, `auth` (`api_key` | `none` | `cli_session`),
  `billing_source`, `model`, `effort`, `cli_flavor`, `cli_native`,
  `cli_timeout`, prices (API only).
- **CLIStatus** (`cliagents.py`): installed/version/authenticated/models/
  efforts/ready — the `dispatcher doctor` payload.
- **RoutingDecision** (`routing.py`): assignments with scores + explanations.
- **CouncilResult** (`council.py`): opinions, synthesis, decision_path.
- **RunWorkspaces** (`workspace.py`): base commit, per-agent worktrees,
  integration, `patch_paths` for boundary validation.
- **RiskAssessment** (`risk.py`), **ContextLedger** (`ledger.py`),
  **MemoryGraph** (`memory_graph.py`), **ReputationStore** (`reputation.py`).
- **Orchestrator signals**: `deliberation_ready`, `plan_ready`, `agent_role`,
  `agent_event`, `log`, `integration_ready` (+change_risk),
  `verification_ready`, `report_ready`, `awaiting_approval` (+change_risk),
  `run_finished`, `run_error`.

## Threading

The orchestrator is a `QThread`; implementation agents run as worker threads,
each in its own worktree/sandbox; CLI subprocesses are killed as process trees
on cancel/timeout. Council (`council.py`) is pure Python — usable headless.
All UI updates happen on the UI thread via queued signals. The run blocks on a
threading `Event` at the approval gate.
