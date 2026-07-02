# Cost controls — running reviews on a Copilot credit budget

Since June 2026, GitHub Copilot bills usage as AI credits computed from input, output, and cached
tokens (1 credit = $0.01). Organizations still on legacy annual plans consume premium requests per
prompt multiplied by a per-model factor. Under both schemes the same two quantities dominate a
review workflow's cost:

1. **Context size per model turn.** Diffs, artifacts, and instruction files are input tokens.
2. **Number of model turns.** In agent mode every tool round trip re-sends the conversation, so
   twenty MCP calls re-bill the growing context twenty times; caching softens but does not remove
   this.

Every lever below is already wired into the toolkit — the point of this page is that they stay on.

## Levers

| Lever | Mechanism | Where |
|---|---|---|
| One evidence pass, not 15–25 tool calls | `collect-mr-evidence.py` fetches MR, story, markers, pipeline, jobs, scanner summaries, and diffs in a single read-only run | `review-mr` step 1 |
| One diff pass locally | `collect-review-diff.py --secret-scan` returns manifest + patch + secret scan in one command | `code-review` step 1 |
| Deterministic pre-work is free | Path filtering, patch budgeting, artifact JSON parsing, and the secret regex/entropy scan run in Python, not in the model | both collectors |
| Token budgets fail closed | `limits.max_file_patch_kb` / `limits.max_total_patch_kb` exclude oversized patches and report them `unavailable` instead of overflowing context (which forces expensive retries) | `review.config.yml` |
| Noise files never enter context | `path_filters.ignore` drops lockfiles, vendored, generated, and minified files before the model sees them | `review.config.yml` |
| No duplicate reviews | The version-3 freshness marker suppresses a full re-review when head SHA, requirement, pipeline, and scanner evidence are unchanged | `gitlab-review-evidence` |
| Cheap model for the cheap job | `code-review` pins `model: ['GPT-5 mini', 'GPT-4.1']` — 0x/low-cost models are fine for a single-pass pre-push gate | agent frontmatter |
| Single-turn agents | Both agents are one-pass by contract: no clarifying questions mid-run, no background subagents, no re-reading files | agent bodies |
| Scanners replace model effort | GitLab Secret Detection and SAST run in CI for free (compute-wise); the agent verifies their reports instead of re-deriving them | `ci/security-scanning.gitlab-ci.yml` |
| Short instruction files | Agent bodies, skills, and instruction files are input tokens on every run; keep conventions specific and brief | `.github/instructions/` |

## Model selection

- **`code-review` (developer, many runs/day):** pinned to a prioritized list of low-cost models.
  Edit the `model:` array in `code-review.agent.md` to whatever your Copilot policy enables;
  remove the key to use the user's picker choice.
- **`review-mr` (reviewer, few runs/day, higher stakes):** intentionally not pinned. Evidence
  reasoning and requirements tracing benefit from a stronger model; the run is already compacted
  by the collector, so even a premium model consumes modest credits.
- Never route reviews through a model API key — the only AI surface is Copilot itself.

## What a run should look like

A healthy `review-mr` run is: 1 collector command → single review turn → up to
`max_inline_comments` thread writes → 1 summary note. If you observe long chains of MCP diff/
pipeline/artifact reads, the script path is broken (usually missing `GITLAB_TOKEN` /
`GITLAB_API_URL` in the environment) — fix that first; the MCP fallback is correct but roughly an
order of magnitude more expensive in tokens and latency.

Practical habits that compound the savings:

- Run `code-review` once per push-ready state, not after every edit.
- Give `review-mr` the story reference up front (`story_project=… story_iid=…`) when the MR
  description is ambiguous — a failed story resolution costs a partial review plus a rerun.
- Keep MRs small; the budgets protect the context window, but ten small MRs review better and
  cheaper than one huge one.
