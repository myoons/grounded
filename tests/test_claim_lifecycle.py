import asyncio
import hashlib
import json
import unittest
from pathlib import Path
from uuid import uuid4

from scripts.evaluate_claim_gold import score
from scripts.evaluate_claim_lifecycle import DATASET, fidelity_check
from server.meeting_contracts import DetectionResult, MeetingStart
from server.meeting_service import MeetingService
from tests.test_meeting import FakeDetector, FakeSpeech, change


class ControlledResearcher:
    def __init__(self):
        self.calls = []

    def ready(self):
        return True

    async def research(self, candidate, progress):
        release = asyncio.Event()
        self.calls.append((candidate, release))
        progress("searching")
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        return {
            "state": "ready",
            "summary": f"revision-{candidate['research_revision']}",
        }


class LifecycleIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.researcher = ControlledResearcher()
        self.service = MeetingService(FakeSpeech(), FakeDetector(), self.researcher)
        await self.service.start(
            MeetingStart(
                consent=True,
                audio_consent_version="runpod-audio-v1",
                cloud_detection=True,
                detection_consent_version="runpod-kimi-k3-v1",
                research_enabled=True,
                research_consent_version="firecrawl-kimi-v1",
            )
        )
        self.segments = []

    async def asyncTearDown(self):
        for _, release in self.researcher.calls:
            release.set()
        await self.service.close()

    async def until(self, predicate):
        async with asyncio.timeout(2):
            while not predicate():
                await asyncio.sleep(0.005)

    def apply(self, action="create", candidate_id=None, **fields):
        segment = {
            "id": str(uuid4()),
            "source": "system",
            "text": "Synthetic lifecycle evidence.",
            "start_ms": 0,
            "end_ms": 1000,
        }
        self.segments.append(segment)
        session = self.service.current
        self.service.apply_changes(
            session,
            DetectionResult(
                changes=[
                    change(
                        segment["id"],
                        action=action,
                        candidate_id=candidate_id,
                        question="Does SQLite WAL require processes on the same host?",
                        topic_key="sqlite-wal-host",
                        category="technical_behavior",
                        statement="SQLite WAL works across a network filesystem.",
                        conditions=[],
                        **fields,
                    )
                ]
            ),
            self.segments,
            [candidate.copy() for candidate in session["candidates"]],
        )
        return session["candidates"]

    async def test_same_id_link_scope_update_stale_completion_and_retraction(self):
        candidate = self.apply()[0]
        identifier = candidate["id"]
        revision = candidate["research_revision"]
        await self.until(lambda: len(self.researcher.calls) == 1)
        self.apply("link", identifier)
        await asyncio.sleep(0.02)
        self.assertEqual(len(self.researcher.calls), 1)
        self.assertEqual(candidate["research_revision"], revision)
        self.assertEqual(len(candidate["evidence_ids"]), 2)

        updated = change(
            self.segments[-1]["id"],
            action="update",
            candidate_id=identifier,
            question=candidate["question"],
            topic_key=candidate["topic_key"],
            category="technical_behavior",
            statement="SQLite WAL requires all processes on the same host.",
            conditions=["All processes on the same host"],
        )
        self.service.apply_changes(
            self.service.current,
            DetectionResult(changes=[updated]),
            self.segments,
            [candidate.copy()],
        )
        self.assertEqual(candidate["id"], identifier)
        self.assertEqual(candidate["research_revision"], revision + 1)
        await self.until(lambda: len(self.researcher.calls) == 2)

        self.researcher.calls[0][1].set()
        await asyncio.sleep(0.02)
        self.assertNotEqual(
            candidate["research"].get("summary"), f"revision-{revision}"
        )
        self.researcher.calls[1][1].set()
        await self.until(lambda: candidate["research"]["state"] == "ready")
        self.assertEqual(candidate["research"]["summary"], f"revision-{revision + 1}")

        self.service.apply_changes(
            self.service.current,
            DetectionResult(changes=[updated]),
            self.segments,
            [candidate.copy()],
        )
        self.assertEqual(candidate["research_revision"], revision + 1)
        self.assertEqual(len(self.researcher.calls), 2)
        updated["action"] = "retract"
        self.service.apply_changes(
            self.service.current,
            DetectionResult(changes=[updated]),
            self.segments,
            [candidate.copy()],
        )
        self.assertEqual(candidate["id"], identifier)
        self.assertEqual(candidate["state"], "retracted")
        self.assertEqual(candidate["research_revision"], revision + 2)

    async def test_plan_correction_researches_new_question_and_rejects_old_answer(self):
        segment = {
            "id": str(uuid4()),
            "source": "system",
            "text": "The Free plan supports export.",
            "start_ms": 0,
            "end_ms": 1000,
        }
        corrected = {
            **segment,
            "id": str(uuid4()),
            "text": "Correction: I meant the Enterprise plan.",
            "start_ms": 1000,
            "end_ms": 2000,
        }
        session = self.service.current
        original = change(
            segment["id"],
            question="Does the Free plan support export?",
            topic_key="export-plan",
            category="capability",
            statement="The Free plan supports export.",
            conditions=["Free plan"],
        )
        self.service.apply_changes(
            session, DetectionResult(changes=[original]), [segment], []
        )
        candidate = session["candidates"][0]
        identifier = candidate["id"]
        revision = candidate["research_revision"]
        await self.until(lambda: len(self.researcher.calls) == 1)
        update = change(
            corrected["id"],
            action="update",
            candidate_id=identifier,
            question="Does the Enterprise plan support export?",
            topic_key="new-topic-suggestion",
            category="capability",
            statement="The Enterprise plan supports export.",
            conditions=["Enterprise plan"],
        )
        self.service.apply_changes(
            session,
            DetectionResult(changes=[update]),
            [segment, corrected],
            [candidate.copy()],
        )
        await self.until(lambda: len(self.researcher.calls) == 2)
        self.assertEqual(candidate["id"], identifier)
        self.assertEqual(candidate["topic_key"], "export-plan")
        self.assertEqual(candidate["research_revision"], revision + 1)
        self.assertEqual(self.researcher.calls[0][0]["question"], original["question"])
        self.assertEqual(self.researcher.calls[1][0]["question"], update["question"])
        self.researcher.calls[1][1].set()
        await self.until(lambda: candidate["research"]["state"] == "ready")
        self.researcher.calls[0][1].set()
        await asyncio.sleep(0.02)
        self.assertEqual(candidate["research"]["summary"], f"revision-{revision + 1}")
        repeated = {**update, "action": "link", "question": "A different wording?"}
        self.service.apply_changes(
            session,
            DetectionResult(changes=[repeated]),
            [segment, corrected],
            [candidate.copy()],
        )
        self.assertEqual(candidate["question"], update["question"])
        self.assertEqual(candidate["research_revision"], revision + 1)
        self.assertEqual(len(self.researcher.calls), 2)


