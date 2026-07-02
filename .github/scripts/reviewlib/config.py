"""Minimal reader for review.config.yml.

Parses only the subset of YAML this toolkit uses — nested mappings of scalars
and lists of scalars — so the scripts stay dependency-free. Unknown keys are
preserved but unused.
"""
from __future__ import annotations

import ast
import fnmatch
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(ValueError):
    """A user-actionable problem in review.config.yml."""


def parse_scalar(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""
    if value[0] in {'"', "'"}:
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError) as exc:
            raise ConfigError(f"invalid quoted value in review.config.yml: {value}") from exc
        return str(parsed)
    return value.split(" #", 1)[0].strip()


def _content_lines(text: str) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append((len(raw) - len(raw.lstrip()), stripped))
    return lines


def _parse_block(lines: list[tuple[int, str]], start: int, indent: int):
    """Parse one mapping or list block whose entries sit at `indent`."""
    if lines[start][1].startswith("- "):
        items: list[str] = []
        index = start
        while index < len(lines) and lines[index][0] == indent and lines[index][1].startswith("- "):
            items.append(parse_scalar(lines[index][1][2:]))
            index += 1
        return items, index

    mapping: dict[str, object] = {}
    index = start
    while index < len(lines) and lines[index][0] == indent:
        entry = lines[index][1]
        key, separator, rest = entry.partition(":")
        if not separator:
            raise ConfigError(f"unparseable line in review.config.yml: {entry}")
        key = key.strip()
        rest = rest.strip()
        index += 1
        if rest:
            mapping[key] = parse_scalar(rest)
            continue
        if index < len(lines) and lines[index][0] > indent:
            mapping[key], index = _parse_block(lines, index, lines[index][0])
        else:
            mapping[key] = ""
    return mapping, index


def loads(text: str) -> dict:
    lines = _content_lines(text)
    if not lines:
        return {}
    parsed, index = _parse_block(lines, 0, lines[0][0])
    if index != len(lines) or not isinstance(parsed, dict):
        raise ConfigError("review.config.yml must be a mapping with consistent indentation")
    return parsed


_LIMIT_DEFAULTS = {"max_file_patch_kb": 64, "max_total_patch_kb": 512}


@dataclass(frozen=True)
class ReviewConfig:
    data: dict = field(default_factory=dict)

    def get(self, *keys: str, default=None):
        node: object = self.data
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    @property
    def base_ref(self) -> str | None:
        value = self.get("local", "base_ref", default="")
        return str(value) or None

    @property
    def ignore_globs(self) -> tuple[str, ...]:
        value = self.get("path_filters", "ignore", default=[])
        if not isinstance(value, list):
            raise ConfigError("path_filters.ignore must be a list of glob patterns")
        return tuple(str(item) for item in value)

    def limit_bytes(self, name: str) -> int:
        raw = self.get("limits", name, default=_LIMIT_DEFAULTS[name])
        try:
            kb = int(str(raw))
        except ValueError as exc:
            raise ConfigError(f"limits.{name} must be an integer (KiB)") from exc
        if kb <= 0:
            raise ConfigError(f"limits.{name} must be positive")
        return kb * 1024

    def scanner(self, name: str) -> tuple[str, str]:
        """Return (mode, artifact path) for `secret_detection` or `sast`."""
        mode = str(self.get("security", name, "mode", default="disabled")).lower()
        if mode not in {"required", "optional", "disabled"}:
            raise ConfigError(f"security.{name}.mode must be required|optional|disabled")
        artifact = str(self.get("security", name, "artifact", default=""))
        if mode != "disabled" and not artifact:
            raise ConfigError(f"security.{name}.artifact is required when the scanner is enabled")
        return mode, artifact

    @property
    def pipeline_mode(self) -> str:
        mode = str(self.get("security", "pipeline", "mode", default="optional")).lower()
        if mode not in {"required", "optional", "disabled"}:
            raise ConfigError("security.pipeline.mode must be required|optional|disabled")
        return mode


def load_config(path: Path) -> ReviewConfig:
    if not path.exists():
        return ReviewConfig()
    return ReviewConfig(loads(path.read_text(encoding="utf-8")))


def is_ignored(path: str, globs: tuple[str, ...]) -> bool:
    for pattern in globs:
        if fnmatch.fnmatch(path, pattern):
            return True
        if pattern.startswith("**/") and fnmatch.fnmatch(path, pattern[3:]):
            return True
    return False
