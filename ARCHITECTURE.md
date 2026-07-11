# Architecture

Dispatcher is a PySide6 desktop app with a background orchestration engine.

## Layers

```
app/
  config.py            projects + dynamic provider profiles; secret-ref persistence
  security/            redaction, path-ownership policy, OS secret store
  providers/           provider-neutral messages/tools + adapters (anthropic, openai-compat)
  tools.py             filesystem/command tools, PathPolicy-enforced, sandbox-routed
  sandbox.py           command isolation backends (restricted / docker / unsafe_local)
  workspace.py         per-agent git worktrees + safe integration
  verification.py      detect + run project checks -> structured pass/fail/…
  orchestrator.py      the Run controller (QThread): plan → isolate → integrate → verify → approve → publish
  git_service.py       read-only git helpers used by the UI (status/clone)
  ui/                  main window, chat, agent panels, settings, approval dialog
```

## Run flow

```
task
  │  (Orchestrator.run on a QThread)
  ▼
prepare workspaces ── refuse if source tree dirty ── record base commit
  ▼
select team (solo | pair | full_council)  ──►  plan (lead assigns disjoint files)
  │                                             safe fallback on failure: single executor
  ▼
per implementer: own git worktree  +  PathPolicy(file scope)  +  sandbox
  │  run_agent tool loop (write/edit/read/run_command/finish)
  ▼
collect per-agent patch (staged diff vs base, from the isolated worktree)
  ▼
integration worktree ── apply patches sequentially (git apply --index) ── report conflicts
  ▼
optional cross-review of the integrated diff (independent reviewer)
  ▼
verification (detected/configured commands, run in the sandbox) → structured result
  ▼
emit report + diff + evidence ──►  AWAIT human approval  ◄── blocks the run
  ▼
approve → commit integration branch (+ push that branch only, if asked & allowed)
reject/cancel → discard all worktrees & temp branches; source untouched
```

## Key objects

- **Provider profile** (`config.providers[...]`): `transport`, `auth`,
  `billing_source`, `model`, `effort`, prices, capabilities.
- **RunWorkspaces** (`workspace.py`): base commit, per-agent `AgentWorkspace`
  (path, branch, `PathPolicy`), integration worktree, patch apply, cleanup.
- **Sandbox** (`sandbox.py`): `run(command, cancel) -> SandboxResult`.
- **VerificationResult** (`verification.py`): `status`, `checks[]`, `risk`,
  `blocks_publication()`.
- **Orchestrator signals**: `plan_ready`, `agent_role`, `agent_event`, `log`,
  `integration_ready`, `verification_ready`, `report_ready`, `awaiting_approval`,
  `run_finished`, `run_error`. Controls: `add_steering`, `disable_agent`,
  `cancel`, `approve(push)`, `reject`.

## Threading

The orchestrator is a `QThread`; implementation agents run as worker threads
inside it, each in its own worktree/sandbox. All UI updates happen on the UI
thread via queued signals. The run blocks on a threading `Event` at the approval
gate; the UI resolves it via `approve()/reject()`.

## Planned (not yet built)

A persistent `Run` domain object backed by SQLite with checkpoints and
restart-resume, a validated task DAG scheduler, and multi-round council review.
See `ROADMAP.md`.
