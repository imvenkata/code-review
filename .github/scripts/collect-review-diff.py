#!/usr/bin/env python3
"""Collect the complete local change set for the code-review agent.

The script is deliberately read-only. It compares the current working tree with
the merge base of the configured/default target branch and appends synthetic
diffs for untracked files. It never stages files, fetches remotes, or changes
repository configuration.

Token budgets from .github/review.config.yml `limits` are enforced here: oversized file
patches are excluded and reported as `unavailable` so the agent reports partial
coverage instead of overflowing its context. `--secret-scan` appends a
deterministic, redacted credential scan of the included added lines.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from reviewlib import codegraph, secretscan
from reviewlib.config import ConfigError, ReviewConfig, is_ignored, load_config


class DiffCollectionError(RuntimeError):
    """A user-actionable failure while discovering the review diff."""


@dataclass(frozen=True)
class Change:
    status: str
    old_path: str
    new_path: str

    @property
    def display_path(self) -> str:
        if self.old_path == self.new_path:
            return self.new_path
        return f"{self.old_path} -> {self.new_path}"


def git(
    *args: str,
    cwd: Path,
    allowed_returncodes: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode not in allowed_returncodes:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise DiffCollectionError(detail or f"git {' '.join(args)} failed")
    return result


def verified_commit(repo: Path, ref: str) -> str | None:
    result = git(
        "rev-parse",
        "--verify",
        f"{ref}^{{commit}}",
        cwd=repo,
        allowed_returncodes=(0, 128),
    )
    if result.returncode != 0:
        return None
    return result.stdout.decode().strip()


def automatic_base_candidates(repo: Path) -> list[str]:
    remotes = git("remote", cwd=repo).stdout.decode().splitlines()
    if "origin" in remotes:
        remotes = ["origin", *(remote for remote in remotes if remote != "origin")]

    candidates = [f"{remote}/HEAD" for remote in remotes]
    for remote in remotes:
        candidates.extend((f"{remote}/main", f"{remote}/master"))
    candidates.extend(("main", "master"))

    # Preserve priority while removing duplicates.
    return list(dict.fromkeys(candidates))


def resolve_base(repo: Path, cli_base: str | None, config: ReviewConfig) -> tuple[str, str]:
    explicit = cli_base or os.environ.get("REVIEW_BASE_REF") or config.base_ref
    if explicit:
        commit = verified_commit(repo, explicit)
        if not commit:
            raise DiffCollectionError(
                f"configured review base {explicit!r} does not resolve to a commit; "
                "fetch it or update local.base_ref"
            )
        return explicit, commit

    for candidate in automatic_base_candidates(repo):
        commit = verified_commit(repo, candidate)
        if commit:
            return candidate, commit

    raise DiffCollectionError(
        "could not determine the target branch; set local.base_ref in .github/review.config.yml "
        "or REVIEW_BASE_REF"
    )


def parse_name_status(raw: bytes) -> list[Change]:
    fields = raw.decode("utf-8", errors="surrogateescape").split("\0")
    if fields and fields[-1] == "":
        fields.pop()

    changes: list[Change] = []
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        if status.startswith(("R", "C")):
            if index + 1 >= len(fields):
                raise DiffCollectionError("unexpected truncated rename/copy entry from git diff")
            old_path, new_path = fields[index], fields[index + 1]
            index += 2
        else:
            if index >= len(fields):
                raise DiffCollectionError("unexpected truncated path entry from git diff")
            old_path = new_path = fields[index]
            index += 1
        changes.append(Change(status=status, old_path=old_path, new_path=new_path))
    return changes


def tracked_changes(repo: Path, merge_base: str) -> list[Change]:
    result = git(
        "diff",
        "--name-status",
        "-z",
        "--find-renames",
        merge_base,
        "--",
        cwd=repo,
    )
    return parse_name_status(result.stdout)


def untracked_paths(repo: Path) -> list[str]:
    result = git("ls-files", "--others", "--exclude-standard", "-z", cwd=repo)
    return [
        path
        for path in result.stdout.decode("utf-8", errors="surrogateescape").split("\0")
        if path
    ]


def indexed_paths(repo: Path) -> list[str]:
    result = git("ls-files", "-z", cwd=repo)
    return [
        path
        for path in result.stdout.decode("utf-8", errors="surrogateescape").split("\0")
        if path and (repo / path).exists()
    ]


def tracked_patch(repo: Path, merge_base: str, changes: list[Change]) -> str:
    if not changes:
        return ""
    paths = sorted({path for change in changes for path in (change.old_path, change.new_path)})
    result = git(
        "diff",
        "--no-ext-diff",
        "--find-renames",
        "--no-color",
        merge_base,
        "--",
        *paths,
        cwd=repo,
    )
    return result.stdout.decode("utf-8", errors="replace")


def untracked_patch(repo: Path, path: str) -> str:
    result = git(
        "diff",
        "--no-index",
        "--no-ext-diff",
        "--no-color",
        "--",
        os.devnull,
        path,
        cwd=repo,
        allowed_returncodes=(0, 1),
    )
    return result.stdout.decode("utf-8", errors="replace")


_CHUNK_PATH = re.compile(r'^diff --git (?:"?a/(?P<old>[^"\n]+)"?|.*) "?b/(?P<new>[^"\n]+)"?$')


def split_patch(patch: str) -> list[tuple[str, str]]:
    """Split a combined patch into (path, chunk) per file."""
    chunks: list[tuple[str, str]] = []
    current: list[str] = []
    path = ""
    for line in patch.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if current:
                chunks.append((path, "".join(current)))
            current = []
            match = _CHUNK_PATH.match(line.rstrip("\n"))
            path = match.group("new") if match else ""
        current.append(line)
    if current:
        chunks.append((path, "".join(current)))
    return chunks


def apply_budgets(
    chunks: list[tuple[str, str]],
    max_file_bytes: int,
    max_total_bytes: int,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Return (kept chunks, [(reason, path)] for excluded chunks)."""
    kept: list[tuple[str, str]] = []
    excluded: list[tuple[str, str]] = []
    total = 0
    for path, chunk in chunks:
        size = len(chunk.encode("utf-8", errors="replace"))
        if size > max_file_bytes:
            excluded.append(("too-large", path))
            continue
        if total + size > max_total_bytes:
            excluded.append(("budget-exhausted", path))
            continue
        total += size
        kept.append((path, chunk))
    return kept, excluded


