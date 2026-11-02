"""Deterministic scheduling sensitivity replay; model delays are simulated."""

import argparse
import asyncio
import heapq
import json
import math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from scripts.runpod_access import credentials
from server import meeting_service
from server.meeting_contracts import DetectionResult, MeetingStart
from server.meeting_detector import MeetingDetector
from server.observability import EventLog

TRACE = tuple(
    {
        "at": 1 + index * 3.5,
        "topic": index,
        "text": f"Product {index} supports CSV export.",
    }
    for index in range(12)
)


class Clock:
    def __init__(self):
        self.now = 0.0
        self.serial = 0
        self.pending = []

    async def sleep(self, delay):
        future = asyncio.get_running_loop().create_future()
        self.serial += 1
        heapq.heappush(self.pending, (self.now + delay, self.serial, future))
        await future

    async def drain(self):
        for _turn in range(50):
            await asyncio.sleep(0)

    async def run(self):
        await self.drain()
        while self.pending:
            moment, _serial, future = heapq.heappop(self.pending)
            if future.done():
                continue
            self.now = moment
            future.set_result(None)
            await self.drain()


class SimulatedDetector:
    def __init__(self, clock, delays):
        self.clock, self.delays = clock, delays
        self.starts = []
        self.last_duration = 0

    def ready(self):
        return True

    async def detect(self, segments, candidates):
        self.starts.append(self.clock.now)
        delay = self.delays[(len(self.starts) - 1) % len(self.delays)]
        await self.clock.sleep(delay)
        self.last_duration = delay
        known = {item["topic_key"] for item in candidates}
        changes = []
        for segment in segments:
            topic = "topic-" + segment["id"]
            if topic in known:
                continue
            changes.append(
                {
                    "action": "create",
                    "candidate_id": None,
                    "topic_key": topic,
                    "evidence_ids": [segment["id"]],
                    "question": "Does " + segment["text"].rstrip(".") + "?",
                    "statement": segment["text"],
                    "category": "capability",
                    "reason": "The integration depends on CSV export.",
                    "conditions": [],
                    "missing_information": [],
                    "related_ids": [],
                }
            )
        return DetectionResult(changes=changes), {"total_tokens": 0}


class SimulatedResearcher:
    def __init__(self, clock, delay):
        self.clock, self.delay = clock, delay

    def ready(self):
        return True

    async def research(self, candidate, progress):
        progress("searching")
        await self.clock.sleep(self.delay)
        return {"state": "complete", "sources": [], "timings": {}}


class ReplayService(meeting_service.MeetingService):
    def _launch(self):
        pass


def percentiles(values):
    ordered = sorted(values)
    return {
        name: ordered[max(0, math.ceil(len(ordered) * quantile) - 1)]
        if ordered
        else None
        for name, quantile in (("p50", 0.5), ("p95", 0.95))
    }


