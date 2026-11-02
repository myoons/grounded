"""Run two bounded live search-and-synthesis checks using synthetic technical claims."""

import asyncio
import json
import os
from pathlib import Path

from scripts.runpod_access import credentials
from scripts.search_access import credentials as search_credentials
from server.meeting_research import MeetingResearcher

CASES = {
    "sqlite": {
        "question": "Does SQLite WAL mode support concurrent writes on a network filesystem?",
        "conditions": [
            "The database file is shared by servers through network storage."
        ],
        "missing_information": [],
        "evidence": [
            {
                "text": "We can use SQLite WAL across our servers because it supports network filesystems."
            }
        ],
    },
    "browser": {
        "question": "Can a normal HTTP page at a remote LAN address use getUserMedia to capture microphone audio?",
        "conditions": [
            "The page is not localhost and has no browser security overrides."
        ],
        "missing_information": [],
        "evidence": [
            {
                "text": "We can serve the microphone app over ordinary HTTP on a LAN address."
            }
        ],
    },
}


async def main():
    os.environ.update(credentials())
    os.environ.update(search_credentials())
    directory = (
        Path.home()
        / "Library/Application Support/Roomshift/artifacts/research-evaluation"
    )
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    async def run(name, candidate):
        try:
            result = await MeetingResearcher().research(candidate)
            assert result["verdict"] in ("contradicted", "conditional")
            assert result["citations"]
            report = {"synthetic": True, "passed": True, "result": result}
        except (
            ValueError,
            TypeError,
            RuntimeError,
            TimeoutError,
            AssertionError,
        ) as error:
            report = {
                "synthetic": True,
                "passed": False,
                "error_type": type(error).__name__,
            }
        (directory / f"{name}.json").write_text(json.dumps(report, indent=2))
        print(
            json.dumps(
                {
                    "case": name,
                    "passed": report["passed"],
                    "error": report.get("error_type"),
                    "verdict": report.get("result", {}).get("verdict"),
                    "elapsed_ms": report.get("result", {}).get("elapsed_ms"),
                }
            ),
            flush=True,
        )
        return report["passed"]

    if not all(
        await asyncio.gather(*(run(name, case) for name, case in CASES.items()))
    ):
        raise SystemExit("Research checks failed; inspect saved evidence.")


if __name__ == "__main__":
    asyncio.run(main())
