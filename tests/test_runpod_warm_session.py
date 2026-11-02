import fcntl
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import runpod_warm_session as warm


class Connection:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, path):
        return self

    def json(self):
        return {"workersMin": 0, "workersMax": 1}

    def raise_for_status(self):
        pass

    def patch(self, path, json):
        self.calls.append((path, json))
        if self.fail and path.endswith("speech") and json["workersMin"] == 1:
            raise RuntimeError("Rejected")
        return self


class WarmSessionTests(unittest.TestCase):
    def test_concurrent_lease_processes_cannot_modify_worker_minimums(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with (root / "warm-session.lock").open("a+") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(warm, "ROOT", root), patch.object(warm, "run") as run:
                    with self.assertRaisesRegex(RuntimeError, "process is active"):
                        warm.main()
                    run.assert_not_called()

    def test_warm_sessions_cannot_exceed_three_hours(self):
        with (
            TemporaryDirectory() as directory,
            patch.object(warm, "ROOT", Path(directory)),
            patch("sys.argv", ["warm", "--seconds", "10801"]),
            self.assertRaises(SystemExit),
        ):
            warm.main()

    def test_normal_exit_and_partial_activation_failure_restore_minimums(self):
        for fail in [False, True]:
            with TemporaryDirectory() as directory:
                root = Path(directory)
                for name, identifier in [
                    ("speech-deployment.json", "speech"),
                ]:
                    (root / name).write_text(json.dumps({"endpoint_id": identifier}))
                connection = Connection(fail)
                with (
                    patch.object(warm, "ROOT", root),
                    patch.object(warm, "JOURNAL", root / "journal.json"),
                    patch.object(warm, "client", return_value=connection),
                    patch.object(warm.time, "sleep"),
                    patch.object(warm.signal, "signal"),
                    patch("sys.argv", ["warm", "--seconds", "30"]),
                ):
                    if fail:
                        with self.assertRaises(RuntimeError):
                            warm.main()
                    else:
                        warm.main()
                self.assertEqual(
                    [value for _, value in connection.calls[-1:]],
                    [{"workersMin": 0}],
                )
                self.assertEqual(
                    json.loads((root / "journal.json").read_text())["state"], "restored"
                )

    def test_old_session_cannot_restore_over_newer_session(self):
        with TemporaryDirectory() as directory:
            journal = Path(directory) / "journal.json"
            journal.write_text(json.dumps({"session_id": "new"}))
            with (
                patch.object(warm, "JOURNAL", journal),
                patch.object(warm, "client") as client,
            ):
                with self.assertRaises(RuntimeError):
                    warm.restore({"session_id": "old", "endpoints": []})
                client.assert_not_called()
