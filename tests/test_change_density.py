import json
import unittest

from scripts.experiments.change_density import (
    DATASET,
    FROZEN,
    BaselineResult,
    ExpandedResult,
    make_case,
    score,
)
from server.meeting_detector import build_context
from server.meeting_service import MeetingService


class ChangeDensityTests(unittest.TestCase):
    def setUp(self):
        self.claims = json.loads(DATASET.read_text())["claims"]

    def test_all_pending_sent_does_not_require_all_claims_extracted(self):
        case = make_case(8, self.claims)
        service = MeetingService.__new__(MeetingService)
        session = {
            "detector": {"processed_revision": 0},
            "revision": len(case["segments"]),
            "segments": case["segments"],
        }
        segments, revision = service._detection_batch(session, [])
        self.assertEqual(len(build_context(segments, [])["transcript"]), 4)
        session["detector"]["processed_revision"] = revision
        self.assertGreater(service._detection_batch(session, [])[1], revision)
        self.assertEqual(score(case, [])["strict_recall"], 0)

    def test_wrong_semantics_and_duplicates_do_not_inflate_recall(self):
        case = make_case(1, self.claims)
        gold = case["gold"][0]
        change = {
            "action": "create",
            "category": "capability",
            "candidate_id": None,
            "statement": "PostgreSQL 16 supports row-level security",
            "question": "Does PostgreSQL 16 support row-level security?",
            "evidence_ids": gold["required_evidence"],
        }
        result = score(case, [change, change])
        self.assertEqual(result["strict_recall"], 1)
        self.assertEqual(result["strict_precision"], 0.5)
        self.assertEqual(result["duplicates"], 1)
        change["statement"], change["question"] = (
            "Unrelated statement",
            "Unrelated question?",
        )
        self.assertEqual(score(case, [change])["strict_recall"], 0)

    def test_frozen_baseline_is_independent_of_production_cap(self):
        frozen = json.loads(FROZEN.read_text())
        self.assertIn("Produce at most three changes.", frozen["baseline_prompt"])
        for row in frozen["rows"]:
            expected = 3 if row["strategy"] == "max3" else 8
            self.assertEqual(
                row["schema"]["properties"]["changes"]["maxItems"], expected
            )
        self.assertEqual(
            BaselineResult.model_json_schema()["properties"]["changes"]["maxItems"], 3
        )
        self.assertEqual(
            ExpandedResult.model_json_schema()["properties"]["changes"]["maxItems"], 8
        )
