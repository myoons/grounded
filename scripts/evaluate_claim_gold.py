"""Score frozen synthetic claim extraction cases against the real Runpod model."""

import argparse
import asyncio
import hashlib
import itertools
import json
import os
import statistics
import time
from pathlib import Path

import httpx

from scripts.evaluate_meeting_detection import CASES
from scripts.runpod_access import credentials
from server import meeting_detector
from server.meeting_detector import MeetingDetector
from server.meeting_model import ENDPOINT, MODEL

DATASET = Path(__file__).resolve().parents[1] / "tests/data/claim_gold.json"


def segments_for(texts):
    return [
        {
            "id": f"00000000-0000-4000-8001-{index:012d}",
            "source": "system",
            "text": text,
        }
        for index, text in enumerate(texts, 1)
    ]


def matches(expected, actual, segments):
    evidence = {segments[index - 1]["id"] for index in expected["required_evidence"]}
    return (
        actual["action"] == expected["action"]
        and actual["category"] == expected["category"]
        and actual["candidate_id"] == expected.get("candidate_id")
        and evidence <= set(actual["evidence_ids"])
    )


def score(case, changes):
    expected = case["expected"]
    segments = segments_for(case["texts"])
    known = {segment["id"] for segment in segments}
    references_valid = all(set(change["evidence_ids"]) <= known for change in changes)
    exact = len(expected) == len(changes) and any(
        all(
            matches(wanted, actual, segments)
            for wanted, actual in zip(expected, ordering)
        )
        for ordering in itertools.permutations(changes)
    )
    return {"passed": exact and references_valid, "references_valid": references_valid}


def development_cases():
    categories = {
        "capability": "technical_behavior",
        "pricing": "limits_pricing",
        "repeat": "technical_behavior",
        "withdrawal": "technical_behavior",
        "license": "policy_licensing",
        "comparison": "performance_comparison",
        "announced_status": "current_status",
        "feature_support": "capability",
        "earlier_context": "limits_pricing",
        "late_claim": "technical_behavior",
        "capability_repeat_run": "technical_behavior",
    }
    result = []
    for name, texts, candidates, action in CASES:
        expected = []
        if action != "none":
            expected = [
                {
                    "action": "link" if action == "repeat" else action,
                    "category": categories[name],
                    "required_evidence": [len(texts)],
                    "candidate_id": candidates[0]["id"] if candidates else None,
                }
            ]
        result.append(
            {
                "id": f"dev_{name}",
                "split": "dev",
                "texts": texts,
                "candidates": candidates,
                "expected": expected,
            }
        )
    return result


async def run(args):
    os.environ.update(credentials())
    if args.prompt_file:
        meeting_detector.PROMPT = args.prompt_file.read_text()
    cases = (
        development_cases()
        if args.split == "dev"
        else json.loads(DATASET.read_text())["cases"]
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "model": MODEL,
        "endpoint": ENDPOINT,
        "reasoning_effort": "low",
        "max_output_tokens": meeting_detector.MAX_OUTPUT_TOKENS,
        "temperature": "provider default",
        "synthetic": True,
        "split": args.split,
        "repetitions": args.repeat,
        "concurrency": args.concurrency,
        "prompt_sha256": hashlib.sha256(meeting_detector.PROMPT.encode()).hexdigest(),
        "dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(),
        "cases_sha256": hashlib.sha256(
            json.dumps(cases, sort_keys=True).encode()
        ).hexdigest(),
        "results": [],
    }
    semaphore = asyncio.Semaphore(args.concurrency)

    async def evaluate(case, repetition):
        async with semaphore:
            started = time.monotonic()
            row = {
                "case": case["id"],
                "repetition": repetition,
                "expected": case["expected"],
            }
            try:
                result, usage = await MeetingDetector().detect(
                    segments_for(case["texts"]), case["candidates"]
                )
                changes = result.model_dump()["changes"]
                row.update(changes=changes, usage=usage, **score(case, changes))
            except (
                ValueError,
                RuntimeError,
                TimeoutError,
                KeyError,
                OSError,
                TypeError,
                httpx.HTTPError,
            ) as error:
                row.update(passed=False, error_type=type(error).__name__)
            row["elapsed_ms"] = round((time.monotonic() - started) * 1000)
            report["results"].append(row)
            args.output.write_text(json.dumps(report, indent=2) + "\n")
            print(
                json.dumps(
                    {
                        key: row[key]
                        for key in ("case", "repetition", "passed", "elapsed_ms")
                    }
                ),
                flush=True,
            )

    await asyncio.gather(
        *(
            evaluate(case, repetition)
            for repetition in range(1, args.repeat + 1)
            for case in cases
        )
    )
    rows = report["results"]
    report["summary"] = {
        "passed": sum(row["passed"] for row in rows),
        "total": len(rows),
        "median_ms": statistics.median(row["elapsed_ms"] for row in rows),
        "max_ms": max(row["elapsed_ms"] for row in rows),
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["summary"]))
    return all(row["passed"] for row in rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("dev", "heldout"), required=True)
    parser.add_argument("--repeat", type=int, choices=range(1, 4), default=1)
    parser.add_argument("--concurrency", type=int, choices=range(1, 5), default=3)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prompt-file", type=Path)
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(run(args)) else 1)


if __name__ == "__main__":
    main()
