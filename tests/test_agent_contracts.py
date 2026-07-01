from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AgentContractTests(unittest.TestCase):
    def test_review_mr_is_manual_and_uses_batched_namespaced_tools(self) -> None:
        agent = (ROOT / ".github/agents/review-mr.agent.md").read_text(encoding="utf-8")

        self.assertIn("target: vscode", agent)
        self.assertIn("disable-model-invocation: true", agent)
        self.assertIn("gitlab-review/get_merge_request_file_diff", agent)
        self.assertIn("gitlab-review/create_merge_request_discussion_note", agent)
        self.assertNotIn("gitlab-review/create_merge_request_note", agent)
        self.assertNotIn("gitlab-review/get_merge_request_diffs", agent)
        self.assertIn("source=ide version=1 state=complete", agent)
        self.assertIn("old_path", agent)
        self.assertIn("old_line", agent)

    def test_local_agent_uses_the_complete_diff_collector(self) -> None:
        agent = (ROOT / ".github/agents/code-review.agent.md").read_text(encoding="utf-8")

        self.assertIn("collect-review-diff.py", agent)
        self.assertNotIn("git diff --merge-base origin/HEAD", agent)

    def test_mcp_example_is_pinned_and_policy_restricted(self) -> None:
        config = json.loads(
            (ROOT / "docs/gitlab-mcp.example.json").read_text(encoding="utf-8")
        )

        server = config["servers"]["gitlab-review"]
        self.assertRegex(server["args"][1], r"@zereight/mcp-gitlab@\d+\.\d+\.\d+")
        self.assertIn("GITLAB_DENIED_TOOLS_REGEX", server["env"])
        self.assertIn("GITLAB_TOOL_POLICY_APPROVE", server["env"])
        self.assertEqual(server["env"]["GITLAB_READ_ONLY_MODE"], "false")

        denied = re.compile(server["env"]["GITLAB_DENIED_TOOLS_REGEX"])
        required_writes = (
            "create_note",
            "create_merge_request_thread",
            "create_merge_request_discussion_note",
            "update_merge_request_note",
            "resolve_merge_request_thread",
            "approve_merge_request",
            "unapprove_merge_request",
            "update_merge_request",
        )
        for required in (
            "get_merge_request",
            "list_merge_request_changed_files",
            *required_writes,
        ):
            self.assertIsNone(denied.match(required), required)
        protected = set(server["env"]["GITLAB_TOOL_POLICY_APPROVE"].split(","))
        self.assertEqual(protected, set(required_writes))
        for dangerous in (
            "merge_merge_request",
            "push_files",
            "delete_branch",
            "create_or_update_file",
            "create_issue",
        ):
            self.assertIsNotNone(denied.match(dangerous), dangerous)


if __name__ == "__main__":
    unittest.main()
