# Security & Threat Model

Dispatcher runs model-authored code and commands against your repositories. This
document states what is **technically enforced**, what is **best-effort**, and
what you remain responsible for.

## Trust boundary

Treat every model as **untrusted**: its tool calls, file writes, and shell
commands may be wrong, adversarial, or prompt-injected via repository content.
Dispatcher's job is to contain the blast radius.

## What is enforced

- **File ownership** (`app/security/pathpolicy.py`): every read/write path is
  resolved with `realpath` (collapsing symlinks and Windows junctions), confined
  to the agent's worktree, and checked against allow/deny/read-only globs. Secret
  files (`.env`, `*.pem`, `id_rsa*`, `.ssh/*`, `.git/*`, …) are denied globally.
  Violations become structured tool errors, not silent writes.
- **No secrets in command execution** (`app/sandbox.py`): the sandbox starts from
  an empty environment and adds only non-secret essentials (`PATH`, a throwaway
  `HOME`/`TMPDIR`, `LANG`). The host environment is **not** inherited, so
  `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `XAI_API_KEY`, `GITHUB_TOKEN`, SSH agent
  vars, cloud creds, etc. are absent inside commands. Provider keys stay in the
  controller process only.
- **Process containment**: commands run in their own process group (POSIX
  `setsid` / Windows new process group). Timeouts and cancellation terminate the
  **whole child tree** (`killpg` / `taskkill /T`), not just the top process.
- **Git safety** (`app/workspace.py`): the source working tree is never written
  to; runs are blocked on a dirty tree; agents commit only inside throwaway
  worktrees; integration uses `git apply --index` of reviewed patches (never
  `git add -A` on the repo); temporary branches are created from a recorded base
  and deleted on cleanup; the target branch is never reset or force-pushed;
  `auto_push` defaults to off; publication pushes only the integration branch.
- **Secret storage** (`app/security/secrets.py`): API keys and GitHub tokens are
  stored in the OS secret store (keyring). The plaintext config holds only a
  reference. Tokens are never embedded in remote URLs written to disk.
- **Redaction** (`app/security/redact.py`): known key/token shapes and registered
  secret values are masked in command output, reports, and UI event streams.

## CLI-native agents (`cli_session` transport)

Official CLI agents (Claude Code / Codex / Grok) edit files with their **own**
tools inside the agent's isolated worktree, so Dispatcher's write-time
`PathPolicy` hook does not see those writes. The containment story is:

- the worktree boundary still holds — the CLI's working directory is the
  throwaway worktree, never your source tree;
- Claude/Grok run under `acceptEdits` (file edits confined to the working
  directory; shell commands are auto-denied headless); Codex runs under its
  official `workspace-write` sandbox;
- **enforcement moves to the integration boundary**: the collected patch's
  paths are validated against the agent's `PathPolicy` (including the global
  secret-file deny list); a patch touching anything outside the agent's zone
  is rejected whole and reported;
- reasoning-only calls (planning/council/review) disable mutating tools and,
  for Codex, use the read-only sandbox;
- Dispatcher never reads, copies or stores the CLIs' session credentials — it
  only observes that the official client has a login and spawns it the way the
  user would.

Residual risk (accepted, documented): a CLI agent could run `git` inside its
own worktree via its native tooling (Codex sandbox permits commands). Branch
state of that throwaway worktree is irrelevant to integration — patches are
collected as `diff --cached` against the recorded base and the source tree
stays untouched; worktree checkouts of user branches are prevented by git
itself (a branch checked out elsewhere cannot be checked out again).

## Best-effort (not a hard guarantee in the default backend)

- **Network isolation** in the *restricted local* sandbox: applied only when a
  Linux network namespace can be created (`unshare -n`), which typically needs
  privileges. When it can't, network is not hard-blocked and the result is
  labeled `host (не изолирована)`. **For strict network isolation, use the Docker
  backend** (`--network none`, mounts only the worktree, no docker socket, CPU/
  memory/pid limits). Set `sandbox_mode: "docker"` in orchestration settings.
- **Keyring availability**: if `keyring` (or a working OS backend) is
  unavailable, secrets fall back to a `0600` file under the config dir and
  `SecretStore.is_secure()` returns `False`. Prefer installing `keyring`.

## Unsafe mode

`sandbox_mode: "unsafe_local"` runs commands with the full host environment in
the worktree — the old, dangerous behavior. It is **off by default**, must be
explicitly selected, and still redacts output. Do not use it with untrusted
tasks.

## What you remain responsible for

- Reviewing the diff and verification evidence before approving.
- Choosing the Docker backend when running untrusted code that must not reach
  the network or the host.
- Scoping provider API keys and GitHub tokens to least privilege.
- Keeping your OS account and disk secured (the config dir is user-readable).

## Reporting

This is a local desktop tool with no server component. If you find a way for a
model-authored command or path to escape the worktree, read a secret, or mutate
the source tree, please open an issue describing the reproduction.
