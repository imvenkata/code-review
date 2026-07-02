from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".github" / "scripts"))

from reviewlib.config import ConfigError, ReviewConfig, loads  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class ReviewConfigTests(unittest.TestCase):
    def test_parses_the_shipped_config(self) -> None:
        config = ReviewConfig(loads((ROOT / "review.config.yml").read_text(encoding="utf-8")))

        self.assertIsNone(config.base_ref)
        self.assertIn("**/*.lock", config.ignore_globs)
        self.assertEqual(config.pipeline_mode, "required")
        self.assertEqual(config.scanner("secret_detection"), ("optional", "gl-secret-detection-report.json"))
        self.assertEqual(config.scanner("sast"), ("optional", "gl-sast-report.json"))
        self.assertEqual(config.limit_bytes("max_file_patch_kb"), 64 * 1024)
        self.assertEqual(config.limit_bytes("max_total_patch_kb"), 512 * 1024)

    def test_defaults_when_sections_are_missing(self) -> None:
        config = ReviewConfig(loads("strictness:\n  default: medium\n"))

        self.assertEqual(config.ignore_globs, ())
        self.assertEqual(config.pipeline_mode, "optional")
        self.assertEqual(config.scanner("sast"), ("disabled", ""))
        self.assertEqual(config.limit_bytes("max_file_patch_kb"), 64 * 1024)

    def test_rejects_invalid_modes_and_limits(self) -> None:
        bad_mode = ReviewConfig(loads("security:\n  pipeline:\n    mode: sometimes\n"))
        with self.assertRaises(ConfigError):
            bad_mode.pipeline_mode  # noqa: B018

        missing_artifact = ReviewConfig(loads("security:\n  sast:\n    mode: required\n"))
        with self.assertRaises(ConfigError):
            missing_artifact.scanner("sast")

        bad_limit = ReviewConfig(loads("limits:\n  max_file_patch_kb: many\n"))
        with self.assertRaises(ConfigError):
            bad_limit.limit_bytes("max_file_patch_kb")


if __name__ == "__main__":
    unittest.main()
