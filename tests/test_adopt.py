from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_SPEC = importlib.util.spec_from_file_location("adopt", ROOT / "scripts" / "adopt.py")
adopt_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(adopt_module)


class AdoptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.target = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_installs_toolkit_and_project_owned_files(self) -> None:
        actions = adopt_module.adopt(self.target)

        for rel in (
            ".github/agents/code-review.agent.md",
            ".github/agents/review-mr.agent.md",
            ".github/skills/review-standards/SKILL.md",
            ".github/skills/requirements-traceability/SKILL.md",
            ".github/skills/gitlab-review-evidence/SKILL.md",
            ".github/scripts/collect-review-diff.py",
            ".github/scripts/collect-mr-evidence.py",
            ".github/scripts/reviewlib/secretscan.py",
            "review.config.yml",
            ".github/instructions/conventions.instructions.md",
        ):
            self.assertTrue((self.target / rel).exists(), rel)
        self.assertTrue(any(action.startswith("created review.config.yml") for action in actions))

    def test_preserves_project_owned_config_and_unrelated_skills(self) -> None:
        (self.target / "review.config.yml").write_text("strictness:\n  default: high\n")
        own_skill = self.target / ".github/skills/project-own-skill/SKILL.md"
        own_skill.parent.mkdir(parents=True)
        own_skill.write_text("project skill\n")
        mcp = self.target / ".vscode/mcp.json"
        mcp.parent.mkdir(parents=True)
        mcp.write_text("{}\n")

        adopt_module.adopt(self.target)

        self.assertIn("default: high", (self.target / "review.config.yml").read_text())
        self.assertEqual(own_skill.read_text(), "project skill\n")
        self.assertEqual(mcp.read_text(), "{}\n")

    def test_resyncs_stale_toolkit_files(self) -> None:
        stale = self.target / ".github/agents/code-review.agent.md"
        stale.parent.mkdir(parents=True)
        stale.write_text("old toolkit version\n")

        adopt_module.adopt(self.target)

        self.assertIn("code-review", stale.read_text())
        self.assertNotEqual(stale.read_text(), "old toolkit version\n")

    def test_refuses_missing_target_and_self_target(self) -> None:
        with self.assertRaises(SystemExit):
            adopt_module.adopt(self.target / "does-not-exist")
        with self.assertRaises(SystemExit):
            adopt_module.adopt(ROOT)


if __name__ == "__main__":
    unittest.main()
