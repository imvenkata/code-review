#!/usr/bin/env python3
"""Phase-2 automated MR review.

Runs headless in GitLab CI on merge_request_event. Uses the same brain as the IDE
agents (.github/skills/review-standards/SKILL.md), the Anthropic API with prompt
caching on that stable prefix, and posts findings back via a bot token.

Env (see ci/ai-review.gitlab-ci.yml):
  ANTHROPIC_API_KEY, REVIEW_BOT_TOKEN            (required, masked CI vars)
  GITLAB_API_URL, CI_PROJECT_ID, CI_MERGE_REQUEST_IID   (predefined in CI)
  REVIEW_MODEL (default claude-opus-4-8), REVIEW_EFFORT (default high),
  REVIEW_STRICTNESS (default from review.config.yml)
"""
from __future__ import annotations

import os
import re
import sys
import json
import fnmatch

import yaml
import requests
import anthropic

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKER_RE = re.compile(r"<!--\s*ai-review head=([0-9a-f]{7,40})\s*-->")
STRICTNESS_THRESHOLD = {"low": 90, "medium": 80, "high": 70}
MAX_DIFF_CHARS = 80_000  # token budget guard for the whole MR

# Footer appended to the summary note. The CI bot is advisory and never acts; this points the
# reviewer to where they can. Override via review.config.yml `review.next_actions` ("" to omit).
DEFAULT_NEXT_ACTIONS = (
    "**Next:** act without leaving your editor — open this MR with the **review-mr** Copilot agent "
    "(approve / reply / resolve from chat). Or use GitLab quick actions in a comment, e.g. "
    "`/approve`, `/label ~needs-changes`, `/assign_reviewer @you`."
)

# Models that accept adaptive thinking + the `effort` parameter. Others (Haiku 4.5,
# Sonnet 4.5, ...) 400 on those params, so we omit them and rely on structured output alone.
REASONING_MODELS = ("claude-opus-4-6", "claude-opus-4-7", "claude-opus-4-8",
                    "claude-sonnet-5", "claude-sonnet-4-6", "claude-fable-5")

FINDINGS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["findings", "summary"],
    "properties": {
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["file", "line", "severity", "title", "explanation",
                             "suggestion", "confidence"],
                "properties": {
                    "file": {"type": "string"},
                    "line": {"type": "integer"},
                    "severity": {"type": "string", "enum": ["critical", "important"]},
                    "title": {"type": "string"},
                    "explanation": {"type": "string"},
                    "suggestion": {"type": "string"},
                    "confidence": {"type": "integer"},
                },
            },
        },
    },
}


def stop(msg: str, code: int = 0):
    print(f"[ai-review] {msg}")
    sys.exit(code)


def require(name: str) -> str:
    """Mandatory env input — fail loudly (not a raw KeyError) if missing/empty."""
    val = os.environ.get(name)
    if not val:
        stop(f"missing required input: {name}", code=1)
    return val


# --- config + shared brain ----------------------------------------------------

def load_config() -> dict:
    path = os.path.join(REPO, "review.config.yml")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_standards() -> str:
    """The review-standards SKILL.md body, minus YAML frontmatter — the cached prefix."""
    path = os.path.join(REPO, ".github", "skills", "review-standards", "SKILL.md")
    with open(path) as f:
        text = f.read()
    if text.startswith("---"):
        text = text.split("---", 2)[-1]
    return text.strip()


# --- gitlab ------------------------------------------------------------------

