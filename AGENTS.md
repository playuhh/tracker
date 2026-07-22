# Repository instructions

`README.md` is the human-facing overview, not an automatically loaded Codex
instruction source. Use these explicit routes instead of relying on links in
the README:

- Before collection, report, privacy, or deployment work, read
  `PROJECT_CONTEXT.md`; it defines the privacy boundary, data semantics,
  verification commands, and deployment runbook.
- For GitHub Actions work, also read the runbook named below before any GitHub
  command.

## GitHub Actions

Before running any `gh auth`, `gh workflow`, or `gh run` command, read
`docs/github-actions-troubleshooting.md` and follow its command order.

- A sandboxed `gh auth status` failure is inconclusive. Never claim that the
  credential is invalid until the documented environment-token check and a
  narrowly escalated read-only authentication check both fail.
- Never run credential-changing commands merely because the sandboxed check
  fails.
- A workflow dispatch runs code already present on the selected remote ref. If
  relevant changes are dirty, uncommitted, unpushed, or absent from that ref,
  do not present a dispatch as deploying those changes. Report the mismatch
  before dispatching and obtain any separate commit/push authorization needed.
- After dispatch, monitor both `run-scraper` and `deploy-report`, then verify the
  deployed Pages result described in `PROJECT_CONTEXT.md`.

## Required verification

For code changes, run `python3 -m unittest discover -q`. For report changes,
also rebuild with `python3 scraper.py --report-only` and run
`python3 privacy_audit.py`. Preserve all privacy rules in
`PROJECT_CONTEXT.md`.
