# Architecture

## System boundary

The system has exactly two AI agents, both running inside the organization's approved GitHub
Copilot surface:

```text
Developer ──> code-review ──> collect-review-diff.py (local Git, read-only) ──> findings in chat

Reviewer  ──> review-mr ──┬─> collect-mr-evidence.py (GitLab REST, GET-only) ─> evidence bundle
                          └─> zereight/gitlab-mcp ─┬─> fallback/deep-dive reads
                                                   └─> new review threads + one summary (confirmed)

GitLab CI ──> tests/linters/SAST/Secret Detection ──> jobs + report artifacts
```

There is no model client, model-provider credential, or AI execution path in GitLab CI.

## Components and ownership

| Component | Responsibility |
|---|---|
| `code-review` agent | Collect and review committed, staged, unstaged, and untracked local changes |
| `review-mr` agent | Orchestrate MR identity, requirements, diff, pipeline, security, and posting |
| Collector scripts + `reviewlib` | Deterministic evidence gathering, path filters, token budgets, redacted secret pre-scan |
| `review-standards` skill | Shared changed-line quality rubric, confidence threshold, output contract |
| `requirements-traceability` skill | Resolve the primary story/work item and map acceptance criteria to evidence |
| `gitlab-review-evidence` skill | Untrusted-content boundary, pipeline/security verification, verdict and freshness |
| Project instruction files | Real path-scoped team conventions |
| GitLab MCP server | Constrained transport for GitLab reads and two review-comment writes |
| GitLab CI | Deterministic execution and security evidence |

## Local review flow

1. Resolve the configured/default target branch without fetching or modifying Git state.
2. Collect the merge-base diff plus staged, unstaged, and untracked changes.
3. Apply path filters, matching project instructions, and `review-standards`.
4. Report only high-confidence Critical/Important findings in chat.

The local agent has no GitLab, edit, network, dependency-installation, or external AI tools.

## MR review flow

1. Validate the explicit project and MR IID.
2. Run `collect-mr-evidence.py` once (GET-only GitLab REST): MR identity and diff refs, the
   explicit or unambiguous story, prior review markers, current-head pipeline and jobs, redacted
   per-scanner report summaries, a deterministic secret pre-scan, and filtered per-file diffs
   under the configured token budgets. MCP reads fill specific gaps (work items, unavailable
   files, head-pinned file context) or replace the script entirely when it cannot run.
3. Compare the complete evidence fingerprint with any prior version-3 marker.
4. Review changed lines and build the acceptance-criteria evidence matrix with per-file coverage
   accounting.
5. Determine a verdict from independent code, requirements, pipeline, and security dimensions.
6. Post verified inline positions, then one final summary and freshness marker (confirmed MCP
   writes only).

Incomplete diff, story, required evidence, or a present scanner job with a broken report yields
`Evidence incomplete`. An absent optional scanner is `Not evaluated` and does not make the run
partial.

## Requirement evidence

The agent accepts an explicit `story_project` + `story_iid`, or exactly one unambiguous primary
reference in the MR description. It does not infer a story from branch names, labels, titles, or
similarity.

Each explicit requirement or acceptance criterion is classified as `Met`, `Not met`,
`Not demonstrated`, or `Not applicable`, with changed code/test and pipeline evidence. Static
inspection is never represented as an executed test.

## Pipeline and security evidence

Pipeline, Secret Detection, and SAST each have one policy mode:

- `required` — absence or broken evidence prevents a complete decision.
- `optional` — absence is non-blocking and reported as `Not evaluated`; present evidence must still
  validate successfully.
- `disabled` — the control is not queried and is reported as `Disabled`.

The aggregate pipeline status and job/report results remain separate evidence:

- Every selected pipeline/report must belong to the current MR head.
- A present scanner job must complete successfully and produce its configured readable artifact,
  even in optional mode.
- Secret values are never copied into comments.
- Missing reports from present jobs, truncated artifacts, and failed/running jobs fail closed.

A green pipeline alone is not evidence of zero scanner findings. GitLab approval policies remain
the authoritative merge control where the organization's tier supports them.

## GitLab write boundary

`collect-mr-evidence.py` issues only HTTP GET requests against the organization's own GitLab
instance (`GITLAB_TOKEN` + `GITLAB_API_URL`); it cannot post, mutate, or contact any third-party
service, and it redacts secret values from scanner reports and its own pre-scan output.

For writes, the `review-mr` agent exposes only:

- `create_merge_request_thread`;
- `create_merge_request_note`.

The MCP server deny policy hides every other write, and both exposed writes require confirmation.
The agent can neither modify repository content nor approve, merge, close, relabel, assign, edit, or
resolve an MR.

MR data, source code, job output, artifacts, notes, and work-item text are all untrusted. Agent and
skill instructions explicitly prohibit treating embedded content as commands.

## Freshness

The version-3 marker fingerprints:

- MR head SHA;
- requirement reference and `updated_at`;
- pipeline mode, ID, and status;
- Secret Detection and SAST modes/statuses.

The agent refreshes MR, requirement, pipeline, and enabled scanner metadata before deciding a
review is current. Partial markers never suppress retries.
