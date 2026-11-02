import array
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.experiments.audio_boundaries import GRID, NODE, ROOT, word_error, write_wav


class AudioBoundaryMetricsTests(unittest.TestCase):
    def test_raw_word_error_penalizes_missing_condition_and_overlap_duplicates(self):
        self.assertEqual(word_error("only enterprise if enabled", "Only enterprise if enabled."), 0)
        self.assertEqual(word_error("only enterprise if enabled", "enterprise enabled"), 0.5)
        self.assertEqual(word_error("only enterprise", "only enterprise only enterprise"), 1)

    def test_frozen_grid_respects_actual_block_and_provider_bounds(self):
        self.assertEqual(GRID["baseline"], (600, 6000, 1200))
        for silence, maximum, overlap in GRID.values():
            self.assertTrue(all(value % 200 == 0 for value in (silence, maximum, overlap)))
            self.assertLessEqual(maximum, 7000)
            self.assertLess(overlap, maximum)

    def test_actual_chunker_zero_overlap_and_baseline_do_not_emit_silence_only_tail(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "signal.wav"
            write_wav(path, array.array("h", [1000] * (16000 * 7)))
            for variant, expected_start in (("baseline", 4800), ("overlap_none", 6000)):
                raw = subprocess.check_output(["node", "--input-type=module", "-e", NODE,
                                               str(path), json.dumps(GRID[variant])], cwd=ROOT)
                chunks = json.loads(raw)
                self.assertEqual(len(chunks), 2)
                self.assertEqual(chunks[0]["end_ms"], 6000)
                self.assertEqual(chunks[1]["start_ms"], expected_start)
                self.assertEqual(chunks[1]["speech_end_ms"], 7000)
