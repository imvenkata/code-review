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

    def test_detects_unquoted_passwords_in_env_yaml_and_properties(self) -> None:
        findings = secretscan.scan_patch(
            patch_for(
                [
                    "DB_PASSWORD=S3cr3tv4lue9!",
                    "db_password: hunter42Extra",
                    "spring.datasource.password = Qw8xTr41pz",
                ],
                path="config/app.env",
            )
        )

        self.assertEqual(
            [finding.rule for finding in findings],
            ["credential-assignment-unquoted"] * 3,
        )
        rendered = secretscan.render_section(findings)
        self.assertNotIn("S3cr3tv4lue9", rendered)
        self.assertNotIn("hunter42Extra", rendered)

    def test_unquoted_rule_skips_code_wiring_and_comparisons(self) -> None:
        quiet = secretscan.scan_patch(
            patch_for(
                [
                    'password = os.environ["DB_PASS"]',
                    "password = get_password()",
                    "password = expected_password",
                    "if password == candidate_value:",
                    "password: ${DB_PASSWORD}",
                ]
            )
        )

        self.assertEqual(quiet, [])

    def test_detects_basic_auth_headers(self) -> None:
        encoded = "YWRtaW46" + "aHVudGVyMg=="
        findings = secretscan.scan_patch(
            patch_for(
                [
                    f'headers["Authorization"] = "Basic {encoded}"',
                    f"Authorization: Basic {encoded}",
                ]
            )
        )

        self.assertEqual(len(findings), 2)
        self.assertTrue(all(f.rule == "basic-auth-header" for f in findings))
        self.assertNotIn(encoded, secretscan.render_section(findings))

    def test_detects_additional_platform_token_shapes(self) -> None:
        stripe = "sk_live_" + "4eC39HqLyjWDarjtT1zd"
        npm = "npm_" + "a1B2c3D4" * 4 + "wXyZ"
        azure_key = "Qw8xTr41pz" * 5 + "=="
        sendgrid = "SG." + "a1B2c3D4e5F6g7H8" + "." + "i9J0k1L2m3N4o5P6"
        openai = "sk-proj-" + "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uv"
        findings = secretscan.scan_patch(
            patch_for(
                [
                    f'stripe = "{stripe}"',
                    f'registry_token = "{npm}"',
                    f'conn = "DefaultEndpointsProtocol=https;AccountKey={azure_key};"',
                    f'mailer = "{sendgrid}"',
                    f'client = OpenAI(api_key="{openai}")',
                ]
            )
        )

        rules = {finding.rule for finding in findings}
        for expected in (
            "stripe-secret-key",
            "npm-token",
            "azure-account-key",
            "sendgrid-api-key",
            "openai-api-key",
        ):
            self.assertIn(expected, rules)
        rendered = secretscan.render_section(findings)
        for value in (stripe, npm, azure_key, sendgrid, openai):
            self.assertNotIn(value, rendered)

    def test_high_entropy_literal_requires_entropy(self) -> None:
        low_entropy = "a" * 48
        self.assertEqual(
            secretscan.scan_patch(patch_for([f'blob = "{low_entropy}"'])), []
        )


if __name__ == "__main__":
    unittest.main()
