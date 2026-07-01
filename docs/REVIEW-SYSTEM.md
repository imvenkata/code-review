# Enterprise MR Review System (Copilot + GitLab)

Two features, one shared brain — modeled on how Claude Code splits local review from PR review,
adapted to GitHub Copilot in VS Code talking to GitLab via `@zereight/mcp-gitlab`.

> Full architecture reference (components, flows, capability ownership): [ARCHITECTURE.md](ARCHITECTURE.md).

## File map

```
.github/
  instructions/
    conventions.instructions.md            # coding-convention EXAMPLE (project-owned; replace with yours)
  skills/
    review-standards/SKILL.md              # ← SHARED BRAIN (rubric, scoring, output) — library skill
  agents/
    code-review.agent.md                   # FEATURE 1 — local diff, pre-push (target: vscode)
    review-mr.agent.md                     # FEATURE 2 — GitLab MR, manual VS Code agent
  scripts/
    collect-review-diff.py                 # read-only complete local-diff collector
review.config.yml                          # target branch, path filters, posting limits, strictness
ci/
  review.py                                # Phase 2 runner: Anthropic API + prompt caching
  ai-review.gitlab-ci.yml                  # Phase 2: includable AI-review CI template
  security-scanning.gitlab-ci.yml          # GitLab Secret Detection + SAST (deterministic layer)
docs/gitlab-mcp.example.json               # restricted gitlab-review MCP EXAMPLE (merge, don't copy)
tests/                                     # deterministic helper, marker, anchor, and agent-contract tests
```

> **File ownership (for reuse):** everything above is toolkit code you copy as-is, except two
> **project-owned** files — `.github/instructions/*.instructions.md` (your coding rules; shipped here
> as a replaceable example) and `.github/copilot-instructions.md` (your project's own main
> instructions, which differ per project). The toolkit **does not ship or touch**
> `copilot-instructions.md`; the review features are self-contained in the skill, agents, and local
> diff helper and need nothing in it. Full table in the [README](../README.md).

## Which Copilot primitive, and why

The Copilot surface has three customization primitives plus instructions — each is the right tool
for a different job here:

| Primitive | File | Used for |
|---|---|---|
| **Custom agent** (`.agent.md`) | `.github/agents/*` | The two **features** — a persona + constrained tool set (you pick the model in chat). Both shipped profiles target VS Code; `review-mr` also disables model invocation so only a human can select the write-capable agent. A separately tested profile is required for cloud/CLI use. |
| **Agent Skill** (`SKILL.md`) | `.github/skills/*` | The **shared brain**. Progressive disclosure (only the `description` sits in context; body loads on demand) = token-efficient. Portable across Copilot, Claude Code, Cursor, Codex. |
| **Instructions** (`.instructions.md`) | `.github/instructions/*` | Path-scoped **coding conventions** the reviewer enforces (`applyTo` globs). |
| **`copilot-instructions.md`** | `.github/` — *project-owned* | The adopting project's own main instructions; differs per project. **Not part of this toolkit** — it ships none, and the review features add nothing here (their logic lives in the skill + agents). The reviewer may *read* it as one optional source of the project's conventions. |

> **Maturity caveat:** Agent Skills shipped late-2025 and were initially experimental. Confirm your
> org's Copilot/VS Code build exposes them as GA before standardizing; otherwise generate thin
> `.prompt.md` wrappers that invoke the same logic as a fallback.

**The "common skill":** both agents are deliberately thin. The 0-100 rubric, the false-positive
exclusion list, confidence threshold, impact severity, and the output contract live once in
[review-standards/SKILL.md](../.github/skills/review-standards/SKILL.md). The agents apply it; the
CI runner reads its body verbatim as the cached prompt prefix. Tune review behavior in one place;
the two features and the CI gate can never drift.

## The two features

| | `code-review` (Feature 1) | `review-mr` (Feature 2) |
|---|---|---|
| Target | Local working-tree / branch diff | A GitLab MR (by project + iid) |
| When | Before you push | On an open MR (manual now, CI later) |
| Output | Findings in chat | Summary note + inline threads on the MR |
| GitLab MCP | none (git only) | read + note + reviewer-action tools (approve, reply, resolve, update — server from `.vscode/mcp.json`) |
| Incremental | n/a | exact-head skip; safe small-delta optimization with full-review fallback |
| Cost profile | cheapest (no network, free model) | one agent pass, one summary note + the inline threads |

