import unittest

from scripts.experiments.context_memory import fixtures, lexical_context, score
from server.meeting_detector import build_context, encoded_size


class ContextMemoryExperimentTests(unittest.TestCase):
    def test_long_dialog_retrieval_retains_explicit_old_reference_within_budget(self):
        case = fixtures()[0]
        baseline = build_context(case["segments"], case["candidates"])
        related = lexical_context(case["segments"], case["candidates"])
        self.assertNotIn(
            case["expected"]["candidate_id"],
            {item["id"] for item in baseline["candidates"]},
        )
        self.assertIn(
            case["expected"]["candidate_id"],
            {item["id"] for item in related["candidates"]},
        )
        self.assertIn(
            case["expected"]["antecedent"],
            {item["id"] for item in related["transcript"]},
        )
        self.assertLessEqual(encoded_size(related), 16000)
        self.assertLessEqual(encoded_size(related["candidates"]), 6000)
        self.assertEqual(related["transcript"][-1], case["segments"][-1])
        positions = {item["id"]: index for index, item in enumerate(case["segments"])}
        order = [positions[item["id"]] for item in related["transcript"]]
        self.assertEqual(order, sorted(order))

    def test_exclusion_is_not_scored_as_success_for_spurious_card(self):
        expected = fixtures()[-1]["expected"]
        self.assertTrue(score({"changes": []}, expected)["action_and_identity"])
        self.assertFalse(
            score({"changes": [{"action": "create"}]}, expected)["action_and_identity"]
        )

    def test_fixture_generation_is_deterministic_and_all_arms_bounded(self):
        self.assertEqual(fixtures(), fixtures())
        for case in fixtures():
            for selector in (build_context, lexical_context):
                self.assertLessEqual(
                    encoded_size(selector(case["segments"], case["candidates"])), 16000
                )
