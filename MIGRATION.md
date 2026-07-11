# Migration

## Config (v0.1 → v0.2)

Your existing `~/.multi_ai_control_center/config.json` is loaded and deep-merged
onto the new defaults, so **projects, chats and provider settings are
preserved**. New fields are added automatically:

- provider profiles gain `transport`, `auth`, `billing_source`,
  `subscription_tier`, `quota_source`;
- a `local` (vLLM) provider profile is added (disabled by default);
- orchestration gains `execution_mode`, `sandbox_mode`, `allow_network`,
  `require_verification`, `publish_default`; `auto_push` now defaults to
  **False** and `max_tool_iterations` to a safer value;
- projects gain `verify_commands`.

No projects or chat history are dropped. The provider list is dynamic — the
three original providers keep their ids (`anthropic`, `openai`, `xai`).

## Secret migration (plaintext → OS keychain)

Previously, API keys and GitHub tokens were stored as plaintext in `config.json`.
On the next save after upgrading:

1. Each plaintext `api_key` / `github_token` is written to the OS secret store
   (keyring), returning a reference.
2. The plaintext value is removed from `config.json`; only the reference
   (`api_key_ref` / `github_token_ref`) is persisted.
3. The in-memory value is retained for the running session so nothing breaks
   mid-run.

If `keyring` (or a working OS backend) is unavailable, secrets fall back to a
`0600` `secrets.json` in the config dir and the UI/`SecretStore.is_secure()`
report an insecure store. Install `keyring` for OS-backed storage.

**Safety:** migration never destroys the old value before the new store confirms
it can be read, and it does not create an extra plaintext backup of secrets.

## Reports

Run reports are now stored **outside** the target repository (under the config
dir's `reports/`), fixing the previous contradiction where the README claimed
reports were committed while `.gitignore` ignored them. Dispatcher no longer
writes report files into your project automatically.

## Behavior changes to be aware of

- Runs are **blocked on a dirty source tree** — commit or stash your work first.
- Nothing is pushed automatically; you approve a diff, and only the integration
  branch is pushed (never your target branch directly).
- Commands now run in a sandbox without your environment variables; a task that
  relied on host env vars or network may need the Docker backend or an explicit
  `allow_network`.
