"""Deterministic codebase-context builder for codebase-aware ("deep") review.

Given the changed diff, this builds a bounded "impact neighborhood" for the
change using only git — no embeddings, no language parsers, no new dependencies:

- **references** — for each symbol defined/changed in the diff, `git grep -w`
  finds where else in the repo it is used, so a signature or behavior change's
  callers are surfaced ("who breaks if I change this?");
- **co-change** — `git log --name-only` over recent history finds files that
  historically change alongside the changed files, ranked by frequency.

It is approximate by design: matching is by symbol *name*, so same-named
identifiers in different scopes/languages can over-surface. Every entry is
evidence for the reviewing agent, which must verify it against the real code
before flagging (the grounding rule in the codebase-aware-review skill).
Matched output is source text already tracked in the repo, never a credential.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from reviewlib.config import is_ignored


# Language-agnostic definition patterns. Each captures the defined `name`. Kept
# small and extensible; add rows here as new languages need coverage.
_DEF_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:async\s+)?def\s+(?P<name>[A-Za-z_]\w*)"),                       # python
    re.compile(r"\b(?:export\s+)?(?:default\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)"),  # js/ts
    re.compile(r"\bfunc\s+(?:\([^)]*\)\s*)?(?P<name>[A-Za-z_]\w*)"),                  # go
    re.compile(r"\bfn\s+(?P<name>[A-Za-z_]\w*)"),                                     # rust
    re.compile(r"\b(?:class|interface|trait|struct|enum|type|module)\s+(?P<name>[A-Za-z_]\w*)"),
    re.compile(r"\b(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*="),   # js bindings
)

# Names shorter than this are too noisy to grep the whole repo for.
_MIN_SYMBOL_LEN = 3
# Hard cap on distinct symbols we run `git grep` for, to bound subprocess cost.
_MAX_SYMBOLS = 40

_HUNK = re.compile(r"^@@ ")
_META = ("diff --git ", "index ", "--- ", "+++ ", "new file", "deleted file",
         "rename ", "copy ", "similarity ", "dissimilarity ", "old mode", "new mode")
_GREP_LINE = re.compile(r"^(?P<path>.+?):(?P<line>\d+):(?P<text>.*)$")


@dataclass(frozen=True)
class ContextEntry:
    provenance: str  # "references" | "co-change"
    symbol: str      # changed symbol (references); "" for co-change
    path: str
    line: int        # 1-based line (references); 0 for co-change
    text: str        # the reference source line, or a co-change count marker


@dataclass(frozen=True)
class Context:
    symbols: list[str] = field(default_factory=list)
    entries: list[ContextEntry] = field(default_factory=list)
    truncated: int = 0


def _git(repo: Path, *args: str, allowed: tuple[int, ...] = (0,)) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode not in allowed:
        return ""
    return result.stdout.decode("utf-8", errors="replace")


def _changed_lines(chunk: str) -> list[str]:
    """Added and removed content lines of a per-file diff chunk (no +/- prefix)."""
    lines: list[str] = []
    in_hunk = False
    for raw in chunk.splitlines():
        if _HUNK.match(raw):
            in_hunk = True
            continue
        if raw.startswith(_META):
            in_hunk = False
            continue
        if not in_hunk:
            continue
        if raw[:1] in ("+", "-"):
            lines.append(raw[1:])
    return lines


def changed_symbols(chunks: list[tuple[str, str]]) -> list[str]:
    """Distinct symbol names defined or changed across the diff chunks."""
    seen: dict[str, None] = {}
    for _path, chunk in chunks:
        for line in _changed_lines(chunk):
            for pattern in _DEF_PATTERNS:
                match = pattern.search(line)
                if match:
                    name = match.group("name")
                    if len(name) >= _MIN_SYMBOL_LEN:
                        seen.setdefault(name, None)
    return list(seen)[:_MAX_SYMBOLS]


def references(
    repo: Path,
    symbol: str,
    ignore_globs: tuple[str, ...],
    changed_paths: set[str],
    cap: int,
) -> list[tuple[str, int, str]]:
    """`git grep -w` hits for `symbol`, excluding changed and ignored files."""
    raw = _git(repo, "grep", "-n", "-w", "-F", "--no-color", "-e", symbol, allowed=(0, 1))
    hits: list[tuple[str, int, str]] = []
    for line in raw.splitlines():
        match = _GREP_LINE.match(line)
        if not match:
            continue
        path = match.group("path")
        if path in changed_paths or is_ignored(path, ignore_globs):
            continue
        hits.append((path, int(match.group("line")), match.group("text").strip()))
        if len(hits) >= cap:
            break
    return hits


def co_changed_files(
    repo: Path,
    changed_paths: set[str],
    lookback: int,
    cap: int,
    ignore_globs: tuple[str, ...],
) -> list[tuple[str, int]]:
    """Files that co-occur in recent commits with the changed files, by frequency."""
    raw = _git(repo, "log", f"-n{lookback}", "--name-only", "--pretty=format:%x00%H")
    counts: dict[str, int] = {}
    for block in raw.split("\x00"):
        rows = block.splitlines()
        if len(rows) < 2:
            continue
        files = {row for row in rows[1:] if row}
        if not files & changed_paths:
            continue
        for path in files:
            if path in changed_paths or is_ignored(path, ignore_globs):
                continue
            counts[path] = counts.get(path, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return ranked[:cap]


def build_context(
    repo: Path,
    chunks: list[tuple[str, str]],
    changed_paths: set[str],
    ignore_globs: tuple[str, ...],
    *,
    max_files: int,
    max_refs_per_symbol: int,
    co_change_lookback: int,
    enable_co_change: bool,
    budget_bytes: int,
) -> Context:
    symbols = changed_symbols(chunks)

    entries: list[ContextEntry] = []
    for symbol in symbols:
        for path, line, text in references(repo, symbol, ignore_globs, changed_paths, max_refs_per_symbol):
            entries.append(ContextEntry("references", symbol, path, line, text))

    if enable_co_change:
        for path, count in co_changed_files(repo, changed_paths, co_change_lookback, max_files, ignore_globs):
            entries.append(ContextEntry("co-change", "", path, 0, f"{count} shared commits"))

    kept, truncated = _apply_budget(entries, max_files=max_files, budget_bytes=budget_bytes)
    return Context(symbols=symbols, entries=kept, truncated=truncated)


def _entry_bytes(entry: ContextEntry) -> int:
    return len(f"{entry.provenance}\t{entry.symbol}\t{entry.path}:{entry.line}\t{entry.text}\n"
               .encode("utf-8", errors="replace"))


def _apply_budget(
    entries: list[ContextEntry], *, max_files: int, budget_bytes: int
) -> tuple[list[ContextEntry], int]:
    kept: list[ContextEntry] = []
    files_seen: set[str] = set()
    total = 0
    truncated = 0
    for entry in entries:
        is_new_file = entry.path not in files_seen
        if is_new_file and len(files_seen) >= max_files:
            truncated += 1
            continue
        size = _entry_bytes(entry)
        if total + size > budget_bytes and kept:
            truncated += 1
            continue
        total += size
        files_seen.add(entry.path)
        kept.append(entry)
    return kept, truncated


def render_section(context: Context) -> str:
    files = sorted({entry.path for entry in context.entries})
    lines = [
        "## Codebase context (deterministic; verify each reference before flagging)",
        f"changed-symbols: {', '.join(context.symbols) if context.symbols else '(none)'}",
        f"context-files: {len(files)}",
        f"truncated: {context.truncated}",
    ]
    if not context.entries:
        lines.append("no related code found")
    for entry in context.entries:
        lines.append(
            f"{entry.provenance}\t{entry.symbol or '-'}\t"
            f"{entry.path}:{entry.line}\t{entry.text}"
        )
    return "\n".join(lines) + "\n"
