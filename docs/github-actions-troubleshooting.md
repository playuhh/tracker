# GitHub Actions troubleshooting

## `gh auth status` reports an invalid token inside Codex

Do not immediately run `gh auth login`, `gh auth refresh`, or `gh auth logout`.
In Codex's default `workspace-write` sandbox, command network access is disabled
unless explicitly permitted. On macOS, a sandboxed process may also be unable to
use the credential stored in the system keyring. Because `gh auth status` tests
the account against GitHub, either restriction can look like an invalid token.

Use this order:

1. Check only whether a token variable is present; never print its value:

   ```bash
   env | sed 's/=.*//' | grep -E '^(GH|GITHUB)_'
   ```

   `GH_TOKEN` and then `GITHUB_TOKEN` take precedence over credentials saved by
   `gh`. If one is unexpectedly present, retry without the injected variables:

   ```bash
   env -u GH_TOKEN -u GITHUB_TOKEN \
     -u GH_ENTERPRISE_TOKEN -u GITHUB_ENTERPRISE_TOKEN \
     gh auth status -h github.com
   ```

2. If the sandboxed check still says the token is invalid, run the same
   read-only command outside the sandbox using a narrowly scoped Codex
   escalation. Do not change credentials first.

3. Interpret the two results:

   - sandbox fails, escalated command succeeds: authentication is healthy; use
     scoped escalation for subsequent `gh` network commands;
   - both fail: investigate the stored credential, account, host, and scopes;
     only then consider reauthentication;
   - an environment-token check fails but the stored keyring credential works
     after unsetting it: fix the injected environment variable instead of the
     keyring login.

4. A healthy status for this repository should show the expected account and
   include `repo` and `workflow` scopes. Never use `--show-token` in logs.

Official references:

- [Codex agent approvals, sandboxing, and network access](https://learn.chatgpt.com/docs/agent-approvals-security)
- [GitHub CLI environment-variable precedence](https://cli.github.com/manual/gh_help_environment)
- [`gh auth status`](https://cli.github.com/manual/gh_auth_status)

## Manually test the tracker workflow

The workflow must exist on the default branch and include `workflow_dispatch`.
Trigger and monitor it with:

```bash
gh workflow run scraper.yml --ref main
gh run watch RUN_ID --compact --exit-status
```

Capture the run URL returned by the first command. If the run fails, inspect the
failed steps before changing code:

```bash
gh run view RUN_ID --json status,conclusion,headSha,url
gh run view RUN_ID --log-failed
```

Run these GitHub network commands with scoped sandbox escalation when Codex's
current command sandbox has no network or keyring access. A successful run must
finish both `run-scraper` and `deploy-report`.

Official references:

- [`gh workflow run`](https://cli.github.com/manual/gh_workflow_run)
- [Manually running a GitHub Actions workflow](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)
