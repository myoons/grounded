import asyncio
import copy
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from server.meeting_contracts import DetectionResult, MeetingStart
from server.meeting_history import MeetingHistory
from server.meeting_service import MeetingError, MeetingService
from tests.test_meeting import FakeDetector, FakeSpeech, change


class ExtractionCheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = TemporaryDirectory()
        self.path = Path(self.directory.name) / "history.sqlite"
        self.history = MeetingHistory(self.path)
        self.service = MeetingService(
            FakeSpeech(), FakeDetector(), history=self.history
        )
        await self.service.start(
            MeetingStart(consent=True, audio_consent_version="runpod-audio-v1")
        )
        self.session = self.service.current
        self.session["research_enabled"] = True
        self.segment = {
            "id": "utterance-1",
            "source": "system",
            "text": "A claim.",
            "start_ms": 0,
            "end_ms": 1000,
            "final": True,
        }
        self.session["segments"] = [self.segment]
        self.session["revision"] = 1
        self.service._persist(required=True)

    async def asyncTearDown(self):
        await self.service.close()
        self.history.close()
        self.directory.cleanup()

    def apply(self, changes, supplied=(), revision=1):
        self.service.apply_changes(
            self.session,
            DetectionResult(changes=changes),
            [self.segment],
            supplied,
            detector_updates={"processed_revision": revision, "state": "ready"},
        )

    async def test_claim_and_cursor_commit_together_before_research_dispatch(self):
        observed = []
        original = self.history.save

        def save(snapshot):
            observed.append(copy.deepcopy(snapshot))
            self.assertEqual(self.service.research_queue.qsize(), 0)
            original(snapshot)

        with patch.object(self.history, "save", side_effect=save):
            self.apply([change(self.segment["id"])])
        self.assertEqual(len(observed), 1)
        self.assertEqual(len(observed[0]["candidates"]), 1)
        self.assertEqual(observed[0]["detector"]["processed_revision"], 1)
        self.assertEqual(self.service.research_queue.qsize(), 1)
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM claims").fetchone()[0], 1
            )
        restored = self.history.store.load(self.session["id"])
        self.assertEqual(restored["detector"]["processed_revision"], 1)
        self.assertEqual(restored["candidates"][0]["research"]["state"], "pending")

    async def test_failed_commit_restores_cursor_claims_and_research_side_effects(self):
        self.apply([change(self.segment["id"])])
        self.service.research_queue.get_nowait()
        self.service.research_queue.task_done()
        candidate = self.session["candidates"][0]
        before = copy.deepcopy(candidate)
        detector_before = copy.deepcopy(self.session["detector"])
        active = asyncio.create_task(asyncio.sleep(60))
        self.service.research_active[candidate["id"]] = active
        update = change(
            self.segment["id"],
            action="update",
            candidate_id=candidate["id"],
            conditions=["Enterprise only"],
        )
        try:
            for failure in (
                sqlite3.OperationalError("disk full"),
                ValueError("bad snapshot"),
            ):
                with (
                    patch.object(self.history, "save", side_effect=failure),
                    self.assertRaises(MeetingError) as caught,
                ):
                    self.apply([update], [copy.deepcopy(candidate)], revision=2)
                self.assertEqual(caught.exception.code, "HISTORY_UNAVAILABLE")
                self.assertIs(self.session["candidates"][0], candidate)
                self.assertEqual(candidate, before)
                self.assertEqual(self.session["detector"], detector_before)
                self.assertEqual(active.cancelling(), 0)
                self.assertEqual(self.service.research_queue.qsize(), 0)
            self.apply([update], [copy.deepcopy(candidate)])
            self.assertEqual(
                candidate["research_revision"], before["research_revision"] + 1
            )
            self.assertEqual(active.cancelling(), 1)
            self.assertEqual(self.service.research_queue.qsize(), 1)
        finally:
            active.cancel()
            await asyncio.gather(active, return_exceptions=True)
            self.service.research_active.pop(candidate["id"], None)

    async def test_failed_create_has_no_claim_cursor_or_queue_then_retries_once(self):
        with (
            patch.object(
                self.history, "save", side_effect=sqlite3.OperationalError("disk full")
            ),
            self.assertRaises(MeetingError),
        ):
            self.apply([change(self.segment["id"])])
        self.assertEqual(self.session["candidates"], [])
        self.assertEqual(self.session["detector"]["processed_revision"], 0)
        self.assertEqual(self.service.research_queue.qsize(), 0)
        restored = self.history.store.load(self.session["id"])
        self.assertEqual(restored["candidates"], [])
        self.assertEqual(restored["detector"]["processed_revision"], 0)
        self.apply([change(self.segment["id"])])
        self.assertEqual(len(self.session["candidates"]), 1)
        self.assertEqual(self.service.research_queue.qsize(), 1)

    async def test_scope_reordering_preserves_revision_and_persisted_order(self):
        self.apply(
            [
                change(
                    self.segment["id"],
                    conditions=["A", "B"],
                    missing_information=["C", "D"],
                )
            ]
        )
        candidate = self.session["candidates"][0]
        revision = candidate["research_revision"]
        self.apply(
            [
                change(
                    self.segment["id"],
                    action="update",
                    candidate_id=candidate["id"],
                    conditions=["B", "A"],
                    missing_information=["D", "C"],
                )
            ],
            [copy.deepcopy(candidate)],
        )
        self.assertEqual(candidate["research_revision"], revision)
        self.assertEqual(candidate["conditions"], ["A", "B"])
        self.assertEqual(candidate["missing_information"], ["C", "D"])
        self.assertEqual(self.service.research_queue.qsize(), 1)
        restored = self.history.store.load(self.session["id"])["candidates"][0]
        self.assertEqual(restored["conditions"], ["A", "B"])
        self.assertEqual(restored["research_revision"], revision)


class ReceiptMigrationTests(unittest.TestCase):
    def test_v1_upgrade_failure_rolls_back_table_creation_and_can_retry(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite"
            history = MeetingHistory(path)
            with history.database:
                history.database.execute("DROP TABLE audio_receipts")
                history.database.execute("PRAGMA user_version=1")
            history.close()
            original = sqlite3.connect

            class FailingConnection(sqlite3.Connection):
                def execute(self, statement, parameters=()):
                    if statement == "PRAGMA user_version=3":
                        raise sqlite3.OperationalError("injected migration failure")
                    return super().execute(statement, parameters)

            def connect(*args, **kwargs):
                return original(*args, factory=FailingConnection, **kwargs)

            with (
                patch("server.meeting_history.sqlite3.connect", side_effect=connect),
                self.assertRaises(sqlite3.OperationalError),
            ):
                MeetingHistory(path)
            with closing(original(path)) as database:
                self.assertEqual(
                    database.execute("PRAGMA user_version").fetchone()[0], 1
                )
                self.assertIsNone(
                    database.execute(
                        "SELECT name FROM sqlite_master WHERE name='audio_receipts'"
                    ).fetchone()
                )
            recovered = MeetingHistory(path)
            self.assertEqual(
                recovered.database.execute("PRAGMA user_version").fetchone()[0], 3
            )
            recovered.close()
