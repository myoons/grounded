import copy
import hashlib
import json
import unittest
from pathlib import Path

from scripts.evaluate_claim_gold import DATASET, development_cases, score, segments_for


class GoldScoringTests(unittest.TestCase):
    def setUp(self):
        self.cases = json.loads(DATASET.read_text())["cases"]
        self.case = self.cases[0]
        self.change = {
            "action": "create",
            "category": "capability",
            "candidate_id": None,
            "evidence_ids": [segments_for(self.case["texts"])[0]["id"]],
        }

    def test_exact_match_and_extra_changes(self):
        self.assertTrue(score(self.case, [self.change])["passed"])
        self.assertFalse(score(self.case, [self.change, self.change])["passed"])

    def test_missing_wrong_category_action_or_evidence_fails(self):
        self.assertFalse(score(self.case, [])["passed"])
        for key, value in (
            ("category", "technical_behavior"),
            ("action", "defer"),
            ("evidence_ids", ["invented-id"]),
            ("candidate_id", "invented-id"),
        ):
            changed = {**self.change, key: value}
            self.assertFalse(score(self.case, [changed])["passed"])

    def test_empty_is_valid_only_when_no_change_expected(self):
        empty = next(case for case in self.cases if not case["expected"])
        self.assertTrue(score(empty, [])["passed"])
        repeat = next(case for case in self.cases if case["id"] == "heldout_repeat")
        self.assertFalse(score(repeat, [])["passed"])

    def test_matching_is_order_independent_but_one_to_one(self):
        case = copy.deepcopy(self.case)
        case["texts"].append("Node.js 18 has reached end of life.")
        case["expected"].append(
            {"action": "create", "category": "current_status", "required_evidence": [2]}
        )
        second = {
            **self.change,
            "category": "current_status",
            "evidence_ids": [segments_for(case["texts"])[1]["id"]],
        }
        self.assertTrue(score(case, [second, self.change])["passed"])
        self.assertFalse(score(case, [self.change, self.change])["passed"])

    def test_dataset_coverage_and_split_separation(self):
        self.assertEqual(len(self.cases), 20)
        self.assertEqual(len({case["id"] for case in self.cases}), 20)
        categories = {
            item["category"] for case in self.cases for item in case["expected"]
        }
        self.assertEqual(len(categories), 6)
        self.assertTrue(all(case["split"] == "heldout" for case in self.cases))
        self.assertTrue(all(case["split"] == "dev" for case in development_cases()))

    def test_recorded_results_reproduce_scores_and_dataset_hash(self):
        root = Path(__file__).resolve().parents[1]
        cases = {case["id"]: case for case in self.cases + development_cases()}
        for path in (root / "docs/evaluations/claim-gold").glob("*.json"):
            report = json.loads(path.read_text())
            self.assertEqual(
                report["dataset_sha256"],
                hashlib.sha256(DATASET.read_bytes()).hexdigest(),
            )
            for row in report["results"]:
                if "changes" in row:
                    self.assertEqual(
                        row["passed"],
                        score(cases[row["case"]], row["changes"])["passed"],
                    )
            self.assertEqual(
                report["summary"]["passed"],
                sum(row["passed"] for row in report["results"]),
            )
