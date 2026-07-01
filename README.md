# Copilot + GitLab Code Review Toolkit

Reusable, **model-agnostic** code-review features for teams on **GitHub Copilot + GitLab**, built as
Copilot **agents** + an **Agent Skill** (portable across Copilot, Claude Code, Cursor, Codex), with an
optional **CI** gate. Two features share one review "brain":

- **`code-review`** agent — review your local diff before you push (findings in chat).
- **`review-mr`** agent — review a GitLab MR and post findings back, then **approve / reply / resolve /
  label from the chat window** (no window switching); reports MR **pipeline status** and won't approve
  over a red pipeline. Needs a GitLab **project ID** + **MR ID**.

Architecture reference: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · design, deployment & CodeRabbit
parity: [docs/REVIEW-SYSTEM.md](docs/REVIEW-SYSTEM.md).

## What's here — and who owns what

| File(s) | Ownership | How to adopt |
|---|---|---|
| `.github/skills/review-standards/SKILL.md` | **Toolkit** — the shared brain | copy as-is |
| `.github/agents/code-review.agent.md`, `review-mr.agent.md` | **Toolkit** — the two features | copy as-is |
| `ci/review.py`, `ci/ai-review.gitlab-ci.yml` | **Toolkit** — optional CI gate | copy `ci/`; add `include: local: '/ci/ai-review.gitlab-ci.yml'` to your `.gitlab-ci.yml` |
| `ci/security-scanning.gitlab-ci.yml` | **Toolkit** — deterministic scanners | `include:` it — GitLab Secret Detection + SAST (the reliable secret/vuln control) |
| `docs/gitlab-mcp.example.json` | **Configure** (merge, don't copy) | add the `gitlab` block to your `.vscode/mcp.json` — set URL + token (you likely already have gitlab-mcp) |
| `review.config.yml` | **Configure** | tune path filters, posting limits, strictness |
| `.github/instructions/*.instructions.md` | **Project-owned** (example shipped) | replace with your real coding rules, or delete |
| `.github/copilot-instructions.md` | **Project-owned — NOT shipped** | keep your own; the features need nothing in it |

> **The review features are self-contained in the skill + agents.** They require **no** edits to your
> project's `copilot-instructions.md` (which differs project to project). Drop the toolkit files in
> without touching your project instructions. The reviewer may *read* your instruction files as a
> source of conventions, but never writes to them.

## Adopt in a repo (VS Code)

1. Copy `.github/skills/`, `.github/agents/`, and — for the CI gate — `ci/`; then `include` both
   `/ci/security-scanning.gitlab-ci.yml` and `/ci/ai-review.gitlab-ci.yml` from your `.gitlab-ci.yml`.
   Do **not** copy `.github/copilot-instructions.md`, and **merge** (don't overwrite) `.vscode/mcp.json`.
2. Point gitlab-mcp at your instance — merge the `gitlab` block from `docs/gitlab-mcp.example.json`
   into your `.vscode/mcp.json` (URL + token), or reuse your existing setup.
3. *(Optional)* add your coding rules in `.github/instructions/*.instructions.md`; tune `review.config.yml`.
4. *(CI gate)* add masked CI/CD vars `ANTHROPIC_API_KEY` + `REVIEW_BOT_TOKEN` (project/group token, `api` scope).
5. Reload, then pick **code-review** / **review-mr** from the Copilot agents dropdown (`/agents`).

Sharing one copy across many repos instead of copying per-repo? See the **central folder** option in
[docs/REVIEW-SYSTEM.md](docs/REVIEW-SYSTEM.md#deployment-to-target-repos-vs-code).

## Design principles

- **Model-agnostic:** the agents don't pin a model — you pick it in the Copilot chat dropdown, so they
  survive model churn. Only the headless CI runner names a model (`REVIEW_MODEL`, default overridable).
- **Token/credit efficient:** included/free models for the heavy path, diff-only reading, an eligibility
  gate, a confidence filter, a trimmed MCP tool surface, and prompt caching in CI.
- **One brain, three entry points:** local agent, MR agent, and CI runner all apply the same
  `review-standards` skill — tune review behavior in one file, nothing drifts.
- **Defense in depth:** GitLab Secret Detection + SAST catch secrets/vulns deterministically, and the
  pipeline must be green to approve; the LLM reviewer covers logic-level issues a scanner can't see —
  no LLM is asked to be a scanner (more reliable *and* cheaper).
