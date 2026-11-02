import asyncio
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from server.meeting_contracts import CandidateReview, DetectionResult, MeetingStart
from server.meeting_research import (
    MeetingResearcher,
    ResearchFinding,
    evidence_span,
    ground_finding,
    quote_options,
)
from server.meeting_search import (
    FirecrawlSearch,
    SourceDocument,
    public_url,
    select_passages,
)
from server.meeting_service import MeetingError, MeetingService
from tests.test_meeting import FakeDetector, FakeSpeech, change

TEXT = "All processes using a database must be on the same host computer. WAL does not work over a network filesystem."


def source():
    return SourceDocument(
        "S1",
        "https://www.sqlite.org/wal.html",
        "Write-Ahead Logging",
        TEXT,
        "2026-11-01T00:00:00Z",
        "test-hash",
    )


def finding():
    return {
        "verdict": "contradicted",
        "summary": "WAL does not support this network-filesystem setup.",
        "recommendation": "Keep the database on one host or choose another architecture.",
        "caveats": ["This finding concerns WAL mode."],
        "citations": [
            {"source_id": "S1", "quote": "WAL does not work over a network filesystem."}
        ],
    }


class EvidenceTests(unittest.TestCase):
    def test_measured_past_tense_predicates_are_usable_evidence(self):
        for text in [
            "On the same machine, Atlas completed the benchmark in two seconds.",
            "Beacon required four seconds.",
            "The fixed workload took 12 milliseconds.",
            "Atlas processed 100 records in two seconds.",
        ]:
            with self.subTest(text=text):
                self.assertTrue(evidence_span(text))
                self.assertIn(text, quote_options(text, "What was measured?"))

    def test_measured_headers_and_incomplete_fragments_are_not_evidence(self):
        for text in [
            "Completed benchmark results for two seconds",
            "Required benchmark time in four seconds",
            "Benchmark: completed in two seconds",
            "Did Atlas complete the benchmark in two seconds?",
            "Atlas completed the benchmark in two seconds and",
        ]:
            with self.subTest(text=text):
                self.assertFalse(evidence_span(text))

    def test_long_license_paragraph_offers_the_material_commercial_clause(self):
        text = "Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files, to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:"
        choices = quote_options(
            text, "Does the MIT License prohibit commercial use of licensed software?"
        )
        self.assertIn("and/or sell copies of the Software", choices)

    def test_document_headings_cannot_be_selected_or_accepted_as_evidence(self):
        text = (
            "Write-Ahead Logging\n\nTable of Contents\n\nHow to use SQLite\n\n" + TEXT
        )
        options = quote_options(
            text, "Does SQLite Write-Ahead Logging support network filesystems?"
        )
        self.assertNotIn("Write-Ahead Logging", options)
        self.assertNotIn("How to use SQLite", options)
        self.assertIn("WAL does not work over a network filesystem.", options)
        invalid = finding()
        invalid["citations"][0]["quote"] = "Write-Ahead Logging"
        document = SourceDocument(
            "S1",
            "https://www.sqlite.org/wal.html",
            "Write-Ahead Logging",
            text,
            "2026-11-02",
            "hash",
        )
        with self.assertRaisesRegex(ValueError, "heading"):
            ground_finding(ResearchFinding.model_validate(invalid), [document])

    def test_hard_wrapped_sentences_preserve_the_decisive_word(self):
        text = "Versioning-enabled buckets can help you recover objects from accidental deletion or\noverwrite."
        choices = quote_options(text, "Can S3 versioning recover overwritten objects?")
        self.assertIn(text, choices)
        self.assertNotIn(text.split("\n")[0], choices)

    def test_quote_choices_are_bounded_exact_spans_even_for_long_table_rows(self):
        text = "| 3.8 | " + "Ended five years ago " * 30 + "\n" + TEXT
        choices = quote_options(text, "Does WAL work over a network filesystem?")
        self.assertTrue(choices)
        self.assertTrue(any("WAL does not work" in quote for quote in choices))
        for quote in choices:
            self.assertIn(quote, text)
            self.assertLessEqual(len(quote.split()), 25)
            self.assertLessEqual(len(quote), 240)

    def test_verdicts_require_exact_retrieved_quotes(self):
        valid = ResearchFinding.model_validate(finding())
        self.assertEqual(ground_finding(valid, [source()]).verdict, "contradicted")
        invalid = finding()
        invalid["citations"] = [
            {"source_id": "S1", "quote": "WAL works on every network filesystem."}
        ]
        with self.assertRaises(ValueError):
            ground_finding(ResearchFinding.model_validate(invalid), [source()])
        invalid["citations"] = []
        with self.assertRaises(ValueError):
            ResearchFinding.model_validate(invalid)

    def test_source_urls_reject_credentials_and_local_destinations(self):
        self.assertEqual(
            public_url("https://www.sqlite.org/wal.html#section"),
            "https://www.sqlite.org/wal.html",
        )
        for url in (
            "file:///etc/passwd",
            "javascript:alert(1)",
            "http://localhost/a",
            "http://127.0.0.1/a",
            "http://127.1/a",
            "http://0177.0.0.1/a",
            "https://1.1.1.1/",
            "https://%zz.example.com/",
            "https://10.0.0.1/",
            "http://[::1]/",
            "https://user:password@example.com/",
            "https://example.com:8877/",
        ):
            with self.assertRaises(ValueError):
                public_url(url)

    def test_relevant_passages_are_found_beyond_the_start_of_a_document(self):
        text = (
            ("Navigation and general background. " * 500)
            + TEXT
            + ("Other background. " * 500)
        )
        selected = select_passages(text, "SQLite WAL network filesystem")
        self.assertIn("WAL does not work", selected)
        self.assertLessEqual(len(selected), 6000)

    def test_document_introduction_is_preserved_even_when_later_terms_repeat(self):
        text = TEXT + (
            " Database file network filesystem SQLite WAL checkpoint. " * 800
        )
        self.assertTrue(
            select_passages(text, "SQLite WAL database file checkpoint").startswith(
                TEXT
            )
        )

    def test_discussion_only_citations_do_not_become_an_authoritative_verdict(self):
        discussion = SourceDocument(
            "S1",
            "https://news.ycombinator.com/item?id=1",
            "Discussion",
            TEXT,
            "2026-11-01",
            "hash",
        )
        result = ground_finding(ResearchFinding.model_validate(finding()), [discussion])
        self.assertEqual(result.verdict, "insufficient")


class SearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_scrapes_sources_but_does_not_treat_snippets_as_evidence(self):
        def handler(request):
            payload = json.loads(request.content)
            self.assertEqual(request.url.host, "api.firecrawl.dev")
            self.assertEqual(payload["limit"], 3)
            self.assertFalse(payload["scrapeOptions"]["skipTlsVerification"])
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "web": [
                            {
                                "url": "https://www.sqlite.org/wal.html",
                                "title": "WAL",
                                "markdown": TEXT,
                            },
                            {"url": "https://example.com/snippet", "description": TEXT},
                            {"url": "http://127.0.0.1/", "markdown": TEXT},
                        ]
                    },
                },
            )

        with patch.dict("os.environ", {"FIRECRAWL_API_KEY": "test-only"}):
            search = FirecrawlSearch(httpx.MockTransport(handler))
        _, documents = await search.search("Does WAL work over network filesystems?")
        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0].identifier, "S1")

    async def test_provider_errors_and_potential_secrets_fail_without_retries(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(429, json={"error": "private detail"})

        with patch.dict("os.environ", {"FIRECRAWL_API_KEY": "test-only"}):
            search = FirecrawlSearch(httpx.MockTransport(handler))
        with self.assertRaises(ValueError):
            await search.search("Does this fc-" + "a" * 32 + " key work?")
        self.assertEqual(calls, [])
        with self.assertRaisesRegex(RuntimeError, "429"):
            await search.search("Does WAL work over network filesystems?")
        self.assertEqual(len(calls), 1)


class ResearcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_headings_only_documents_do_not_generate_an_answer(self):
        class Search:
            async def search(self, question):
                return question, [
                    SourceDocument(
                        "S1",
                        "https://www.sqlite.org/wal.html",
                        "WAL",
                        "Write-Ahead Logging\n\nTable of Contents\n\nHow to use SQLite",
                        "2026-11-02",
                        "hash",
                    )
                ]

        class Model:
            async def complete(self, *args, **kwargs):
                raise AssertionError("Headings must not invoke synthesis.")

        result = await MeetingResearcher(Search(), Model()).research(
            {"question": "Does SQLite support this?"}
        )
        self.assertEqual(result["verdict"], "insufficient")
        self.assertEqual(result["citations"], [])
        self.assertEqual(result["timings"]["synthesis_ms"], 0)

    async def test_unknown_quote_identifier_is_rejected(self):
        class Search:
            async def search(self, question):
                return question, [source()]

        class Model:
            async def complete(self, *args, **kwargs):
                result = finding()
                result["citations"] = {"S1": "S2-Q1"}
                return json.dumps(result), {}

        with self.assertRaisesRegex(ValueError, "Unknown evidence quote"):
            await MeetingResearcher(Search(), Model()).research(
                {"question": "Does WAL support this?"}
            )

    async def test_synthesis_is_grounded_and_full_documents_are_not_exported(self):
        class Search:
            async def search(self, question):
                return question, [source()]

        class Model:
            async def complete(self, _prompt, payload, schema, _name, max_tokens):
                self.payload = payload
                self.schema = schema
                result = finding()
                result["citations"] = {
                    "S1": next(
                        key
                        for key, text in payload["sources"][0]["quote_options"].items()
                        if text == result["citations"][0]["quote"]
                    )
                }
                return json.dumps(result), {"total_tokens": 100}

        model = Model()
        result = await MeetingResearcher(Search(), model).research(
            {"question": "Does WAL work over network filesystems?"}
        )
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["citations"][0]["source_id"], "S1")
        self.assertNotIn("text", result["sources"][0])
        self.assertIn(TEXT, model.payload["sources"][0]["excerpts"])
        self.assertTrue(model.payload["sources"][0]["quote_options"])
        self.assertEqual(
            set(result["timings"]), {"search_ms", "synthesis_ms", "validation_ms"}
        )
        self.assertFalse(
            model.schema["properties"]["citations"]["additionalProperties"]
        )

    async def test_no_readable_sources_never_becomes_a_false_verdict(self):
        class Search:
            async def search(self, question):
                return question, []

        class Model:
            async def complete(self, *args, **kwargs):
                raise AssertionError("No evidence must not invoke synthesis.")

        result = await MeetingResearcher(Search(), Model()).research(
            {"question": "Can the service do this?"}
        )
        self.assertEqual(result["verdict"], "insufficient")
        self.assertEqual(result["citations"], [])
        self.assertEqual(result["timings"]["synthesis_ms"], 0)

    async def test_qualitative_comparison_cannot_be_published_as_measured_refutation(
        self,
    ):
        class Search:
            async def search(self, question):
                return question, [source()]

        class Model:
            async def complete(self, _prompt, payload, *args, **kwargs):
                result = finding()
                result["citations"] = {
                    "S1": next(
                        key
                        for key, text in payload["sources"][0]["quote_options"].items()
                        if text == result["citations"][0]["quote"]
                    )
                }
                result["comparison_basis"] = "qualitative_only"
                return json.dumps(result), {}

        result = await MeetingResearcher(Search(), Model()).research(
            {
                "question": "Is A always twice as fast as B?",
                "category": "performance_comparison",
            }
        )
        self.assertEqual(result["verdict"], "insufficient")
        self.assertTrue(result["summary"].startswith("Not established."))


