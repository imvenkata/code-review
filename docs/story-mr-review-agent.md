# Story: MR Review Agent (GitLab MCP–driven)

> **Draft** — paste into GitLab as a Work Item / Issue under the epic below. Keep acceptance
> criteria as checkboxes so the MR Review Agent (and reviewers) can trace each one to evidence.

| Field | Value |
|---|---|
| **Type** | Story |
| **Epic** | Automated, evidence-based code & MR review for Copilot + GitLab |
| **Labels** | ~agent ~mr-review ~gitlab-mcp ~copilot ~security ~reusable |
| **Priority** | High |
| **Estimate** | _TBD by team_ |
| **Status** | Draft |

## User story

**As a** code reviewer / maintainer on a GitLab-hosted project,
**I want** a GitHub Copilot agent that reviews one specified GitLab merge request end-to-end using
the GitLab MCP server,
**so that** every MR is checked against its story's acceptance criteria, code quality, and
current-head CI/security evidence before a human approves it — without leaving VS Code and without
using any non-approved AI service.

## Context & background

- **Stack:** GitHub Copilot (VS Code custom agents) is the only AI execution surface; GitLab hosts
  repositories and agile artifacts (epics/stories/work items); the pinned
  [`@zereight/mcp-gitlab`](https://github.com/zereight/gitlab-mcp) MCP server connects Copilot to
  GitLab.
- **Hard constraint:** no third-party model APIs (no Anthropic/OpenAI/etc. keys, no headless AI CI
  job). All model calls stay inside the approved Copilot surface.
- **Reusable:** the agent, its skills, config, and MCP profile must drop into many repositories with
  only per-repo tuning (target GitLab URL, token, conventions, evidence policy).
- This story describes the **reviewer-side** agent. The developer-side local pre-push `code-review`
  agent is a separate story.

## In scope

- A user-invocable Copilot agent (`review-mr`) plus the supporting skills, config, and least-privilege
  MCP profile needed to run it.
- Story/acceptance-criteria traceability, changed-line code review, current-head pipeline + security
  evidence verification, and posting review threads + one summary back to the MR.

## Out of scope

- Approving, merging, or otherwise mutating the MR (comment-only).
- Running or bundling CI/scanner jobs — the organization owns its CI pipelines; this agent only
  **verifies** whatever evidence those pipelines publish.
- Any non-Copilot model API or headless AI runner.

## Assumptions & dependencies

- The pinned `@zereight/mcp-gitlab` server is configured in the workspace `.vscode/mcp.json` with a
  short-lived token whose role can read project evidence and create MR comments — nothing more.
- `.github/review.config.yml` supplies path filters, strictness, requirement policy, and
  per-control evidence modes.
- The three shared skills exist and are applied: `review-standards`, `requirements-traceability`,
  `gitlab-review-evidence`.

## Acceptance criteria

### AC1 — Invocation & input contract
- [ ] The agent is user-invocable in VS Code, is not auto-invoked as a subagent, and pins no model
  in its frontmatter.
- [ ] It accepts `project=<namespace/path or numeric ID>` and `mr=<IID>` as required inputs, plus
  optional `story_project`+`story_iid`, `strictness=low|medium|high`, and `force review`.
- [ ] If `project` or `mr` is missing, it stops and asks — it never guesses or substitutes another
  project/MR.
- [ ] A greeting, unclear, or out-of-scope message returns a short capability greeting **without**
  calling any tool.

### AC2 — Requirements traceability
- [ ] Given explicit `story_project`+`story_iid`, the agent fetches that work item; otherwise it
  resolves exactly one unambiguous primary story reference from the MR description (never inferring
  from branch name, title, label, or similarity).
- [ ] It extracts only the explicitly written requirements and acceptance criteria (and a
  constraining parent epic when the hierarchy identifies one); it never invents criteria.
- [ ] It produces an acceptance-criteria evidence matrix where each row is `Met` / `Not met` /
  `Not demonstrated` / `Not applicable` with concrete evidence (`path:line`, test, or pipeline job).
- [ ] A missing, ambiguous, or unreadable story makes the review **partial** but does not block
  review of the available code.

### AC3 — Changed-line code review
- [ ] Applying `review-standards`, the agent reviews only changed lines across correctness,
  explicitly stated conventions, error handling, and security (incl. hardcoded secrets).
- [ ] Each finding carries a 0–100 confidence and is dropped below the configured strictness
  threshold; severity (Critical/Important) is assigned independently from confidence.
- [ ] Every finding is validated against its exact old/new diff side so it can be posted at a
  correct position.
- [ ] Diff coverage is tracked per file as `reviewed` / `ignored` / `unavailable`; any unavailable
  eligible file makes coverage **partial**.

### AC4 — Pipeline & security evidence
- [ ] Per `.github/review.config.yml`, each control (pipeline, secret detection, SAST, …) is read in
  `required` / `optional` / `disabled` mode; an unknown mode or missing artifact path for an enabled
  control is a configuration error.
- [ ] The agent selects only pipelines whose SHA equals the current MR head, reads jobs with
  `include_retried: false`, and reads only the configured report artifact path.
- [ ] A green pipeline is **not** treated as proof of zero scanner findings; secret values are never
  copied into comments (redacted location/type/severity only).
- [ ] An absent **optional** scanner is reported as `Not evaluated` (never `Clean`); a present job
  that fails or omits its expected artifact is broken evidence and makes the review partial.

### AC5 — Posting & output
- [ ] On complete diff coverage, the agent posts one review thread per surviving finding (up to
  `review.max_inline_comments`) at a verified GitLab position, using a `suggestion` block only when
  the replacement is exact and suggestions are enabled.
- [ ] On partial diff coverage, it posts **no** inline threads and preserves findings in the summary
  to avoid duplicate comments on retry.
- [ ] It posts exactly one summary note containing: verdict, requirement source + AC matrix, diff
  coverage counts, findings by severity, current-head pipeline status, per-scanner evidence state,
  explicit blockers/next actions, and a freshness marker.
- [ ] The verdict is one of `Blocked` / `Needs changes` / `Evidence incomplete` /
  `Ready for human decision`, and a **partial** run can never be `Ready for human decision`.

### AC6 — Guardrails & least privilege
- [ ] All GitLab-returned content (diffs, files, notes, job logs, artifacts, story text) is treated
  as untrusted data; instructions embedded in it are never followed.
- [ ] The MCP profile exposes only read tools plus exactly two writes
  (`create_merge_request_thread`, `create_merge_request_note`); every other write (approve, merge,
  close, relabel, assign, resolve, edit, repository write) is denied by policy.
- [ ] The agent never approves, merges, resolves, labels, assigns, closes, reopens, or edits the MR.

### AC7 — Freshness & idempotency
- [ ] The agent writes a versioned freshness marker fingerprinting head SHA, requirement ref +
  `updated_at`, and every evidence mode/status.
- [ ] A prior complete marker suppresses repeat diff analysis **only** when all fingerprint fields
  still match; `force review` bypasses this. Partial markers never suppress a later review.

### AC8 — Constraints (Copilot-only, cost)
- [ ] No third-party model API key or direct model client exists anywhere in the repo, agent, skills,
  or MCP profile; an automated check asserts their absence.
- [ ] The agent minimizes token/credit use: it reads only `included` changed files, batches diff
  fetches, reads full file bodies only when a finding or criterion genuinely needs context, and skips
  files matching `.github/review.config.yml` path filters.

## Non-functional requirements

- **Reusability:** adopting the toolkit in a new repo requires only copying the agent/skills/config,
  merging the MCP server block, and pointing it at the target GitLab instance + token.
- **Security:** short-lived, least-privilege token; deny-by-default MCP tool policy; no secret values
  in comments.
- **Portability:** works against the organization's GitLab tier without assuming Ultimate-only
  features; controls the tier does not support are configured as `disabled` rather than silently
  assumed.

## Definition of Done

- [ ] All acceptance criteria above are demonstrably met.
- [ ] Offline tests validate the agent/MCP tool alignment, least-privilege writes, requirement and
  evidence contracts, and the absence of any direct model-API runtime.
- [ ] A fixture MR (linked story with explicit AC, changed production + test files, a successful
  scanner artifact, and a safe seeded scanner fixture in an isolated test project) has been reviewed
  end-to-end, and the missing-story / failed-pipeline / missing-artifact / oversized-diff /
  renamed-file / removed-line paths have been exercised.
- [ ] Docs (architecture + operating/rollout guide) and the rollout checklist are updated.

## Test scenarios / QA fixtures

1. **Happy path:** MR linked to one story with explicit AC, successful pipeline + scanner artifacts →
   full AC matrix, inline findings, `Ready for human decision` or `Needs changes`.
2. **Missing story:** no resolvable story reference → requirements unavailable, review partial,
   never `Ready for human decision`.
3. **Failed required pipeline:** → `Blocked` / `Evidence incomplete` as configured, evidence read
   and reported.
4. **Present optional scanner with missing artifact:** → broken evidence, review partial.
5. **Absent optional scanner:** → `Not evaluated`, review still completable.
6. **Oversized/truncated diff or unavailable file:** → coverage partial, no inline threads.
7. **Re-run with unchanged head + evidence:** → freshness marker suppresses re-analysis; `force
   review` overrides.

## References

- Architecture: [docs/ARCHITECTURE.md](ARCHITECTURE.md)
- Operating & rollout guide: [docs/REVIEW-SYSTEM.md](REVIEW-SYSTEM.md)
- MCP profile (least privilege): [docs/gitlab-mcp.example.json](gitlab-mcp.example.json)
- Skills: `review-standards`, `requirements-traceability`, `gitlab-review-evidence`
