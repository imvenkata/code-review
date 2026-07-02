from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / ".github" / "agents"


def frontmatter_value(text: str, key: str) -> str:
    match = re.search(rf"(?m)^{re.escape(key)}:\s*(.+)$", text)
    if not match:
        raise AssertionError(f"missing frontmatter key: {key}")
    return match.group(1).strip()


def agent_tools(text: str) -> set[str]:
    return set(ast.literal_eval(frontmatter_value(text, "tools")))


class AgentContractTests(unittest.TestCase):
    def test_exactly_two_human_invocable_copilot_agents(self) -> None:
        agent_paths = sorted(AGENTS.glob("*.agent.md"))
        self.assertEqual(
            [path.name for path in agent_paths],
            ["code-review.agent.md", "review-mr.agent.md"],
        )

        for path in agent_paths:
            agent = path.read_text(encoding="utf-8")
            self.assertIn("target: vscode", agent)
            self.assertIn("user-invocable: true", agent)
            self.assertIn("disable-model-invocation: true", agent)
            # Models are chosen in the Copilot chat picker, never hardcoded:
            # pinned names go stale as models churn.
            self.assertNotRegex(agent, r"(?m)^model:")

    def test_local_agent_is_read_only_and_uses_current_vscode_tools(self) -> None:
        agent = (AGENTS / "code-review.agent.md").read_text(encoding="utf-8")

        self.assertEqual(
            agent_tools(agent),
            {"search/codebase", "execute/runInTerminal"},
        )
        self.assertIn("collect-review-diff.py --secret-scan", agent)
        self.assertIn("secret-candidate", agent)
        self.assertIn("Run only the read-only diff collector", agent)
        self.assertNotIn("gitlab-review/", agent)
        self.assertIn("## Scope gate and greeting", agent)
        self.assertIn("do not call any tool", agent)
        self.assertIn("Hi, I'm the Code Review agent.", agent)
        self.assertIn("weather queries", agent)

    def test_mr_agent_has_requirement_and_security_evidence_reads(self) -> None:
        agent = (AGENTS / "review-mr.agent.md").read_text(encoding="utf-8")
        tools = agent_tools(agent)

        required_reads = {
            "gitlab-review/get_merge_request",
            "gitlab-review/get_work_item",
            "gitlab-review/get_issue",
            "gitlab-review/list_merge_request_changed_files",
            "gitlab-review/get_merge_request_file_diff",
            "gitlab-review/list_merge_request_pipelines",
            "gitlab-review/get_pipeline",
            "gitlab-review/list_pipeline_jobs",
            "gitlab-review/list_job_artifacts",
            "gitlab-review/get_job_artifact_file",
        }
        self.assertTrue(required_reads.issubset(tools), required_reads - tools)

        self.assertIn("execute/runInTerminal", tools)
        self.assertIn("collect-mr-evidence.py", agent)
        self.assertIn("never put a token on the command line", agent)
        self.assertIn("requirements-traceability", agent)
        self.assertIn("gitlab-review-evidence", agent)
        self.assertIn("configured report path", agent)
        self.assertIn("requirement reference and `updated_at`", agent)
        self.assertIn("pipeline mode, ID, and status", agent)
        self.assertIn("version-3 IDE marker", agent)
        self.assertIn("Secret Detection mode/status and SAST mode/status", agent)
        self.assertIn("## Scope gate and greeting", agent)
        self.assertIn("do not call any tool", agent)
        self.assertIn("Hi, I'm the MR Review agent.", agent)
        self.assertIn("weather queries", agent)

    def test_mr_agent_exposes_only_new_review_comment_writes(self) -> None:
        agent = (AGENTS / "review-mr.agent.md").read_text(encoding="utf-8")
        tools = agent_tools(agent)
        write_tools = {
            tool
            for tool in tools
            if any(
                segment in tool
                for segment in (
                    "/create_",
                    "/update_",
                    "/delete_",
                    "/approve_",
                    "/unapprove_",
                    "/resolve_",
                    "/merge_",
                    "/push_",
                )
            )
        }

        self.assertEqual(
            write_tools,
            {
                "gitlab-review/create_merge_request_thread",
                "gitlab-review/create_merge_request_note",
            },
        )
        self.assertNotIn("gitlab-review/create_note", tools)
        self.assertIn("Never approve, merge, resolve, label, assign", agent)

    def test_mcp_example_matches_agent_and_is_policy_restricted(self) -> None:
        config = json.loads(
            (ROOT / "docs/gitlab-mcp.example.json").read_text(encoding="utf-8")
        )
        server = config["servers"]["gitlab-review"]
        env = server["env"]

        self.assertRegex(server["args"][1], r"@zereight/mcp-gitlab@\d+\.\d+\.\d+")
        self.assertEqual(env["GITLAB_READ_ONLY_MODE"], "false")
        self.assertEqual(
            set(env["GITLAB_TOOLSETS"].split(",")),
            {"merge_requests", "repositories", "pipelines"},
        )
        self.assertEqual(
            set(env["GITLAB_TOOLS"].split(",")),
            {"get_issue", "get_work_item"},
        )

        denied = re.compile(env["GITLAB_DENIED_TOOLS_REGEX"])
        required_reads = (
            "get_merge_request",
            "get_work_item",
            "get_issue",
            "list_pipeline_jobs",
            "list_job_artifacts",
            "get_job_artifact_file",
        )
        required_writes = (
            "create_merge_request_thread",
            "create_merge_request_note",
        )
        for required in (*required_reads, *required_writes):
            self.assertIsNone(denied.match(required), required)

        protected = set(env["GITLAB_TOOL_POLICY_APPROVE"].split(","))
        self.assertEqual(protected, set(required_writes))

        for dangerous in (
            "merge_merge_request",
            "approve_merge_request",
            "unapprove_merge_request",
            "update_merge_request",
            "resolve_merge_request_thread",
            "create_note",
            "create_issue",
            "push_files",
            "create_or_update_file",
            "delete_branch",
        ):
            self.assertIsNotNone(denied.match(dangerous), dangerous)

    def test_skills_define_untrusted_content_and_fail_closed_evidence(self) -> None:
        requirements = (
            ROOT / ".github/skills/requirements-traceability/SKILL.md"
        ).read_text(encoding="utf-8")
        evidence = (
            ROOT / ".github/skills/gitlab-review-evidence/SKILL.md"
        ).read_text(encoding="utf-8")

        self.assertIn("untrusted data", requirements)
        self.assertRegex(
            requirements,
            r"do not claim\s+requirement compliance",
        )
        self.assertIn("The agent never approves the MR", evidence)
        self.assertIn("report `Not evaluated`", evidence)
        self.assertIn("succeeds without its expected artifact", evidence)
        self.assertIn("version=3", evidence)

    def test_no_direct_model_api_runtime_or_ci_job(self) -> None:
        self.assertFalse((ROOT / "ci/review.py").exists())
        self.assertFalse((ROOT / "ci/ai-review.gitlab-ci.yml").exists())

        checked_paths = [
            ROOT / "README.md",
            ROOT / "review.config.yml",
            ROOT / "docs/ARCHITECTURE.md",
            ROOT / "docs/REVIEW-SYSTEM.md",
            *AGENTS.glob("*.agent.md"),
            *(ROOT / ".github/skills").glob("*/SKILL.md"),
            *(ROOT / ".github/scripts").rglob("*.py"),
        ]
        forbidden = (
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "anthropic.Anthropic",
            "from openai",
            "import openai",
        )
        for path in checked_paths:
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, text, f"{path}: {token}")

    def test_config_defines_explicit_evidence_policies_and_no_ci_ships(self) -> None:
        config = (ROOT / "review.config.yml").read_text(encoding="utf-8")

        # Company CI templates are org-owned; this toolkit must not ship CI jobs.
        self.assertFalse((ROOT / "ci").exists())
        self.assertIn("require_story: true", config)
        self.assertRegex(config, r"pipeline:\n\s+mode: required")
        self.assertRegex(config, r"secret_detection:\n\s+mode: optional")
        self.assertRegex(
            config,
            r"(?s)secret_detection:.*?artifact: gl-secret-detection-report\.json",
        )
        self.assertRegex(config, r"sast:\n\s+mode: optional")
        self.assertRegex(config, r"(?s)sast:.*?artifact: gl-sast-report\.json")


if __name__ == "__main__":
    unittest.main()
