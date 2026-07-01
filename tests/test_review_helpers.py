from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


REVIEW_PATH = Path(__file__).resolve().parents[1] / "ci" / "review.py"
sys.modules.setdefault("anthropic", types.ModuleType("anthropic"))
SPEC = importlib.util.spec_from_file_location("review_runner", REVIEW_PATH)
assert SPEC and SPEC.loader
review_runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review_runner)


class ReviewHelperTests(unittest.TestCase):
    def test_changed_lines_preserves_diff_side(self) -> None:
        diff = "\n".join(
            (
                "@@ -3,3 +3,3 @@",
                " context",
                "-removed",
                "+added",
                " context",
            )
        )

        lines = review_runner.changed_lines(diff)

        self.assertEqual(lines["old"], {4})
        self.assertEqual(lines["new"], {4})

    def test_changed_lines_handles_pure_addition_and_deletion(self) -> None:
        diff = "\n".join(
            (
                "@@ -0,0 +1,2 @@",
                "+first",
                "+second",
                "@@ -8,2 +9,0 @@",
                "-old first",
                "-old second",
            )
        )

        lines = review_runner.changed_lines(diff)

        self.assertEqual(lines["new"], {1, 2})
        self.assertEqual(lines["old"], {8, 9})

    def test_marker_records_source_version_state_and_head(self) -> None:
        head = "a" * 40
        rendered = review_runner.marker("ci", "partial", head)
        match = review_runner.MARKER_RE.search(rendered)

        self.assertIsNotNone(match)
        assert match
        self.assertEqual(match.groups(), ("ci", "1", "partial", head))
        self.assertFalse(review_runner.has_complete_marker(rendered, "ci", head))
        self.assertFalse(
            review_runner.has_complete_marker(
                review_runner.marker("ide", "complete", head),
                "ci",
                head,
            )
        )
        self.assertTrue(
            review_runner.has_complete_marker(
                review_runner.marker("ci", "complete", head),
                "ci",
                head,
            )
        )

    def test_discussion_position_supports_renames_and_removed_lines(self) -> None:
        position = review_runner.discussion_position(
            {
                "base_sha": "base",
                "head_sha": "head",
                "start_sha": "start",
            },
            {
                "old_path": "src/old_name.py",
                "new_path": "src/new_name.py",
            },
            {
                "side": "old",
                "line": 17,
            },
        )

        self.assertEqual(position["old_path"], "src/old_name.py")
        self.assertEqual(position["new_path"], "src/new_name.py")
        self.assertEqual(position["old_line"], 17)
        self.assertNotIn("new_line", position)


if __name__ == "__main__":
    unittest.main()
