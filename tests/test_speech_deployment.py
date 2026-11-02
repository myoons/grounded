import unittest
from pathlib import Path

from scripts.runpod_speech import configuration


class DeploymentTests(unittest.TestCase):
    def test_container_context_and_copy_use_an_explicit_file_allowlist(self):
        root = Path(__file__).resolve().parents[1]
        expected = {
            "Dockerfile",
            "requirements.lock",
            "__init__.py",
            "download.py",
            "engine.py",
            "handler.py",
        }
        patterns = (root / ".dockerignore").read_text().splitlines()
        self.assertEqual(patterns[0], "*")
        self.assertEqual(
            set(patterns[1:]),
            {"!speech_worker/", *(f"!speech_worker/{name}" for name in expected)},
        )
        dockerfile = (root / "speech_worker/Dockerfile").read_text()
        source_copy = next(
            line
            for line in dockerfile.splitlines()
            if line.startswith("COPY speech_worker/__init__.py ")
        )
        self.assertEqual(
            set(source_copy.split()[1:-1]),
            {f"speech_worker/{name}" for name in expected if name.endswith(".py")},
        )
        self.assertNotIn("COPY speech_worker /", dockerfile)

    def test_gpu_speech_scaling_and_execution_are_bounded(self):
        settings = configuration("template")
        self.assertEqual(settings["workersMin"], 0)
        self.assertEqual(settings["workersMax"], 1)
        self.assertEqual(settings["gpuCount"], 1)
        self.assertEqual(settings["executionTimeoutMs"], 20000)
        self.assertEqual(settings["idleTimeout"], 30)
