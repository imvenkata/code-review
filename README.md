# Copilot + GitLab review agents

Two GitHub Copilot custom agents for teams whose repositories and agile work live in GitLab:

- **`code-review`** — developer pre-push review of the complete local change set. Read-only and
  local; findings stay in chat.
- **`review-mr`** — reviewer workflow for one GitLab MR. It traces the MR to a story/work item,
  evaluates acceptance criteria, reviews changed code, verifies current-head pipeline and security
  report evidence, and posts review threads plus one summary.

The only AI execution surface is the organization's approved GitHub Copilot environment. This
toolkit does not call any model API, run a headless AI CI job, or require a model-provider key.
GitLab access is through pinned [`@zereight/mcp-gitlab`](https://github.com/zereight/gitlab-mcp).

Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · operating and rollout guide:
[docs/REVIEW-SYSTEM.md](docs/REVIEW-SYSTEM.md).

## Components

| Path | Purpose |
|---|---|
| `.github/agents/code-review.agent.md` | Local developer review agent |
| `.github/agents/review-mr.agent.md` | Evidence-based GitLab MR review agent |
| `.github/skills/review-standards/SKILL.md` | Shared changed-line review rubric |
| `.github/skills/requirements-traceability/SKILL.md` | Story/epic and acceptance-criteria evidence |
| `.github/skills/gitlab-review-evidence/SKILL.md` | CI/security evidence, trust boundaries, verdicts, freshness |
| `.github/scripts/collect-review-diff.py` | Read-only complete local-diff collector |
| `.github/instructions/*.instructions.md` | Project-owned, path-scoped coding conventions |
| `review.config.yml` | Path filters, strictness, evidence requirements, comment limits |
| `docs/gitlab-mcp.example.json` | Pinned, least-privilege VS Code MCP configuration |
| `ci/security-scanning.gitlab-ci.yml` | GitLab Secret Detection and SAST templates |

There is intentionally no AI review job in `ci/`.

## Adopt in a repository

1. Copy `.github/agents/`, `.github/skills/`, `.github/scripts/`, `review.config.yml`, and the
   GitLab-native security template.
2. Replace or remove the placeholder `.github/instructions/conventions.instructions.md`.
3. Merge the `gitlab-review` server from `docs/gitlab-mcp.example.json` into `.vscode/mcp.json`.
   Keep the package pin and tool policy until a newer version passes compatibility testing.
4. Point `GITLAB_API_URL` at your GitLab instance and use a short-lived token with the minimum role
   needed to read project evidence and create MR comments.
5. Include `/ci/security-scanning.gitlab-ci.yml` from the target repository's `.gitlab-ci.yml`.
6. Tune `requirements` and `security` in `review.config.yml` to the GitLab tier and controls the
   organization actually enforces.
7. In VS Code Chat diagnostics, verify both agents, all three skills, and every namespaced MCP tool
   load without errors.

Do not overwrite a repository's existing `.vscode/mcp.json`, `.gitlab-ci.yml`, or
`.github/copilot-instructions.md`.

## Use

Select `code-review` for local changes.

Select `review-mr` with:

```text
project=group/service mr=482
```

When the MR description does not contain exactly one unambiguous primary story reference, provide:

```text
project=group/service mr=482 story_project=group/service story_iid=731
```

Both agents are scope-locked. Greetings, unclear prompts, and unrelated questions return a short
capability greeting without calling tools; they do not act as general-purpose chat agents.

The MR summary reports:

- requirement source and acceptance-criteria matrix;
- reviewed, ignored, and unavailable diff coverage;
- Critical/Important changed-line findings;
- current-head pipeline policy and job status;
- per-scanner policy plus redacted SAST and Secret Detection evidence (`Not evaluated` when an
  optional scanner is absent);
- one verdict: `Blocked`, `Needs changes`, `Evidence incomplete`, or
  `Ready for human decision`.

The agent never approves, merges, resolves, labels, assigns, closes, or edits the MR. Human approval
and merge enforcement remain in GitLab.

Pipeline, Secret Detection, and SAST controls use `required`, `optional`, or `disabled` modes in
`review.config.yml`. Optional controls are inspected when present; absence is non-blocking but is
never reported as clean.

## Validate this toolkit

```bash
PYTHONPYCACHEPREFIX=/tmp/code-review-pycache python3 -m unittest discover -s tests -v
python3 -m json.tool docs/gitlab-mcp.example.json >/dev/null
```

The tests validate local diff collection, agent/MCP tool alignment, least-privilege writes,
requirements/security evidence contracts, and the absence of a direct model API runtime.
