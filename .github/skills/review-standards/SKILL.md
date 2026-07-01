---
name: review-standards
description: Shared code-review rubric, false-positive exclusions, 0-100 confidence scoring, and the output contract. Apply whenever reviewing a code diff or a merge request. Used by the code-review and review-mr agents.
user-invocable: false
---

# Review standards (shared brain)

> Both review agents (`code-review`, `review-mr`) apply this skill. Tune review behavior
> here in one place so the two agents never drift. Keep it lean — it is loaded into context
> whenever a review runs.

## Mandate

Review **only the lines changed in the diff**. Surface what a thoughtful senior engineer would
block a merge on. Optimize for **signal**: a few real findings beat a long list of nitpicks.
When in doubt, stay silent.

## What to flag

1. **Correctness / logic bugs** introduced by the change (off-by-one, null/undefined, wrong
   operator, broken control flow, race conditions, resource leaks).
2. **Convention violations** explicitly stated in the project's own instruction files
   (`.github/instructions/*.instructions.md`, `AGENTS.md`, or `copilot-instructions.md` — whichever
   the project uses; treat them as read-only sources of rules).
3. **Error handling**: swallowed exceptions, empty catches, silent fallbacks that hide failures,
   missing error propagation.
4. **Security-sensitive changes**: authz/authn gaps, injection (SQL/command/path), SSRF, path
   traversal, unsafe deserialization, and missing input validation at a trust boundary. Also flag
   **hardcoded secrets** on changed lines — passwords, API keys, tokens, private keys, connection
   strings, high-entropy literals. (Secrets and known-vuln patterns are caught deterministically by
   GitLab Secret Detection + SAST in CI; you are the *second net* for what those miss and for
   logic-level security a scanner can't see.)

## What NOT to flag (apply aggressively)

- Pre-existing issues on lines the diff did not touch.
- Routine findings a linter, type checker, formatter, compiler, or the CI security scanners
  (SAST / Secret Detection / dependency scanning) would catch — assume CI runs those. (Still surface
  a hardcoded secret you happen to see, in case scanning isn't enabled on this repo.)
- Pedantic nitpicks a senior engineer would not comment on.
- "Add more tests / docs / logging" unless a stated convention requires it.
- Changes that are plausibly intentional and consistent with the broader change.
- Issues already silenced in code with an explicit ignore comment.

## Confidence scoring (0-100) — required for every candidate finding

Confidence is the probability that the finding is valid, not its impact:

- **0-25** — speculative, pre-existing, or does not survive light scrutiny.
- **26-50** — plausible but missing material evidence.
- **51-75** — likely valid, with some uncertainty about the failure path.
- **76-90** — strongly supported by the diff and surrounding evidence.
- **91-100** — directly demonstrated or effectively certain.

**Drop every finding below the threshold** (default 80; overridable per run or in `review.config.yml`).
If nothing survives, say so plainly and add nothing else.

## Impact severity (surviving findings)

- **Critical** — must fix before merge: security boundary bypass, data loss/corruption, widespread
  outage, or a core workflow that is reliably broken.
- **Important** — should fix before merge: material correctness, reliability, error-handling, or
  explicit-convention failure that does not meet the Critical impact bar.

Assign severity independently from confidence. A confidence-100 low-impact defect is not Critical,
and a confidence-85 authorization bypass remains Critical.

## Output contract

Per finding: one-line description, `file:line`, the rule or the concrete failure scenario
(inputs -> wrong result), and a concrete fix. Brief. No emojis. Quote the convention text verbatim
when the finding is convention-based. When proposing a code fix on a merge request, use a GitLab
```suggestion block so the author can apply it in one click. For merge-request findings, also retain
the diff side (`new` for added/modified lines, `old` for removed lines) so the comment can be
anchored without guessing. When a structured-output schema has a `suggestion` field, put only the
replacement source in that field; the caller adds the Markdown fence.