Both apply `review-standards`, `conventions.instructions.md`, and `review.config.yml`. Invoke an
agent from the Chat view dropdown or the `/agents` menu.

## Deployment to target repos (VS Code)

GitLab has no org-wide `.github` repo mechanism, and Copilot reads from the open workspace, so
distribute one of two ways:

1. **Per-repo (simplest):** copy the review features — `.github/skills/`, `.github/agents/`,
   `.github/scripts/`, `review.config.yml`, and (for the CI gate) `ci/` — into each repo, then
   `include` both
   `/ci/security-scanning.gitlab-ci.yml` (Secret Detection + SAST) and `/ci/ai-review.gitlab-ci.yml`
   (AI review) from your own `.gitlab-ci.yml`. **Don't** copy
   `.github/copilot-instructions.md` (each project keeps its own) or overwrite `.vscode/mcp.json`
   (merge the `gitlab-review` block from `docs/gitlab-mcp.example.json` instead); adapt
   `.github/instructions/*.instructions.md` to the project. Sync with a script.
2. **Central folder (DRY):** keep this repo checked out locally and point VS Code at it — user/workspace
   `settings.json`:
   ```jsonc
   "chat.agentFilesLocations":       { "/path/to/code-review/.github/agents": true },
   "chat.agentSkillsLocations":      { "/path/to/code-review/.github/skills": true },
   "chat.instructionsFilesLocations":{ "/path/to/code-review/.github/instructions": true }
   ```
   Per-repo `review.config.yml`, `conventions.instructions.md`, and
   `.github/scripts/collect-review-diff.py` still live in each repo because the local agent executes
   the helper from the target workspace.

## gitlab-mcp tooling — constrain at both boundaries

The server exposes a broad GitLab surface, so an agent allow-list alone is insufficient for a
workspace-wide MCP process. The example creates a dedicated, version-pinned `gitlab-review` server.
Its deny regex hides non-review writes, and its confirmation policy requires `_confirmed: true` for
every remaining write. The explicit, scoped review request authorizes review posts; approval and
other reviewer actions require their own confirmation. The agent also applies a namespaced
allow-list. Tools used:

- Read: `get_merge_request`, `list_merge_request_changed_files`, `get_merge_request_file_diff`,
  `get_file_contents`, `get_merge_request_notes`, `mr_discussions`, `get_branch_diffs`,
  `get_merge_request_approval_state`, `list_merge_request_pipelines`, `get_pipeline`
- Write (review): `create_merge_request_thread` (inline, needs `position` with base/head/start SHA),
  `create_note` (summary + source/state marker)
- Reviewer actions (interactive, on request): `approve_merge_request`, `unapprove_merge_request`,
  `create_merge_request_discussion_note` (reply), `update_merge_request_note`,
  `resolve_merge_request_thread`, `update_merge_request` (labels/title/description/assignees).
  Merging and repository/branch writes are hidden by server policy.

Merge the example's `gitlab-review` block into `.vscode/mcp.json`; retain the exact package pin
until a newer version passes the compatibility smoke test.

## Token / credit playbook (the #1 constraint)

- **Run on included models** (GPT-5 mini / GPT-4.1 / GPT-4o) — 0 credits on paid plans. The agents
  don't pin a model (so they stay reusable as models change) — pick an included/free one in the chat
  dropdown; bump to **Auto** (10% discount) or a premium model only for hard diffs.
- **One agent pass per review.** In agent mode only your prompt is billed; the internal tool loop
  (fetch → review → post) is free. Never split into multiple invocations.
- **Avoid the built-in Copilot "code review"** button: model multiplier **13**, and it can't touch
  GitLab MRs anyway.
- **Progressive disclosure (skills)** keeps the rubric out of context until a review runs.
- **Diff-only reading** — never pull whole files speculatively (enforced in both agents and the runner).
- **Eligibility gate + complete source-specific marker** mean drafts, trivial MRs, and
  already-reviewed revisions cost almost nothing.
- **Confidence filter ≥80** cuts output tokens and human re-review round-trips.
- **Trim the MCP tool surface** (above) — recurring per-request saving.
- **Don't ask the LLM to do a linter's job** — let GitLab CI run linters/SAST; the reviewer ignores
  anything a linter would catch.

