import asyncio
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from server.meeting_contracts import DetectionResult, MeetingStart
from server.meeting_history import MeetingHistory
from server.meeting_service import MeetingError, MeetingService
from tests.test_meeting import FakeSpeech, change


class ControlledDetector:
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.calls = []
        self.fail = False

    def ready(self):
        return True

    async def detect(self, segments, candidates):
        self.calls.append((segments, candidates))
        self.entered.set()
        await self.release.wait()
        if self.fail:
            raise RuntimeError("fixture unavailable")
        return DetectionResult(changes=[change(segments[-1]["id"])]), {}


class ControlledResearcher:
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.calls = []
        self.fail = False

    def ready(self):
        return True

    async def research(self, candidate, progress):
        self.calls.append(candidate["id"])
        progress("analyzing")
        self.entered.set()
        await self.release.wait()
        if self.fail:
            raise ValueError("fixture invalid evidence")
        return {"state": "ready", "verdict": "insufficient", "sources": []}


class FinalizationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = TemporaryDirectory()
        self.history = MeetingHistory(Path(self.directory.name) / "meetings.sqlite")
        self.speech = FakeSpeech()
        self.detector = ControlledDetector()
        self.researcher = ControlledResearcher()
        self.service = MeetingService(
            self.speech, self.detector, self.researcher, self.history
        )
        self.command = MeetingStart(
            consent=True,
            audio_consent_version="runpod-audio-v1",
            cloud_detection=True,
            detection_consent_version="runpod-kimi-k3-v1",
            research_enabled=True,
            research_consent_version="firecrawl-kimi-v1",
        )
        self.identifier = (await self.service.start(self.command))["id"]

    async def asyncTearDown(self):
        await self.service.close()
        self.history.close()
        self.directory.cleanup()

    def accept(self):
        self.speech.results.append(
            [{"text": "SQLite supports network filesystems.", "start": 0.1, "end": 0.8}]
        )
        return self.service.accept(
            self.identifier, 1, "system", 0, 0, 1000, 800, 0, bytes(32000)
        )

    async def finish(self):
        await self.service.action(self.identifier, "stop")
        await asyncio.wait_for(asyncio.shield(self.service.finalizer), 3)
        return self.service.view(self.identifier)

    async def test_end_drains_audio_then_extraction_then_research_and_persists(self):
        self.speech.gate = asyncio.Event()
        self.accept()
        finishing = await self.service.action(self.identifier, "stop")
        self.assertEqual(finishing["state"], "finishing")
        self.assertEqual(finishing["epoch"], 1)
        self.assertEqual(
            self.service.history_list()["meetings"][0]["state"], "finishing"
        )
        self.assertFalse(self.service.view(self.identifier)["archived"])
        with self.assertRaises(MeetingError):
            self.accept()
        with self.assertRaises(MeetingError):
            await self.service.start(self.command)
        with self.assertRaises(MeetingError):
            await self.service.clear(self.identifier)
        self.speech.gate.set()
        complete = await self.finish()
        self.assertEqual(complete["finalization"]["state"], "complete")
        self.assertEqual(len(complete["segments"]), 1)
        self.assertEqual(complete["candidates"][0]["research"]["state"], "ready")
        self.assertEqual(
            self.service.history_list()["meetings"][0]["finalization"]["state"],
            "complete",
        )
        self.assertEqual(
            self.history.load(self.identifier)["candidates"], complete["candidates"]
        )

    async def test_end_during_detector_retains_inflight_result(self):
        self.detector.release.clear()
        self.accept()
        await asyncio.wait_for(self.detector.entered.wait(), 2)
        await self.service.action(self.identifier, "stop")
        self.detector.release.set()
        result = await self.finish()
        self.assertEqual(len(self.detector.calls), 1)
        self.assertEqual(len(self.researcher.calls), 1)
        self.assertEqual(result["finalization"]["state"], "complete")

    async def test_end_during_research_and_repeated_end_are_idempotent(self):
        self.researcher.release.clear()
        self.accept()
        await asyncio.wait_for(self.researcher.entered.wait(), 2)
        await self.service.action(self.identifier, "stop")
        task = self.service.finalizer
        await self.service.action(self.identifier, "stop")
        self.assertIs(self.service.finalizer, task)
        self.researcher.release.set()
        result = await self.finish()
        self.assertEqual(result["finalization"]["state"], "complete")
        self.assertEqual(len(self.researcher.calls), 1)
        self.assertEqual(
            (await self.service.action(self.identifier, "stop"))["state"], "stopped"
        )

    async def test_detector_failure_is_terminal_incomplete(self):
        self.detector.fail = True
        self.accept()
        result = await self.finish()
        self.assertEqual(result["finalization"]["state"], "incomplete")
        self.assertEqual(result["detector"]["state"], "error")
        self.assertEqual(len(self.detector.calls), 1)

    async def test_transcription_failure_is_terminal_without_replaying_audio(self):
        async def fail(_pcm, _language):
            raise RuntimeError("fixture speech failure")

        self.speech.transcribe = fail
        self.accept()
        result = await self.finish()
        self.assertEqual(result["finalization"]["state"], "incomplete")
        self.assertEqual(result["sources"]["system"]["state"], "error")
        self.assertEqual(self.detector.calls, [])

    async def test_end_processes_speech_arriving_during_inflight_detection(self):
        self.detector.release.clear()
        self.accept()
        await asyncio.wait_for(self.detector.entered.wait(), 2)
        self.speech.results.append(
            [{"text": "The second claim arrived later.", "start": 0.1, "end": 0.8}]
        )
        self.service.accept(
            self.identifier, 1, "system", 1, 1000, 2000, 1800, 0, bytes(32000)
        )
        await self.service.action(self.identifier, "stop")
        self.detector.release.set()
        await asyncio.wait_for(asyncio.shield(self.service.finalizer), 8)
        result = self.service.view(self.identifier)
        self.assertEqual(result["finalization"]["state"], "complete")
        self.assertEqual(len(self.detector.calls), 2)
        self.assertEqual(
            self.detector.calls[-1][0][-1]["text"], "The second claim arrived later."
        )
        self.assertEqual(result["detector"]["processed_revision"], 2)

    async def test_revision_guard_rejects_obsolete_result_while_finishing(self):
        first_entered = asyncio.Event()
        calls = []

        async def research(candidate, progress):
            calls.append(candidate["research_revision"])
            progress("analyzing")
            if len(calls) == 1:
                first_entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    return {"state": "ready", "summary": "obsolete"}
            return {"state": "ready", "summary": "current"}

        self.researcher.research = research
        self.accept()
        await asyncio.wait_for(first_entered.wait(), 2)
        await self.service.action(self.identifier, "stop")
        candidate = self.service.current["candidates"][0]
        candidate["conditions"] = ["Enterprise plan only"]
        self.service._refresh_research(self.service.current, candidate)
        result = await self.finish()
        self.assertEqual(result["finalization"]["state"], "complete")
        self.assertEqual(calls, [1, 2])
        self.assertEqual(candidate["research"]["summary"], "current")

    async def test_research_failure_is_terminal_without_automatic_retry(self):
        self.researcher.fail = True
        self.accept()
        result = await self.finish()
        self.assertEqual(result["finalization"]["state"], "incomplete")
        self.assertEqual(result["candidates"][0]["research"]["state"], "error")
        self.assertEqual(len(self.researcher.calls), 1)

    async def test_budget_exhaustion_finishes_without_hanging(self):
        self.service.current["research"]["limit"] = 0
        self.accept()
        result = await self.finish()
        self.assertEqual(result["finalization"]["state"], "incomplete")
        self.assertEqual(result["candidates"][0]["research"]["state"], "budget_limit")
        self.assertEqual(self.researcher.calls, [])

    async def test_detection_budget_marks_unprocessed_transcript_incomplete(self):
        self.service.current["detector"]["requests"] = self.service.current[
            "capacity_policy"
        ]["detection_requests"]
        self.accept()
        result = await self.finish()
        self.assertEqual(result["finalization"]["state"], "incomplete")
        self.assertEqual(result["detector"]["state"], "budget_limit")
        self.assertEqual(self.detector.calls, [])

    async def test_timeout_cancels_unfinished_audio_and_records_incomplete(self):
        self.service.finalization_timeout = 0.05
        self.speech.gate = asyncio.Event()
        self.accept()
        result = await self.finish()
        self.assertEqual(result["finalization"]["state"], "incomplete")
        self.assertEqual(result["state"], "stopped")
        self.speech.gate.set()
        await asyncio.sleep(0)
        self.assertEqual(result["segments"], [])

    async def test_upload_failure_does_not_hide_completed_accepted_work(self):
        self.accept()
        await self.service.action(
            self.identifier, "stop", finalization_error="Last audio upload failed."
        )
        result = await self.finish()
        self.assertEqual(result["finalization"]["state"], "incomplete")
        self.assertIn("upload failed", result["finalization"]["message"])
        self.assertEqual(result["candidates"][0]["research"]["state"], "ready")

    async def test_restart_exposes_interrupted_finalization_not_eternal_finishing(self):
        self.speech.gate = asyncio.Event()
        self.accept()
        await self.service.action(self.identifier, "stop")
        snapshot = self.history.load(self.identifier)
        summary = self.history.list()["meetings"][0]
        for item in (snapshot, summary):
            self.assertEqual(item["state"], "stopped")
            self.assertEqual(item["finalization"]["state"], "incomplete")
            self.assertTrue(item["interrupted"])
        await self.service.close()
        self.assertEqual(
            self.history.load(self.identifier)["finalization"]["state"], "incomplete"
        )

    async def test_pending_research_larger_than_queue_is_drained(self):
        session = self.service.current
        for position in range(12):
            segment = {
                "id": str(uuid4()),
                "source": "system",
                "text": "Claim",
                "final": True,
                "start_ms": position * 1000,
                "end_ms": position * 1000 + 900,
            }
            session["segments"].append(segment)
            self.service.apply_changes(
                session,
                DetectionResult(
                    changes=[
                        change(
                            segment["id"],
                            topic_key=f"topic-{position}",
                            question=f"Does product {position} support streaming?",
                        )
                    ]
                ),
                [segment],
                [],
            )
        result = await self.finish()
        self.assertEqual(result["finalization"]["state"], "complete")
        self.assertEqual(len(self.researcher.calls), 12)
        self.assertTrue(
            all(item["research"]["state"] == "ready" for item in result["candidates"])
        )

    async def test_context_batch_does_not_skip_older_unprocessed_utterances(self):
        session = self.service.current
        for position in range(20):
            session["segments"].append(
                {
                    "id": str(uuid4()),
                    "source": "system",
                    "text": f"Claim {position}: " + "word " * 380,
                    "final": True,
                    "possible_echo_of": None,
                    "finalized_at": time.monotonic(),
                }
            )
        session["revision"] = 20
        first, revision = self.service._detection_batch(session, [])
        self.assertGreater(revision, 0)
        self.assertLess(revision, 20)
        self.assertEqual(first[0]["id"], session["segments"][0]["id"])
        session["detector"]["processed_revision"] = revision
        second, next_revision = self.service._detection_batch(session, [])
        self.assertGreater(next_revision, revision)
        self.assertIn(
            session["segments"][revision]["id"], {item["id"] for item in second}
        )
