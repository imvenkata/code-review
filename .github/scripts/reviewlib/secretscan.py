"""Deterministic secret/credential scan over unified-diff added lines.

Runs for free (no model tokens) before the AI review. Findings are candidates:
the agent verifies context and drops placeholders. Matched values are always
redacted — only a short prefix, length, and entropy are reported.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    rule: str
    confidence: str  # high | medium
    path: str
    line: int
    redacted: str


# (rule, confidence, pattern, value-capture group or None)
_RULES: tuple[tuple[str, str, re.Pattern[str], int | None], ...] = (
    ("private-key", "high",
     re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY"), None),
    ("aws-access-key-id", "high", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), 0),
    ("github-token", "high",
     re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})\b"), 0),
    ("gitlab-token", "high", re.compile(r"\bglpat-[0-9A-Za-z_\-]{20,}\b"), 0),
    ("slack-token", "high", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"), 0),
    ("google-api-key", "high", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), 0),
    ("jwt", "medium",
     re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b"), 0),
    ("url-credentials", "high",
     re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]{1,64}:([^@\s'\"]{4,})@"), 1),
    ("credential-assignment", "medium",
     re.compile(
         r"(?i)\b(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|client[_-]?secret|auth)"
         r"[\w-]*\s*[:=]>?\s*['\"]([^'\"]{6,})['\"]"), 1),
    ("high-entropy-literal", "medium",
     re.compile(r"['\"]([A-Za-z0-9+/=_-]{40,})['\"]"), 1),
)

_ALLOW_MARKERS = ("gitleaks:allow", "pragma: allowlist secret", "nosecret")

_PLACEHOLDER = re.compile(
    r"(?i)^(\$|%|<|\{\{)|example|changeme|placeholder|dummy|your[_-]|xxxx|\*\*\*|redacted"
    r"|^(true|false|none|null)$"
)

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_NEW_FILE = re.compile(r"^\+\+\+ (?:b/)?(.+)$")


def _entropy(value: str) -> float:
    if not value:
        return 0.0
    counts: dict[str, int] = {}
    for char in value:
        counts[char] = counts.get(char, 0) + 1
    total = len(value)
    return -sum((n / total) * math.log2(n / total) for n in counts.values())


def _redact(value: str | None) -> str:
    if not value:
        return "(match redacted)"
    return f"{value[:4]}… ({len(value)} chars, entropy={_entropy(value):.1f})"


def _line_findings(text: str, path: str, line: int) -> list[Finding]:
    lowered = text.lower()
    if any(marker in lowered for marker in _ALLOW_MARKERS):
        return []
    findings: list[Finding] = []
    for rule, confidence, pattern, group in _RULES:
        for match in pattern.finditer(text):
            value = match.group(group) if group is not None else None
            if value is not None and _PLACEHOLDER.search(value):
                continue
            if rule == "high-entropy-literal" and (value is None or _entropy(value) < 4.0):
                continue
            findings.append(Finding(rule, confidence, path, line, _redact(value)))
            break  # one hit per rule per line keeps output compact
    return findings


def scan_patch(patch: str) -> list[Finding]:
    findings: list[Finding] = []
    path = ""
    new_line = 0
    in_hunk = False
    for raw in patch.splitlines():
        header = _NEW_FILE.match(raw)
        if header:
            path = "" if header.group(1) == "/dev/null" else header.group(1)
            in_hunk = False
            continue
        hunk = _HUNK.match(raw)
        if hunk:
            new_line = int(hunk.group(1))
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if raw.startswith("+"):
            findings.extend(_line_findings(raw[1:], path, new_line))
            new_line += 1
        elif raw.startswith("-"):
            continue
        elif raw.startswith("\\"):
            continue
        else:
            new_line += 1
    return findings


def render_section(findings: list[Finding]) -> str:
    lines = ["## Secret scan (deterministic, added lines only)"]
    if not findings:
        lines.append("no candidates")
    for finding in findings:
        lines.append(
            f"secret-candidate\t{finding.rule}\t{finding.confidence}\t"
            f"{finding.path}:{finding.line}\t{finding.redacted}"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("patch", nargs="?", help="unified diff file; defaults to stdin")
    parser.add_argument("--fail-on-findings", action="store_true",
                        help="exit 1 when candidates are found (for git hooks)")
    args = parser.parse_args()

    if args.patch:
        with open(args.patch, encoding="utf-8", errors="replace") as handle:
            patch = handle.read()
    else:
        patch = sys.stdin.read()

    findings = scan_patch(patch)
    print(render_section(findings), end="")
    return 1 if (args.fail_on_findings and findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
