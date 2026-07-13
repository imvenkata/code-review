from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCK = ".github/.code-review-toolkit.lock"

OWNED_SAMPLE = [
    ".github/agents/code-review.agent.md",
    ".github/agents/review-mr.agent.md",
    ".github/skills/review-standards/SKILL.md",
    ".github/skills/requirements-traceability/SKILL.md",
    ".github/skills/gitlab-review-evidence/SKILL.md",
    ".github/scripts/collect-review-diff.py",
    ".github/scripts/collect-mr-evidence.py",
    ".github/scripts/reviewlib/secretscan.py",
]
SEEDS = [
    "review.config.yml",
    ".github/instructions/conventions.instructions.md",
]


def run_install(
    target: Path, *args: str, source: Path = ROOT
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(source / "install.sh"), *args],
        cwd=target,
        capture_output=True,
        text=True,
    )


class InstallScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.target = self.tmp / "target"
        self.target.mkdir()
        subprocess.run(["git", "init", "--quiet", str(self.target)], check=True)

    def copy_source(self) -> Path:
        source = self.tmp / "source"
        shutil.copytree(
            ROOT,
            source,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "tests"),
        )
        return source

    def assert_ok(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_fresh_install_copies_owned_files_seeds_and_lock(self) -> None:
        result = run_install(self.target)
        self.assert_ok(result)

        for path in [*OWNED_SAMPLE, *SEEDS, ".vscode/mcp.json", LOCK]:
            self.assertTrue((self.target / path).is_file(), path)

        lock = (self.target / LOCK).read_text(encoding="utf-8")
        self.assertRegex(lock, r"(?m)^version=")
        self.assertRegex(lock, r"(?m)^commit=")
        for path in OWNED_SAMPLE:
            self.assertIn(path, lock)
        for path in SEEDS:
            self.assertNotIn(path, lock.split("files:", 1)[1])

        self.assertEqual(list((self.target / ".github").rglob("*.pyc")), [])
        self.assertEqual(list((self.target / ".github").rglob("__pycache__")), [])
        self.assertFalse((self.target / ".gitlab-ci.yml").exists())
        self.assertFalse((self.target / ".github/copilot-instructions.md").exists())

        # The MCP server uses a promptString token; the installer creates no
        # separate credential file.
        self.assertFalse((self.target / ".gitlab-review.env").exists())
        mcp = json.loads((self.target / ".vscode/mcp.json").read_text(encoding="utf-8"))
        server = mcp["servers"]["gitlab-review"]
        self.assertNotIn("envFile", server)
        self.assertEqual(server["env"]["GITLAB_PERSONAL_ACCESS_TOKEN"], "${input:gitlabPat}")
        self.assertTrue(
            any(item.get("id") == "gitlabPat" for item in mcp["inputs"])
        )

    def test_update_never_overwrites_project_owned_files(self) -> None:
        self.assert_ok(run_install(self.target))

        config = self.target / "review.config.yml"
        conventions = self.target / ".github/instructions/conventions.instructions.md"
        config.write_text("limits: {custom: true}\n", encoding="utf-8")
        conventions.unlink()
        mcp_before = (self.target / ".vscode/mcp.json").read_text(encoding="utf-8")

        self.assert_ok(run_install(self.target, "--update"))

        self.assertEqual(
            config.read_text(encoding="utf-8"), "limits: {custom: true}\n"
        )
        # A deliberately removed seed must not come back on update.
        self.assertFalse(conventions.exists())
        self.assertEqual(
            (self.target / ".vscode/mcp.json").read_text(encoding="utf-8"), mcp_before
        )

    def test_check_reports_up_to_date_then_drift(self) -> None:
        result = run_install(self.target, "--check")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("not installed", result.stdout)

        self.assert_ok(run_install(self.target))

        result = run_install(self.target, "--check")
        self.assert_ok(result)
        self.assertIn("up to date", result.stdout)

        drifted = self.target / ".github/agents/code-review.agent.md"
        drifted.write_text("local edit\n", encoding="utf-8")
        result = run_install(self.target, "--check")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("differs: .github/agents/code-review.agent.md", result.stdout)

    def test_update_replaces_drift_and_removes_files_dropped_upstream(self) -> None:
        source = self.copy_source()
        self.assert_ok(run_install(self.target, source=source))

        drifted = self.target / ".github/agents/code-review.agent.md"
        drifted.write_text("local edit\n", encoding="utf-8")
        dropped = ".github/skills/review-standards/SKILL.md"
        (source / dropped).unlink()

        self.assert_ok(run_install(self.target, "--update", source=source))

        self.assertEqual(
            drifted.read_text(encoding="utf-8"),
            (source / ".github/agents/code-review.agent.md").read_text(
                encoding="utf-8"
            ),
        )
        self.assertFalse((self.target / dropped).exists())
        self.assertFalse((self.target / dropped).parent.exists())
        self.assertNotIn(dropped, (self.target / LOCK).read_text(encoding="utf-8"))

    def test_mcp_merge_preserves_existing_servers(self) -> None:
        example = json.loads(
            (ROOT / "docs/gitlab-mcp.example.json").read_text(encoding="utf-8")
        )
        vscode = self.target / ".vscode"
        vscode.mkdir()
        (vscode / "mcp.json").write_text(
            json.dumps({"servers": {"other": {"type": "stdio", "command": "x"}}}),
            encoding="utf-8",
        )

        result = run_install(self.target)
        self.assert_ok(result)
        self.assertIn("existing servers untouched", result.stdout)

        merged = json.loads((vscode / "mcp.json").read_text(encoding="utf-8"))
        self.assertIn("other", merged["servers"])
        self.assertEqual(
            merged["servers"]["gitlab-review"], example["servers"]["gitlab-review"]
        )
        # The example's gitlabPat promptString input is merged in alongside the
        # server, so VS Code prompts for the token.
        self.assertEqual(merged["inputs"], example["inputs"])

    def test_refuses_non_git_dir_subdirectory_and_toolkit_repo(self) -> None:
        plain = self.tmp / "plain"
        plain.mkdir()
        result = run_install(plain)
        self.assertEqual(result.returncode, 2)
        self.assertIn("not inside a git repository", result.stderr)

        sub = self.target / "sub"
        sub.mkdir()
        result = run_install(sub)
        self.assertEqual(result.returncode, 2)
        self.assertIn("repository root", result.stderr)

        result = run_install(ROOT)
        self.assertEqual(result.returncode, 2)
        self.assertIn("toolkit repository itself", result.stderr)

    def test_update_flag_requires_prior_install(self) -> None:
        result = run_install(self.target, "--update")
        self.assertEqual(result.returncode, 2)
        self.assertIn("nothing installed", result.stderr)

    def test_dry_run_writes_nothing(self) -> None:
        result = run_install(self.target, "--dry-run")
        self.assert_ok(result)
        self.assertIn("[dry-run]", result.stdout)
        self.assertEqual(
            [p.name for p in self.target.iterdir()],
            [".git"],
        )


if __name__ == "__main__":
    unittest.main()
