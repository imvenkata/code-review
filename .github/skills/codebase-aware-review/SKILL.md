---
name: codebase-aware-review
description: Cross-file impact rubric for the code-review agent's deep mode. Uses the collector's "## Codebase context" section (references to changed symbols + co-changing files) to catch breakage the diff alone hides. Applied only when the user asks for mode=deep.
user-invocable: false
---

# Codebase-aware review (deep mode)

> The `code-review` agent applies this skill **in addition to** `review-standards` when the user
> asks for `mode=deep`. Standard mode never loads it. This skill widens the review from changed
> lines to the change's **impact neighborhood** — but keeps every anti-noise rule in
> `review-standards`. Do not restate that rubric here; this skill only adds the cross-file lens.

## Where the context comes from

Run the collector with `--codebase-context`. It appends a `## Codebase context` section built
deterministically from git — no guessing:

- `references\t<symbol>\t<path>:<line>\t<code>` — a place elsewhere in the repo that uses a symbol
  your diff defined or changed (`git grep -w`). These are the **callers a change can break**.
- `co-change\t-\t<path>:0\t<n> shared commits` — a file that historically changes together with a
  changed file. A **hint** to look, not evidence of a defect.

The matching is by symbol *name*, so it is approximate: a `references` hit may be a same-named but
unrelated symbol. Treat every entry as a lead to verify, never as a proven fact.

## The cross-file impact lens

For each symbol your diff changed, check its listed `references` for breakage the diff introduces:

1. **Signature / arity / type change** — every caller must still pass the right arguments and use
   the return correctly. A new required parameter, a removed one, a reordered or retyped argument,
   or a changed return shape breaks callers that the diff does not show.
2. **Behavioral / contract change** — same signature, different meaning (units, nullability, error
   vs. return, side effects, ordering, thrown exceptions). Check whether callers rely on the old
   contract.
3. **Rename / delete / move** — references to the old name are now orphaned. Confirm they were
   updated or are dead.
4. **Shared constant / enum / type change** — downstream sites that switch on or depend on the old
   value or variant.

For `co-change` files, do a light check only: does this change imply an update that a
historically-coupled file also needs (a parallel branch, a mirrored config, a paired test)?

## Grounding rule (required — this is the false-positive control)

A cross-file finding may be reported **only if** you have confirmed the referenced code actually
breaks:

- The reference line is present in `## Codebase context`, **and**
- you have verified it is the same symbol (not a name collision) by reading the reference in
  context — open the file at that line, or use `search/codebase` to confirm the definition it
  resolves to.

If you cannot confirm the reference is the same symbol and is genuinely broken by the change, **do
not report it** — or report it only at reduced confidence per the `review-standards` 0–100 scale.
Never invent a caller that is not in the context section or that you have not read. An unverifiable
lead is dropped, not guessed.

## Semantic gap-fill (optional)

The deterministic pass only knows exact symbol names. When `deep.enable_semantic` is on, use the
editor's `search/codebase` tool to find what it cannot: parallel implementations that should change
together, the tests that cover the changed behavior, and callers reached indirectly (dependency
injection, dynamic dispatch, string-keyed registries). Same grounding rule applies to anything it
surfaces.

## Scope discipline (unchanged from review-standards, restated for the wider surface)

- Flag only issues **this change introduces** in related files. Never flag a pre-existing bug in a
  neighborhood file the diff did not touch — deep mode widens *impact analysis*, not the set of
  code you critique.
- Keep the confidence threshold and severity model from `review-standards`. Breadth must not lower
  the bar; a wider surface is a bigger chance to add noise, so stay stingy.

## Output

Use the `review-standards` output contract. For every cross-file finding, additionally:

- name the impacted file and line (`path:line`), and
- state which context source led you there — `references` (name the symbol) or `co-change`, or
  `search/codebase` for a semantic lead — so the developer can trace and trust it.

If the change has no cross-file impact, say so briefly (e.g. "No cross-file impact: the changed
symbols have no external references") and fall back to the standard changed-line report.