class GitLab:
    def __init__(self):
        self.base = require("GITLAB_API_URL").rstrip("/")
        self.pid = require("CI_PROJECT_ID")          # mandatory: GitLab project ID
        self.iid = require("CI_MERGE_REQUEST_IID")   # mandatory: MR ID (iid)
        self.s = requests.Session()
        self.s.headers["PRIVATE-TOKEN"] = require("REVIEW_BOT_TOKEN")

    def _mr(self, suffix=""):
        return f"{self.base}/projects/{self.pid}/merge_requests/{self.iid}{suffix}"

    def _get(self, url, **kw):
        r = self.s.get(url, **kw)
        if r.status_code == 404:
            stop(f"not found — check project id ({self.pid}) / MR id ({self.iid}): GET {url} -> 404", code=1)
        if not r.ok:
            stop(f"gitlab error {r.status_code} on {url}: {r.text[:200]}", code=1)
        return r

    def _paged(self, suffix: str) -> list:
        out, page = [], 1
        while True:
            batch = self._get(self._mr(suffix), params={"per_page": 100, "page": page}).json()
            out += batch
            if len(batch) < 100:
                return out
            page += 1

    def me(self) -> str:
        return self._get(f"{self.base}/user").json()["username"]

    def mr(self) -> dict:
        return self._get(self._mr()).json()

    def diffs(self) -> list:
        return self._paged("/diffs")   # paginated; supersedes the deprecated /changes

    def notes(self) -> list:
        return self._paged("/notes")

    def post_note(self, body: str):
        self.s.post(self._mr("/notes"), json={"body": body})

    def post_thread(self, body: str, position: dict):
        r = self.s.post(self._mr("/discussions"), json={"body": body, "position": position})
        if not r.ok:
            print(f"[ai-review] inline thread failed ({r.status_code}): {r.text[:200]}")


# --- diff parsing ------------------------------------------------------------

HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def anchorable_lines(diff: str) -> set[int]:
    """New-file line numbers that exist in this diff (added or context) -> postable."""
    valid, new_no = set(), 0
    for line in diff.splitlines():
        m = HUNK_RE.match(line)
        if m:
            new_no = int(m.group(1))
            continue
        if new_no == 0:
            continue  # ignore any header lines before the first hunk
        if line.startswith("+") and not line.startswith("+++"):
            valid.add(new_no)
            new_no += 1
        elif line.startswith("-") and not line.startswith("---"):
            continue
        elif line.startswith("\\"):  # "\ No newline at end of file"
            continue
        else:  # context
            valid.add(new_no)
            new_no += 1
    return valid


def is_ignored(path: str, globs: list[str]) -> bool:
    for g in globs:
        # fnmatch is not glob: `**` is not special and `*` spans `/`. So also try the
        # pattern with a leading `**/` stripped, which lets `**/foo` match root-level `foo`.
        if fnmatch.fnmatch(path, g):
            return True
        if g.startswith("**/") and fnmatch.fnmatch(path, g[3:]):
            return True
    return False


# --- main --------------------------------------------------------------------

