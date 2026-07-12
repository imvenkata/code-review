#!/usr/bin/env python3
"""Install or update this review toolkit in another repository.

Toolkit-owned files (agents, the three toolkit skills, collector scripts) are
synced and overwritten so updates propagate. Project-owned files
(review.config.yml, the placeholder instructions file) are created only when
absent. Files the toolkit must never touch (.vscode/mcp.json, .gitlab-ci.yml,
.github/copilot-instructions.md, a project's own skills or instructions) are
left alone; merge docs/gitlab-mcp.example.json into the MCP config manually.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Overwritten on every run: the toolkit owns these exact paths and nothing else.
SYNCED = (
    ".github/agents/code-review.agent.md",
    ".github/agents/review-mr.agent.md",
    ".github/skills/review-standards",
    ".github/skills/requirements-traceability",
    ".github/skills/gitlab-review-evidence",
    ".github/scripts/collect-review-diff.py",
    ".github/scripts/collect-mr-evidence.py",
    ".github/scripts/reviewlib",
)

# Created once, then owned and tuned by the target project.
CREATED_IF_ABSENT = (
    "review.config.yml",
    ".github/instructions/conventions.instructions.md",
)


def _copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__"))
    else:
        shutil.copy2(source, destination)


def adopt(target: Path) -> list[str]:
    if not target.is_dir():
        raise SystemExit(f"[adopt] target is not a directory: {target}")
    if target.resolve() == ROOT:
        raise SystemExit("[adopt] target is this toolkit repository itself")

    actions: list[str] = []
    for rel in SYNCED:
        _copy(ROOT / rel, target / rel)
        actions.append(f"synced  {rel}")
    for rel in CREATED_IF_ABSENT:
        destination = target / rel
        if destination.exists():
            actions.append(f"kept    {rel} (project-owned)")
            continue
        _copy(ROOT / rel, destination)
        actions.append(f"created {rel}")
    return actions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", help="path to the repository adopting the toolkit")
    args = parser.parse_args()

    target = Path(args.target)
    if not (target / ".git").exists():
        print(f"[adopt] warning: {target} does not look like a git repository", file=sys.stderr)

    for action in adopt(target):
        print(action)
    print(
        "\nNext steps:\n"
        "  1. Replace the placeholder .github/instructions/conventions.instructions.md.\n"
        "  2. Merge docs/gitlab-mcp.example.json into the repo's .vscode/mcp.json (never overwrite).\n"
        "  3. Tune review.config.yml (strictness, security modes, path filters) for this repo.\n"
        "  4. Verify agents, skills, and MCP tools in VS Code Chat diagnostics."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
