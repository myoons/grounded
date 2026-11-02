import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

from scripts import build_figures


class FigureTests(unittest.TestCase):
    def test_figures_regenerate_exactly_and_have_accessible_descriptions(self):
        original = build_figures.OUTPUT
        with tempfile.TemporaryDirectory() as directory:
            generated = Path(directory)
            (generated / "measurements.json").write_bytes(
                (original / "measurements.json").read_bytes()
            )
            with patch.object(build_figures, "OUTPUT", generated):
                build_figures.main()
            manifest = json.loads((generated / "manifest.json").read_text())
            self.assertEqual(len(manifest), 10)
            for figure in manifest:
                name = figure["name"] + ".svg"
                self.assertEqual(
                    (original / name).read_bytes(), (generated / name).read_bytes()
                )
                tree = ElementTree.fromstring((generated / name).read_text())
                self.assertEqual(tree.attrib["role"], "img")
                self.assertTrue(tree.find("{http://www.w3.org/2000/svg}desc").text)
                self.assertNotIn("<script", (generated / name).read_text())

    def test_measurements_are_bounded_and_disclose_their_scope(self):
        data = json.loads((build_figures.OUTPUT / "measurements.json").read_text())
        self.assertIn("one observation", data["provenance"])
        self.assertIn("excludes", data["scope"])
        for row in data["rows"]:
            self.assertEqual(len(row["report_sha256"]), 64)
            components = sum(
                row[key] for key in ("search_ms", "synthesis_ms", "validation_ms")
            )
            self.assertLessEqual(abs(row["total_ms"] - components), 2)
            self.assertLess(row["total_ms"], 30000)
