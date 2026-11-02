"""Synthetic persistence and response-size probe; not an audio/model benchmark."""

import argparse
import asyncio
import json
import platform
import resource
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from server.capacity_policy import CapacityPolicy
from server.meeting_contracts import MeetingStart
from server.meeting_history import MeetingHistory
from server.meeting_service import MeetingService
from tests.test_meeting import FakeDetector, FakeSpeech


async def run():
    policy = CapacityPolicy()
    with TemporaryDirectory() as directory:
        history = MeetingHistory(Path(directory) / "meetings.sqlite")
        service = MeetingService(
            FakeSpeech(), FakeDetector(), history=history, capacity=policy
        )
        try:
            await service.start(
                MeetingStart(consent=True, audio_consent_version="runpod-audio-v1")
            )
            timings = []
            for index in range(policy.max_segments):
                service.current["segments"].append(
                    {
                        "id": f"segment-{index}",
                        "source": "microphone" if index % 2 else "system",
                        "text": "Synthetic capacity fixture. " * 8,
                        "start_ms": (index // 2) * 2400,
                        "end_ms": (index // 2) * 2400 + 2400,
                        "final": True,
                        "possible_echo_of": None,
                    }
                )
                started = time.perf_counter()
                service._persist(required=True)
                timings.append((time.perf_counter() - started) * 1000)
            loaded = history.store.load(service.current["id"])
            assert len(loaded["segments"]) == policy.max_segments
            normal_bytes = len(json.dumps(service.view(service.current["id"])).encode())
            for segment in service.current["segments"]:
                segment["text"] = "x" * 2000
            maximum_text_bytes = len(
                json.dumps(service.view(service.current["id"])).encode()
            )
            service.current["candidates"] = [
                {
                    "id": f"claim-{index}",
                    "state": "candidate",
                    "category": "capability",
                    "statement": "x" * 240,
                    "question": "x" * 240,
                    "reason": "x" * 500,
                    "topic_key": f"fixture-{index}",
                    "conditions": ["x" * 240] * 4,
                    "missing_information": ["x" * 240] * 4,
                    "related_ids": [],
                    "evidence_ids": [f"segment-{position}" for position in range(4)],
                    "evidence": [
                        {
                            "id": f"segment-{position}",
                            "source": "system",
                            "text": "x" * 2000,
                        }
                        for position in range(4)
                    ],
                    "research_revision": 1,
                    "research": {
                        "state": "ready",
                        "verdict": "conditional",
                        "summary": "x" * 360,
                        "recommendation": "x" * 360,
                        "caveats": ["x" * 240] * 3,
                        "sources": [
                            {
                                "id": f"S{source}",
                                "url": "https://example.com/" + "x" * 2000,
                                "title": "x" * 500,
                                "content_hash": "a" * 64,
                            }
                            for source in range(3)
                        ],
                        "citations": [
                            {"source_id": f"S{source}", "quote": "x" * 240}
                            for source in range(3)
                        ],
                    },
                }
                for index in range(policy.max_candidates)
            ]
            full_view = json.dumps(service.view(service.current["id"]))
            full_graph_bytes = len(full_view.encode())
            parse_started = time.perf_counter()
            assert len(json.loads(full_view)["candidates"]) == policy.max_candidates
            parse_ms = (time.perf_counter() - parse_started) * 1000
            service.current["candidates"] = []
            service.current["segments"] = loaded["segments"]
            return {
                "scope": "Synthetic two-channel transcript only; excludes candidate payload, GPU, capture and renderer performance. 768 commits, one observation per size. Not an SLA.",
                "platform": platform.platform(),
                "policy": policy.model_dump(),
                "segments_restored": len(loaded["segments"]),
                "snapshot_bytes_normal": normal_bytes,
                "snapshot_bytes_maximum_ascii_text": maximum_text_bytes,
                "snapshot_bytes_full_stress_graph": full_graph_bytes,
                "full_stress_graph_parse_ms": round(parse_ms, 3),
                "full_graph_note": "128 synthetic cards with maximum statement/conditions/evidence and 3 citations; URLs 2020 characters/titles500 are chosen stress values, not a proof of all byte maxima. Requires response cap above 2MB.",
                "last_commit_ms": round(timings[-1], 3),
                "maximum_commit_ms": round(max(timings), 3),
                "total_commit_ms": round(sum(timings), 3),
                "database_bytes": history.path.stat().st_size
                if hasattr(history, "path")
                else (Path(directory) / "meetings.sqlite").stat().st_size,
                "process_peak_rss_native_units": resource.getrusage(
                    resource.RUSAGE_SELF
                ).ru_maxrss,
            }
        finally:
            await service.close()
            history.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(run())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
