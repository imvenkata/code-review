#!/usr/bin/env python3
"""Collect the complete local change set for the code-review agent.

The script is deliberately read-only. It compares the current working tree with
the merge base of the configured/default target branch and appends synthetic
diffs for untracked files. It never stages files, fetches remotes, or changes
repository configuration.
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


class DiffCollectionError(RuntimeError):
    """A user-actionable failure while discovering the review diff."""


@dataclass(frozen=True)
class Config:
    base_ref: str | None
    ignore_globs: tuple[str, ...]


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


def parse_scalar(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""
    if value[0] in {'"', "'"}:
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError) as exc:
            raise DiffCollectionError(f"invalid quoted value in review.config.yml: {value}") from exc
        return str(parsed)
    return value.split(" #", 1)[0].strip()


def load_config(path: Path) -> Config:
    if not path.exists():
        return Config(base_ref=None, ignore_globs=())

    top_level: str | None = None
    reading_ignore = False
    base_ref: str | None = None
    ignore_globs: list[str] = []

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(raw_line) - len(raw_line.lstrip())
        if indent == 0:
            top_level = stripped[:-1] if stripped.endswith(":") else None
            reading_ignore = False
            continue

        if top_level == "local" and indent == 2 and stripped.startswith("base_ref:"):
            configured = parse_scalar(stripped.partition(":")[2])
            base_ref = configured or None
            continue

        if top_level == "path_filters":
            if indent == 2:
                reading_ignore = stripped == "ignore:"
                continue
            if reading_ignore and indent >= 4 and stripped.startswith("- "):
                pattern = parse_scalar(stripped[2:])
                if pattern:
                    ignore_globs.append(pattern)

    return Config(base_ref=base_ref, ignore_globs=tuple(ignore_globs))


def is_ignored(path: str, globs: tuple[str, ...]) -> bool:
    for pattern in globs:
        if fnmatch.fnmatch(path, pattern):
            return True
        if pattern.startswith("**/") and fnmatch.fnmatch(path, pattern[3:]):
            return True
    return False


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


def resolve_base(repo: Path, cli_base: str | None, config: Config) -> tuple[str, str]:
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
        "could not determine the target branch; set local.base_ref in review.config.yml "
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


def untracked_patch(repo: Path, paths: list[str]) -> str:
    patches: list[str] = []
    for path in paths:
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
        patches.append(result.stdout.decode("utf-8", errors="replace"))
    return "".join(patches)


def render_manifest(
    *,
    base_ref: str,
    merge_base: str,
    included: list[Change],
    ignored: list[Change],
    included_untracked: list[str],
    ignored_untracked: list[str],
) -> str:
    lines = [
        "# review-diff v1",
        f"base-ref: {json.dumps(base_ref)}",
        f"merge-base: {merge_base}",
        f"reviewable-files: {len(included) + len(included_untracked)}",
        f"ignored-files: {len(ignored) + len(ignored_untracked)}",
        "",
        "## File manifest",
    ]
    for change in included:
        lines.append(f"included\t{change.status}\t{json.dumps(change.display_path)}")
    for path in included_untracked:
        lines.append(f"included\tuntracked\t{json.dumps(path)}")
    for change in ignored:
        lines.append(f"ignored\t{change.status}\t{json.dumps(change.display_path)}")
    for path in ignored_untracked:
        lines.append(f"ignored\tuntracked\t{json.dumps(path)}")
    lines.extend(("", "## Patch", ""))
    return "\n".join(lines)


def repository_root(start: Path) -> Path:
    result = git("rev-parse", "--show-toplevel", cwd=start)
    return Path(result.stdout.decode().strip())


def collect(start: Path, cli_base: str | None, config_path: str) -> str:
    repo = repository_root(start)
    config_file = Path(config_path)
    if not config_file.is_absolute():
        config_file = repo / config_file
    config = load_config(config_file)

    if not verified_commit(repo, "HEAD"):
        initial_paths = indexed_paths(repo)
        untracked = untracked_paths(repo)
        included_initial = [
            path for path in initial_paths if not is_ignored(path, config.ignore_globs)
        ]
        ignored_initial = [path for path in initial_paths if path not in included_initial]
        included_untracked = [
            path for path in untracked if not is_ignored(path, config.ignore_globs)
        ]
        ignored_untracked = [path for path in untracked if path not in included_untracked]
        initial_changes = [Change("initial", path, path) for path in included_initial]
        ignored_changes = [Change("initial", path, path) for path in ignored_initial]
        manifest = render_manifest(
            base_ref="<empty repository>",
            merge_base="<none>",
            included=initial_changes,
            ignored=ignored_changes,
            included_untracked=included_untracked,
            ignored_untracked=ignored_untracked,
        )
        return manifest + untracked_patch(
            repo,
            [*included_initial, *included_untracked],
        )

    base_ref, base_commit = resolve_base(repo, cli_base, config)
    merge_base_result = git("merge-base", "HEAD", base_commit, cwd=repo)
    merge_base = merge_base_result.stdout.decode().strip()
    if not merge_base:
        raise DiffCollectionError(f"HEAD and {base_ref!r} do not have a merge base")

    changes = tracked_changes(repo, merge_base)
    included = [
        change for change in changes if not is_ignored(change.new_path, config.ignore_globs)
    ]
    ignored = [change for change in changes if change not in included]

    untracked = untracked_paths(repo)
    included_untracked = [
        path for path in untracked if not is_ignored(path, config.ignore_globs)
    ]
    ignored_untracked = [path for path in untracked if path not in included_untracked]

    manifest = render_manifest(
        base_ref=base_ref,
        merge_base=merge_base,
        included=included,
        ignored=ignored,
        included_untracked=included_untracked,
        ignored_untracked=ignored_untracked,
    )
    return manifest + tracked_patch(repo, merge_base, included) + untracked_patch(
        repo, included_untracked
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="target branch/ref; overrides config and REVIEW_BASE_REF")
    parser.add_argument(
        "--config",
        default="review.config.yml",
        help="review configuration path relative to the repository root",
    )
    args = parser.parse_args()

    try:
        output = collect(Path.cwd(), args.base, args.config)
    except DiffCollectionError as exc:
        print(f"[collect-review-diff] {exc}", file=sys.stderr)
        return 2
    print(output, end="" if output.endswith("\n") else "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
