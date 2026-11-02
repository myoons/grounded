import asyncio
import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from server.api import create_app
from server.meeting_contracts import MeetingPresentation, MeetingStart
from server.meeting_history import MeetingHistory
from server.meeting_service import MeetingError, MeetingService
from tests.test_meeting import FakeDetector, FakeSpeech


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = TemporaryDirectory()
        self.path = Path(self.directory.name) / "meetings.sqlite"
        self.history = MeetingHistory(self.path)
        self.service = MeetingService(
            FakeSpeech(), FakeDetector(), history=self.history
        )
        self.session = await self.service.start(
            MeetingStart(
                title="Architecture review",
                consent=True,
                audio_consent_version="runpod-audio-v1",
            )
        )
        self.identifier = self.session["id"]

    async def asyncTearDown(self):
        await self.service.close()
        self.history.close()
        self.directory.cleanup()

    async def test_transcript_findings_links_and_view_survive_restart(self):
        segment_id, candidate_id = str(uuid4()), str(uuid4())
        current = self.service.current
        current["segments"] = [
            {"id": segment_id, "source": "system", "text": "A claim.", "final": True}
        ]
        current["candidates"] = [
            {
                "id": candidate_id,
                "state": "candidate",
                "evidence_ids": [segment_id],
                "research_revision": 2,
                "evidence": [{"id": segment_id, "text": "A claim."}],
                "research": {
                    "state": "ready",
                    "verdict": "supported",
                    "summary": "A saved finding.",
                    "sources": [
                        {
                            "id": "S1",
                            "title": "Source",
                            "url": "https://www.sqlite.org/wal.html",
                        }
                    ],
                    "citations": [
                        {
                            "source_id": "S1",
                            "quote": "WAL does not work over a network filesystem.",
                        }
                    ],
                },
            }
        ]
        await self.service.action(self.identifier, "stop")
        await self.service.finalizer
        self.service.update_presentation(
            self.identifier,
            MeetingPresentation(
                expanded=[candidate_id], transcript_scroll=120, cards_scroll=90
            ),
        )
        await self.service.clear(self.identifier)
        self.history.close()
        self.history = MeetingHistory(self.path)
        self.service.history = self.history
        saved = self.service.view(self.identifier)
        self.assertTrue(saved["archived"])
        self.assertEqual(saved["title"], "Architecture review")
        self.assertEqual(saved["segments"], current["segments"])
        self.assertEqual(saved["candidates"], current["candidates"])
        self.assertEqual(saved["presentation"]["expanded"], [candidate_id])
        self.assertEqual(saved["presentation"]["transcript_scroll"], 120)
        self.assertEqual(
            self.service.source(self.identifier, candidate_id, "S1", 2)["title"],
            "Source",
        )
        with self.assertRaises(MeetingError):
            self.service.source(self.identifier, candidate_id, "S1", 1)
        with self.assertRaises(MeetingError):
            await self.service.action(self.identifier, "resume")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("pcm", json.dumps(saved))
        self.assertNotIn("receipts", saved)

    async def test_interrupted_snapshot_never_resumes_cloud_or_capture(self):
        self.service._persist(required=True)
        saved = self.history.load(self.identifier)
        self.assertEqual(saved["state"], "stopped")
        self.assertTrue(saved["interrupted"])
        self.assertEqual(saved["segments"], [])

    async def test_failed_save_keeps_memory_and_blocks_detach(self):
        await self.service.action(self.identifier, "stop")
        await self.service.finalizer
        with patch.object(
            self.history, "save", side_effect=sqlite3.OperationalError("disk full")
        ):
            with self.assertRaises(MeetingError):
                await self.service.clear(self.identifier)
            self.assertEqual(self.service.current["id"], self.identifier)
            self.assertTrue(self.service.view(self.identifier)["storage_error"])
        await self.service.clear(self.identifier)
        self.assertIsNone(self.service.current)
        self.assertIsNotNone(self.history.load(self.identifier))

    async def test_snapshot_transaction_failure_preserves_last_good_record(self):
        before = self.history.load(self.identifier)
        self.history.database.execute(
            "CREATE TRIGGER reject_update BEFORE UPDATE ON meetings BEGIN SELECT RAISE(ABORT, 'fixture failure'); END"
        )
        self.service.current["title"] = "Unsaved title"
        self.service._persist()
        self.assertEqual(self.history.load(self.identifier)["title"], before["title"])
        self.history.database.execute("DROP TRIGGER reject_update")
        self.service._persist(required=True)
        self.assertEqual(self.history.load(self.identifier)["title"], "Unsaved title")

    async def test_automatic_transcription_checkpoint_and_pagination(self):
        self.service.speech.results = [
            [{"text": "Saved speech", "start": 0.1, "end": 0.9}]
        ]
        self.service.accept(
            self.identifier, 1, "system", 0, 0, 1000, 900, 100, bytes(32000)
        )
        for _attempt in range(100):
            if self.history.load(self.identifier)["segments"]:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(
            self.history.load(self.identifier)["segments"][0]["text"], "Saved speech"
        )
        snapshot = self.service._snapshot(self.service.current)
        for index in range(51):
            self.history.save(
                {**snapshot, "id": str(uuid4()), "title": f"Meeting {index}"}
            )
        first = self.history.list()
        second = self.history.list(first["next_offset"])
        self.assertEqual(len(first["meetings"]), 50)
        self.assertEqual(len(second["meetings"]), 2)
        self.assertIsNone(second["next_offset"])

    async def test_unknown_presentation_card_is_rejected(self):
        with self.assertRaises(MeetingError):
            self.service.update_presentation(
                self.identifier, MeetingPresentation(expanded=["unknown"])
            )


class HistoryAPIRestartTests(unittest.TestCase):
    def test_restart_lists_and_reopens_saved_meeting_without_capture(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            options = {
                "base_url": "http://localhost",
                "headers": {"X-Roomshift-Client": "1"},
            }
            first = create_app(root)
            with TestClient(first, **options) as client:
                first.state.meetings.speech = FakeSpeech()
                created = client.post(
                    "/api/meetings",
                    json={
                        "title": "Persistent meeting",
                        "consent": True,
                        "audio_consent_version": "runpod-audio-v1",
                        "language": "en",
                    },
                ).json()
                identifier = created["id"]
                client.post(
                    f"/api/meetings/{identifier}/action", json={"action": "stop"}
                ).raise_for_status()
                client.delete(f"/api/meetings/{identifier}").raise_for_status()
            with TestClient(create_app(root), **options) as client:
                self.assertIsNone(
                    client.get("/api/meetings/capabilities").json()["existing_session"]
                )
                listing = client.get("/api/meetings").json()
                self.assertEqual(listing["meetings"][0]["id"], identifier)
                saved = client.get(f"/api/meetings/{identifier}").json()
                self.assertEqual(saved["title"], "Persistent meeting")
                self.assertTrue(saved["archived"])
                self.assertEqual(saved["state"], "stopped")
                self.assertEqual(
                    client.post(
                        f"/api/meetings/{identifier}/action", json={"action": "resume"}
                    ).status_code,
                    404,
                )