## CodeRabbit parity (what we match / skip / defer)

| CodeRabbit capability | Here |
|---|---|
| Line-by-line inline comments | ✅ `create_merge_request_thread` |
| One-click fix suggestions | ✅ GitLab ```suggestion blocks |
| PR summary / walkthrough | ✅ summary note in `review-mr` + the CI runner |
| Incremental review on new commits | ✅ source-specific complete/partial markers + safe delta fallback |
| Config-as-code, path filters | ✅ `review.config.yml` |
| Path-based instructions | ✅ `.github/instructions/*.instructions.md` |
| Review strictness profiles | ✅ `strictness` arg (low/medium/high) |
| SAST / secret detection | ✅ GitLab Secret Detection + SAST (`ci/security-scanning.gitlab-ci.yml`); the reviewer is the second net, not a duplicate |
| Pipeline / CI status awareness | ✅ `review-mr` reports MR pipeline status and gates approval on it |
| Auto-trigger on every MR | ✅ Phase 2 — `ci/ai-review.gitlab-ci.yml` on `merge_request_event` |
| "Learnings" memory from feedback | 🔶 curate a `learnings` resource manually; online learning needs infra |
| Cross-file architectural diagrams | ❌ skipped (low ROI, high tokens) |

## Phase 2 — automated CI gate

[ci/ai-review.gitlab-ci.yml](../ci/ai-review.gitlab-ci.yml) — an includable CI template (add
`include: local: '/ci/ai-review.gitlab-ci.yml'` to your pipeline) — runs
[ci/review.py](../ci/review.py) on `merge_request_event`.
The runner: deterministic eligibility gate (draft / bot-authored / already-reviewed head) → fetch
diffs (diff-only, path-filtered, with explicit coverage accounting) → one Anthropic call with the
`review-standards` body as a
**prompt-cached** prefix and structured-output findings → drop findings below the confidence
threshold and any line/side it can't anchor to the diff → post inline threads followed by a summary
with a source/state marker. Truncated, oversized, or failed files produce a partial review that
cannot suppress a retry. The summary ends with a short "next actions" footer
(open in the `review-mr` agent, or GitLab quick actions like `/approve`) — the CI bot is advisory and
never self-approves. Customize the footer via `review.next_actions` in `review.config.yml`.

**Defense in depth:** also `include` [ci/security-scanning.gitlab-ci.yml](../ci/security-scanning.gitlab-ci.yml)
— GitLab **Secret Detection + SAST** are the reliable, deterministic control for secrets and known
vulnerabilities; the AI reviewer complements them (logic-level security) rather than duplicating them.
Separately, the interactive `review-mr` agent reads the MR's pipeline status and **won't approve over
a red or pending pipeline**.

Setup: add CI/CD variables `ANTHROPIC_API_KEY` and `REVIEW_BOT_TOKEN` (a project/group access token,
`api` scope), both masked. Optional: `REVIEW_MODEL` (default `claude-opus-4-8`; set `claude-sonnet-5`
or `claude-haiku-4-5` to cut cost), `REVIEW_EFFORT`, `REVIEW_STRICTNESS`. The runner auto-omits
`thinking`/`effort` for models that don't support them (e.g. Haiku 4.5), so switching to a cheaper
model won't 400.

> **Caching caveat:** prompt caching needs a **≥4096-token** stable prefix on Opus to actually cache.
> Until `review-standards` + conventions are that large, the prefix silently won't cache — the bigger
> cost lever there is model choice. The runner prints `cache_read=<n>` so you can see when it kicks in.
>
> The runner reviews the full MR diff guarded by a complete CI marker. True per-commit incremental
> review (compare API between the last-reviewed sha and head) is a natural next step — the IDE
> `review-mr` agent already does this via `get_branch_diffs`.

## Design notes

- **The split is right.** Local pre-push review and authoritative MR review have different triggers,
  outputs, and cost profiles. Keep them as two agents.
- **Shared logic lives in the skill, never duplicated in an agent** — the single most important rule
  for not drifting as the system grows.
- **Don't fan out into multiple billable agents** the way Claude Code does — here that multiplies
  credits. The four lenses (bugs/conventions/errors/security) run inside one pass.
- **CI is the enforcement point; the IDE agents are the fast feedback loop** — same brain, three
  entry points (local agent, MR agent, CI runner).
