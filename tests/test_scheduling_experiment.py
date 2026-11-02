import unittest

from scripts.experiments.scheduling import percentiles, replay


class SchedulingReplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_replay_is_deterministic_and_drains_bounded_queue(self):
        first = await replay("5s", 1)
        self.assertEqual(first, await replay("5s", 1))
        self.assertTrue(first["exact_expected_claims"])
        self.assertEqual(first["research_invocations"], 12)
        self.assertEqual(first["max_research_queue"], 8)
        self.assertEqual(first["audio_event_delay_seconds"], 0)

    async def test_workers_reduce_answer_wait_without_changing_extraction(self):
        serial = await replay("5s", 1)
        parallel = await replay("5s", 3)
        self.assertEqual(serial["claim_seconds"], parallel["claim_seconds"])
        self.assertEqual(
            serial["detector_invocations"], parallel["detector_invocations"]
        )
        self.assertGreater(
            serial["answer_seconds"]["p95"], parallel["answer_seconds"]["p95"]
        )

    async def test_zero_interval_remains_singleflight_when_model_is_slow(self):
        fixed = await replay("5s", 2, (8.0,))
        immediate = await replay("0s", 2, (8.0,))
        self.assertEqual(fixed["claim_seconds"], immediate["claim_seconds"])
        self.assertEqual(
            fixed["detector_invocations"], immediate["detector_invocations"]
        )

    async def test_fast_model_interval_trades_calls_for_latency(self):
        fixed = await replay("5s", 2, (0.5,))
        immediate = await replay("0s", 2, (0.5,))
        self.assertGreater(
            fixed["claim_seconds"]["p95"], immediate["claim_seconds"]["p95"]
        )
        self.assertLess(
            fixed["detector_invocations"], immediate["detector_invocations"]
        )

    def test_percentiles_are_nearest_rank(self):
        self.assertEqual(percentiles([4, 1, 3, 2]), {"p50": 2, "p95": 4})


if __name__ == "__main__":
    unittest.main()
