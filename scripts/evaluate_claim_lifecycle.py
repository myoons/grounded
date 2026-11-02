"""Evaluate lifecycle action selection and explicit statement-fidelity proxies."""

import argparse
import asyncio
import hashlib
import json
import os
import time
from pathlib import Path

import httpx

from scripts.evaluate_claim_gold import score, segments_for
from scripts.runpod_access import credentials
from server.meeting_detector import PROMPT, MeetingDetector
from server.meeting_model import MODEL

DATASET = Path(__file__).resolve().parents[1] / "tests/data/claim_lifecycle_gold.json"


def fidelity_check(case, changes):
    rubric = case["fidelity"]
    if rubric is None:
        return None
    if len(changes) != 1:
        return False
    text = " ".join([changes[0]["statement"], *changes[0]["conditions"]]).lower()
    return (
        all(term in text for term in rubric["all_terms"])
        and any(term in text for term in rubric["any_terms"])
        and not any(term in text for term in rubric["forbidden_phrases"])
    )


async def run(args):
    os.environ.update(credentials())
    cases = json.loads(DATASET.read_text())["cases"]
    report = {
        "model": MODEL,
        "reasoning_effort": "low",
        "max_output_tokens": 1200,
        "temperature": "provider default",
        "concurrency": 3,
        "repetitions": args.repeat,
        "prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
        "dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(),
        "synthetic": True,
        "results": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(3)

    async def evaluate(case, repetition):
        async with semaphore:
            started = time.monotonic()
            row = {"case": case["id"], "repetition": repetition}
            try:
                result, usage = await MeetingDetector().detect(
                    segments_for(case["texts"]), case["candidates"]
                )
                changes = result.model_dump()["changes"]
                structural = score(case, changes)
                fidelity = fidelity_check(case, changes)
                row.update(
                    changes=changes,
                    usage=usage,
                    structural_passed=structural["passed"],
                    fidelity_proxy=fidelity,
                    passed=structural["passed"] and fidelity is not False,
                )
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
    report["summary"] = {
        "passed": sum(row["passed"] for row in report["results"]),
        "total": len(report["results"]),
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["summary"]))
    return all(row["passed"] for row in report["results"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeat", type=int, choices=range(1, 4), default=2)
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(run(args)) else 1)


if __name__ == "__main__":
    main()
