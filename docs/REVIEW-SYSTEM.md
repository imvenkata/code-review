# Review system operating guide

## Roles

### Developer: `code-review`

Use before pushing. It reviews the complete local change set and reports findings only in chat. It
does not contact GitLab, edit files, run arbitrary commands, or run tests.

### Reviewer: `review-mr`

Use for an open GitLab MR with:

```text
project=<namespace/path or numeric ID> mr=<IID>
```

Add `story_project` and `story_iid` when the MR description does not identify one primary story.
The reviewer agent posts review threads and a summary, but never takes approval or lifecycle
actions.

## Evidence contract

A complete MR review requires:

1. Complete eligible diff coverage.
2. A readable primary story/work item when `requirements.require_story` is true.
3. Every `required` pipeline/scanner control to be present and readable for the exact MR head.
4. Every present optional scanner job to complete and publish its configured report artifact.

Coverage and pass/fail are distinct. A review can be complete and `Blocked` because it fully read a
failed required pipeline or security report with findings. Missing required evidence, or broken
evidence from a present optional scanner, produces a partial marker and `Evidence incomplete`.
An absent optional scanner is reported as `Not evaluated` and does not block a complete review.

Configure each control in `review.config.yml` with:

- `required` — absence/failure blocks completeness;
- `optional` — inspect when present; absence is non-blocking;
- `disabled` — do not inspect.

## Evidence collector contract

`collect-mr-evidence.py` is the primary evidence path: one read-only run per review, using
`GITLAB_TOKEN` (or `GITLAB_PERSONAL_ACCESS_TOKEN`) and `GITLAB_API_URL` (or `CI_API_V4_URL`) from
the environment — never from the command line. It returns the `# mr-evidence v1` bundle and exits
non-zero with a clear message when the environment or `review.config.yml` is unusable, in which
case the agent uses the MCP reads below for the whole review. Keep terminal auto-approval off so
each collector run stays visible and confirmable.

## GitLab MCP contract

Use the pinned server configuration in `gitlab-mcp.example.json`.

Read tools:

- MR identity/diffs/notes: `get_merge_request`, `list_merge_request_changed_files`,
  `get_merge_request_file_diff`, `get_merge_request_notes`, `get_branch_diffs`;
- repository context: `get_file_contents`;
- requirements: `get_work_item`, `get_issue`;
- pipeline/security: `list_merge_request_pipelines`, `get_pipeline`, `list_pipeline_jobs`,
  `list_job_artifacts`, `get_job_artifact_file`.

Write tools:

- `create_merge_request_thread`;
- `create_merge_request_note`.

Do not add approval, merge, issue mutation, repository write, branch write, note edit, resolution,
or MR-update tools to this profile.

## Security scanning evidence

Company CI templates are organization-owned and out of scope for this toolkit; it ships no CI
jobs. The MR agent verifies whatever scanner evidence the organization's pipeline publishes:
configure each control's `mode` and `artifact` path in `review.config.yml` (the defaults are
GitLab's standard `gl-secret-detection-report.json` / `gl-sast-report.json`, which GitLab's own
templates and most org wrappers emit). When a scanner is required or present, the agent verifies
its job and artifact; it does not replace the scanner. The toolkit defaults both scanners to
`optional`; repositories with enforced controls should override their modes to `required`.

Independent of CI, both review features run a deterministic password/secret pre-scan
(`.github/scripts/reviewlib/secretscan.py`) over changed lines: structured token regexes (GitLab/
GitHub/AWS/Slack/Google tokens, private keys, URL and basic-auth credentials) plus heuristic
plaintext-password detection (credential-keyword assignments filtered by placeholder rules and
Shannon entropy). It runs on any GitLab tier, costs no model tokens, redacts every matched value,
and its candidates are verified by the agent before being reported. It complements — never
replaces — a required organization Secret Detection control. Tune or extend the rule set in one
place; both agents and any git hook (`--fail-on-findings`) pick it up.

Dependency scanning is tier/version dependent and is not silently assumed. When enabled for the
organization, add its required job/report contract explicitly to `review.config.yml` and
`gitlab-review-evidence`.

## Rollout checklist

1. Replace the placeholder coding-conventions file with enforceable project rules.
2. Confirm the GitLab instance version and licensing for work items and security features.
3. Validate the pinned MCP server's `tools/list` against the agent frontmatter.
4. Verify the token cannot merge or push and has only the minimum commenting role.
5. Disable global MCP/terminal auto-approval through enterprise policy where available.
6. Run a fixture MR containing:
   - one linked story with explicit acceptance criteria;
   - changed production and test files;
   - successful SAST and Secret Detection artifacts when the organization's pipeline provides
     them;
   - a safe seeded fake credential on a changed line to confirm the deterministic pre-scan flags
     and redacts it.
7. Exercise missing-story, failed-pipeline, missing-artifact, oversized-diff, renamed-file, and
   removed-line paths.
8. Confirm partial reviews never produce `Ready for human decision`.
9. Confirm no provider key or direct model client exists in repository or CI variables.
10. Run `collect-mr-evidence.py` by hand against the fixture MR and confirm one command returns
    the full bundle; then confirm a `review-mr` session performs one collector run, one review
    turn, and only the confirmed writes (see [COST-CONTROLS.md](COST-CONTROLS.md)).

## Distribution

Per-repository distribution is the most predictable:

- run `python3 scripts/adopt.py <target-repo>` from the toolkit checkout (or copy the agents,
  skills, collector scripts, and config manually); re-run it to sync toolkit updates — it
  overwrites only toolkit-owned paths and never a project's own config, skills, or instructions;
- keep project instructions project-owned;
- merge, never overwrite, the MCP configuration;
- sync toolkit updates through normal reviewed repository changes.

Use VS Code Chat customization diagnostics after every rollout to detect ignored or unavailable
tool names. VS Code ignores tools that are not available, so a loaded agent is not by itself proof
that its complete MCP contract is usable.
