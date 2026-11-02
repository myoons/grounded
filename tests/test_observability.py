import asyncio
import contextlib
import io
import json
import math
import stat
import tempfile
import unittest
from pathlib import Path

from server.meeting_contracts import MeetingStart
from server.meeting_service import MeetingService
from server.observability import EventLog, error_cause
from tests import test_meeting_research as research_fixtures
from tests.test_meeting import FakeDetector, FakeSpeech


class EventLogTests(unittest.TestCase):
    def test_allowlist_drops_content_and_nonfinite_values(self):
        records = []
        log = EventLog(sink=records.append)
        log.emit(
            stage="audio",
            status="failed",
            source="https://secret.example",
            transcript="private words",
            error=RuntimeError("secret"),
            cause="api_key_secret",
            duration_ms=math.inf,
            wait_ms=-1,
            session_id="private words",
            sequence=2,
        )
        self.assertEqual(
            set(records[0]), {"schema_version", "at", "stage", "status", "sequence"}
        )
        self.assertNotIn("secret", json.dumps(records))
        self.assertEqual(error_cause(RuntimeError("secret")), "processing_error")

    def test_rotation_and_permissions_are_bounded(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "logs"
            log = EventLog(directory, max_bytes=1024, backups=2)
            for sequence in range(100):
                log.emit(stage="audio", status="admitted", sequence=sequence)
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            self.assertEqual(len(list(directory.iterdir())), 3)
            for path in directory.iterdir():
                self.assertLessEqual(path.stat().st_size, 1024)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                for line in path.read_text().splitlines():
                    self.assertEqual(json.loads(line)["stage"], "audio")

    def test_failed_sink_is_nonfatal_and_warning_is_bounded(self):
        def broken(_record):
            raise OSError("sensitive error")

        log = EventLog(sink=broken)
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            for _ in range(20):
                log.emit(stage="audio", status="admitted")
        self.assertEqual(output.getvalue().count("trace may be incomplete"), 1)
        self.assertNotIn("sensitive", output.getvalue())


class PipelineEventsTests(unittest.IsolatedAsyncioTestCase):
    async def test_detector_logs_applied_checkpoint(self):
        records = []
        speech = FakeSpeech()
        speech.results = [[{"text": "Private spoken content", "start": 0, "end": 0.8}]]
        service = MeetingService(
            speech, FakeDetector(), events=EventLog(sink=records.append)
        )
        session = await service.start(
            MeetingStart(
                consent=True,
                audio_consent_version="runpod-audio-v1",
                cloud_detection=True,
                detection_consent_version="runpod-kimi-k3-v1",
            )
        )
        try:
            service.accept(
                session["id"], 1, "microphone", 0, 0, 1000, 900, 100, bytes(32000)
            )
            async with asyncio.timeout(2):
                while not any(
                    row.get("stage") == "detection" and row.get("status") == "complete"
                    for row in records
                ):
                    await asyncio.sleep(0.01)
            rows = [row for row in records if row.get("stage") == "detection"]
            self.assertEqual([row["status"] for row in rows], ["started", "complete"])
            self.assertEqual(rows[-1]["processed_revision"], 1)
            self.assertEqual(rows[-1]["revision"], 1)
            self.assertGreaterEqual(rows[-1]["duration_ms"], 0)
        finally:
            await service.close()

    async def test_real_service_events_correlate_without_content(self):
        records = []
        speech = FakeSpeech()
        speech.results = [[{"text": "Private spoken content", "start": 0, "end": 0.8}]]
        service = MeetingService(
            speech, FakeDetector(), events=EventLog(sink=records.append)
        )
        session = await service.start(
            MeetingStart(consent=True, audio_consent_version="runpod-audio-v1")
        )
        try:
            service.accept(
                session["id"], 1, "microphone", 0, 0, 1000, 900, 100, bytes(32000)
            )
            await asyncio.wait_for(service.queue.join(), 2)
            audio = [row for row in records if row.get("sequence") == 0]
            self.assertEqual(
                [row["status"] for row in audio],
                ["admitted", "started", "provider_returned", "processed"],
            )
            self.assertTrue(all(row["session_id"] == session["id"] for row in audio))
            self.assertTrue(
                all(
                    row["epoch"] == 1 and row["source"] == "microphone" for row in audio
                )
            )
            self.assertGreaterEqual(audio[1]["wait_ms"], 0)
            self.assertNotIn("Private spoken", json.dumps(records))
        finally:
            await service.close()

    async def test_logging_failure_does_not_break_transcription(self):
        def broken(_record):
            raise OSError("private provider message")

        service = MeetingService(
            FakeSpeech(), FakeDetector(), events=EventLog(sink=broken)
        )
        with contextlib.redirect_stderr(io.StringIO()):
            session = await service.start(
                MeetingStart(consent=True, audio_consent_version="runpod-audio-v1")
            )
            try:
                service.accept(
                    session["id"], 1, "microphone", 0, 0, 1000, 900, 100, bytes(32000)
                )
                await asyncio.wait_for(service.queue.join(), 2)
                self.assertEqual(
                    service.current["sources"]["microphone"]["state"], "no_speech"
                )
            finally:
                await service.close()


class ResearchEventsTests(unittest.IsolatedAsyncioTestCase):
    add = research_fixtures.ResearchLifecycleTests.add
    until = research_fixtures.ResearchLifecycleTests.until
    asyncTearDown = research_fixtures.ResearchLifecycleTests.asyncTearDown

    async def asyncSetUp(self):
        await research_fixtures.ResearchLifecycleTests.asyncSetUp(self)
        self.records = []
        self.service.events = EventLog(sink=self.records.append)

    async def test_research_identity_and_failure_are_observable(self):
        self.researcher.fail = True
        candidate = self.add()
        await self.until(lambda: candidate["research"]["state"] == "error")
        rows = [row for row in self.records if row.get("stage") == "research"]
        self.assertEqual(
            [row["status"] for row in rows], ["queued", "started", "progress", "failed"]
        )
        self.assertTrue(all(row["claim_id"] == candidate["id"] for row in rows))
        self.assertTrue(
            all(
                row["research_revision"] == candidate["research_revision"]
                for row in rows
            )
        )
        self.assertEqual(rows[-1]["cause"], "processing_error")
        self.assertNotIn("Controlled search failure", json.dumps(rows))