class FidelityProxyTests(unittest.TestCase):
    def test_preserved_live_results_reproduce_rubric_and_hash(self):
        cases = {case["id"]: case for case in json.loads(DATASET.read_text())["cases"]}
        report = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "docs/evaluations/claim-lifecycle/results.json"
            ).read_text()
        )
        self.assertEqual(
            report["dataset_sha256"], hashlib.sha256(DATASET.read_bytes()).hexdigest()
        )
        for row in report["results"]:
            case = cases[row["case"]]
            structural = score(case, row["changes"])["passed"]
            fidelity = fidelity_check(case, row["changes"])
            self.assertEqual(row["structural_passed"], structural)
            self.assertEqual(row["fidelity_proxy"], fidelity)
            self.assertEqual(row["passed"], structural and fidelity is not False)
        self.assertEqual(report["summary"]["total"], len(report["results"]))
        self.assertEqual(
            report["summary"]["passed"], sum(row["passed"] for row in report["results"])
        )

    def test_wrong_old_scope_can_pass_structure_but_fails_proxy(self):
        case = next(
            item
            for item in json.loads(DATASET.read_text())["cases"]
            if item["id"] == "scope_update"
        )
        self.assertFalse(
            fidelity_check(
                case,
                [
                    {
                        "statement": "SQLite WAL works across a network filesystem.",
                        "conditions": ["All processes on the same host"],
                    }
                ],
            )
        )
        self.assertTrue(
            fidelity_check(
                case,
                [
                    {
                        "statement": "SQLite WAL requires processes on the same host.",
                        "conditions": [],
                    }
                ],
            )
        )
