#!/usr/bin/env bash
# Install or update the Copilot + GitLab review toolkit in another repository.
#
# From the adopting repository's root:
#
#   /path/to/code-review/install.sh            # install, or update if installed
#   /path/to/code-review/install.sh --check    # report drift; exit 1 if behind
#   /path/to/code-review/install.sh --dry-run  # print actions without writing
#
# Or without a local toolkit checkout:
#
#   install.sh --repo git@gitlab.example.com:group/code-review.git --ref v0.1.0
#
# File ownership is declared in install.manifest: [owned] paths are replaced
# on every run and pruned when removed upstream; [seed] paths and the
# .vscode/mcp.json merge happen on first install only and are never
# overwritten. .gitlab-ci.yml and .github/copilot-instructions.md are never
# written. State lives in .github/.code-review-toolkit.lock — commit it.

set -euo pipefail

LOCK=".github/.code-review-toolkit.lock"

die() { printf 'install.sh: error: %s\n' "$*" >&2; exit 2; }
note() { printf '%s\n' "$*"; }

usage() {
  cat <<'EOF'
Usage: install.sh [--update | --check] [--dry-run] [--repo <url> [--ref <tag>]] [--target <dir>]

Install or update the Copilot + GitLab review toolkit in the git repository
rooted at --target (default: current directory).

  (no mode)     install on first run; update when the lock file exists
  --update      update only; fail if the toolkit is not installed yet
  --check       report version and file drift; exit 1 if behind, 0 if current
  --dry-run     print every action without writing anything
  --repo URL    clone the toolkit from URL instead of running from a checkout
                (also read from $REVIEW_TOOLKIT_REPO)
  --ref REF     tag or branch to clone with --repo
  --target DIR  adopting repository root (default: current directory)

First install also seeds project-owned files (.github/review.config.yml and the
conventions placeholder) and merges the pinned gitlab-review server into
.vscode/mcp.json without touching other servers. Updates replace only the
toolkit-owned paths listed in install.manifest and delete files that were
removed upstream; project-owned files are never overwritten or re-created.
State: .github/.code-review-toolkit.lock (commit it).
EOF
}