def render_manifest(
    *,
    base_ref: str,
    merge_base: str,
    included: list[str],
    ignored: list[str],
    unavailable: list[tuple[str, str]],
    statuses: dict[str, str],
    secret_findings: list[secretscan.Finding] | None,
    codebase_context: str | None = None,
) -> str:
    lines = [
        "# review-diff v1",
        f"base-ref: {json.dumps(base_ref)}",
        f"merge-base: {merge_base}",
        f"reviewable-files: {len(included)}",
        f"ignored-files: {len(ignored)}",
        f"unavailable-files: {len(unavailable)}",
    ]
    if secret_findings is not None:
        lines.append(f"secret-candidates: {len(secret_findings)}")
    lines.extend(("", "## File manifest"))
    for path in included:
        lines.append(f"included\t{statuses.get(path, 'modified')}\t{json.dumps(path)}")
    for path in ignored:
        lines.append(f"ignored\t{statuses.get(path, 'modified')}\t{json.dumps(path)}")
    for reason, path in unavailable:
        lines.append(f"unavailable\t{reason}\t{json.dumps(path)}")
    body = "\n".join(lines) + "\n"
    if secret_findings is not None:
        body += "\n" + secretscan.render_section(secret_findings)
    if codebase_context:
        body += "\n" + codebase_context
    return body + "\n## Patch\n\n"


def repository_root(start: Path) -> Path:
    result = git("rev-parse", "--show-toplevel", cwd=start)
    return Path(result.stdout.decode().strip())


