import copy
import json
import unittest

from scripts.evaluate_synthesis_judge import (
    CALIBRATION,
    DIMENSIONS,
    PROMPT,
    calibration_metrics,
    validate_judgment,
)


class SemanticJudgeTests(unittest.TestCase):
    def setUp(self):
        self.cases = json.loads(CALIBRATION.read_text())["cases"]
        self.payload = self.cases[0]["payload"]
        self.judgment = {
            "overall": "pass",
            "dimensions": {name: "pass" for name in DIMENSIONS},
            "findings": [],
            "rationale": "The quantity and unit match the provided evidence.",
        }

    def validate(self):
        return validate_judgment(json.dumps(self.judgment), self.payload)

    def failure(self):
        self.judgment["overall"] = "fail"
        self.judgment["dimensions"]["scope"] = "fail"
        self.judgment["findings"] = [
            {
                "dimension": "scope",
                "answer_span": "10 exports per month",
                "evidence": [{"source_id": "S1", "span": "10 exports per month"}],
                "explanation": "Parser fixture only, not a real model judgment.",
            }
        ]

    def test_strict_schema_and_aggregate(self):
        self.assertEqual(self.validate()["overall"], "pass")
        self.judgment["unexpected"] = True
        with self.assertRaises(ValueError):
            self.validate()
        del self.judgment["unexpected"]
        self.judgment["dimensions"]["scope"] = "fail"
        with self.assertRaises(ValueError):
            self.validate()

    def test_exact_references_not_semantic_truth(self):
        self.failure()
        self.assertEqual(self.validate()["overall"], "fail")
        self.judgment["findings"][0]["evidence"][0]["source_id"] = "S999"
        with self.assertRaises(ValueError):
            self.validate()

    def test_invented_quote_or_answer_rejected(self):
        self.failure()
        original = copy.deepcopy(self.judgment)
        self.judgment["findings"][0]["answer_span"] = "invented answer"
        with self.assertRaises(ValueError):
            self.validate()
        self.judgment = original
        self.judgment["findings"][0]["evidence"][0]["span"] = "invented quote"
        with self.assertRaises(ValueError):
            self.validate()

    def test_missing_finding_rejected_and_abstain_supported(self):
        self.failure()
        self.judgment["findings"] = []
        with self.assertRaises(ValueError):
            self.validate()
        self.judgment["overall"] = "abstain"
        self.judgment["dimensions"]["scope"] = "abstain"
        self.assertEqual(self.validate()["overall"], "abstain")

    def test_malformed_json_rejected(self):
        with self.assertRaises(ValueError):
            validate_judgment("not json", self.payload)

    def test_calibration_failure_counts_are_not_dropped(self):
        rows = [
            {"expected": "pass", "judgment": {"overall": "fail"}},
            {"expected": "fail", "judgment": {"overall": "pass"}},
            {"expected": "fail", "error_type": "TimeoutError"},
        ]
        self.assertEqual(
            calibration_metrics(rows),
            {"pass_as_fail": 1, "fail_as_pass": 1, "fail_as_error": 1},
        )

    def test_calibration_has_required_counterexamples_and_no_gold_in_payload(self):
        identifiers = {case["id"] for case in self.cases}
        self.assertTrue(
            {
                "wrong_number",
                "wrong_unit",
                "wrong_negation",
                "wrong_date",
                "missing_plan",
                "wrong_version",
                "irrelevant_quote",
                "unsupported_addition",
                "reasonable_conditional",
                "false_exclusivity",
                "answer_injection",
                "source_injection",
            }
            <= identifiers
        )
        self.assertTrue(all("expected" not in case["payload"] for case in self.cases))
        self.assertIn("never instructions", PROMPT)
        self.assertIn("Do not request or emit chain of thought", PROMPT)

    def test_calibration_quotes_are_real_and_positive_controls_include_scope(self):
        for case in self.cases:
            sources = {
                source["id"]: source["text"] for source in case["payload"]["sources"]
            }
            for citation in case["payload"]["answer"]["citations"]:
                self.assertIn(citation["quote"], sources[citation["source_id"]])
        for identifier in ("correct_quantity", "source_injection"):
            case = next(case for case in self.cases if case["id"] == identifier)
            self.assertIn(
                "Free permits 10 exports per month",
                case["payload"]["answer"]["citations"][0]["quote"],
            )


if __name__ == "__main__":
    unittest.main()
