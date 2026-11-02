"""Evaluate production synthesis with frozen fictional evidence, without web search."""

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import httpx

from scripts.runpod_access import credentials
from server.meeting_model import MODEL, KimiModel
from server.meeting_research import PROMPT, MeetingResearcher, normalized_quote
from server.meeting_search import SourceDocument

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "tests/data/synthesis_gold.json"


def score(case, result):
    prose = " ".join([result["summary"], result["recommendation"], *result["caveats"]])
    documents = {
        f"S{index + 1}": item["text"] for index, item in enumerate(case["documents"])
    }
    citations = result["citations"]
    checks = {
        "verdict": result["verdict"] in case["expect"]["verdicts"],
        "scope_terms": all(
            re.search(pattern, prose, re.IGNORECASE)
            for pattern in case["expect"]["required"]
        ),
        "forbidden_terms_absent": not any(
            re.search(pattern, prose, re.IGNORECASE)
            for pattern in case["expect"]["forbidden"]
        ),
        "citation_integrity": all(
            item["source_id"] in documents
            and normalized_quote(item["quote"])
            in normalized_quote(documents[item["source_id"]])
            for item in citations
        ),
        "factual_verdict_has_citation": result["verdict"] == "insufficient"
        or bool(citations),
    }
    return {
        "checks": checks,
        "passed": all(checks.values()),
        "semantic_review": "required",
    }


def score_v2(case, result):
    result_score = score(case, result)
    summary = result["summary"]
    patterns = {
        "capability_supported": [r"\bpaid\s+plans?\b"],
        "plan_overgeneralization": [
            r"\bfree\b[^.;]*\b(?:ten|10)\s+exports?\s+(?:per|a|each)\s+month\b",
        ],
        "license_conditional": [
            r"\bnotice\b",
            r"\b(?:every|each|all)\b[^.;]*\bcop(?:y|ies)\b",
        ],
        "technical_condition": [r"\bsnapshot\s+mode\b[^.;]*\benabled\b"],
        "status_supported": [r"\bJanuary\s+1,?\s+2025\b"],
    }
    result_score["checks"]["explicit_summary_scope"] = all(
        re.search(pattern, summary, re.IGNORECASE)
        for pattern in patterns.get(case["id"], [])
    )
    if case["id"] == "plan_overgeneralization":
        free_scope = re.search(r"\bFree\b([^.;]*)", summary, re.IGNORECASE)
        result_score["checks"]["free_quantity_and_unit"] = bool(
            free_scope
        ) and not bool(
            re.search(
                r"\b(?:hundred|thousand|million|billion|day|daily|week|weekly|year|yearly)\b|\b(?!10\b)\d+\b",
                free_scope.group(1),
                re.IGNORECASE,
            )
        )
    result_score["passed"] = all(result_score["checks"].values())
    result_score["rubric"] = "v2 post-hoc scope regression; not semantic accuracy"
    return result_score


class FixedSearch:
    def __init__(self, case):
        self.case = case

    def ready(self):
        return True

    async def search(self, question):
        return question, [
            SourceDocument(
                f"S{index + 1}",
                item["url"],
                item["title"],
                item["text"],
                "2026-11-02T00:00:00+00:00",
                hashlib.sha256(item["text"].encode()).hexdigest(),
            )
            for index, item in enumerate(self.case["documents"])
        ]


class ObservedModel(KimiModel):
    content = None
    usage = None

    async def complete(self, *args, **kwargs):
        self.content, self.usage = await super().complete(*args, **kwargs)
        return self.content, self.usage


def rescore_report(original):
    cases = {case["id"]: case for case in json.loads(GOLD.read_text())["cases"]}
    reports = []
    for row in original["reports"]:
        scored = {"id": row["id"], "repeat": row["repeat"]}
        if "result" in row:
            scored.update(score_v2(cases[row["id"]], row["result"]))
        else:
            scored.update(
                passed=False,
                checks={"completed_result": False},
                error_type=row.get("error_type", "MissingResult"),
            )
        reports.append(scored)
    return reports


async def evaluate(output, repeats):
    os.environ.update(credentials())
    raw = GOLD.read_bytes()
    cases = json.loads(raw)["cases"]
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    semaphore = asyncio.Semaphore(3)

    async def run(case, repeat):
        async with semaphore:
            model = ObservedModel()
            candidate = {
                key: case[key] for key in ("category", "statement", "question")
            }
            candidate.update(
                conditions=[],
                missing_information=[],
                evidence=[{"text": case["statement"]}],
            )
            report = {"id": case["id"], "repeat": repeat}
            try:
                result = await MeetingResearcher(
                    search=FixedSearch(case), model=model
                ).research(candidate)
                report.update(result=result, **score(case, result))
            except (
                ValueError,
                TypeError,
                RuntimeError,
                TimeoutError,
                KeyError,
                httpx.HTTPError,
            ) as error:
                report.update(passed=False, error_type=type(error).__name__)
            report["raw_model_content"] = model.content
            report["usage"] = model.usage
            if model.content:
                try:
                    report["raw_verdict"] = json.loads(model.content).get("verdict")
                except json.JSONDecodeError:
                    pass
            (output / f"{case['id']}-{repeat}.json").write_text(
                json.dumps(report, indent=2)
            )
            print(
                json.dumps(
                    {
                        key: report.get(key)
                        for key in ("id", "repeat", "passed", "checks", "error_type")
                    }
                ),
                flush=True,
            )
            return report

    reports = await asyncio.gather(
        *(run(case, repeat) for repeat in range(1, repeats + 1) for case in cases)
    )
    source_commit = await asyncio.to_thread(
        subprocess.check_output, ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    )
    summary = {
        "created_at": datetime.now(UTC).isoformat(),
        "source_commit": source_commit.strip(),
        "gold_sha256": hashlib.sha256(raw).hexdigest(),
        "prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
        "model": MODEL,
        "concurrency": 3,
        "repeats": repeats,
        "total": len(reports),
        "passed": sum(item["passed"] for item in reports),
        "model_calls_completed": sum(
            item["raw_model_content"] is not None for item in reports
        ),
        "scope": "Synthetic fixed evidence; no live search; lexical scope checks do not prove entailment; manual semantic review required.",
        "reports": reports,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2))
    print(
        json.dumps({key: value for key, value in summary.items() if key != "reports"}),
        flush=True,
    )
    return summary["passed"] == summary["total"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rescore",
        type=Path,
        help="Rescore recorded outputs with v2; no provider calls.",
    )
    parser.add_argument("--repeats", type=int, choices=[1, 2, 3], default=2)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path.home()
        / "Library/Application Support/Roomshift/artifacts/synthesis-gold",
    )
    args = parser.parse_args()
    if args.rescore:
        raw = args.rescore.read_bytes()
        original = json.loads(raw)
        reports = rescore_report(original)
        report = {
            "scope": "Post-hoc v2 lexical regression on preserved outputs, not new inference or held-out accuracy. Five cases have additional summary constraints; remaining cases retain v1 checks.",
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "gold_sha256": hashlib.sha256(GOLD.read_bytes()).hexdigest(),
            "original_passed": original["passed"],
            "total": len(reports),
            "passed": sum(row["passed"] for row in reports),
            "reports": reports,
        }
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "summary-v2.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
        print(
            json.dumps(
                {key: value for key, value in report.items() if key != "reports"}
            )
        )
        raise SystemExit(0 if report["passed"] == report["total"] else 1)
    raise SystemExit(0 if asyncio.run(evaluate(args.output, args.repeats)) else 1)


if __name__ == "__main__":
    main()