def collect(
    start: Path,
    cli_base: str | None,
    config_path: str,
    secret_scan: bool,
    codebase_context: bool = False,
) -> str:
    repo = repository_root(start)
    config_file = Path(config_path)
    if not config_file.is_absolute():
        config_file = repo / config_file
    try:
        config = load_config(config_file)
        ignore_globs = config.ignore_globs
        max_file_bytes = config.limit_bytes("max_file_patch_kb")
        max_total_bytes = config.limit_bytes("max_total_patch_kb")
    except ConfigError as exc:
        raise DiffCollectionError(str(exc)) from exc

    statuses: dict[str, str] = {}
    if not verified_commit(repo, "HEAD"):
        base_ref, merge_base = "<empty repository>", "<none>"
        tracked_text = ""
        candidate_paths = indexed_paths(repo)
        for path in candidate_paths:
            statuses[path] = "initial"
        untracked = [path for path in untracked_paths(repo) if path not in candidate_paths]
    else:
        base_ref, base_commit = resolve_base(repo, cli_base, config)
        merge_base_result = git("merge-base", "HEAD", base_commit, cwd=repo)
        merge_base = merge_base_result.stdout.decode().strip()
        if not merge_base:
            raise DiffCollectionError(f"HEAD and {base_ref!r} do not have a merge base")

        changes = tracked_changes(repo, merge_base)
        candidate_paths = []
        included_changes = []
        for change in changes:
            statuses[change.display_path] = change.status
            if is_ignored(change.new_path, ignore_globs):
                continue
            candidate_paths.append(change.display_path)
            included_changes.append(change)
        tracked_text = tracked_patch(repo, merge_base, included_changes)
        untracked = untracked_paths(repo)
        # Re-map ignored tracked paths for the manifest below.
        candidate_paths = [change.display_path for change in included_changes]
        changes_all = changes
        ignored_tracked = [
            change.display_path
            for change in changes_all
            if is_ignored(change.new_path, ignore_globs)
        ]

    for path in untracked:
        statuses.setdefault(path, "untracked")

    included_untracked = [path for path in untracked if not is_ignored(path, ignore_globs)]
    ignored_untracked = [path for path in untracked if path not in included_untracked]

    if merge_base == "<none>":
        included_tracked_display = [
            path for path in candidate_paths if not is_ignored(path, ignore_globs)
        ]
        ignored_tracked = [
            path for path in candidate_paths if path not in included_tracked_display
        ]
        chunks = [
            (path, untracked_patch(repo, path))
            for path in [*included_tracked_display, *included_untracked]
        ]
    else:
        included_tracked_display = candidate_paths
        chunks = split_patch(tracked_text)
        chunks.extend((path, untracked_patch(repo, path)) for path in included_untracked)

    kept, excluded = apply_budgets(
        chunks,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
    )
    kept_patch = "".join(chunk for _, chunk in kept)
    excluded_paths = {path for _, path in excluded}

    included_paths = [
        path
        for path in [*included_tracked_display, *included_untracked]
        if path not in excluded_paths
        and path.split(" -> ")[-1] not in excluded_paths
    ]
    ignored_paths = [*ignored_tracked, *ignored_untracked]

    findings = secretscan.scan_patch(kept_patch) if secret_scan else None

    context_section: str | None = None
    if codebase_context and merge_base != "<none>":
        try:
            context = codegraph.build_context(
                repo,
                kept,
                {path for path, _ in kept},
                ignore_globs,
                max_files=config.deep_int("max_files"),
                max_refs_per_symbol=config.deep_int("max_refs_per_symbol"),
                co_change_lookback=config.deep_int("co_change_lookback"),
                enable_co_change=config.deep_flag("enable_co_change"),
                budget_bytes=config.deep_int("context_budget_kb") * 1024,
            )
        except ConfigError as exc:
            raise DiffCollectionError(str(exc)) from exc
        context_section = codegraph.render_section(context)

    manifest = render_manifest(
        base_ref=base_ref,
        merge_base=merge_base,
        included=included_paths,
        ignored=ignored_paths,
        unavailable=excluded,
        statuses=statuses,
        secret_findings=findings,
        codebase_context=context_section,
    )
    return manifest + kept_patch


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="target branch/ref; overrides config and REVIEW_BASE_REF")
    parser.add_argument(
        "--config",
        default=".github/review.config.yml",
        help="review configuration path relative to the repository root",
    )
    parser.add_argument(
        "--secret-scan",
        action="store_true",
        help="append a deterministic redacted credential scan of included added lines",
    )
    parser.add_argument(
        "--codebase-context",
        action="store_true",
        help="append a bounded codebase-context section (references to changed "
        "symbols + co-changing files) for codebase-aware 'deep' review",
    )
    args = parser.parse_args()

    try:
        output = collect(
            Path.cwd(), args.base, args.config, args.secret_scan, args.codebase_context
        )
    except DiffCollectionError as exc:
        print(f"[collect-review-diff] {exc}", file=sys.stderr)
        return 2
    print(output, end="" if output.endswith("\n") else "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
