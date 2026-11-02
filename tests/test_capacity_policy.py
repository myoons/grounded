import asyncio
import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from server.capacity_policy import LEGACY_POLICY, CapacityPolicy, meeting_policy
from server.meeting_contracts import DetectionResult, MeetingStart
from server.meeting_detector import detection_schema
from server.meeting_service import ClaimCapacityError, MeetingService
from tests import test_meeting_history
from tests.test_meeting import FakeDetector, FakeSpeech, change


class PolicyValidationTests(unittest.TestCase):
    def test_operator_values_are_validated_and_frozen(self):
        for value in (-1, 769, True, "768"):
            with self.assertRaises(ValidationError):
                CapacityPolicy(max_segments=value)
        with self.assertRaises(ValidationError):
            CapacityPolicy().max_segments = 1
        with self.assertRaises(ValidationError):
            CapacityPolicy(max_duration_seconds=3601)
        with (
            patch.dict(os.environ, {"GROUNDED_DETECTION_REQUESTS": "oops"}),
            self.assertRaises(ValueError),
        ):
            CapacityPolicy.from_environment()

    def test_legacy_policy_is_explicit_and_not_current_environment(self):
        session = {}
        self.assertEqual(meeting_policy(session), LEGACY_POLICY)
        self.assertEqual(session["capacity_policy"]["max_segments"], 256)

    def test_eight_changes_contract(self):
        schema = detection_schema([{"id": "speech"}], [])
        self.assertEqual(schema["properties"]["changes"]["maxItems"], 8)

    def test_pending_batches_advance_chronologically_without_skipping(self):
        service = MeetingService.__new__(MeetingService)
        session = {
            "detector": {"processed_revision": 0},
            "revision": 11,
            "segments": [
                {
                    "id": str(index),
                    "source": "system",
                    "text": "A checkable claim.",
                    "final": True,
                    "possible_echo_of": None,
                }
                for index in range(11)
            ],
        }
        checkpoints = []
        while session["detector"]["processed_revision"] < session["revision"]:
            previous = session["detector"]["processed_revision"]
            segments, revision = service._detection_batch(session, [])
            self.assertLessEqual(revision - previous, 4)
            self.assertTrue(
                {str(index) for index in range(previous, revision)}
                <= {segment["id"] for segment in segments}
            )
            checkpoints.append(revision)
            session["detector"]["processed_revision"] = revision
        self.assertEqual(checkpoints, [4, 8, 11])


class CapacityHistoryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_meeting_history.HistoryTests.asyncSetUp
    asyncTearDown = test_meeting_history.HistoryTests.asyncTearDown

    async def test_saved_policy_survives_operator_config_change(self):
        original = self.service.current["capacity_policy"].copy()
        self.service._persist(required=True)
        self.service.capacity = CapacityPolicy(max_segments=2)
        self.assertEqual(
            self.history.store.load(self.identifier)["capacity_policy"], original
        )


class BudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_saturated_batch_retries_without_advancing_or_applying(self):
        class DenseDetector(FakeDetector):
            async def detect(self, segments, _candidates):
                return DetectionResult(
                    changes=[
                        change(segments[-1]["id"], topic_key=f"claim-{index}")
                        for index in range(8)
                    ]
                ), {}

        service = MeetingService(FakeSpeech(), DenseDetector())
        try:
            await service.start(
                MeetingStart(
                    consent=True,
                    audio_consent_version="runpod-audio-v1",
                    cloud_detection=True,
                    detection_consent_version="runpod-kimi-k3-v1",
                )
            )
            service.current["segments"] = [
                {
                    "id": str(index),
                    "source": "system",
                    "text": "A claim.",
                    "final": True,
                    "possible_echo_of": None,
                }
                for index in range(4)
            ]
            service.current["revision"] = 4
            service.changed.set()
            async with asyncio.timeout(2):
                while service.current["detector"].get("batch_limit") != 1:
                    await asyncio.sleep(0.01)
            self.assertEqual(service.current["detector"]["processed_revision"], 0)
            self.assertEqual(service.current["candidates"], [])
            self.assertEqual(service.current["detector"]["requests"], 1)
        finally:
            await service.close()

    async def test_claim_bound_rolls_back_without_making_capture_fatal(self):
        service = MeetingService(
            FakeSpeech(), FakeDetector(), capacity=CapacityPolicy(max_candidates=1)
        )
        try:
            await service.start(
                MeetingStart(consent=True, audio_consent_version="runpod-audio-v1")
            )
            segments = [
                {
                    "id": "speech",
                    "source": "system",
                    "text": "Two independent claims.",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "final": True,
                    "possible_echo_of": None,
                }
            ]
            service.current["segments"] = segments
            with self.assertRaises(ClaimCapacityError):
                service.apply_changes(
                    service.current,
                    DetectionResult(
                        changes=[
                            change("speech", topic_key="one"),
                            change(
                                "speech",
                                topic_key="two",
                                question="Does SQLite support network filesystems?",
                                statement="SQLite supports network filesystems.",
                            ),
                        ]
                    ),
                    segments,
                    [],
                    detector_updates={"processed_revision": 1},
                )
            self.assertEqual(service.current["candidates"], [])
            self.assertEqual(service.current["detector"]["processed_revision"], 0)
            self.assertIsNone(service.current["error"])
        finally:
            await service.close()

    async def test_zero_ai_budget_does_not_block_transcription_and_finishes_incomplete(
        self,
    ):
        speech = FakeSpeech()
        speech.results = [[{"text": "Hello", "start": 0.1, "end": 0.8}]]
        service = MeetingService(
            speech, FakeDetector(), capacity=CapacityPolicy(detection_requests=0)
        )
        try:
            session = await service.start(
                MeetingStart(
                    consent=True,
                    audio_consent_version="runpod-audio-v1",
                    cloud_detection=True,
                    detection_consent_version="runpod-kimi-k3-v1",
                )
            )
            service.accept(session["id"], 1, "system", 0, 0, 1000, 800, 0, bytes(32000))
            await service.queue.join()
            await service.action(session["id"], "stop")
            await asyncio.wait_for(service.finalizer, 3)
            self.assertEqual(len(service.current["segments"]), 1)
            self.assertEqual(service.current["detector"]["requests"], 0)
            self.assertEqual(service.current["finalization"]["state"], "incomplete")
            self.assertIsNone(service.current["error"])
        finally:
            await service.close()
