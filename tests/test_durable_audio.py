import asyncio
import copy
import hashlib
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from server.meeting_contracts import MeetingStart
from server.meeting_history import MeetingHistory
from server.meeting_service import MeetingError, MeetingService
from tests.test_meeting import FakeDetector, FakeSpeech
from tests.test_meeting_finalization import ControlledDetector, ControlledResearcher


class DurableAudioTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = TemporaryDirectory()
        self.path = Path(self.directory.name) / "history.sqlite"
        self.history = MeetingHistory(self.path)
        self.speech = FakeSpeech()
        self.service = MeetingService(self.speech, FakeDetector(), history=self.history)
        self.session = await self.service.start(
            MeetingStart(consent=True, audio_consent_version="runpod-audio-v1")
        )
        self.identifier = self.session["id"]
        self.pcm = bytes(32000)
        self.digest = hashlib.sha256(self.pcm).hexdigest()

    async def asyncTearDown(self):
        await self.service.close()
        self.history.close()
        self.directory.cleanup()

    def accept(self, sequence=0, pcm=None, epoch=1):
        return self.service.accept(
            self.identifier,
            epoch,
            "microphone",
            sequence,
            sequence * 1000,
            sequence * 1000 + 1000,
            sequence * 1000 + 900,
            90000,
            self.pcm if pcm is None else pcm,
            durable=True,
        )

    def status(self, sequence=0):
        return self.service.audio_status(
            self.identifier, 1, "microphone", sequence, self.digest
        )

    async def drain(self):
        await asyncio.wait_for(self.service.queue.join(), 2)

    async def test_pending_is_not_ack_and_terminal_survives_restart(self):
        self.speech.gate = asyncio.Event()
        self.speech.results = [[{"text": "Hello", "start": 0.1, "end": 0.8}]]
        self.assertEqual(self.accept()["state"], "pending")
        self.assertEqual(self.status()["status"], "pending")
        self.assertEqual(self.accept()["state"], "pending")
        self.speech.gate.set()
        await self.drain()
        self.assertEqual(self.status()["status"], "processed")
        self.assertEqual(len(self.history.store.load(self.identifier)["segments"]), 1)
        await self.service.close()
        self.history.close()
        self.history = MeetingHistory(self.path)
        self.service = MeetingService(self.speech, FakeDetector(), history=self.history)
        self.assertEqual(self.status()["status"], "processed")
        await self.service.resume_processing(self.identifier)
        self.assertTrue(self.accept()["duplicate"])
        self.assertEqual(len(self.service.current["segments"]), 1)

    async def test_no_speech_commits_receipt_without_segment(self):
        self.accept()
        await self.drain()
        self.assertEqual(self.status()["status"], "no_speech")
        self.assertEqual(self.history.store.load(self.identifier)["segments"], [])

    async def test_capacity_is_permanent_but_never_acknowledges_audio_deletion(self):
        self.speech.results = [[{"text": "Hello", "start": 0.1, "end": 0.8}]]
        self.accept()
        await self.drain()
        segment = self.service.current["segments"][0]
        self.service.current["capacity_policy"]["max_segments"] = 4
        for index in range(1, 4):
            self.service.current["segments"].append(
                copy.deepcopy(segment) | {"id": f"capacity-{index}"}
            )
        self.service._persist(required=True)
        self.assertEqual(self.status()["status"], "processed")
        self.assertTrue(self.accept()["duplicate"])
        with self.assertRaises(MeetingError) as caught:
            self.accept(sequence=1)
        self.assertEqual(caught.exception.code, "AUDIO_CAPACITY_REACHED")
        chunk = {
            "session_id": self.identifier,
            "epoch": 1,
            "source": "microphone",
            "sequence": 1,
            "start_ms": 1000,
            "end_ms": 2000,
            "speech_end_ms": 1900,
            "sha256": self.digest,
        }
        self.history.receive_audio(chunk)
        self.history.fail_audio(chunk)
        expected = {
            "status": "failed",
            "retryable": False,
            "code": "AUDIO_CAPACITY_REACHED",
        }
        self.assertEqual(self.status(1), expected)
        await self.service.close()
        self.history.close()
        self.history = MeetingHistory(self.path)
        self.service = MeetingService(self.speech, FakeDetector(), history=self.history)
        self.assertEqual(self.status(1), expected)
        self.assertEqual(self.status()["status"], "processed")

    async def test_uncertain_empty_or_partial_transcript_never_acknowledges_deletion(
        self,
    ):
        for words in ([], [{"text": "Partial", "start": 0.1, "end": 0.8}]):
            with self.subTest(words=words):
                self.speech.transcribe = AsyncMock(
                    return_value={"words": words, "language": "en", "uncertain": True}
                )
                self.accept()
                await self.drain()
                self.assertEqual(self.status()["status"], "failed")
                self.assertEqual(
                    self.history.store.load(self.identifier)["segments"], []
                )
                self.assertEqual(
                    self.service.current["sources"]["microphone"]["last_word_ms"], -1
                )
        self.speech.transcribe = AsyncMock(
            return_value={
                "words": [{"text": "Complete", "start": 0.1, "end": 0.8}],
                "language": "en",
                "uncertain": False,
            }
        )
        self.accept()
        await self.drain()
        self.assertEqual(self.status()["status"], "processed")
        self.assertEqual(len(self.history.store.load(self.identifier)["segments"]), 1)

    async def test_conflicts_and_stale_epoch_rejected(self):
        self.accept()
        with self.assertRaises(MeetingError) as raised:
            self.accept(pcm=b"\x01" * 32000)
        self.assertEqual(raised.exception.code, "CHUNK_CONFLICT")
        with self.assertRaises(MeetingError) as raised:
            self.accept(epoch=2)
        self.assertEqual(raised.exception.code, "STALE_EPOCH")
        with self.assertRaises(MeetingError) as raised:
            self.service.audio_status(self.identifier, 1, "microphone", 0, "0" * 64)
        self.assertEqual(raised.exception.code, "CHUNK_CONFLICT")

    async def test_full_queue_does_not_create_receipt(self):
        with (
            patch.object(self.service.queue, "full", return_value=True),
            self.assertRaises(MeetingError) as raised,
        ):
            self.accept(4)
        self.assertEqual(raised.exception.status, 429)
        self.assertEqual(self.status(4), {"status": "missing", "retryable": True})

    async def test_same_source_waits_for_earlier_durable_ack(self):
        self.accept()
        with self.assertRaises(MeetingError) as raised:
            self.accept(1)
        self.assertEqual(raised.exception.code, "BACKPRESSURE")
        await self.drain()
        self.accept(1)
        await self.drain()
        self.assertEqual(self.status(1)["status"], "no_speech")

    async def test_failed_commit_rolls_back_transcript_and_allows_retry(self):
        self.speech.results = [[{"text": "Hello", "start": 0.1, "end": 0.8}]] * 2
        original = self.history.store.save

        def fail_after_save(snapshot):
            original(snapshot)
            raise sqlite3.OperationalError("disk failure")

        with patch.object(self.history.store, "save", side_effect=fail_after_save):
            self.accept()
            await self.drain()
        self.assertEqual(self.status(), {"status": "failed", "retryable": True})
        self.assertEqual(self.service.current["segments"], [])
        self.assertEqual(self.history.store.load(self.identifier)["segments"], [])
        self.accept()
        await self.drain()
        self.assertEqual(self.status()["status"], "processed")
        self.assertEqual(len(self.history.store.load(self.identifier)["segments"]), 1)

    async def test_restart_pending_receipt_requires_explicit_recovery(self):
        self.speech.gate = asyncio.Event()
        self.accept()
        await asyncio.sleep(0)
        await self.service.close()
        self.history.close()
        self.history = MeetingHistory(self.path)
        self.speech = FakeSpeech()
        self.service = MeetingService(self.speech, FakeDetector(), history=self.history)
        with self.assertRaises(MeetingError):
            self.accept()
        restored = await self.service.resume_processing(self.identifier)
        self.assertEqual(restored["epoch"], 1)
        with self.assertRaises(MeetingError) as raised:
            self.service.accept(
                self.identifier, 1, "microphone", 0, 0, 1000, 900, 0, self.pcm
            )
        self.assertEqual(raised.exception.code, "PROCESSING_ONLY")
        self.accept()
        await self.drain()
        self.assertEqual(self.status()["status"], "no_speech")

    async def test_cancelled_and_completed_meetings_cannot_be_recovered(self):
        await self.service.cancel_processing(self.identifier)
        with self.assertRaises(MeetingError):
            await self.service.resume_processing(self.identifier)
        with self.assertRaises(MeetingError):
            self.accept()
        self.assertEqual(
            self.history.store.load(self.identifier)["finalization"]["state"],
            "cancelled",
        )

    async def test_completed_meeting_recovery_rejected(self):
        await self.service.action(self.identifier, "stop")
        await self.service.finalizer
        with self.assertRaises(MeetingError):
            await self.service.resume_processing(self.identifier)

    async def test_failed_audio_cannot_be_marked_complete(self):
        with patch.object(
            self.speech, "transcribe", side_effect=RuntimeError("unavailable")
        ):
            self.accept()
            await self.drain()
        await self.service.action(self.identifier, "stop")
        await self.service.finalizer
        self.assertEqual(self.service.current["finalization"]["state"], "incomplete")

    async def test_recovery_finishes_extraction_without_remaining_audio(self):
        detector = ControlledDetector()
        researcher = ControlledResearcher()
        current = self.service.current
        current.update(cloud_detection=True, research_enabled=True)
        current["detector"]["blocked"] = True
        self.service.detector = detector
        self.service.researcher = researcher
        self.speech.results = [
            [{"text": "SQLite supports network filesystems.", "start": 0.1, "end": 0.8}]
        ]
        self.accept()
        await self.drain()
        self.assertEqual(self.status()["status"], "processed")
        await self.service.close()
        self.service = MeetingService(self.speech, detector, researcher, self.history)
        await self.service.resume_processing(self.identifier)
        await self.service.action(self.identifier, "stop")
        await asyncio.wait_for(self.service.finalizer, 3)
        self.assertEqual(len(detector.calls), 1)
        self.assertIsNone(self.service.current["detector"]["latency_ms"])
        self.assertEqual(len(researcher.calls), 1)
        self.assertEqual(self.service.current["finalization"]["state"], "complete")

    async def test_crash_snapshot_requeues_inflight_research(self):
        await self.test_recovery_finishes_extraction_without_remaining_audio()
        snapshot = self.history.store.load(self.identifier)
        snapshot["state"] = "capturing"
        snapshot["candidates"][0]["research"] = {"state": "analyzing"}
        self.history.save(snapshot)
        await self.service._halt()
        self.service.current = None
        researcher = ControlledResearcher()
        self.service.researcher = researcher
        await self.service.resume_processing(self.identifier)
        await self.service.action(self.identifier, "stop")
        await asyncio.wait_for(self.service.finalizer, 3)
        self.assertEqual(len(researcher.calls), 1)
        self.assertEqual(
            self.service.current["candidates"][0]["research"]["state"], "ready"
        )

    async def test_receipt_write_failure_never_enqueues(self):
        with (
            patch.object(
                self.history,
                "receive_audio",
                side_effect=sqlite3.OperationalError("full"),
            ),
            self.assertRaises(MeetingError) as raised,
        ):
            self.accept()
        self.assertEqual(raised.exception.status, 503)
        self.assertTrue(self.service.queue.empty())


class ReceiptMigrationTests(unittest.TestCase):
    def test_v1_upgrade_and_pending_recovery(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite"
            history = MeetingHistory(path)
            history.database.execute("DROP TABLE audio_receipts")
            history.database.execute("PRAGMA user_version=1")
            history.database.commit()
            history.close()
            history = MeetingHistory(path)
            self.assertEqual(
                history.database.execute("PRAGMA user_version").fetchone()[0], 3
            )
            self.assertEqual(
                history.database.execute("PRAGMA foreign_key_check").fetchall(), []
            )
            history.close()
