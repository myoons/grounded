import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.runpod_access import credentials


class CredentialTests(unittest.TestCase):
    def test_reads_quoted_value_without_evaluating_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / ".config/roomshift/runpod.env"
            path.parent.mkdir(parents=True)
            path.write_text(
                "export RUNPOD_API_KEY='literal-$(not-executed)'\nUNRELATED=ignored\n"
            )
            path.chmod(0o600)
            with patch("scripts.runpod_access.Path.home", return_value=root):
                self.assertEqual(
                    credentials(), {"RUNPOD_API_KEY": "literal-$(not-executed)"}
                )

    def test_rejects_world_readable_credential(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / ".config/roomshift/runpod.env"
            path.parent.mkdir(parents=True)
            path.write_text("RUNPOD_API_KEY=example\n")
            path.chmod(0o644)
            with (
                patch("scripts.runpod_access.Path.home", return_value=root),
                self.assertRaisesRegex(ValueError, "mode 600"),
            ):
                credentials()
