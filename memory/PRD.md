# PRD — Copilot + GitLab Code/MR Review Agents Toolkit

## Original problem statement
Build two reusable GitHub Copilot custom agents for teams using GitLab (repos + agile) via
zereight/gitlab-mcp, with NO third-party model APIs (no Claude/OpenAI keys):
1. `code-review` — developer pre-push local diff review.
2. `review-mr` — reviewer MR agent: code quality + story/acceptance-criteria validation +
   CI pipeline status + security/password detection, combining skills, guardrails, instructions.
Goal: reusable across many projects. Latest session: re-assess readiness + fix gaps.

## Architecture
- `.github/agents/` — two Copilot agents (vscode target, no hardcoded models).
- `.github/skills/` — review-standards, requirements-traceability, gitlab-review-evidence.
- `.github/scripts/` — read-only collectors (`collect-review-diff.py`, `collect-mr-evidence.py`)
  + `reviewlib` (config parser, deterministic redacted secret scan).
- `review.config.yml` — strictness, path filters, token budgets, pipeline/scanner modes.
- `docs/gitlab-mcp.example.json` — pinned zereight/mcp-gitlab@2.1.28, deny-regex, 2 confirmed writes.
- `install.sh` + `install.manifest` — multi-repo install/update; manifest-scoped, lock-tracked,
  clones via `--repo` (supersedes the retired `scripts/adopt.py`).
- `tests/` — 45 unittest tests (incl. `test_install.py`); run: `python3 -m unittest discover -s tests`.

## Implemented (history)
- Earlier sessions: agents hardened, script-first evidence collection, token/credit controls,
  deterministic password detection, org CI declared out of scope, no hardcoded models.
- Jun 2026 (this session, readiness review):
  - Fixed merged-results pipeline blocker: `select_head_pipeline` prefers MR `head_pipeline`,
    falls back to head-SHA or `refs/merge-requests/<iid>/merge` ref match; mismatch annotated;
    skill + ARCHITECTURE updated.
  - Secret scan: added stripe-secret-key, npm-token, sendgrid-api-key, azure-account-key,
    openai-api-key rules (redacted).
  - Added `scripts/adopt.py` (syncs toolkit-owned paths only; keeps project-owned config/skills)
    + tests; README/REVIEW-SYSTEM updated.
  - All 41 tests pass.

## Verdict
Ready for multi-project use. Per-rollout: replace conventions placeholder, verify MCP tools/list
per GitLab version, set scanner modes to `required` where org enforces them.

## Backlog
- P2: Dependency-scanning evidence contract (tier-dependent, deliberately not assumed).
- P2: Optional git pre-push hook wiring for `secretscan.py --fail-on-findings`.
- P2: Verify pinned MCP version bump when a newer zereight release passes compatibility testing.