MODE=install
DRY_RUN=0
REPO_URL="${REVIEW_TOOLKIT_REPO:-}"
REF=""
TARGET="$PWD"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --update) MODE=update ;;
    --check) MODE=check ;;
    --dry-run) DRY_RUN=1 ;;
    --repo) [[ $# -ge 2 ]] || die "--repo needs a clone URL"; REPO_URL="$2"; shift ;;
    --ref) [[ $# -ge 2 ]] || die "--ref needs a tag or branch"; REF="$2"; shift ;;
    --target) [[ $# -ge 2 ]] || die "--target needs a directory"; TARGET="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
  shift
done

# --- Locate the toolkit source (a checkout next to this script, or a clone).
CLONE_DIR=""
cleanup() { if [[ -n "$CLONE_DIR" ]]; then rm -rf "$CLONE_DIR"; fi; }
trap cleanup EXIT

if [[ -n "$REPO_URL" ]]; then
  CLONE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/code-review-toolkit.XXXXXX")"
  git clone --quiet --depth 1 ${REF:+--branch "$REF"} "$REPO_URL" "$CLONE_DIR" \
    || die "clone failed: $REPO_URL${REF:+ (ref $REF)}"
  SRC="$CLONE_DIR"
else
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd -P)" || SCRIPT_DIR=""
  if [[ -n "$SCRIPT_DIR" && -f "$SCRIPT_DIR/install.manifest" ]]; then
    SRC="$SCRIPT_DIR"
  else
    die "not running from a toolkit checkout; pass --repo <clone url> (and optionally --ref <tag>)"
  fi
fi
SRC="$(cd "$SRC" && pwd -P)"

# --- Validate the target: must be the root of a git repository, not the toolkit.
TARGET="$(cd "$TARGET" 2>/dev/null && pwd -P)" || die "target directory not found: $TARGET"
TOPLEVEL="$(cd "$TARGET" && git rev-parse --show-toplevel 2>/dev/null)" \
  || die "target is not inside a git repository: $TARGET"
[[ "$TOPLEVEL" == "$TARGET" ]] || die "run from the repository root: $TOPLEVEL"
[[ "$TARGET" != "$SRC" ]] || die "target is the toolkit repository itself"

# --- Read the manifest.
MANIFEST="$SRC/install.manifest"
[[ -f "$MANIFEST" ]] || die "missing install.manifest in $SRC"

manifest_section() {
  awk -v sec="[$1]" '
    $0 == sec { insec = 1; next }
    /^\[/ { insec = 0 }
    insec && NF && substr($1, 1, 1) != "#" { print $1 }
  ' "$MANIFEST"
}

OWNED_PATHS="$(manifest_section owned)"
SEED_PATHS="$(manifest_section seed)"
[[ -n "$OWNED_PATHS" ]] || die "install.manifest has no [owned] entries"

NEVER_TOUCH=".gitlab-ci.yml
.github/copilot-instructions.md
.vscode/mcp.json"

while IFS= read -r p; do
  [[ -n "$p" ]] || continue
  if grep -qxF "$p" <<<"$NEVER_TOUCH"; then
    die "install.manifest must not list $p"
  fi
done <<<"$OWNED_PATHS
$SEED_PATHS"

under_owned() {
  local f="$1" p
  for p in $OWNED_PATHS; do
    case "$f" in "$p"/*|"$p") return 0 ;; esac
  done
  return 1
}

# --- Expand owned paths to the exact file set this version ships.
owned_files() {
  local p
  while IFS= read -r p; do
    [[ -n "$p" ]] || continue
    if [[ -d "$SRC/$p" ]]; then
      (cd "$SRC" && find "$p" -type f ! -name '*.pyc' ! -path '*/__pycache__/*')
    elif [[ -f "$SRC/$p" ]]; then
      printf '%s\n' "$p"
    else
      die "manifest path missing in source: $p"
    fi
  done <<<"$OWNED_PATHS" | sort
}
NEW_FILES="$(owned_files)"

SRC_VERSION="$(git -C "$SRC" describe --tags --always --dirty 2>/dev/null || echo unknown)"
SRC_COMMIT="$(git -C "$SRC" rev-parse HEAD 2>/dev/null || echo unknown)"

# --- Read the previous install, if any.
OLD_VERSION=""
OLD_COMMIT=""
OLD_FILES=""
FRESH=1
if [[ -f "$TARGET/$LOCK" ]]; then
  FRESH=0
  OLD_VERSION="$(sed -n 's/^version=//p' "$TARGET/$LOCK")"
  OLD_COMMIT="$(sed -n 's/^commit=//p' "$TARGET/$LOCK")"
  OLD_FILES="$(awk 'insec { print } $0 == "files:" { insec = 1 }' "$TARGET/$LOCK")"
fi

# --- Check mode: report drift, change nothing. Exit 1 when out of date.
if [[ "$MODE" == check ]]; then
  if (( FRESH )); then
    note "not installed (no $LOCK)"
    exit 1
  fi
  drift=0
  if [[ "$OLD_COMMIT" != "$SRC_COMMIT" ]]; then
    note "installed: ${OLD_VERSION:-unknown} (${OLD_COMMIT:-unknown})"
    note "source:    $SRC_VERSION ($SRC_COMMIT)"
    drift=1
  fi
  while IFS= read -r f; do
    [[ -n "$f" ]] || continue
    if [[ ! -f "$TARGET/$f" ]]; then
      note "missing: $f"; drift=1
    elif ! cmp -s "$SRC/$f" "$TARGET/$f"; then
      note "differs: $f"; drift=1
    fi
  done <<<"$NEW_FILES"
  while IFS= read -r f; do
    [[ -n "$f" ]] || continue
    grep -qxF "$f" <<<"$NEW_FILES" && continue
    if [[ -f "$TARGET/$f" ]]; then
      note "stale:   $f"; drift=1
    fi
  done <<<"$OLD_FILES"
  if (( drift )); then
    note "out of date with $SRC_VERSION — run install.sh --update"
    exit 1
  fi
  note "up to date: $SRC_VERSION"
  exit 0
fi

if [[ "$MODE" == update ]] && (( FRESH )); then
  die "nothing installed in $TARGET yet; run without --update"
fi

run() {
  if (( DRY_RUN )); then
    note "[dry-run] $*"
  else
    "$@"
  fi
}

# --- Copy toolkit-owned files.
created=0
updated=0
unchanged=0
while IFS= read -r f; do
  [[ -n "$f" ]] || continue
  if [[ -f "$TARGET/$f" ]]; then
    if cmp -s "$SRC/$f" "$TARGET/$f"; then
      unchanged=$((unchanged + 1))
      continue
    fi
    updated=$((updated + 1))
  else
    created=$((created + 1))
  fi
  run mkdir -p "$TARGET/$(dirname "$f")"
  run cp "$SRC/$f" "$TARGET/$f"
done <<<"$NEW_FILES"

# --- Delete owned files this installer wrote previously that no longer ship.
removed=0
while IFS= read -r f; do
  [[ -n "$f" ]] || continue
  grep -qxF "$f" <<<"$NEW_FILES" && continue
  [[ -f "$TARGET/$f" ]] || continue
  if ! under_owned "$f"; then
    note "skipping stale file outside owned paths: $f"
    continue
  fi
  removed=$((removed + 1))
  run rm "$TARGET/$f"
done <<<"$OLD_FILES"

if (( removed )) && (( ! DRY_RUN )); then
  while IFS= read -r p; do
    [[ -n "$p" && -d "$TARGET/$p" ]] || continue
    find "$TARGET/$p" -depth -type d -empty -delete
  done <<<"$OWNED_PATHS"
fi

# --- First install only: seed project-owned files and set up mcp.json.
seeded=0
mcp_status=""
if (( FRESH )); then
  while IFS= read -r f; do
    [[ -n "$f" ]] || continue
    [[ -e "$TARGET/$f" ]] && continue
    [[ -f "$SRC/$f" ]] || die "manifest seed missing in source: $f"
    seeded=$((seeded + 1))
    run mkdir -p "$TARGET/$(dirname "$f")"
    run cp "$SRC/$f" "$TARGET/$f"
  done <<<"$SEED_PATHS"

  MCP_EXAMPLE="$SRC/docs/gitlab-mcp.example.json"
  MCP_TARGET="$TARGET/.vscode/mcp.json"
  if [[ -f "$MCP_EXAMPLE" ]]; then
    if [[ ! -f "$MCP_TARGET" ]]; then
      run mkdir -p "$TARGET/.vscode"
      run cp "$MCP_EXAMPLE" "$MCP_TARGET"
      mcp_status=created
    elif (( DRY_RUN )); then
      note "[dry-run] merge gitlab-review server into .vscode/mcp.json if missing"
      mcp_status=dry-run
    elif command -v python3 >/dev/null 2>&1; then
      mcp_status="$(python3 - "$MCP_EXAMPLE" "$MCP_TARGET" <<'PY'
import json
import sys

example_path, target_path = sys.argv[1], sys.argv[2]
with open(example_path, encoding="utf-8") as fh:
    example = json.load(fh)
try:
    with open(target_path, encoding="utf-8") as fh:
        target = json.load(fh)
except ValueError:
    target = None
if not isinstance(target, dict):
    # JSONC or unexpected shape: leave the file alone, ask for a manual merge.
    print("manual")
    raise SystemExit(0)
servers = target.setdefault("servers", {})
if "gitlab-review" in servers:
    print("present")
    raise SystemExit(0)
servers["gitlab-review"] = example["servers"]["gitlab-review"]
example_inputs = [item for item in example.get("inputs", []) if isinstance(item, dict)]
if example_inputs:
    inputs = target.setdefault("inputs", [])
    existing = {item.get("id") for item in inputs if isinstance(item, dict)}
    inputs.extend(item for item in example_inputs if item.get("id") not in existing)
with open(target_path, "w", encoding="utf-8") as fh:
    json.dump(target, fh, indent=2)
    fh.write("\n")
print("merged")
PY
)"
    else
      mcp_status=manual
    fi
  fi
fi

# --- Record what was installed so updates and --check have ground truth.
if (( ! DRY_RUN )); then
  mkdir -p "$TARGET/.github"
  {
    echo "# Managed by the code-review toolkit installer. Commit this file; do not edit."
    echo "version=$SRC_VERSION"
    echo "commit=$SRC_COMMIT"
    echo "installed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "files:"
    printf '%s\n' "$NEW_FILES"
  } >"$TARGET/$LOCK"
fi

# --- Summary.
if (( FRESH )); then
  note "installed $SRC_VERSION: $created toolkit files"
else
  note "updated to $SRC_VERSION (was ${OLD_VERSION:-unknown}): $created new, $updated updated, $unchanged unchanged, $removed removed"
fi
if (( seeded )); then
  note "seeded $seeded project-owned file(s) — yours now, never overwritten on update"
fi
case "$mcp_status" in
  created) note ".vscode/mcp.json: created from the pinned example" ;;
  merged)  note ".vscode/mcp.json: added the pinned gitlab-review server; existing servers untouched" ;;
  present) note ".vscode/mcp.json: gitlab-review server already present — left untouched" ;;
  manual)  note ".vscode/mcp.json: could not merge automatically — merge the gitlab-review server from docs/gitlab-mcp.example.json by hand" ;;
esac
if (( FRESH )) && (( ! DRY_RUN )); then
  note ""
  note "Finish per-project setup (see README 'Adopt in a repository'):"
  note "  1. Set GITLAB_API_URL in .vscode/mcp.json; the token is entered via its promptString"
  note "  2. For the fast collector, export GITLAB_TOKEN + GITLAB_API_URL in the reviewer's shell"
  note "     (without them, evidence collection falls back to the slower MCP reads)"
  note "  3. Replace or remove the placeholder .github/instructions/conventions.instructions.md"
  note "  4. Tune .github/review.config.yml (scanner artifact paths, budgets, strictness)"
  note "  5. Verify agents, skills, and MCP tools in VS Code Chat diagnostics"
fi
exit 0
