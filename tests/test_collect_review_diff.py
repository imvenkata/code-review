from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "scripts"
    / "collect-review-diff.py"
)


def run(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


class CollectReviewDiffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        run("git", "init", "-b", "main", cwd=self.repo)
        run("git", "config", "user.email", "review-test@example.com", cwd=self.repo)
        run("git", "config", "user.name", "Review Test", cwd=self.repo)
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        run("git", "add", "app.py", cwd=self.repo)
        run("git", "commit", "-m", "base", cwd=self.repo)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def collect(self, *extra: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.pop("REVIEW_BASE_REF", None)
        return subprocess.run(
            [sys.executable, str(SCRIPT), *extra],
            cwd=self.repo,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_includes_committed_unstaged_and_untracked_changes(self) -> None:
        run("git", "switch", "-c", "feature", cwd=self.repo)
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        run("git", "add", "app.py", cwd=self.repo)
        run("git", "commit", "-m", "feature change", cwd=self.repo)
        (self.repo / "app.py").write_text("value = 3\n", encoding="utf-8")
        (self.repo / "new.py").write_text("created = True\n", encoding="utf-8")

        result = self.collect()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('base-ref: "main"', result.stdout)
        self.assertIn("reviewable-files: 2", result.stdout)
        self.assertIn('"new.py"', result.stdout)
        self.assertIn("+value = 3", result.stdout)
        self.assertIn("+created = True", result.stdout)

    def test_uses_configured_base_and_rejects_an_invalid_one(self) -> None:
        (self.repo / "review.config.yml").write_text(
            'local:\n  base_ref: "missing-target"\n',
            encoding="utf-8",
        )

        result = self.collect()

        self.assertEqual(result.returncode, 2)
        self.assertIn("does not resolve to a commit", result.stderr)

    def test_applies_review_config_path_filters_to_untracked_files(self) -> None:
        (self.repo / "review.config.yml").write_text(
            'path_filters:\n  ignore:\n    - "**/*.lock"\n',
            encoding="utf-8",
        )
        (self.repo / "dependency.lock").write_text("ignored\n", encoding="utf-8")
        (self.repo / "review.py").write_text("reviewed = True\n", encoding="utf-8")

        result = self.collect()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('ignored\tuntracked\t"dependency.lock"', result.stdout)
        self.assertIn('included\tuntracked\t"review.py"', result.stdout)
        self.assertNotIn("+ignored", result.stdout)
        self.assertIn("+reviewed = True", result.stdout)

    def test_explicit_base_overrides_repository_config(self) -> None:
        (self.repo / "review.config.yml").write_text(
            'local:\n  base_ref: "missing-target"\n',
            encoding="utf-8",
        )

        result = self.collect("--base", "main")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('base-ref: "main"', result.stdout)

    def test_unborn_repository_reviews_initial_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            run("git", "init", "-b", "main", cwd=repo)
            (repo / "first.py").write_text("initial = True\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT)],
                cwd=repo,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('base-ref: "<empty repository>"', result.stdout)
        self.assertIn('included\tuntracked\t"first.py"', result.stdout)
        self.assertIn("+initial = True", result.stdout)


if __name__ == "__main__":
    unittest.main()
