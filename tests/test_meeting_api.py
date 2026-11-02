import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from fastapi.testclient import TestClient

from server.api import create_app
from tests.test_meeting import FakeSpeech


class MeetingAPITests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.app = create_app(Path(self.temporary.name))
        self.client = TestClient(
            self.app, base_url="http://localhost", headers={"X-Roomshift-Client": "1"}
        )
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def start(self):
        self.app.state.meetings.speech = FakeSpeech()
        response = self.client.post(
            "/api/meetings",
            json={
                "consent": True,
                "audio_consent_version": "runpod-audio-v1",
                "cloud_detection": False,
                "language": "en",
            },
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["id"]

    def test_meeting_only_routes_and_request_limit(self):
        self.assertEqual(
            self.client.get("/api/health").json()["service"], "roomshift-research"
        )
        paths = self.client.get("/openapi.json").json()["paths"]
        self.assertTrue(
            all(
                path == "/api/health" or path.startswith("/api/meetings")
                for path in paths
            )
        )
        self.assertEqual(self.client.get("/api/workspaces/old").status_code, 404)
        self.assertEqual(
            self.client.post(
                "/api/meetings", content=b"x" * (256 * 1024 + 1)
            ).status_code,
            413,
        )

    def test_english_only_contract(self):
        from server.meeting_contracts import MeetingStart

        self.assertEqual(
            MeetingStart(
                consent=True, audio_consent_version="runpod-audio-v1"
            ).language,
            "en",
        )
        for language in ("auto", "ko", "fr"):
            response = self.client.post(
                "/api/meetings",
                json={
                    "consent": True,
                    "audio_consent_version": "runpod-audio-v1",
                    "language": language,
                },
            )
            self.assertEqual(response.status_code, 422)

    def test_retired_feedback_routes_are_not_exposed(self):
        paths = self.client.get("/openapi.json").json()["paths"]
        self.assertFalse(any("missed" in path for path in paths))
        identifier = self.start()
        response = self.client.patch(
            f"/api/meetings/{identifier}/candidates/{uuid4()}",
            json={"action": "evaluate", "evaluation": "useful"},
        )
        self.assertEqual(response.status_code, 422)

    def test_meeting_reads_require_the_same_local_client_boundary(self):
        identifier = self.start()
        for path in ("/api/meetings/capabilities", f"/api/meetings/{identifier}"):
            self.assertEqual(
                self.client.get(
                    path, headers={"Origin": "https://untrusted.example"}
                ).status_code,
                403,
            )
            self.assertEqual(
                self.client.get(path, headers={"X-Roomshift-Client": "0"}).status_code,
                403,
            )
        self.assertEqual(self.client.get("/api/health").status_code, 200)

    def test_old_clients_cannot_send_audio_to_new_destination(self):
        self.assertEqual(
            self.client.post(
                "/api/meetings", json={"consent": True, "language": "en"}
            ).status_code,
            422,
        )

    def test_old_detection_consent_does_not_authorize_the_public_model(self):
        response = self.client.post(
            "/api/meetings",
            json={
                "consent": True,
                "audio_consent_version": "runpod-audio-v1",
                "cloud_detection": True,
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_detection_consent_does_not_implicitly_authorize_search(self):
        response = self.client.post(
            "/api/meetings",
            json={
                "consent": True,
                "audio_consent_version": "runpod-audio-v1",
                "cloud_detection": True,
                "detection_consent_version": "runpod-kimi-k3-v1",
                "research_enabled": True,
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_consent_origin_and_no_automatic_search(self):
        self.assertEqual(
            self.client.post("/api/meetings", json={"consent": False}).status_code, 422
        )
        self.assertEqual(
            self.client.post(
                "/api/meetings",
                json={"consent": True},
                headers={"Origin": "https://example.com"},
            ).status_code,
            403,
        )
        identifier = self.start()
        state = self.client.get(f"/api/meetings/{identifier}").json()
        self.assertFalse(state["search_enabled"])
        self.assertFalse(state["cloud_detection"])
        self.assertEqual(state["segments"], [])
        self.assertEqual(self.client.get(f"/api/meetings/{uuid4()}").status_code, 404)

    def test_binary_metadata_and_old_epoch_are_rejected(self):
        identifier = self.start()
        path = f"/api/meetings/{identifier}/audio"
        headers = {
            "Content-Type": "application/octet-stream",
            "X-Meeting-Epoch": "1",
            "X-Audio-Source": "system",
            "X-Audio-Sequence": "0",
            "X-Audio-Start": "0",
            "X-Audio-End": "1000",
            "X-Audio-Speech-End": "900",
            "X-Audio-Age": "100",
        }
        self.assertEqual(
            self.client.post(
                path, content=bytes(32000), headers={**headers, "X-Audio-End": "nan"}
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.post(
                path,
                content=bytes(32000),
                headers={**headers, "X-Audio-Source": "named-participant"},
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.post(path, content=bytes(32000), headers=headers).status_code,
            200,
        )
        self.client.post(f"/api/meetings/{identifier}/action", json={"action": "pause"})
        self.client.post(
            f"/api/meetings/{identifier}/action", json={"action": "resume"}
        )
        self.assertEqual(
            self.client.post(path, content=bytes(32000), headers=headers).status_code,
            409,
        )
        self.client.delete(f"/api/meetings/{identifier}")
        self.assertEqual(
            self.client.get(f"/api/meetings/{identifier}").status_code, 200
        )
        self.assertTrue(
            self.client.get(f"/api/meetings/{identifier}").json()["archived"]
        )
