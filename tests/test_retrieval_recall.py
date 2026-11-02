import unittest

from scripts.experiments.retrieval_recall import (
    adjacent_passages,
    inclusion,
    summarize_search,
)


class RetrievalRecallTests(unittest.TestCase):
    def setUp(self):
        self.case = {
            "id": "fixture",
            "url": "https://example.org/guide",
            "question": "Does archive export require enterprise?",
            "decisive": "Archive export is supported.",
            "condition": "Enterprise accounts only.",
        }

    def test_condition_loss_is_not_full_recall(self):
        self.assertEqual(
            inclusion(self.case["decisive"], self.case),
            {
                "decisive": True,
                "condition": False,
                "both": False,
            },
        )

    def test_url_recall_is_separate_from_scrape_availability(self):
        body = {
            "success": True,
            "data": {
                "web": [
                    {"url": "https://unrelated.org/page", "markdown": "text " * 30},
                    {"url": "https://example.org/guide", "markdown": ""},
                ]
            },
        }
        result = summarize_search(body, self.case)
        self.assertTrue(result["prefixes"][0]["canonical_url_found"])
        self.assertFalse(result["prefixes"][0]["canonical_source_available"])

    def test_rank_four_is_only_in_larger_prefix(self):
        unrelated = {"url": "https://unrelated.org/page", "markdown": "text " * 30}
        gold = {
            "url": self.case["url"],
            "markdown": (
                self.case["decisive"]
                + "\n\n"
                + self.case["condition"]
                + " context" * 20
            ),
        }
        result = summarize_search(
            {"success": True, "data": {"web": [unrelated] * 3 + [gold]}}, self.case
        )
        self.assertFalse(result["prefixes"][0]["canonical_full_evidence"])
        self.assertTrue(result["prefixes"][1]["canonical_full_evidence"])

    def test_neighbors_preserve_adjacent_condition_with_budget(self):
        text = (
            "Background.\n\n" + self.case["decisive"] + "\n\n" + self.case["condition"]
        )
        selected = adjacent_passages(text, self.case["question"], 100)
        self.assertLessEqual(len(selected), 100)
        self.assertTrue(inclusion(selected, self.case)["both"])

    def test_error_page_can_pass_runtime_length_gate(self):
        body = {
            "success": True,
            "data": {
                "web": [
                    {
                        "url": self.case["url"],
                        "markdown": "The server load is currently too high. Please try again later. "
                        * 3,
                    }
                ]
            },
        }
        source = summarize_search(body, self.case)["sources"][0]
        self.assertTrue(source["available"])
        self.assertTrue(source["server_error_boilerplate"])
        self.assertFalse(source["full_evidence"]["both"])


if __name__ == "__main__":
    unittest.main()
