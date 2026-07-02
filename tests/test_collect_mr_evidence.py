from __future__ import annotations

import importlib.util
import json
import sys
import unittest
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".github" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from reviewlib.config import ReviewConfig, loads  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "collect_mr_evidence", SCRIPTS / "collect-mr-evidence.py"
)
mr_evidence = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mr_evidence)

HEAD = "headsha1234567890"
CONFIG_TEXT = """
path_filters:
  ignore:
    - "**/package-lock.json"
limits:
  max_file_patch_kb: 64
  max_total_patch_kb: 512
security:
  pipeline:
    mode: required
  secret_detection:
    mode: optional
    artifact: gl-secret-detection-report.json
  sast:
    mode: optional
    artifact: gl-sast-report.json
"""

# Assembled at runtime so this test file never contains a scannable secret literal.
DIFF_TOKEN = "glpat-" + "abcdEFGH" * 3
LEAKED_REPORT_VALUE = "glpat-" + "SHOULDNOTAPPEAR123456"

MR = {
    "title": "Add config loader",
    "state": "opened",
    "draft": False,
    "author": {"username": "dev1"},
    "source_branch": "feature/config",
    "target_branch": "main",
    "sha": HEAD,
    "updated_at": "2026-07-01T10:00:00Z",
    "description": "Implements #7",
    "diff_refs": {"base_sha": "base111", "start_sha": "start222", "head_sha": HEAD},
}

STORY = {
    "title": "Config loading story",
    "state": "opened",
    "issue_type": "issue",
    "updated_at": "2026-06-30T09:00:00Z",
    "description": "## Acceptance criteria\n- [ ] loads yaml\n- [ ] rejects bad input",
}

NOTES = [
    {
        "body": "Prior summary\n<!-- ai-review source=ide version=3 state=complete "
        f"head={HEAD} pipeline=900 -->"
    }
]

PIPELINES = [
    {"id": 899, "sha": "oldsha", "status": "success"},
    {"id": 900, "sha": HEAD, "status": "success"},
]

JOBS = [
    {"id": 1, "name": "secret_detection", "stage": "test", "status": "success"},
    {"id": 2, "name": "semgrep-sast", "stage": "test", "status": "success"},
    {"id": 3, "name": "unit-tests", "stage": "test", "status": "success"},
]

SECRET_REPORT = {
    "scan": {"scanner": {"name": "gitleaks", "version": "8.0"}},
    "vulnerabilities": [
        {
            "severity": "critical",
            "name": "GitLab personal access token",
            "location": {"file": "config.py", "start_line": 3},
            "raw_source_code_extract": LEAKED_REPORT_VALUE,
        }
    ],
}

SAST_REPORT = {
    "scan": {"scanner": {"name": "semgrep", "version": "1.2"}},
    "vulnerabilities": [],
}

DIFFS = [
    {
        "old_path": "config.py",
        "new_path": "config.py",
        "new_file": True,
        "diff": f'@@ -0,0 +1,3 @@\n+import os\n+TOKEN = "{DIFF_TOKEN}"\n+x = 1\n',
    },
    {
        "old_path": "package-lock.json",
        "new_path": "package-lock.json",
        "new_file": False,
        "diff": "@@ -1 +1 @@\n-a\n+b\n",
    },
    {
        "old_path": "big.py",
        "new_path": "big.py",
        "new_file": False,
        "diff": "@@ -0,0 +1,3000 @@\n" + ("+padline " + "y" * 30 + "\n") * 3000,
    },
]


def fake_fetch_map(overrides: dict | None = None):
    routes = {
        "/api/v4/projects/group%2Fapp/merge_requests/42": MR,
        "/api/v4/projects/group%2Fapp/issues/7": STORY,
        "/api/v4/projects/group%2Fapp/merge_requests/42/notes": NOTES,
        "/api/v4/projects/group%2Fapp/merge_requests/42/pipelines": PIPELINES,
        "/api/v4/projects/group%2Fapp/pipelines/900": {
            "id": 900,
            "sha": HEAD,
            "status": "success",
        },
        "/api/v4/projects/group%2Fapp/pipelines/900/jobs": JOBS,
        "/api/v4/projects/group%2Fapp/jobs/1/artifacts/gl-secret-detection-report.json": SECRET_REPORT,
        "/api/v4/projects/group%2Fapp/jobs/2/artifacts/gl-sast-report.json": SAST_REPORT,
        "/api/v4/projects/group%2Fapp/merge_requests/42/diffs": DIFFS,
    }
    routes.update(overrides or {})

    def fetch(url: str, headers: dict[str, str]) -> tuple[int, bytes]:
        assert "PRIVATE-TOKEN" in headers
        path = urllib.parse.urlsplit(url).path
        if path not in routes:
            return 404, b'{"message": "404 Not Found"}'
        return 200, json.dumps(routes[path]).encode("utf-8")

    return fetch


