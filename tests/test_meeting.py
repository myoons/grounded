import asyncio
import time
import unittest
from uuid import uuid4

from server.meeting_contracts import (
    CandidateReview,
    DetectionResult,
    MeetingStart,
)
from server.meeting_service import MeetingError, MeetingService


class FakeSpeech:
    def __init__(self):
        self.results = []
        self.gate = None
        self.stopped = 0

    def ready(self):
        return True

    async def transcribe(self, _pcm, _language, _context=""):
        if self.gate:
            await self.gate.wait()
        return {"words": self.results.pop(0) if self.results else [], "language": "en"}

    async def stop(self):
        self.stopped += 1


class FakeDetector:
    def ready(self):
        return True

    async def detect(self, _segments, _candidates):
        return DetectionResult(changes=[]), {"total_tokens": 10}


def change(evidence_id, **fields):
    return {
        "action": "create",
        "candidate_id": None,
        "evidence_ids": [evidence_id],
        "question": "Can a standard Google Meet application access participant audio in real time?",
        "reason": "The proposed architecture depends on this capability.",
        "conditions": ["Standard meeting"],
        "missing_information": [],
        "topic_key": "google-meet-live-participant-audio",
        "related_ids": [],
        **fields,
    }


class MeetingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.speech = FakeSpeech()
        self.service = MeetingService(self.speech, FakeDetector())
        self.session = await self.service.start(
            MeetingStart(consent=True, audio_consent_version="runpod-audio-v1")
        )
        self.identifier = self.session["id"]

    async def asyncTearDown(self):
        await self.service.close()

    def accept(self, sequence=0, start=0, source="microphone", epoch=1, pcm=None):
        return self.service.accept(
            self.identifier,
            epoch,
            source,
            sequence,
            start,
            start + 1000,
            start + 900,
            100,
            pcm or bytes(32000),
        )

    async def wait_segments(self, count):
        for _attempt in range(100):
            if len(self.service.current["segments"]) >= count:
                return
            await asyncio.sleep(0.01)
        self.fail("Transcription did not finish")

    async def test_audio_idempotency_conflict_and_bounded_queue(self):
        self.accept()
        self.assertTrue(self.accept()["duplicate"])
        with self.assertRaises(MeetingError):
            self.accept(pcm=b"\x01" * 32000)
        for sequence in range(1, 4):
            self.accept(sequence, sequence * 1000)
        with self.assertRaises(MeetingError) as caught:
            self.accept(4, 4000)
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(self.service.queue.qsize(), 4)

    async def test_overlap_uses_word_times_and_preserves_source(self):
        self.speech.results = [
            [
                {"text": "Meet", "start": 0.1, "end": 0.5},
                {"text": " API", "start": 0.5, "end": 0.9},
            ],
            [
                {"text": " API", "start": 0, "end": 0.3},
                {"text": " supports audio", "start": 0.3, "end": 0.8},
            ],
        ]
        self.accept()
        await self.wait_segments(1)
        self.accept(1, 600)
        await self.wait_segments(2)
        segments = self.service.current["segments"]
        self.assertEqual(
            [item["text"] for item in segments], ["Meet API", "supports audio"]
        )
        self.assertTrue(
            all(item["final"] and item["source"] == "microphone" for item in segments)
        )

    async def test_echo_is_preserved_and_marked_not_silently_deleted(self):
        words = [{"text": "Google Meet supports live audio", "start": 0.1, "end": 0.9}]
        self.speech.results = [words, words]
        self.accept()
        self.accept(source="system")
        await self.wait_segments(2)
        first, second = self.service.current["segments"]
        self.assertEqual(second["possible_echo_of"], first["id"])
        self.assertEqual(second["source"], "system")

    async def test_pause_cancels_work_discards_buffers_and_rejects_old_epoch(self):
        self.speech.gate = asyncio.Event()
        self.speech.results = [[{"text": "old speech", "start": 0.1, "end": 0.9}]]
        self.accept()
        await asyncio.sleep(0.01)
        paused = await self.service.action(self.identifier, "pause")
        self.assertEqual(paused["state"], "paused")
        self.assertEqual(self.service.queue.qsize(), 0)
        self.assertEqual(paused["segments"], [])
        await self.service.action(self.identifier, "resume")
        with self.assertRaises(MeetingError):
            self.accept(epoch=1)
        self.assertGreaterEqual(self.speech.stopped, 2)

    async def test_old_session_cannot_mix_into_new_session(self):
        await self.service.action(self.identifier, "stop")
        await self.service.finalizer
        new = await self.service.start(
            MeetingStart(consent=True, audio_consent_version="runpod-audio-v1")
        )
        self.assertNotEqual(new["id"], self.identifier)
        with self.assertRaises(MeetingError):
            self.accept()

    async def test_uncertain_text_cannot_hide_later_valid_speech_as_an_echo(self):
        outcomes = [True, False]

        async def transcribe(_pcm, _language, _context=""):
            return {
                "words": [
                    {"text": "A clear technical statement.", "start": 0.1, "end": 0.9}
                ],
                "language": "en",
                "uncertain": outcomes.pop(0),
            }

        self.speech.transcribe = transcribe
        self.accept(source="microphone")
        await self.wait_segments(1)
        self.accept(source="system")
        await self.wait_segments(2)
        segments = self.service.current["segments"]
        self.assertFalse(segments[0]["final"])
        self.assertTrue(segments[1]["final"])
        self.assertIsNone(segments[1]["possible_echo_of"])

    async def test_repeat_links_evidence_without_research_or_scope_changes(self):
        first = {
            "id": str(uuid4()),
            "source": "system",
            "text": "An API supports streaming.",
            "start_ms": 0,
            "end_ms": 1000,
        }
        second = {
            **first,
            "id": str(uuid4()),
            "text": "That API supports streaming, as discussed.",
        }
        session = self.service.current
        self.service.apply_changes(
            session,
            DetectionResult(
                changes=[
                    change(
                        first["id"],
                        category="capability",
                        statement="The API supports streaming.",
                    )
                ]
            ),
            [first],
            [],
        )
        candidate = session["candidates"][0]
        revision = candidate["research_revision"]
        self.service.apply_changes(
            session,
            DetectionResult(
                changes=[
                    change(
                        second["id"],
                        action="link",
                        candidate_id=candidate["id"],
                        conditions=["Must not alter scope"],
                    )
                ]
            ),
            [first, second],
            [candidate.copy()],
        )
        self.assertEqual(candidate["research_revision"], revision)
        self.assertEqual(candidate["category"], "capability")
        self.assertNotIn("Must not alter scope", candidate["conditions"])
        self.assertEqual(candidate["evidence_ids"], [first["id"], second["id"]])

    async def test_pause_publishes_an_empty_queue_before_slow_remote_cleanup(self):
        release = asyncio.Event()

        async def slow_stop():
            await release.wait()

        self.speech.gate = asyncio.Event()
        self.speech.stop = slow_stop
        self.accept()
        await asyncio.sleep(0.01)
        self.accept(1, 1000)
        task = asyncio.create_task(self.service.action(self.identifier, "pause"))
        try:
            await asyncio.sleep(0.01)
            visible = self.service.view(self.identifier)
            self.assertEqual(visible["state"], "paused")
            self.assertEqual(visible["queue_depth"], 0)
            self.assertFalse(task.done())
        finally:
            release.set()
            await task

    async def test_new_speech_does_not_starve_completed_detection(self):
        started = asyncio.Event()
        release = asyncio.Event()

        class SlowDetector(FakeDetector):
            async def detect(self, segments, _candidates):
                started.set()
                await release.wait()
                return DetectionResult(changes=[change(segments[0]["id"])]), {
                    "total_tokens": 10
                }

        self.service.detector = SlowDetector()
        self.service.current["cloud_detection"] = True
        self.speech.results = [
            [{"text": "A technical claim.", "start": 0.1, "end": 0.8}],
            [{"text": "More discussion.", "start": 0.1, "end": 0.8}],
        ]
        self.accept()
        await asyncio.wait_for(started.wait(), 3)
        self.accept(1, 1000)
        await self.wait_segments(2)
        release.set()
        for _attempt in range(100):
            if self.service.current["candidates"]:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(len(self.service.current["candidates"]), 1)
        self.assertEqual(self.service.current["detector"]["state"], "catching_up")
        self.assertEqual(self.service.current["detector"]["processed_revision"], 1)

    async def test_detection_citations_dedup_correction_and_human_edit(self):
        session = self.service.current
        evidence = {
            "id": str(uuid4()),
            "source": "microphone",
            "text": "The API supports live audio.",
            "start_ms": 0,
            "end_ms": 1000,
        }
        self.service.apply_changes(
            session, DetectionResult(changes=[change(evidence["id"])]), [evidence], []
        )
        candidate = session["candidates"][0]
        self.service.apply_changes(
            session,
            DetectionResult(changes=[change(evidence["id"])]),
            [evidence],
            [candidate],
        )
        self.assertEqual(len(session["candidates"]), 1)
        self.assertEqual(candidate["evidence"][0]["text"], evidence["text"])
        self.service.review(
            self.identifier,
            candidate["id"],
            CandidateReview(
                action="edit",
                question="Which authorized Meet media access is available?",
            ),
        )
        self.service.apply_changes(
            session,
            DetectionResult(
                changes=[
                    change(
                        evidence["id"], action="update", candidate_id=candidate["id"]
                    )
                ]
            ),
            [evidence],
            [candidate],
        )
        self.assertEqual(
            candidate["question"], "Which authorized Meet media access is available?"
        )
        self.service.apply_changes(
            session,
            DetectionResult(
                changes=[
                    change(
                        evidence["id"], action="retract", candidate_id=candidate["id"]
                    )
                ]
            ),
            [evidence],
            [candidate],
        )
        self.assertEqual(candidate["state"], "retracted")

    async def test_invalid_evidence_rejects_whole_batch_and_explicit_action_defers(
        self,
    ):
        evidence = {
            "id": str(uuid4()),
            "source": "system",
            "text": "That service is free.",
            "start_ms": 0,
            "end_ms": 1000,
        }
        result = DetectionResult(
            changes=[change(evidence["id"]), change("invented-id")]
        )
        with self.assertRaises(ValueError):
            self.service.apply_changes(self.service.current, result, [evidence], [])
        self.assertEqual(self.service.current["candidates"], [])
        self.service.apply_changes(
            self.service.current,
            DetectionResult(
                changes=[
                    change(
                        evidence["id"],
                        action="defer",
                        missing_information=["Service name"],
                    )
                ]
            ),
            [evidence],
            [],
        )
        self.assertEqual(self.service.current["candidates"][0]["state"], "deferred")

    async def test_clear_removes_in_memory_session(self):
        await self.service.clear(self.identifier)
        with self.assertRaises(MeetingError):
            self.service.view(self.identifier)

    async def test_clear_is_atomic_against_a_concurrent_start(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow_stop():
            entered.set()
            await release.wait()

        self.speech.stop = slow_stop
        clearing = asyncio.create_task(self.service.clear(self.identifier))
        await entered.wait()
        starting = asyncio.create_task(
            self.service.start(
                MeetingStart(consent=True, audio_consent_version="runpod-audio-v1")
            )
        )
        await asyncio.sleep(0)
        self.assertFalse(starting.done())
        release.set()
        await clearing
        current = await starting
        self.assertEqual(self.service.current["id"], current["id"])
        self.assertNotEqual(current["id"], self.identifier)

    async def test_retry_detection_clears_old_error_without_replaying_audio(self):
        session = self.service.current
        session["cloud_detection"] = True
        session["detector"].update(blocked=True, state="error", error="Old failure")
        result = await self.service.action(self.identifier, "retry_detection")
        self.assertFalse(result["detector"]["blocked"])
        self.assertNotIn("error", result["detector"])
        self.assertTrue(self.service.queue.empty())

    async def test_resume_reawakens_unprocessed_transcript_without_new_audio(self):
        session = self.service.current
        session["revision"] = 1
        await self.service.action(self.identifier, "pause")
        self.assertFalse(self.service.changed.is_set())
        await self.service.action(self.identifier, "resume")
        self.assertTrue(self.service.changed.is_set())
        self.assertTrue(self.service.queue.empty())

    async def test_duration_limit_requires_new_session(self):
        await self.service.action(self.identifier, "pause")
        self.service.current["created"] = time.monotonic() - 3601
        with self.assertRaises(MeetingError):
            await self.service.action(self.identifier, "resume")

    async def test_model_cannot_assign_a_new_permanent_candidate_id(self):
        evidence = {
            "id": str(uuid4()),
            "source": "microphone",
            "text": "The Meet API supports audio.",
            "start_ms": 0,
            "end_ms": 1000,
        }
        self.service.apply_changes(
            self.service.current,
            DetectionResult(
                changes=[change(evidence["id"], candidate_id="invented-id")]
            ),
            [evidence],
            [],
        )
        self.assertNotEqual(self.service.current["candidates"][0]["id"], "invented-id")

    async def test_tail_word_duplicate_is_removed_only_in_overlap(self):
        self.speech.results = [
            [{"text": "Use the API.", "start": 0.1, "end": 0.9}],
            [{"text": "API.", "start": 0.1, "end": 0.5}],
            [{"text": "API.", "start": 0.1, "end": 0.5}],
        ]
        self.accept()
        await self.wait_segments(1)
        self.accept(1, 600)
        await asyncio.sleep(0.03)
        self.assertEqual(len(self.service.current["segments"]), 1)
        self.accept(2, 3000)
        await self.wait_segments(2)
        self.assertEqual(self.service.current["segments"][-1]["text"], "API.")

    async def test_new_vague_premise_can_be_deferred_without_fabricated_existing_id(
        self,
    ):
        evidence = {
            "id": str(uuid4()),
            "source": "system",
            "text": "That service is free.",
            "start_ms": 0,
            "end_ms": 1000,
        }
        self.service.apply_changes(
            self.service.current,
            DetectionResult(
                changes=[
                    change(
                        evidence["id"],
                        action="defer",
                        missing_information=["Service name"],
                    )
                ]
            ),
            [evidence],
            [],
        )
        self.assertEqual(self.service.current["candidates"][0]["state"], "deferred")
