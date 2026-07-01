# Architecture — Copilot + GitLab Code Review Toolkit

The complete design reference: components, how each feature works end to end, the capability
ownership model, cross-cutting mechanisms, and the integration/data flows. For adoption steps see
[../README.md](../README.md); for deployment specifics and CodeRabbit parity see
[REVIEW-SYSTEM.md](REVIEW-SYSTEM.md).

## Contents

1. [System overview](#1-system-overview)
2. [Components](#2-components)
3. [The shared brain (review-standards)](#3-the-shared-brain-review-standards)
4. [Feature flows](#4-feature-flows)
5. [Capabilities & ownership (defense in depth)](#5-capabilities--ownership-defense-in-depth)
6. [Cross-cutting mechanisms](#6-cross-cutting-mechanisms)
7. [Integration & data flow](#7-integration--data-flow)
8. [Configuration surfaces](#8-configuration-surfaces)
9. [Security & permissions model](#9-security--permissions-model)
10. [Deployment & distribution](#10-deployment--distribution)
11. [Boundaries / non-goals](#11-boundaries--non-goals)
12. [Extension points](#12-extension-points)

---

## 1. System overview

The toolkit reproduces Claude Code's two-way split — fast **local** review + authoritative **PR/MR**
review — for teams on **GitHub Copilot (VS Code)** and **GitLab**, with an optional headless **CI**
gate. All three entry points apply **one** review "brain" so behavior never drifts.

```
 ENTRY POINTS               SHARED LOGIC                 INTEGRATIONS            TARGET
 ------------               ------------                 ------------            ------

 code-review  ─┐
 (IDE · local) │
               │
 review-mr  ───┼──▶  review-standards skill  ──┬──▶  zereight/gitlab-mcp ──▶  GitLab
 (IDE · MR)    │     (rubric · 0–100 score ·   │      (read · notes ·           (MR notes,
               │      false-positive list ·    │       threads · actions ·      inline threads,
 ci/review.py ─┘      security lens ·          │       pipeline status)         approvals,
 (CI · headless)      output contract)         │                                pipeline)
                                               └──▶  Anthropic API  ──────────▶ GitLab REST
                                                     (prompt cache + JSON)      (from CI job)
```

- **IDE agents** talk to GitLab through the **gitlab-mcp** server (the developer's own token).
- **CI runner** talks to the **Anthropic API** (central key, prompt caching) and posts via the
  **GitLab REST API** (a bot token).
- The **shared brain** is a portable Agent Skill applied by the agents and read verbatim by the
  runner as its cached system prompt.

Alongside the LLM review sits a **deterministic** layer — GitLab Secret Detection + SAST and the
pipeline status — so no LLM is asked to be a scanner (see §5).

---

## 2. Components

| Component | File(s) | Responsibility |
|---|---|---|
| **Shared brain** (Agent Skill) | `.github/skills/review-standards/SKILL.md` | The review rubric, 0–100 confidence scoring, false-positive exclusions, security lens, and output contract. Applied by both agents; read verbatim by the CI runner. Portable (Copilot / Claude Code / Cursor / Codex). |
| **Feature 1 — local review** (agent) | `.github/agents/code-review.agent.md` | Reviews the working-tree / branch diff before push; reports in chat; no network. |
| **Local diff collector** | `.github/scripts/collect-review-diff.py` | Read-only base resolution and complete committed/staged/unstaged/untracked patch collection for Feature 1. |
| **Feature 2 — MR review** (agent) | `.github/agents/review-mr.agent.md` | Reviews a GitLab MR, posts a summary note + inline threads, and performs reviewer actions (approve/reply/resolve/update) from chat with a pipeline gate. |
| **CI runner** (headless) | `ci/review.py` | Deterministic eligibility gate → diff fetch → one Anthropic call (cached + structured output) → confidence/anchor filter → post summary + inline threads. |
| **AI-review CI template** | `ci/ai-review.gitlab-ci.yml` | Includable job that runs the runner on `merge_request_event` (advisory, `stage: .post`). |
| **Security scanning template** | `ci/security-scanning.gitlab-ci.yml` | Includable GitLab **Secret Detection + SAST** — the deterministic secret/vuln control. |
| **Config-as-code** | `review.config.yml` | Path filters, posting limits, strictness default, toggles, and the summary footer. |
| **Coding conventions** (project-owned example) | `.github/instructions/*.instructions.md` | Path-scoped rules the reviewer enforces (`applyTo` globs). Replace with the project's real rules. |
| **MCP server config** (example) | `docs/gitlab-mcp.example.json` | The trimmed gitlab-mcp server block to merge into the developer's `.vscode/mcp.json`. |

Not shipped (project/environment-owned; would clobber): `.github/copilot-instructions.md`, a root
`.gitlab-ci.yml`, a live `.vscode/mcp.json`. See §10.

---

## 3. The shared brain (review-standards)

A single **Agent Skill** holds all review logic so the two agents stay thin and the three entry
points never diverge.

- **Mandate:** review only the changed lines; optimize for signal; stay silent when unsure.
- **Four lenses:** (1) correctness/logic bugs, (2) convention violations from the project's
  instruction files, (3) error handling / silent failures, (4) security — authz/authn, injection,
  SSRF, path traversal, unsafe deserialization, boundary validation, and **hardcoded secrets** (as a
  *second net* behind Secret Detection/SAST).
- **False-positive exclusions:** pre-existing issues, anything a linter/type-checker/formatter/
  compiler or the CI security scanners would catch, pedantic nitpicks, style/coverage unless a
  convention requires it.
- **Confidence scoring (0–100):** every candidate is scored; the default threshold is **80** (tunable
  per run / in `review.config.yml`). Below threshold → dropped.
- **Impact severity:** Critical/Important is assigned from production impact independently of the
  confidence score.
- **Output contract:** one-line description, `file:line`, the rule or a concrete failure scenario, a
  concrete fix, and a GitLab ` ```suggestion ` block when the exact fix is proposable.

Progressive disclosure keeps it cheap: only the skill's *description* sits in context; the body loads
when a review actually runs.

---

## 4. Feature flows

### 4.1 `code-review` — local diff (IDE, pre-push)

Fast, cheap, no network, no posting. Model is whatever the developer picked in the chat dropdown.

```
developer ──▶ code-review agent
                 │ 1. collect-review-diff.py → configured/default target merge-base
                 │    + committed/staged/unstaged/untracked changes
                 │ 2. apply review-standards skill + matching instructions + path filters
                 │ 3. review changed lines across the 4 lenses
                 │ 4. confidence filter + independent impact severity
                 └─▶ 5. print findings in chat, grouped by severity (or "No issues found")
```

### 4.2 `review-mr` — GitLab MR (IDE, interactive)

Two phases: an automated review pass, then reviewer-driven actions — all in the chat window.

```
reviewer ──▶ review-mr agent            (requires: project ID + MR IID — else it asks and stops)
   │
   │  REVIEW PASS (one agent pass; the internal tool loop is free)
   │   0. get_merge_request → eligibility gate (draft / bot / trivial → stop) + capture diff_refs, head sha
   │   1. get_merge_request_notes → find complete source=ide marker
   │        • marker == head  → "up to date", stop (unless force review)
   │        • older marker    → use only a small, demonstrably complete delta
   │   2. inventory files + get_merge_request_file_diff batches → coverage accounting
   │        • incomplete delta/file → full fallback or partial review
   │   3. apply review-standards (4 lenses) on changed lines only
   │   4. confidence filter; impact severity; validate old/new diff side
   │   5. list_merge_request_pipelines → pipeline status for head
   │   6. create_merge_request_thread × N; summary last with complete/partial marker
   │
   └─ REVIEWER ACTIONS (on explicit request, from chat)
       • approve / unapprove  (approve_merge_request)   ── PIPELINE GATE: refuse over red/pending unless "approve anyway"
       • reply / edit note    (create_merge_request_discussion_note / update_merge_request_note)
       • resolve thread       (resolve_merge_request_thread)
       • labels / assignees   (update_merge_request)
       (merge is intentionally NOT enabled)
```

Actions run **as the developer's token** — the review agent is a hands-free front end to GitLab, not
an autonomous bot.

### 4.3 CI auto-review — headless (GitLab pipeline)

Runs on every MR via the includable template; central Anthropic key with prompt caching; posts via a
bot token. Never self-approves.

```
merge_request_event ──▶ pipeline (stage .post) ──▶ ci/review.py
   0. deterministic gate: state/draft/bot-authored/already-reviewed head  → skip (exit 0)
   1. GET /merge_requests/:iid → diff_refs ;  GET /diffs (paginated) → changed files
   2. apply path filters ; parse hunks → old/new changed-line sets ; account for unavailable files
   3. Anthropic messages.create:
        • system = review-standards body  [cache_control: ephemeral]      ← prompt cache
        • output_config.format = JSON schema (findings[] + summary)        ← structured output
        • thinking/effort sent ONLY for reasoning models (Haiku etc. omit) ← model-safe
   4. drop findings < threshold ; validate exact old/new side and changed line
   5. POST inline discussions (both paths + old_line/new_line), then summary
      • complete coverage → state=complete marker
      • unavailable diff → state=partial marker; no merge-safety conclusion
      • inline delivery failure → preserve the full finding in the summary
```

Failure posture: `allow_failure: true` — the reviewer never breaks a developer's pipeline; missing
inputs/tokens fail loudly with a clear message rather than a stack trace.

---

## 5. Capabilities & ownership (defense in depth)

The system is deliberately **layered** — each concern is owned by the tool that is most reliable (and
cheapest) at it. No LLM is asked to be a scanner.

| Concern | Owner | Notes |
|---|---|---|
| Logic / correctness bugs | **LLM reviewer** | The layer only it can do — semantic, contextual. |
| Convention adherence | **LLM reviewer** | Only rules stated verbatim in the project's instruction files. |
| Error handling / silent failures | **LLM reviewer** | Empty catches, swallowed errors, unsafe fallbacks. |
| Security — authz / business logic / injection | **LLM reviewer** | Contextual issues a scanner can't see. |
| Secrets / credentials | **GitLab Secret Detection** (primary) + LLM (second net) | Deterministic scanner is the control; LLM flags obvious leaks it sees. |
| Known vulnerabilities | **GitLab SAST** | Semgrep-based analyzers; results in artifacts/MR widget. |
| CI green before merge | **Pipeline gate** | `review-mr` reads status and won't approve over red/pending. |
| Style / format / duplication / coverage | **Linters** | LLM explicitly skips these. |

> Tier note: the SAST / Secret-Detection **jobs** run on any GitLab tier (JSON reports as artifacts);
> the rich MR **security widget** and merge-approval gating on findings require GitLab **Ultimate**.

---

## 6. Cross-cutting mechanisms

**Source/state review markers.** Summaries embed
`<!-- ai-review source=<ide|ci> version=1 state=<complete|partial> head=<sha> -->`. IDE and CI only
deduplicate against their own source, and only a supported complete marker suppresses work. The IDE
may use a small old-head-to-new-head delta when it is demonstrably complete; otherwise it performs
the full batched review. Partial runs remain retryable.

**Confidence filter + anchor validation.** Findings below the strictness threshold are dropped; every
inline finding must map to an exact changed line on the old or new diff side. Impact severity is
assigned separately from confidence. This prevents a high-confidence low-impact issue from being
misclassified as Critical.

**Token / credit efficiency (design constraint).**
- IDE agents don't pin a model → run on **included/free** models (0 credits); the free agent-mode tool
  loop means one invocation ≈ one billable prompt regardless of how many MCP calls it makes.
- Built-in Copilot "code review" (multiplier 13, GitHub-only) is avoided entirely.
- **Diff-only** reading; **eligibility gate** + **complete source marker** make skips inexpensive.
- **Restricted MCP surface:** a dedicated pinned server hides non-review writes and protects
  sensitive actions with confirmation; the agent also has a namespaced allow-list.
- **Confidence ≥80** cuts output tokens and human re-review round-trips.

**Prompt caching (CI).** The runner puts the `review-standards` body in a cached system block; on a
busy repo the identical instruction prefix is reused across MRs. (Caveat: Opus needs a ≥4096-token
stable prefix to cache — below that, model choice is the bigger lever. The runner prints `cache_read`.)

**Structured output (CI).** The Anthropic call constrains the response to a JSON schema
(`findings[]` + `summary`), so parsing is guaranteed; `thinking`/`effort` are sent only for models
that support them, so switching `REVIEW_MODEL` to a cheaper model can't 400.

---

## 7. Integration & data flow

**gitlab-mcp (IDE path).** A pinned `@zereight/mcp-gitlab` process named `gitlab-review` bridges
Copilot to GitLab. Server-side deny/confirmation policies are the primary boundary; every allowed
write requires `_confirmed: true`, and the agent's
namespaced tool allow-list is defense in depth. Tools used by `review-mr`:

- *Read:* `get_merge_request`, `list_merge_request_changed_files`, `get_merge_request_file_diff`,
  `get_file_contents`, `get_merge_request_notes`, `mr_discussions`, `get_branch_diffs`,
  `get_merge_request_approval_state`, `list_merge_request_pipelines`, `get_pipeline`.
- *Write (review):* `create_note`, `create_merge_request_thread`.
- *Reviewer actions:* `approve_merge_request`, `unapprove_merge_request`,
  `create_merge_request_discussion_note`, `update_merge_request_note`,
  `resolve_merge_request_thread`, `update_merge_request`.

**Anthropic API (CI path).** `messages.create` with a cached system prefix, structured-output format,
and model-gated thinking/effort. Model via `REVIEW_MODEL` (default `claude-opus-4-8`).

**GitLab REST (CI path).** The runner reads (`/merge_requests/:iid`, `/diffs`, `/notes`) and writes
(`/notes`, `/discussions` with a `position`) using a bot token (`PRIVATE-TOKEN`).

**Inline comment positioning.** Both paths anchor inline comments with a `position` object carrying
`base_sha`/`head_sha`/`start_sha` (from the MR's `diff_refs`), `position_type: "text"`, both
`old_path` and `new_path`, plus `new_line` for additions/modifications or `old_line` for removals.

---

## 8. Configuration surfaces

| Surface | Where | Controls |
|---|---|---|
| `review.config.yml` | repo root | `local.base_ref`; `strictness.default`; `path_filters.ignore`; `review.{max_inline_comments, enable_suggestions, skip_drafts, skip_bot_authored, next_actions}` |
| Model (IDE) | Copilot chat dropdown | The agents don't pin a model — chat selection wins |
| Model (CI) | `REVIEW_MODEL` env | Default `claude-opus-4-8`; set `claude-sonnet-5` / `claude-haiku-4-5` to cut cost |
| Effort / strictness (CI) | `REVIEW_EFFORT`, `REVIEW_STRICTNESS` env | Effort auto-omitted for non-reasoning models |
| Secrets (CI) | masked CI/CD vars | `ANTHROPIC_API_KEY`, `REVIEW_BOT_TOKEN` |
| MCP tool surface | `docs/gitlab-mcp.example.json` | exact package pin, dedicated server, denied writes, confirmation-protected actions |
| Coding conventions | `.github/instructions/*.instructions.md` | Path-scoped rules the reviewer enforces |

---

## 9. Security & permissions model

- **IDE actions run as the developer.** `review-mr` uses the developer's PAT (from their MCP config).
  Approvals, notes, and updates are performed *as that user*, who needs the corresponding GitLab
  rights. The agent is a hands-free UI, not a service identity.
- **Manual invocation only.** `review-mr` has `disable-model-invocation: true`, preventing another
  Copilot agent from automatically delegating to this write-capable profile.
- **CI runs as a bot.** A project/group access token (`REVIEW_BOT_TOKEN`, `api` scope) posts the
  review. The CI runner **never approves** — automated self-approval is out of scope by design.
- **Read-only profile.** `GITLAB_READ_ONLY_MODE=true` strips every write tool for an "analyze-only"
  variant; the default (`false`) is required for posting notes/threads and reviewer actions.
- **Guardrails.** The dedicated MCP policy hides merging and other non-review writes. Every allowed
  write requires `_confirmed: true`; the explicit scoped review request authorizes review posts,
  while reviewer actions require a separate confirmation. Approval also remains pipeline-gated.
- **Secrets never in files.** Tokens live in the developer's MCP config (input-prompted) or masked CI
  variables — never committed.

---

## 10. Deployment & distribution

GitLab has no org-wide `.github` mechanism and Copilot reads the open workspace, so adopters either
**copy** the toolkit files per-repo or point VS Code at a **central** checkout
(`chat.agentFilesLocations` / `chat.agentSkillsLocations` / `chat.instructionsFilesLocations`).

**File ownership taxonomy** (the reusability contract):

| Class | Files | Adoption |
|---|---|---|
| **Reusable** (copy as-is) | `skills/review-standards/`, both agents, `.github/scripts/`, `ci/review.py` | drop in unchanged |
| **Configure** | `review.config.yml`, `ci/*.gitlab-ci.yml` (via `include:`), `docs/gitlab-mcp.example.json` (merge) | tune / include / merge — never overwrite the adopter's pipeline or MCP file |
| **Project-owned** | `.github/instructions/*` (example), `.github/copilot-instructions.md` (**not shipped**) | keep the project's own; the features need nothing here |

CI is added by **inclusion**, never by shipping a root `.gitlab-ci.yml`:

```yaml
include:
  - local: '/ci/security-scanning.gitlab-ci.yml'   # Secret Detection + SAST
  - local: '/ci/ai-review.gitlab-ci.yml'           # AI review
```

---

## 11. Boundaries / non-goals

- **Not a scanner.** The LLM reviewer does not replace SAST / Secret Detection / dependency scanning /
  linters — it complements them.
- **Not a style/format/coverage checker.** Those belong to linters and are explicitly excluded.
- **No autonomous merge or self-approval.** Merge is off; CI never approves; IDE approvals are
  human-triggered and pipeline-gated.
- **Non-deterministic output.** Same MR can yield different findings across runs; the ≥80 filter,
  changed-lines-only rule, and anchor validation keep this tolerable — reproducibility is not promised.
- **Editor scope.** Agents/skills are VS Code / Copilot IDE features; teams on other editors are
  covered by the CI path.

---

## 12. Extension points

- **Fold scanner results into the review** — have `review-mr` read SAST/Secret-Detection report
  artifacts (`list_job_artifacts` / `get_job_artifact_file`) and summarize them instead of duplicating.
- **True per-commit incremental in CI** — compare API between the last-reviewed sha and head (the IDE
  agent already does this via `get_branch_diffs`).
- **"Learnings" memory** — curate a resource the skill references, tuned from reviewer 👍/👎.
- **Specialized lenses** — add conditional passes (tests, types) as internal branches, not extra
  billable agents.
- **Additional entry points** — the same `review-standards` brain can drive a Copilot cloud/CLI agent
  (`target: github-copilot`) once GitLab access is wired in that environment.

---

*Companion docs:* [../README.md](../README.md) (adoption) · [REVIEW-SYSTEM.md](REVIEW-SYSTEM.md)
(deployment details, CodeRabbit parity, token playbook).