def build(overrides: dict | None = None, **kwargs) -> str:
    client = mr_evidence.GitLab(
        "https://gitlab.example.com/api/v4", "token", fetch=fake_fetch_map(overrides)
    )
    config = ReviewConfig(loads(CONFIG_TEXT))
    return mr_evidence.build_bundle(
        client,
        config,
        kwargs.get("project", "group/app"),
        kwargs.get("mr", "42"),
        kwargs.get("story_project"),
        kwargs.get("story_iid"),
    )


class BuildBundleTests(unittest.TestCase):
    def test_bundle_contains_all_evidence_sections(self) -> None:
        output = build()

        self.assertIn("# mr-evidence v1", output)
        self.assertIn(f'head-sha: "{HEAD}"', output)
        self.assertIn('reference: "group/app#7"', output)
        self.assertIn("- [ ] loads yaml", output)
        self.assertIn("latest-marker: <!-- ai-review source=ide version=3", output)
        self.assertIn("pipeline-id: 900", output)
        self.assertIn("secret_detection\ttest\tsuccess\tid=1", output)
        self.assertIn("Secret Detection: mode=optional state=Findings", output)
        self.assertIn("counts=(Critical=1)", output)
        self.assertIn("SAST: mode=optional state=Clean", output)

    def test_redacts_scanner_and_diff_secret_values(self) -> None:
        output = build()

        self.assertNotIn(LEAKED_REPORT_VALUE, output.replace(DIFF_TOKEN, ""))
        self.assertIn("secret-candidate\tgitlab-token", output)
        self.assertIn("config.py:2", output)
        # The diff itself must still be present for review, but the secret-scan
        # section must not repeat the raw value.
        scan_section = output.split("## Secret scan")[1].split("## Patch")[0]
        self.assertNotIn(DIFF_TOKEN, scan_section)

    def test_manifest_applies_filters_and_budgets(self) -> None:
        output = build()

        self.assertIn('reviewed\tadded\t"config.py"', output)
        self.assertIn('ignored\tmodified\t"package-lock.json"', output)
        self.assertIn('unavailable\ttoo-large\t"big.py"', output)
        self.assertIn(f'+TOKEN = "{DIFF_TOKEN}"', output)
        self.assertNotIn("padline", output)

    def test_missing_head_pipeline_fails_closed(self) -> None:
        output = build(
            {"/api/v4/projects/group%2Fapp/merge_requests/42/pipelines": [
                {"id": 899, "sha": "oldsha", "status": "success"}
            ]}
        )

        self.assertIn("status: none-for-current-head", output)
        self.assertIn(
            "Secret Detection: mode=optional state=Not evaluated reason=no-current-head-pipeline",
            output,
        )

    def test_ambiguous_story_reference_is_reported_not_guessed(self) -> None:
        ambiguous = dict(MR, description="Relates to #7 and #8")
        output = build({"/api/v4/projects/group%2Fapp/merge_requests/42": ambiguous})

        self.assertIn("source: description (2 reference(s))", output)
        self.assertIn("reason: no unambiguous primary story reference", output)

    def test_explicit_story_arguments_take_precedence(self) -> None:
        output = build(story_project="group/app", story_iid="7")

        self.assertIn("source: explicit", output)
        self.assertIn('title: "Config loading story"', output)

    def test_broken_scanner_report_is_unavailable(self) -> None:
        output = build(
            {"/api/v4/projects/group%2Fapp/jobs/2/artifacts/gl-sast-report.json": {
                "unexpected": "shape"
            }}
        )

        self.assertIn("SAST: mode=optional state=Unavailable", output)
        self.assertIn("reason=broken-report", output)


class StoryRefTests(unittest.TestCase):
    def test_reference_forms_and_deduplication(self) -> None:
        refs = mr_evidence.find_story_refs(
            "See https://gitlab.example.com/group/app/-/issues/12, group/other#3, and #12",
            "group/app",
        )

        self.assertIn(("group/app", "12"), refs)
        self.assertIn(("group/other", "3"), refs)
        self.assertEqual(len(refs), 2)


if __name__ == "__main__":
    unittest.main()
