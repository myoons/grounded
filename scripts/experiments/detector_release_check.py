"""Small real-provider regression against the current production adapter."""

import argparse
import asyncio
import hashlib
import json
import os
import time
from pathlib import Path

from scripts.experiments.change_density import DATASET, make_case, score
from scripts.runpod_access import credentials
from server.meeting_detector import PROMPT, MeetingDetector


async def evaluate(count, claims):
    case = make_case(count, claims)
    started = time.perf_counter()
    result, usage = await MeetingDetector().detect(case["segments"], [])
    changes = result.model_dump()["changes"]
    return {
        "case": case["id"],
        "scope": "Adapter only; all synthetic utterances supplied directly, not service batching.",
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "changes": changes,
        "score": score(case, changes),
        "usage": usage,
    }


async def run():
    os.environ.update(credentials())
    claims = json.loads(DATASET.read_text())["claims"]
    rows = await asyncio.gather(*(evaluate(count, claims) for count in (5, 8)))
    return {
        "prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
        "dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(),
        "output_cap": 8,
        "max_tokens": 3200,
        "repetitions": 1,
        "rows": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(run())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            [{"case": row["case"], "score": row["score"]} for row in report["rows"]]
        )
    )
