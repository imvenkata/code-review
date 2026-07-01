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

- **0-25** — false positive, doesn't survive light scrutiny, or pre-existing.
- **26-50** — minor nitpick, not in any instruction file.
- **51-75** — real but low-impact / rare in practice.
- **76-90** — important; will be hit in practice or violates a stated convention.
- **91-100** — critical bug or explicit convention violation, confirmed by the evidence.

**Drop every finding below the threshold** (default 80; overridable per run or in `review.config.yml`).
If nothing survives, say so plainly and add nothing else.

## Severity buckets (surviving findings)

- **Critical (90-100)** — must fix before merge.
- **Important (80-89)** — should fix.

## Output contract

Per finding: one-line description, `file:line`, the rule or the concrete failure scenario
(inputs -> wrong result), and a concrete fix. Brief. No emojis. Quote the convention text verbatim
when the finding is convention-based. When proposing a code fix on a merge request, use a GitLab
```suggestion block so the author can apply it in one click.
