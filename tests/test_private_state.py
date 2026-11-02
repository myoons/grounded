import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.private_state import save_private_json


class PrivateStateTests(unittest.TestCase):
    def test_replaces_complete_records_with_private_permissions(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "journal.json"
            save_private_json(path, {"state": "before"})
            save_private_json(path, {"state": "after"})
            self.assertEqual(json.loads(path.read_text()), {"state": "after"})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_failed_replacement_preserves_the_previous_journal(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "journal.json"
            save_private_json(path, {"state": "before"})
            with (
                patch(
                    "scripts.private_state.os.replace",
                    side_effect=OSError("fixture failure"),
                ),
                self.assertRaises(OSError),
            ):
                save_private_json(path, {"state": "after"})
            self.assertEqual(json.loads(path.read_text()), {"state": "before"})
            self.assertEqual(list(Path(directory).iterdir()), [path])
