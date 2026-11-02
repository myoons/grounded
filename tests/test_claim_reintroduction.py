import copy
import unittest

from server.meeting_contracts import DetectionResult, MeetingStart
from server.meeting_service import MeetingService
from tests.test_meeting import FakeDetector, FakeSpeech, change


class ClaimReintroductionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = MeetingService(FakeSpeech(), FakeDetector())
        await self.service.start(
            MeetingStart(consent=True, audio_consent_version="runpod-audio-v1")
        )
        self.session = self.service.current
        self.segments = [
            {
                "id": identifier,
                "source": "microphone",
                "text": text,
                "start_ms": index * 1000,
                "end_ms": (index + 1) * 1000,
            }
            for index, (identifier, text) in enumerate(
                [
                    ("first", "The Free plan supports streaming."),
                    ("withdrawal", "Withdraw that assumption."),
                    ("new", "Check again: Enterprise supports streaming."),
                    ("repeat", "Enterprise supports streaming."),
                ]
            )
        ]
        self.apply("first", statement="The Free plan supports streaming.")
        self.original = self.session["candidates"][0]
        self.apply(
            "withdrawal",
            action="retract",
            candidate_id=self.original["id"],
            statement=self.original["statement"],
        )

    async def asyncTearDown(self):
        await self.service.close()

    def apply(self, evidence_id, **fields):
        self.service.apply_changes(
            self.session,
            DetectionResult(changes=[change(evidence_id, **fields)]),
            self.segments,
            copy.deepcopy(self.session["candidates"]),
        )

    async def test_reintroduction_creates_linked_card_and_preserves_withdrawal(self):
        previous = copy.deepcopy(self.original)
        self.apply("new", statement="The Enterprise plan supports streaming.")
        self.assertEqual(len(self.session["candidates"]), 2)
        fresh = self.session["candidates"][1]
        self.assertNotEqual(fresh["id"], previous["id"])
        self.assertEqual(fresh["related_ids"], [previous["id"]])
        self.assertEqual(self.original, previous)
        self.assertEqual(fresh["evidence_ids"], ["new"])
        revision = fresh["research_revision"]
        self.apply("repeat", statement=fresh["statement"])
        self.assertEqual(len(self.session["candidates"]), 2)
        self.assertEqual(fresh["research_revision"], revision)
        self.assertEqual(fresh["evidence_ids"], ["new", "repeat"])

    async def test_update_after_withdrawal_creates_new_identity(self):
        self.apply(
            "new",
            action="update",
            candidate_id=self.original["id"],
            statement="The Enterprise plan supports streaming.",
        )
        self.assertEqual(len(self.session["candidates"]), 2)
        self.assertEqual(self.original["state"], "retracted")

    async def test_repetition_or_old_evidence_does_not_resurrect(self):
        self.apply("new", statement=self.original["statement"])
        self.apply("first", statement="The Enterprise plan supports streaming.")
        self.apply(
            "new",
            action="link",
            candidate_id=self.original["id"],
            statement="The Enterprise plan supports streaming.",
        )
        self.assertEqual(len(self.session["candidates"]), 1)
        self.assertEqual(self.original["state"], "retracted")

    async def test_user_ignored_item_stays_ignored(self):
        self.original["state"] = "ignored"
        self.apply("new", statement="The Enterprise plan supports streaming.")
        self.assertEqual(len(self.session["candidates"]), 1)
        self.assertEqual(self.original["state"], "ignored")

    async def test_unknown_evidence_rejected_without_mutation(self):
        previous = copy.deepcopy(self.original)
        with self.assertRaises(ValueError):
            self.apply("invented", statement="The Enterprise plan supports streaming.")
        self.assertEqual(self.session["candidates"], [previous])
