from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".github" / "scripts"))

from reviewlib import secretscan  # noqa: E402


def patch_for(lines: list[str], path: str = "src/app.py") -> str:
    added = "\n".join(f"+{line}" for line in lines)
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        f"@@ -1,1 +10,{len(lines)} @@\n"
        f"{added}\n"
    )


class SecretScanTests(unittest.TestCase):
    def test_detects_known_token_shapes_with_line_numbers(self) -> None:
        # Built by concatenation so this file never contains a scannable literal.
        gitlab_token = "glpat-" + "a1B2" * 6
        aws_key = "AKIA" + "IOSFODNN7QXMPL0Q"
        findings = secretscan.scan_patch(
            patch_for([f'token = "{gitlab_token}"', f"key = '{aws_key}'"])
        )

        rules = {finding.rule for finding in findings}
        self.assertIn("gitlab-token", rules)
        self.assertIn("aws-access-key-id", rules)
        by_rule = {finding.rule: finding for finding in findings}
        self.assertEqual(by_rule["gitlab-token"].line, 10)
        self.assertEqual(by_rule["aws-access-key-id"].line, 11)
        self.assertEqual(by_rule["gitlab-token"].path, "src/app.py")

    def test_redacts_matched_values(self) -> None:
        secret = "hunter2secret" + "Zq9" * 5
        findings = secretscan.scan_patch(patch_for([f'password = "{secret}"']))

        self.assertEqual([finding.rule for finding in findings], ["credential-assignment"])
        rendered = secretscan.render_section(findings)
        self.assertNotIn(secret, rendered)
        self.assertNotIn(secret[4:], rendered)
        self.assertIn("chars, entropy=", rendered)

    def test_ignores_placeholders_allowlists_and_removed_lines(self) -> None:
        quiet_patch = patch_for(
            [
                'password = "${DB_PASSWORD}"',
                'password = "changeme-example"',
                'token = "glpat-' + "x" * 24 + '"  # gitleaks:allow',
            ]
        )
        removed_patch = quiet_patch.replace("+password", "-password").replace(
            "+token", "-token"
        )

        self.assertEqual(secretscan.scan_patch(quiet_patch), [])
        self.assertEqual(secretscan.scan_patch(removed_patch), [])

    def test_detects_url_credentials_and_private_keys(self) -> None:
        findings = secretscan.scan_patch(
            patch_for(
                [
                    'DATABASE_URL = "postgres://svc:S3cr3tPass@db.internal:5432/app"',
                    "-----BEGIN RSA PRIVATE KEY-----",
                ]
            )
        )

        rules = [finding.rule for finding in findings]
        self.assertIn("url-credentials", rules)
        self.assertIn("private-key", rules)
        rendered = secretscan.render_section(findings)
        self.assertNotIn("S3cr3tPass", rendered)

    def test_high_entropy_literal_requires_entropy(self) -> None:
        low_entropy = "a" * 48
        self.assertEqual(
            secretscan.scan_patch(patch_for([f'blob = "{low_entropy}"'])), []
        )


if __name__ == "__main__":
    unittest.main()