def main():
    cfg = load_config()
    ignore = (cfg.get("path_filters") or {}).get("ignore", [])
    review_cfg = cfg.get("review") or {}
    max_inline = int(review_cfg.get("max_inline_comments", 15))
    enable_suggestions = review_cfg.get("enable_suggestions", True)
    skip_drafts = review_cfg.get("skip_drafts", True)
    skip_bot = review_cfg.get("skip_bot_authored", True)
    next_actions = review_cfg.get("next_actions", DEFAULT_NEXT_ACTIONS)
    strictness = os.environ.get("REVIEW_STRICTNESS") or (cfg.get("strictness") or {}).get("default", "medium")
    threshold = STRICTNESS_THRESHOLD.get(strictness, 80)

    gl = GitLab()
    mr = gl.mr()

    # 0. Eligibility gate (deterministic — no LLM spend).
    if mr.get("state") not in ("opened",):
        stop(f"skipped: MR state is {mr.get('state')}")
    if skip_drafts and (mr.get("draft") or mr.get("work_in_progress")):
        stop("skipped: draft / WIP")
    bot = gl.me()
    if skip_bot and mr.get("author", {}).get("username") == bot:
        stop("skipped: MR authored by the review bot (loop guard)")

    diff_refs = mr["diff_refs"]
    head_sha = diff_refs["head_sha"]

    # 1. Incremental guard — skip if we already reviewed this exact head.
    for n in gl.notes():
        if n.get("author", {}).get("username") != bot:
            continue
        m = MARKER_RE.search(n.get("body", ""))
        if m and head_sha.startswith(m.group(1)):
            stop("skipped: head already reviewed (up to date)")

    # 2. Fetch diffs, apply path filters, collect anchorable lines (diff-only).
    files, blocks, total = {}, [], 0
    for ch in gl.diffs():
        path = ch["new_path"]
        if ch.get("deleted_file") or is_ignored(path, ignore):
            continue
        diff = ch.get("diff", "")
        if total + len(diff) > MAX_DIFF_CHARS:
            blocks.append(f"### {path}\n[diff omitted — MR exceeds size budget]")
            continue
        total += len(diff)
        files[path] = {"old_path": ch.get("old_path") or path, "lines": anchorable_lines(diff)}
        blocks.append(f"### {path}\n```diff\n{diff}\n```")
    if not blocks:
        stop("skipped: no reviewable changes after path filters")

    # 3. Review — standards cached as the stable system prefix; volatile diff in user turn.
    client = anthropic.Anthropic()
    model = os.environ.get("REVIEW_MODEL", "claude-opus-4-8")
    effort = os.environ.get("REVIEW_EFFORT", "high")
    user = (
        f"Review this GitLab merge request titled {mr.get('title')!r}.\n"
        f"Keep only findings with confidence >= {threshold}. Anchor each finding to a line that "
        f"exists in the diff (new-file line number). Use GitLab ```suggestion blocks when you can "
        f"propose the exact fix.\n\n" + "\n\n".join(blocks)
    )
    kwargs = dict(
        model=model,
        max_tokens=16000,
        system=[{"type": "text", "text": load_standards(),
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        output_config={"format": {"type": "json_schema", "schema": FINDINGS_SCHEMA}},
    )
    if model.startswith(REASONING_MODELS):   # thinking/effort only where supported
        kwargs["thinking"] = {"type": "adaptive"}
        kwargs["output_config"]["effort"] = effort
    resp = client.messages.create(**kwargs)

    if resp.stop_reason == "refusal":
        stop("skipped: model declined to review this content")
    if resp.stop_reason == "max_tokens":
        stop("review output hit max_tokens — raise max_tokens or lower effort", code=1)
    try:
        payload = json.loads(next(b.text for b in resp.content if b.type == "text"))
    except (StopIteration, json.JSONDecodeError) as e:
        stop(f"could not parse model output: {e}", code=1)
    cached = resp.usage.cache_read_input_tokens
    print(f"[ai-review] model={model} cache_read={cached} findings={len(payload['findings'])}")

    # 4. Filter by confidence + validate the anchor line exists in the diff.
    kept = []
    for f in payload["findings"]:
        if f["confidence"] < threshold:
            continue
        meta = files.get(f["file"])
        if not meta or f["line"] not in meta["lines"]:
            print(f"[ai-review] dropped unanchorable finding: {f['file']}:{f['line']}")
            continue
        kept.append(f)
    kept.sort(key=lambda f: (f["severity"] != "critical", -f["confidence"]))

    # 5. Post — summary note (carries the marker) + inline threads.
    marker = f"<!-- ai-review head={head_sha} -->"
    footer = f"\n\n---\n{next_actions}" if next_actions else ""
    if not kept:
        gl.post_note(f"### Code review\n\nNo blocking issues found.{footer}\n\n{marker}")
        stop("done: no findings")

    shown, overflow = kept[:max_inline], kept[max_inline:]
    crit = sum(1 for f in kept if f["severity"] == "critical")
    summary = (
        f"### Code review\n\n{payload['summary']}\n\n"
        f"Found {len(kept)} issue(s): {crit} critical, {len(kept) - crit} important."
    )
    if overflow:
        summary += f"\n\nTop {max_inline} posted inline; {len(overflow)} more omitted."
    gl.post_note(summary + footer + f"\n\n{marker}")

    for f in shown:
        meta = files[f["file"]]
        body = f"**{f['title']}** ({f['severity']}, confidence {f['confidence']})\n\n{f['explanation']}"
        if enable_suggestions and f.get("suggestion"):
            body += f"\n\n```suggestion\n{f['suggestion']}\n```"
        gl.post_thread(body, {
            "base_sha": diff_refs["base_sha"],
            "head_sha": diff_refs["head_sha"],
            "start_sha": diff_refs["start_sha"],
            "position_type": "text",
            "new_path": f["file"],
            "old_path": meta["old_path"],   # correct for renamed files
            "new_line": f["line"],
        })
    stop(f"done: posted {len(shown)} inline finding(s)")


if __name__ == "__main__":
    main()
