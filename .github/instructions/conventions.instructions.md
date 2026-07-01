---
description: "Project coding conventions enforced during review. REPLACE the examples below with your real rules before use."
applyTo: "src/**"
---

# Coding conventions

> ⚠️ PLACEHOLDER — the rules below are **illustrative examples**, not this project's rules.
> Until you replace them with your team's actual conventions, **do not enforce anything in this
> file**: ignore it during review. Flag a convention violation only when the rule is real and
> written here verbatim.

Once you edit this file, keep the list short and **specific** — vague guidance produces no findings
and bloats every request. Example shape to replace:

- (example) Public functions that can fail return a Result/typed error — do not throw across module boundaries.
- (example) No `console.log` / stray debug prints in committed code.
- (example) Database access goes through the repository layer, never inline in handlers.
- (example) New HTTP handlers validate input at the boundary before use.

> Add more files (e.g. `backend.instructions.md` with `applyTo: "src/api/**"`) for path-specific
> rules — they auto-apply only when matching files are in context, so they cost tokens only when relevant.
