"""Measure real search and synthesis using synthetic claims, never meeting audio."""

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from scripts.runpod_access import credentials
from scripts.search_access import credentials as search_credentials
from server.meeting_model import KimiModel
from server.meeting_research import MeetingResearcher
from server.meeting_search import FirecrawlSearch

CASES = [
    (
        "capability",
        "Amazon S3 versioning allows recovery of overwritten objects.",
        "Does enabling Amazon S3 versioning allow recovery of overwritten objects?",
        ["supported", "conditional"],
        ["docs.aws.amazon.com"],
    ),
    (
        "limits_pricing",
        "Cloudflare Workers Free has unlimited CPU time per HTTP request.",
        "Does Cloudflare Workers Free provide unlimited CPU time per HTTP request?",
        ["contradicted"],
        ["developers.cloudflare.com"],
    ),
    (
        "policy_licensing",
        "The MIT License prohibits commercial use.",
        "Does the MIT License prohibit commercial use of licensed software?",
        ["contradicted"],
        ["opensource.org", "choosealicense.com"],
    ),
    (
        "performance_comparison",
        "Redis is always twice as fast as Memcached for every workload.",
        "Is Redis always twice as fast as Memcached regardless of workload or hardware?",
        ["conditional", "insufficient", "contradicted"],
        [],
    ),
    (
        "technical_behavior",
        "SQLite WAL works across multiple servers over a network filesystem.",
        "Does SQLite WAL support a database shared across servers on a network filesystem?",
        ["contradicted"],
        ["sqlite.org", "www.sqlite.org"],
    ),
    (
        "current_status",
        "Python 3.8 has reached end of life.",
        "Has Python 3.8 reached its official end-of-life date?",
        ["supported"],
        ["devguide.python.org", "peps.python.org", "www.python.org"],
    ),
]


class ObservedSearch(FirecrawlSearch):
    async def search(self, question):
        query, documents = await super().search(question)
        self.documents = documents
        return query, documents


class ObservedModel(KimiModel):
    async def complete(self, *args, **kwargs):
        self.content, self.usage = await super().complete(*args, **kwargs)
        return self.content, self.usage


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--label", choices=["baseline", "improved", "repeat"], default="baseline"
    )
    parser.add_argument("--case", choices=[case[0] for case in CASES])
    args = parser.parse_args()
    os.environ.update(credentials())
    os.environ.update(search_credentials())
    directory = (
        Path.home()
        / "Library/Application Support/Roomshift/artifacts/claim-answer-evaluation"
        / args.label
    )
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    semaphore = asyncio.Semaphore(3)

    async def run(case):
        category, statement, question, verdicts, primary_hosts = case
        candidate = {
            "category": category,
            "statement": statement,
            "question": question,
            "conditions": [],
            "missing_information": [],
            "evidence": [{"text": statement}],
        }
        async with semaphore:
            started = time.monotonic()
            search = ObservedSearch()
            model = ObservedModel()
            try:
                result = await MeetingResearcher(search=search, model=model).research(
                    candidate
                )
                cited = {item["source_id"] for item in result["citations"]}
                hosts = [
                    urlsplit(item["url"]).hostname
                    for item in result["sources"]
                    if item["id"] in cited
                ]
                checks = {
                    "expected_verdict": result["verdict"] in verdicts,
                    "has_citations": bool(cited),
                    "primary_source": not primary_hosts
                    or bool(set(hosts) & set(primary_hosts)),
                }
                report = {
                    "synthetic": True,
                    "candidate": candidate,
                    "result": result,
                    "checks": checks,
                    "automated_pass": all(checks.values()),
                    "semantic_review": "pending",
                    "retrieved_excerpts": [
                        {**document.metadata(), "text": document.text}
                        for document in search.documents
                    ],
                }
            except (
                ValueError,
                TypeError,
                RuntimeError,
                TimeoutError,
                KeyError,
                httpx.HTTPError,
            ) as error:
                report = {
                    "synthetic": True,
                    "candidate": candidate,
                    "automated_pass": False,
                    "error_type": type(error).__name__,
                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                }
                if hasattr(error, "errors"):
                    report["validation_errors"] = error.errors(
                        include_input=False, include_context=False, include_url=False
                    )
                report["model_content"] = getattr(model, "content", None)
                report["usage"] = getattr(model, "usage", None)
                report["retrieved_excerpts"] = [
                    {**document.metadata(), "text": document.text}
                    for document in getattr(search, "documents", [])
                ]
            (directory / f"{category}.json").write_text(json.dumps(report, indent=2))
            print(
                json.dumps(
                    {
                        "case": category,
                        "automated_pass": report["automated_pass"],
                        "checks": report.get("checks"),
                        "error_type": report.get("error_type"),
                        "timings": report.get("result", {}).get("timings"),
                        "elapsed_ms": report.get("result", {}).get(
                            "elapsed_ms", report.get("elapsed_ms")
                        ),
                    }
                ),
                flush=True,
            )
            return report

    reports = await asyncio.gather(
        *(run(case) for case in CASES if not args.case or case[0] == args.case)
    )
    print(
        json.dumps(
            {
                "completed": len(reports),
                "automated_pass": sum(report["automated_pass"] for report in reports),
                "semantic_review_required": True,
            }
        )
    )
    if not all(report["automated_pass"] for report in reports):
        raise SystemExit("Claim-answer evaluation failed; inspect the saved reports.")


if __name__ == "__main__":
    asyncio.run(main())
