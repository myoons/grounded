import copy
import hashlib
import json
import unittest
from pathlib import Path

from scripts.evaluate_synthesis_gold import (
    GOLD,
    FixedSearch,
    rescore_report,
    score,
    score_v2,
)
from server.meeting_research import MeetingResearcher


class SynthesisGoldTests(unittest.TestCase):
    def setUp(self):
        self.cases = json.loads(GOLD.read_text())["cases"]
        self.case = next(
            item for item in self.cases if item["id"] == "plan_overgeneralization"
        )
        self.result = {
            "verdict": "contradicted",
            "summary": "No. Free permits ten exports per month.",
            "recommendation": "Verify the plan before relying on unlimited exports.",
            "caveats": [],
            "citations": [
                {
                    "source_id": "S1",
                    "quote": "LumaBox Free permits ten exports per month.",
                }
            ],
        }

    def test_coverage_and_unique_ids(self):
        self.assertEqual(len({item["category"] for item in self.cases}), 6)
        self.assertEqual(len({item["id"] for item in self.cases}), len(self.cases))
        for case in self.cases:
            self.assertLessEqual(len(case["expect"]["verdicts"]), 2)

    def test_valid_scoped_result(self):
        self.assertTrue(score(self.case, self.result)["passed"])
        self.assertTrue(score_v2(self.case, self.result)["passed"])

    def test_v2_rejects_wrong_quantity_and_unit_with_genuine_citation(self):
        for summary in [
            "No. Free allows one million exports per day.",
            "No. Free permits ten exports per day.",
            "No. Free permits 100 exports per month.",
            "No. Free permits ten exports per month and one million extra exports.",
        ]:
            with self.subTest(summary=summary):
                self.result["summary"] = summary
                self.assertTrue(score(self.case, self.result)["passed"])
                self.assertFalse(score_v2(self.case, self.result)["passed"])

    def test_v2_scope_cannot_be_supplied_only_by_caveats(self):
        self.result["summary"] = "No. The Free plan has a limit."
        self.result["caveats"] = ["Ten exports per month."]
        self.assertFalse(score_v2(self.case, self.result)["passed"])

    def test_exact_quote_does_not_pass_wrong_verdict(self):
        self.result["verdict"] = "supported"
        self.assertFalse(score(self.case, self.result)["checks"]["verdict"])

    def test_missing_scope_is_detected(self):
        self.result["summary"] = "No. The documents disprove this claim."
        self.assertFalse(score(self.case, self.result)["checks"]["scope_terms"])

    def test_unknown_source_and_fabricated_quote(self):
        for citation in [
            {"source_id": "S9", "quote": "LumaBox Free permits ten exports per month."},
            {"source_id": "S1", "quote": "LumaBox Free permits unlimited exports."},
        ]:
            result = copy.deepcopy(self.result)
            result["citations"] = [citation]
            self.assertFalse(score(self.case, result)["checks"]["citation_integrity"])

    def test_no_evidence_is_not_false(self):
        case = next(item for item in self.cases if item["id"] == "empty_sources")
        self.result.update(verdict="insufficient", citations=[])
        self.assertTrue(score(case, self.result)["passed"])
        self.result["verdict"] = "contradicted"
        self.assertFalse(score(case, self.result)["passed"])

    def test_rescore_preserves_transport_failures_in_denominator(self):
        rows = rescore_report(
            {
                "reports": [
                    {"id": self.case["id"], "repeat": 1, "result": self.result},
                    {
                        "id": self.case["id"],
                        "repeat": 2,
                        "passed": False,
                        "error_type": "TimeoutError",
                    },
                ]
            }
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(row["passed"] for row in rows), 1)
        self.assertEqual(rows[1]["error_type"], "TimeoutError")

    def test_factual_answer_cannot_pass_without_citation(self):
        self.result["citations"] = []
        self.assertFalse(score_v2(self.case, self.result)["passed"])

    def test_live_regression_counts_hash_and_both_rubrics_reproduce(self):
        folder = (
            Path(__file__).resolve().parents[1]
            / "docs/evaluations/synthesis-selector-regression"
        )
        raw = (folder / "results.json").read_bytes()
        report = json.loads(raw)
        rescored = json.loads((folder / "score-v2.json").read_text())
        self.assertEqual(
            report["gold_sha256"], hashlib.sha256(GOLD.read_bytes()).hexdigest()
        )
        self.assertEqual(rescored["source_sha256"], hashlib.sha256(raw).hexdigest())
        cases = {case["id"]: case for case in self.cases}
        self.assertEqual(report["total"], len(report["reports"]))
        for row in report["reports"]:
            self.assertEqual(
                row["passed"], score(cases[row["id"]], row["result"])["passed"]
            )
        self.assertEqual(
            report["passed"], sum(row["passed"] for row in report["reports"])
        )
        fresh = rescore_report(report)
        self.assertEqual(rescored["reports"], fresh)
        self.assertEqual(rescored["passed"], sum(row["passed"] for row in fresh))


class SynthesisPipelineRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_numeric_benchmark_reaches_model_with_exact_quote_options(self):
        case = next(
            item
            for item in json.loads(GOLD.read_text())["cases"]
            if item["id"] == "matched_performance"
        )

        class Model:
            calls = 0

            async def complete(self, _prompt, payload, _schema, _name, max_tokens):
                self.calls += 1
                choices = payload["sources"][0]["quote_options"]
                quote_id = next(
                    key for key, value in choices.items() if "completed" in value
                )
                return json.dumps(
                    {
                        "verdict": "supported",
                        "summary": "The specified benchmark measured two versus four seconds on the same machine.",
                        "recommendation": "Do not extrapolate beyond this dataset.",
                        "caveats": [],
                        "citations": {"S1": quote_id},
                        "comparison_basis": "matched_measurement",
                    }
                ), {}

        model = Model()
        result = await MeetingResearcher(FixedSearch(case), model).research(case)
        self.assertEqual(model.calls, 1)
        self.assertEqual(result["verdict"], "supported")
        self.assertIn(result["citations"][0]["quote"], case["documents"][0]["text"])


if __name__ == "__main__":
    unittest.main()
