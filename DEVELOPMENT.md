# Development

## Setup

```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev,secrets]"                   # app + pytest + keyring
python run.py
```

Linux Qt system libs (headless/CI): `libegl1 libgl1 libxkbcommon0 libdbus-1-3`.

## Tests

```bash
python -m pytest -q
```

- No test hits a real API — provider transports are monkeypatched.
- Qt tests run offscreen (`QT_QPA_PLATFORM=offscreen`, set in `tests/conftest.py`).
- Tests that need git/POSIX shells skip cleanly where unavailable.

Suites:

| File | Covers |
|---|---|
| `test_baseline_engine.py` | tools, agent loop, message conversion, config |
| `test_security.py` | redaction, path policy, secret store |
| `test_workspace.py` | worktree isolation, safe integration, conflicts |
| `test_sandbox.py` | env-scrubbing, timeout, cancel-kills-tree, redaction, docker fallback |
| `test_verification.py` | command detection + status aggregation + blocking |
| `test_orchestrator_e2e.py` | full run: source untouched until approval, rejection, dirty block, verify-fail block |
| `test_ui_smoke.py` | offscreen window/dialog construction, no autoping |

## Code structure

See `ARCHITECTURE.md`. Guidelines:

- Keep provider-specific request/response shapes inside adapters; the
  orchestrator must not branch on provider name.
- Route file writes through a `PathPolicy` and commands through a sandbox.
- Never add `git add -A` against the source repo, branch resets, or force-push.
- Redact anything that could contain a secret before it reaches logs/UI/reports.
- Prefer typed dataclasses and structured results over passing raw JSON strings.

## Packaging (Windows)

```bash
pip install pyinstaller
pyinstaller build.spec     # -> dist/MultiAIControlCenter/
```

Build on Windows for a Windows binary (PyInstaller does not cross-compile).

## Conventions

- Atomic commits per concern; keep `pytest -q` green at each commit.
- Update `IMPLEMENTATION_STATUS.md` when a phase/criterion changes state.
- Document platform-specific limitations honestly (see `SECURITY.md`).
