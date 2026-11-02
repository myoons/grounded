import json
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from server.meeting_detector import (
    MAX_CONTEXT_CHARS,
    MeetingDetector,
    build_context,
    detection_schema,
    encoded_size,
)
from server.meeting_model import MODEL
from tests.test_meeting import change


class ContextTests(unittest.TestCase):
    def test_complete_utterances_and_older_context_are_preserved(self):
        segments = [
            {"id": str(uuid4()), "source": "system", "text": "x" * 400}
            for _ in range(12)
        ]
        result = build_context(segments, [])
        self.assertEqual(result["transcript"], segments)
        self.assertEqual(result["omitted_segments"], 0)

    def test_context_budget_drops_whole_old_turns_not_sentence_tails(self):
        segments = [
            {"id": str(uuid4()), "source": "system", "text": "x" * 400}
            for _ in range(100)
        ]
        result = build_context(segments, [])
        self.assertLessEqual(encoded_size(result), MAX_CONTEXT_CHARS)
        self.assertEqual(result["transcript"][-1], segments[-1])
        self.assertTrue(all(len(item["text"]) == 400 for item in result["transcript"]))
        self.assertGreater(result["omitted_segments"], 0)

    def test_provider_schema_is_inlined_and_reference_constrained(self):
        segment = {"id": str(uuid4()), "source": "system", "text": "A claim."}
        schema = detection_schema([segment], [])
        serialized = json.dumps(schema)
        self.assertNotIn('"$ref"', serialized)
        self.assertNotIn('"$defs"', serialized)
        properties = schema["properties"]["changes"]["items"]["properties"]
        self.assertEqual(properties["evidence_ids"]["items"]["enum"], [segment["id"]])
        self.assertEqual(properties["candidate_id"], {"type": "null"})
        self.assertEqual(len(properties["category"]["enum"]), 6)
        self.assertIn("category", schema["properties"]["changes"]["items"]["required"])
        self.assertIn("statement", schema["properties"]["changes"]["items"]["required"])


class DetectorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict("os.environ", {"RUNPOD_API_KEY": "test-only"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.segment = {
            "id": str(uuid4()),
            "source": "system",
            "text": "A technical claim.",
        }

    def completion(self, content, finish="stop", model=MODEL):
        return {
            "id": "job",
            "status": "COMPLETED",
            "output": [
                {
                    "model": model,
                    "choices": [
                        {"finish_reason": finish, "message": {"content": content}}
                    ],
                    "usage": {"total_tokens": 100},
                }
            ],
        }

    async def test_k3_passthrough_preserves_strict_schema_and_reasoning_effort(self):
        def handler(request):
            self.assertEqual(request.url.path, "/v2/moonshot-kimi/run")
            body = json.loads(request.content)
            sent = body["input"]["openai_input"]
            self.assertEqual(sent["model"], "kimi-k3")
            self.assertEqual(sent["reasoning_effort"], "low")
            self.assertNotIn("thinking", sent)
            self.assertNotIn("temperature", sent)
            self.assertTrue(sent["response_format"]["json_schema"]["strict"])
            self.assertEqual(body["policy"]["executionTimeout"], 45000)
            self.assertEqual(body["policy"]["ttl"], 90000)
            return httpx.Response(200, json=self.completion('{"changes":[]}'))

        result, usage = await MeetingDetector(httpx.MockTransport(handler)).detect(
            [self.segment], []
        )
        self.assertEqual(result.changes, [])
        self.assertEqual(usage["context_segments"], 1)

    async def test_invalid_unknown_and_truncated_outputs_never_become_cards(self):
        wrong_id = change(str(uuid4()))
        for payload in (
            self.completion("not JSON"),
            self.completion(json.dumps({"changes": [wrong_id]})),
            self.completion('{"changes":[]}', finish="length"),
            self.completion('{"changes":[]}', model="different-model"),
            self.completion('{"changes":[],"unexpected":true}'),
        ):

            def handler(_request, payload=payload):
                return httpx.Response(200, json=payload)

            with self.assertRaises(ValueError):
                await MeetingDetector(httpx.MockTransport(handler)).detect(
                    [self.segment], []
                )

    async def test_provider_failure_is_not_silently_retried(self):
        calls = []

        def handler(request):
            calls.append(request.url.path)
            return httpx.Response(
                200,
                json={
                    "id": "job",
                    "status": "FAILED",
                    "error": "private provider detail",
                },
            )

        with self.assertRaisesRegex(RuntimeError, "Runpod job failed"):
            await MeetingDetector(httpx.MockTransport(handler)).detect(
                [self.segment], []
            )
        self.assertEqual(sum(path.endswith("/run") for path in calls), 1)
