# Dispatcher

A desktop control plane (Python + PySide6) that coordinates AI coding agents
over your repositories with **isolation, technically-enforced file ownership,
sandboxed command execution, deterministic verification, and an explicit human
approval gate** before anything is committed or pushed.

Dispatcher can drive:

- **direct provider APIs** (Anthropic Messages, OpenAI Chat Completions, and any
  OpenAI-compatible endpoint);
- **local OpenAI-compatible models** (e.g. vLLM) with no API billing;
- one, two, or many agents — the provider/agent list is dynamic, not a fixed set.

It is Windows-first and also runs on Linux and macOS.

> This project began as a three-model "control center" prototype. It has been
> redesigned into a safety-first orchestration layer. The documentation below
> describes what the code actually does today; see `ROADMAP.md` and
> `IMPLEMENTATION_STATUS.md` for what is implemented vs planned.

---

## Safety model (why this is not "three bots editing one folder")

| Concern | What Dispatcher does |
|---|---|
| Shared writable repo | Each agent works in its **own git worktree** created from a recorded base commit. Agents never share a writable tree. |
| File ownership | A `PathPolicy` **technically enforces** each agent's allowed paths (deny/read-only globs, symlink & junction escape blocked). Out-of-scope writes return a structured tool error. |
| Command execution | Commands run in a **sandbox**: the host environment is *not* inherited (so provider keys/tokens are absent), a throwaway HOME, process-group termination, hard timeout, output redaction. Backends: restricted (default), Docker (one container per command, no socket, `--network none`, resource caps), or an opt-in, clearly-flagged unsafe-local mode. |
| Git safety | The source working tree is never modified. Runs are blocked if it is dirty. Accepted work is applied as reviewed patches into an **integration branch** with explicit staging — no `git add -A` on your repo, no branch reset, no force-push. `auto_push` defaults to **off**. |
| Verification | A real **verification stage** (detected or configured commands) runs before publication. `fail`/`unknown`/`cancelled` **blocks** automatic publication. |
| Human approval | You see the **full diff + verification evidence** and approve/reject. Publishing pushes only the integration branch — never directly into your target branch. |
| Recoverability | After any cancelled or failed run, the original repository is untouched and fully recoverable. |
| Secrets | API keys and tokens live in the **OS secret store** (keyring); only references are written to config. Logs/reports/UI are redacted. |

---

## Providers and billing

Each agent shows its provider, model, **transport**, **auth**, and **billing
source** so they are never conflated:

- **Direct API** — billed per-token by the provider (Anthropic, OpenAI, xAI).
- **Subscription** — e.g. **Grok on SuperGrok**. The programmatic call still uses
  an xAI API key, but the account is on a subscription pool; Dispatcher labels it
  `Grok · SuperGrok` and marks its quota **"not exposed by provider"** rather than
  inventing a number.
- **Local** — an OpenAI-compatible endpoint such as vLLM: no API billing.

**Rate limits, subscription quotas, and financial budgets are shown as separate
concepts** — a rate-limit header is never labeled as "remaining subscription
allowance". Rate-limit windows are read from real responses; a manual refresh is
available and is clearly marked as a billable request (no automatic paid pings).

Official **subscription-backed coding agents** (Codex / Claude Agent SDK / xAI
build agents) are represented behind a capability-gated transport boundary and
are currently **marked unavailable** — Dispatcher does not scrape cookies,
automate consumer web apps, or emulate unsupported auth. See `PROVIDERS.md`.

---

## Install & run

Requires Python 3.10+ and git.

```bash
pip install -e .            # or: pip install -r requirements.txt
pip install keyring        # recommended: store secrets in the OS keychain
python run.py
```

On Linux, Qt may need: `sudo apt install libegl1 libgl1 libxkbcommon0 libdbus-1-3`.
For the Docker sandbox backend, Docker Desktop / WSL2 is required (otherwise
Dispatcher falls back to the restricted local sandbox and says so).

### First run

1. **⚙ Settings** → add an API key for each provider you want (one is enough).
   Adjust model/effort as needed; defaults are factual, not "maxed" presets.
2. **＋ New project** → point at a local git repo and (optionally) its GitHub
   remote and branch. Optionally set per-project verification commands.
3. Type a task, send it, watch each agent work in its isolated worktree, then
   review the diff + verification and **approve or reject**.

---

## Windows / Docker / WSL2

- Windows is a first-class target; packaging via PyInstaller (`build.spec`).
- The Docker sandbox mounts only the agent worktree, no home, no docker socket,
  `--network none` by default, with CPU/memory/pids limits.
- WSL2 is a supported host for Docker Desktop.
- The restricted local sandbox does not hard-block network without a Linux
  network namespace; use the Docker backend for strict network isolation. See
  `SECURITY.md`.

---

## Tests

No test requires a paid API — providers are mocked.

```bash
pip install pytest
python -m pytest -q
```

Coverage includes security (redaction, path policy, secret store), git
workspace isolation & safe integration, sandbox env-scrubbing/timeout/cancel,
verification status, provider/billing model, UI construction, and an
end-to-end orchestrator run proving the source tree is untouched until approval.

---

## Documentation

- `ARCHITECTURE.md` — layers, run flow, modules.
- `SECURITY.md` — threat model, sandbox model, what is enforced vs best-effort.
- `PROVIDERS.md` — transports, billing sources, local vLLM, subscription agents.
- `MIGRATION.md` — config migration and secret migration behavior.
- `DEVELOPMENT.md` — setup, tests, packaging, code structure.
- `ROADMAP.md` / `IMPLEMENTATION_STATUS.md` — phases and current status.

## Known limitations (see ROADMAP.md)

- Persistent, restart-resumable runs (SQLite) are **not yet** implemented; a run
  lives for the app session.
- Multi-round "council" (proposal → critique → …) beyond the isolation +
  integration + review primitives is planned.
- Docker container-per-agent for the *implementation* step (not just commands)
  and official subscription-agent transports require further work / credentials.