class FakeResearcher:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = None
        self.calls = 0
        self.fail = False
        self.ignore_cancellation = False

    def ready(self):
        return True

    async def research(self, _candidate, progress):
        self.calls += 1
        progress("searching")
        self.started.set()
        if self.release:
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                if not self.ignore_cancellation:
                    raise
        if self.fail:
            raise RuntimeError("Controlled search failure")
        return {
            "state": "ready",
            **finding(),
            "sources": [source().metadata()],
            "elapsed_ms": 10,
        }


class ResearchLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.researcher = FakeResearcher()
        self.service = MeetingService(FakeSpeech(), FakeDetector(), self.researcher)
        session = await self.service.start(
            MeetingStart(
                consent=True,
                audio_consent_version="runpod-audio-v1",
                cloud_detection=True,
                detection_consent_version="runpod-kimi-k3-v1",
                research_enabled=True,
                research_consent_version="firecrawl-kimi-v1",
            )
        )
        self.identifier = session["id"]
        self.evidence = {
            "id": str(uuid4()),
            "source": "system",
            "text": "A technical claim.",
            "start_ms": 0,
            "end_ms": 1000,
        }

    async def asyncTearDown(self):
        await self.service.close()

    def add(self, **fields):
        current = self.service.current
        self.service.apply_changes(
            current,
            DetectionResult(changes=[change(self.evidence["id"], **fields)]),
            [self.evidence],
            list(current["candidates"]),
        )
        return current["candidates"][0]

    async def until(self, predicate):
        for _attempt in range(100):
            if predicate():
                return
            await asyncio.sleep(0.01)
        self.fail("Research did not reach the expected state.")

    async def test_results_publish_once_and_repeats_do_not_research_again(self):
        candidate = self.add()
        await self.until(lambda: candidate["research"]["state"] == "ready")
        self.add(action="update", candidate_id=candidate["id"])
        original = candidate["question"]
        self.add(
            action="update",
            candidate_id=candidate["id"],
            question="Can the same capability work in this environment?",
        )
        await asyncio.sleep(0.02)
        self.assertEqual(self.researcher.calls, 1)
        self.assertEqual(candidate["question"], original)
        self.assertEqual(
            self.service.source(
                self.identifier, candidate["id"], "S1", candidate["research_revision"]
            )["url"],
            source().url,
        )

    async def test_withdrawal_discards_even_a_cancellation_resistant_result(self):
        self.researcher.release = asyncio.Event()
        self.researcher.ignore_cancellation = True
        candidate = self.add()
        await self.researcher.started.wait()
        self.add(action="retract", candidate_id=candidate["id"])
        await asyncio.sleep(0.03)
        self.assertEqual(candidate["state"], "retracted")
        self.assertEqual(candidate["research"]["state"], "not_requested")
        self.assertNotIn("summary", candidate["research"])

    async def test_pause_cancels_work_and_resume_explicitly_requeues_it(self):
        self.researcher.release = asyncio.Event()
        candidate = self.add()
        await self.researcher.started.wait()
        await self.service.action(self.identifier, "pause")
        self.assertEqual(candidate["research"]["state"], "interrupted")
        self.assertTrue(self.service.research_queue.empty())
        self.researcher.release = None
        await self.service.action(self.identifier, "resume")
        await self.until(lambda: candidate["research"]["state"] == "ready")
        self.assertEqual(self.researcher.calls, 2)

    async def test_failure_keeps_capture_running_and_retry_is_explicit(self):
        self.researcher.fail = True
        candidate = self.add()
        await self.until(lambda: candidate["research"]["state"] == "error")
        await asyncio.sleep(0.03)
        self.assertEqual(self.researcher.calls, 1)
        self.assertEqual(self.service.current["state"], "capturing")
        self.researcher.fail = False
        self.service.review(
            self.identifier, candidate["id"], CandidateReview(action="retry_research")
        )
        await self.until(lambda: candidate["research"]["state"] == "ready")

    async def test_missing_context_can_be_researched_conditionally_but_defer_cannot(
        self,
    ):
        candidate = self.add(missing_information=["Deployment version"])
        await self.until(lambda: candidate["research"]["state"] == "ready")
        self.add(
            action="defer",
            candidate_id=candidate["id"],
            missing_information=["Service identity"],
        )
        self.assertEqual(candidate["state"], "deferred")
        self.assertEqual(candidate["research"]["state"], "not_requested")

    async def test_budget_is_enforced_before_external_work(self):
        self.service.current["research"]["requests"] = self.service.current["research"][
            "limit"
        ]
        candidate = self.add()
        await asyncio.sleep(0.02)
        self.assertEqual(candidate["research"]["state"], "budget_limit")
        self.assertEqual(self.researcher.calls, 0)

    async def test_retry_versions_sources_so_old_links_cannot_open_a_new_document(self):
        candidate = self.add()
        await self.until(lambda: candidate["research"]["state"] == "ready")
        old_revision = candidate["research_revision"]
        candidate["research"]["verdict"] = "insufficient"
        self.service.review(
            self.identifier, candidate["id"], CandidateReview(action="retry_research")
        )
        self.assertGreater(candidate["research_revision"], old_revision)
        with self.assertRaises(MeetingError) as caught:
            self.service.source(self.identifier, candidate["id"], "S1", old_revision)
        self.assertEqual(caught.exception.code, "SOURCE_CHANGED")
        await self.until(lambda: candidate["research"]["state"] == "ready")
        self.assertEqual(self.researcher.calls, 2)

    async def test_two_workers_bound_concurrency_and_drain_waiting_candidates(self):
        self.researcher.release = asyncio.Event()
        for index in range(10):
            self.add(
                question=f"Does API {index} support streaming?",
                topic_key=f"api-{index}-streaming",
            )
        await self.until(lambda: len(self.service.research_active) == 2)
        self.assertEqual(self.researcher.calls, 2)
        self.assertLessEqual(self.service.research_queue.qsize(), 8)
        self.researcher.release.set()
        await self.until(
            lambda: all(
                item["research"]["state"] == "ready"
                for item in self.service.current["candidates"]
            )
        )
        self.assertEqual(self.researcher.calls, 10)
