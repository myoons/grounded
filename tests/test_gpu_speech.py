import asyncio
import base64
import json
import math
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from server.meeting_gpu_speech import (
    MODEL,
    REVISION,
    RunpodTranscriber,
    validate_output,
)
from speech_worker.engine import SpeechEngine, decode_request, validate_model_files


def output(**fields):
    return {
        "words": [{"text": " Hello", "start": 0, "end": 0.5}],
        "language": "en",
        "uncertain": False,
        "model": MODEL,
        "revision": REVISION,
        "device": "cuda",
        "inference_ms": 100,
        **fields,
    }


class SpeechContractTests(unittest.TestCase):
    def test_real_decoding_is_unprimed_and_keeps_legitimate_short_speech(self):
        observed = {}

        class Model:
            def transcribe(self, _audio, **options):
                observed.update(options)
                segment = SimpleNamespace(
                    no_speech_prob=0.01,
                    avg_logprob=-0.1,
                    compression_ratio=1,
                    words=[SimpleNamespace(word="Thank you.", start=0.0, end=0.1)],
                )
                return iter([segment]), None

        engine = object.__new__(SpeechEngine)
        engine.model = Model()
        engine.gpu_name = "Test GPU"
        numeric = SimpleNamespace(
            frombuffer=lambda *_args, **_kwargs: SimpleNamespace(
                astype=lambda _dtype: 1000.0
            ),
            float32=float,
            mean=lambda value: value,
            sqrt=math.sqrt,
        )
        with patch.dict("sys.modules", {"numpy": numeric}):
            result = engine.transcribe(
                {"language": "en", "pcm": base64.b64encode(bytes(3200)).decode()}
            )
        self.assertNotIn("initial_prompt", observed)
        self.assertFalse(observed["condition_on_previous_text"])
        self.assertTrue(observed["vad_filter"])
        self.assertEqual(result["words"][0]["text"], "Thank you.")

    def test_worker_consumes_synthetic_warmup_before_becoming_ready(self):
        observed = {}

        class FakeModel:
            def __init__(self, _path, **options):
                observed["options"] = options

            def transcribe(self, audio, **options):
                observed["samples"] = len(audio)
                observed["decoding"] = options

                def segments():
                    observed["consumed"] = True
                    yield None

                return segments(), None

        with (
            patch.dict(
                "sys.modules",
                {
                    "faster_whisper": SimpleNamespace(WhisperModel=FakeModel),
                    "numpy": SimpleNamespace(
                        zeros=lambda count, dtype: [0] * count, float32=float
                    ),
                },
            ),
            patch("speech_worker.engine.validate_model_files"),
            patch(
                "speech_worker.engine.subprocess.check_output",
                return_value="Test GPU\n",
            ),
        ):
            engine = SpeechEngine()
        self.assertEqual(engine.gpu_name, "Test GPU")
        self.assertEqual(observed["options"]["device"], "cuda")
        self.assertEqual(observed["samples"], 16000)
        self.assertFalse(observed["decoding"]["vad_filter"])
        self.assertTrue(observed["consumed"])

    def test_large_v3_cannot_start_with_missing_or_80_bin_preprocessor(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in (
                "config.json",
                "model.bin",
                "tokenizer.json",
                "vocabulary.json",
            ):
                (root / name).write_text("{}")
            with self.assertRaises(ValueError):
                validate_model_files(root)
            config = root / "preprocessor_config.json"
            config.write_text(json.dumps({"feature_size": 80, "sampling_rate": 16000}))
            with self.assertRaises(ValueError):
                validate_model_files(root)
            config.write_text(json.dumps({"feature_size": 128, "sampling_rate": 16000}))
            validate_model_files(root)

    def test_worker_accepts_only_bounded_english_pcm(self):
        request = {"language": "en", "pcm": base64.b64encode(bytes(32000)).decode()}
        self.assertEqual(len(decode_request(request)), 32000)
        for invalid in (
            {**request, "language": "auto"},
            {**request, "audio": "https://example.com"},
            {**request, "pcm": "!"},
            {**request, "context": "x" * 1001},
            {**request, "context": "prior words"},
            {**request, "pcm": base64.b64encode(bytes(224002)).decode()},
        ):
            with self.assertRaises(ValueError):
                decode_request(invalid)

    def test_cloud_output_model_device_timing_and_shape_are_verified(self):
        self.assertEqual(validate_output(output(), 1)["language"], "en")
        for invalid in (
            output(device="cpu"),
            output(revision="other"),
            output(language="ko"),
            output(words=[{"text": "word", "start": 0, "end": float("nan")}]),
            output(words=[{"text": "word", "start": 0, "end": 5}]),
            output(uncertain="false"),
            output(inference_ms=float("inf")),
        ):
            with self.assertRaises(ValueError):
                validate_output(invalid, 1)


class GPUSpeechTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.environment = patch.dict(
            "os.environ",
            {"RUNPOD_API_KEY": "test-only", "ROOMSHIFT_SPEECH_ENDPOINT_ID": "test"},
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    async def test_request_never_primes_context_or_uses_audio_urls(self):
        seen = []

        def handler(request):
            seen.append(request)
            if request.url.path.endswith("/run"):
                payload = json.loads(request.content)
                self.assertEqual(payload["input"]["language"], "en")
                self.assertEqual(set(payload["input"]), {"pcm", "language"})
                self.assertEqual(payload["policy"]["ttl"], 60000)
                return httpx.Response(200, json={"id": "job-1", "status": "IN_QUEUE"})
            return httpx.Response(
                200, json={"id": "job-1", "status": "COMPLETED", "output": output()}
            )

        speech = RunpodTranscriber(httpx.MockTransport(handler))
        result = await speech.transcribe(bytes(32000), "en")
        self.assertEqual(result["words"][0]["text"], " Hello")
        self.assertEqual(len(seen), 2)
        self.assertFalse(speech.jobs.busy)

    async def test_timeout_cancels_known_job_without_resubmission(self):
        seen = []

        def handler(request):
            seen.append(request.url.path)
            return httpx.Response(200, json={"id": "job-1", "status": "IN_QUEUE"})

        speech = RunpodTranscriber(httpx.MockTransport(handler), timeout=0.01)
        with self.assertRaises(TimeoutError):
            await speech.transcribe(bytes(32000), "en")
        self.assertEqual(sum(path.endswith("/run") for path in seen), 1)
        self.assertTrue(any(path.endswith("/cancel/job-1") for path in seen))
        self.assertFalse(speech.jobs.busy)

    async def test_stop_during_submission_discards_and_cancels_late_job(self):
        submitted = asyncio.Event()
        release = asyncio.Event()
        seen = []

        async def handler(request):
            seen.append(request.url.path)
            if request.url.path.endswith("/run"):
                submitted.set()
                await release.wait()
            return httpx.Response(200, json={"id": "job-1", "status": "IN_QUEUE"})

        speech = RunpodTranscriber(httpx.MockTransport(handler))
        task = asyncio.create_task(speech.transcribe(bytes(32000), "en"))
        await submitted.wait()
        with self.assertRaises(RuntimeError):
            await speech.transcribe(bytes(32000), "en")
        await speech.stop()
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(any(path.endswith("/cancel/job-1") for path in seen))

    async def test_malformed_cloud_output_is_not_accepted(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "id": "job-1",
                    "status": "COMPLETED",
                    "output": output(device="cpu"),
                },
            )

        speech = RunpodTranscriber(httpx.MockTransport(handler))
        with self.assertRaises(ValueError):
            await speech.transcribe(bytes(32000), "en")

    async def test_oversized_submission_response_is_rejected_before_parsing(self):
        def handler(_request):
            return httpx.Response(200, content=b" " * 1_000_001)

        speech = RunpodTranscriber(httpx.MockTransport(handler))
        with self.assertRaisesRegex(ValueError, "size limit"):
            await speech.transcribe(bytes(32000), "en")
        self.assertFalse(speech.jobs.busy)
