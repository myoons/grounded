import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.audit_submission import inspect, inspect_history, private_artifact


class SubmissionAuditTests(unittest.TestCase):
    def test_private_artifact_names(self):
        for name in (
            ".env",
            ".env.production",
            "runpod.env",
            "meeting.m4a",
            "history.db",
            "signing.key",
        ):
            self.assertTrue(private_artifact(name))
        self.assertFalse(private_artifact(".env.example"))

    def test_removed_secret_is_still_found_in_history(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", directory], check=True)
            target = root / "runpod.env"
            target.write_text("private-fixture-value")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.invalid",
                    "commit",
                    "-qm",
                    "fixture",
                ],
                cwd=root,
                check=True,
            )
            target.unlink()
            report = inspect_history(root, [b"private-fixture-value"])
            self.assertFalse(report["passed"])
            self.assertNotIn("private-fixture-value", str(report))
            self.assertEqual(
                {item["kind"] for item in report["findings"]},
                {"configured_secret", "private_or_generated_artifact"},
            )

    def test_png_bytes_skip_language_check_but_not_secret_checks(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", directory], check=True)
            (root / "desktop").mkdir()
            (root / "desktop/package-lock.json").write_text('{"packages":{}}')
            (root / "uv.lock").write_text("version = 1\n")
            (root / "figure.png").write_bytes(
                b"\x89PNG\r\n\x1a\n" + "\uac00".encode() + b"private-fixture-value"
            )
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            report = inspect(root, [b"private-fixture-value"])
            self.assertEqual(
                report["findings"],
                [{"path": "figure.png", "kind": "configured_secret"}],
            )

    def test_configured_secrets_fail_without_returning_the_value(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", directory], check=True)
            (root / "desktop").mkdir()
            (root / "desktop/package-lock.json").write_text('{"packages":{}}')
            (root / "uv.lock").write_text("version = 1\n")
            (root / "example.py").write_text("value = 'private-fixture-value'\n")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            report = inspect(root, [b"private-fixture-value"])
            self.assertFalse(report["passed"])
            self.assertNotIn("private-fixture-value", str(report))
            self.assertEqual(report["findings"][0]["kind"], "configured_secret")