async def replay(policy="5s", workers=2, detector_delays=(2.0,), research_delay=24.0):
    if policy not in ("5s", "0s", "adaptive") or workers not in (1, 2, 3):
        raise ValueError("Unsupported replay policy")
    clock = Clock()
    detector = SimulatedDetector(clock, detector_delays)
    events = []

    async def stop_speech():
        pass

    service = ReplayService(
        speech=SimpleNamespace(ready=lambda: True, stop=stop_speech),
        detector=detector,
        researcher=SimulatedResearcher(clock, research_delay),
        events=EventLog(sink=lambda event: events.append((clock.now, event))),
    )
    await service.start(
        MeetingStart(consent=True, audio_consent_version="runpod-audio-v1")
    )
    service.watchdog.cancel()
    await asyncio.gather(service.watchdog, return_exceptions=True)
    session = service.current
    session["cloud_detection"] = True
    session["research_enabled"] = True
    commits = {}
    audio_ticks = []
    max_pending = 0

    async def detector_sleep(_production_delay):
        interval = 5 if policy == "5s" else 0
        if policy == "adaptive":
            interval = min(5, max(0.5, detector.last_duration / 2))
        last_start = detector.starts[-1] if detector.starts else -100
        await clock.sleep(max(0.25, interval - (clock.now - last_start)))

    async def inject():
        nonlocal max_pending
        for entry in TRACE:
            await clock.sleep(entry["at"] - clock.now)
            audio_ticks.append(clock.now)
            session["segments"].append(
                {
                    "id": str(entry["topic"]),
                    "source": "system",
                    "text": entry["text"],
                    "start_ms": int(entry["at"] * 1000),
                    "end_ms": int(entry["at"] * 1000 + 500),
                    "final": True,
                    "possible_echo_of": None,
                    "finalized_at": clock.now,
                }
            )
            session["revision"] += 1
            max_pending = max(
                max_pending,
                session["revision"] - session["detector"]["processed_revision"],
            )
            service.changed.set()

    original_apply = service.apply_changes

    def apply(*args, **kwargs):
        original_apply(*args, **kwargs)
        for candidate in session["candidates"]:
            commits.setdefault(candidate["topic_key"], clock.now)

    proxy = SimpleNamespace(**{name: getattr(asyncio, name) for name in dir(asyncio)})
    proxy.sleep = detector_sleep
    tasks = []
    with (
        patch.object(meeting_service, "asyncio", proxy),
        patch.object(
            meeting_service, "time", SimpleNamespace(monotonic=lambda: clock.now)
        ),
        patch.object(service, "apply_changes", apply),
    ):
        tasks = [asyncio.create_task(service._detect()), asyncio.create_task(inject())]
        tasks.extend(
            asyncio.create_task(service._research()) for _worker in range(workers)
        )
        try:
            await clock.run()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    candidates = session["candidates"]
    expected = {"topic-" + str(entry["topic"]) for entry in TRACE}
    assert {item["topic_key"] for item in candidates} == expected, session["detector"]
    assert session["detector"]["processed_revision"] == len(TRACE)
    assert all(item["research"]["state"] == "complete" for item in candidates)
    assert audio_ticks == [entry["at"] for entry in TRACE]
    claim_delays = [
        commits["topic-" + str(entry["topic"])] - entry["at"] for entry in TRACE
    ]
    answer_delays = [
        item["research"]["timings"]["final_transcript_to_answer_ms"] / 1000
        for item in candidates
    ]
    return {
        "policy": policy,
        "research_workers": workers,
        "detector_simulated_seconds": list(detector_delays),
        "research_simulated_seconds": research_delay,
        "claim_seconds": percentiles(claim_delays),
        "answer_seconds": percentiles(answer_delays),
        "detector_invocations": len(detector.starts),
        "research_invocations": session["research"]["requests"],
        "max_unprocessed_transcripts": max_pending,
        "max_research_queue": max(
            (event.get("research_queue_depth", 0) for _at, event in events), default=0
        ),
        "audio_event_delay_seconds": 0,
        "exact_expected_claims": True,
        "tokens_actual": 0,
        "tokens_estimate": None,
        "token_estimate_note": "No tokenizer or fabricated dollar estimate; multiply invocation counts by separately measured fixed-request usage only as a rough sensitivity estimate.",
    }


async def sample_kimi(count):
    if not 1 <= count <= 12:
        raise ValueError("Live sample must be between 1 and 12 calls")
    detector = MeetingDetector()
    detector.model.key = credentials()["RUNPOD_API_KEY"]
    segment = {
        "id": "sample-1",
        "source": "system",
        "text": "Amazon S3 supports object versioning.",
    }
    rows = []
    for index in range(count):
        try:
            result, usage = await detector.detect([segment], [])
            rows.append(
                {"index": index, "usage_actual": usage, "changes": result.model_dump()}
            )
        except (
            httpx.HTTPError,
            ValueError,
            TypeError,
            RuntimeError,
            TimeoutError,
        ) as error:
            rows.append({"index": index, "error_type": type(error).__name__})
    return rows


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live-calls", type=int, default=0)
    args = parser.parse_args()
    rows = []
    for delays in ((0.5,), (2.0,), (8.0,), (1.0, 3.0, 9.0)):
        for policy in ("5s", "0s", "adaptive"):
            for workers in (1, 2, 3):
                rows.append(await replay(policy, workers, delays))
    samples = await sample_kimi(args.live_calls) if args.live_calls else []
    usages = [
        item["usage_actual"]["total_tokens"]
        for item in samples
        if "usage_actual" in item
    ]
    if usages:
        for row in rows:
            row["tokens_estimate"] = {
                "detector_only": round(
                    row["detector_invocations"] * sum(usages) / len(usages)
                ),
                "research": None,
                "basis": "Invocation count times mean actual fixed one-claim request tokens; growing replay contexts and outputs differ. Not billed usage or a cost forecast.",
            }
    output = {
        "scope": "Real MeetingService detection, batching, transactional apply, bounded research queue and research worker loops; deterministic simulated detector/research delays. Audio events are finalized transcript arrivals, not PCM/ASR. This demonstrates event independence, not measured audio processing capacity or production SLAs.",
        "limitations": "Synthetic oracle claims, no empirical accuracy comparison. No alternative models tested. No provider contention modeled. Adaptive interval=min(5,max(.5,last_duration/2)); all policies retain .25s debounce and singleflight. Model delay sensitivity is not production timing data. Research timeout remains real asyncio timeout and is not exercised.",
        "trace": TRACE,
        "rows": rows,
        "live_fixed_request_samples": samples,
        "live_sample_tokens_actual": sum(usages),
        "live_sample_caveat": "Sequential fixed detector request, current Kimi only; other experiments may overlap. Illustrative service latency, not isolated hardware benchmark or model-selection proof.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n")
    args.output.chmod(0o600)


if __name__ == "__main__":
    asyncio.run(main())
