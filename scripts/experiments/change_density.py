"""Frozen synthetic backlog experiment; never changes production detector settings."""

import argparse
import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import httpx
from pydantic import Field, create_model

from scripts.private_state import save_private_json
from scripts.runpod_access import credentials
from server.meeting_contracts import CandidateChange, DetectionResult
from server.meeting_model import KimiModel
from server.runpod_jobs import RunpodJobClient

DATASET = Path(__file__).with_name("change_density_cases.json")
FROZEN = Path(__file__).with_name("change_density_baseline.json")
BaselineResult = create_model(
    "BaselineResult",
    __base__=DetectionResult,
    changes=(list[CandidateChange], Field(max_length=3)),
)
ExpandedResult = create_model(
    "ExpandedResult",
    __base__=DetectionResult,
    changes=(list[CandidateChange], Field(max_length=8)),
)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def recording_jobs(responses):
    class RecordingJobs(RunpodJobClient):
        async def run(self, *job_args, **job_kwargs):
            response = await super().run(*job_args, **job_kwargs)
            responses.append(response)
            return response

    return RecordingJobs


def make_case(count, claims):
    selected = claims[:count]
    texts = [claim["text"] for claim in selected]
    texts += [
        "Correction to the PostgreSQL statement: I mean version 16, not version 15. PostgreSQL 16 supports row-level security; our tenant isolation choice depends on that.",
        "To repeat, PostgreSQL 16 supports row-level security, which we need for tenant isolation.",
    ]
    segments = [
        {
            "id": f"density-{count}-{index}",
            "source": "system",
            "text": text,
            "final": True,
            "possible_echo_of": None,
        }
        for index, text in enumerate(texts, 1)
    ]
    gold = []
    for index, claim in enumerate(selected, 1):
        gold.append(
            {
                "id": claim["id"],
                "action": "create",
                "category": claim["category"],
                "required_evidence": [segments[count]["id"]]
                if index == 1
                else [segments[index - 1]["id"]],
                "evidence_group": [
                    segments[0]["id"],
                    segments[count]["id"],
                    segments[count + 1]["id"],
                ]
                if index == 1
                else [segments[index - 1]["id"]],
                "terms": claim["terms"],
            }
        )
    return {"id": f"density_{count}", "segments": segments, "gold": gold}


def strict_match(expected, change):
    text = (change["statement"] + " " + change["question"]).lower()
    return (
        change["action"] == expected["action"]
        and change["category"] == expected["category"]
        and change["candidate_id"] is None
        and set(expected["required_evidence"]) <= set(change["evidence_ids"])
        and all(
            any(term in text for term in alternatives)
            for alternatives in expected["terms"]
        )
    )


def score(case, changes):
    matched = set()
    duplicate_count = 0
    evidence_claims = set()
    details = []
    for change in changes:
        possible = [gold for gold in case["gold"] if strict_match(gold, change)]
        evidence_claims.update(
            gold["id"]
            for gold in case["gold"]
            if set(gold["required_evidence"]) <= set(change["evidence_ids"])
        )
        available = [gold for gold in possible if gold["id"] not in matched]
        if available:
            matched.add(available[0]["id"])
        elif possible:
            duplicate_count += 1
        details.append([gold["id"] for gold in possible])
    return {
        "strict_recall": len(matched) / len(case["gold"]),
        "strict_precision": len(matched) / len(changes) if changes else None,
        "evidence_claim_recall": len(evidence_claims) / len(case["gold"]),
        "duplicates": duplicate_count,
        "matched": sorted(matched),
        "missing": [gold["id"] for gold in case["gold"] if gold["id"] not in matched],
        "strict_matches_by_output": details,
        "manual_semantic_review_required": True,
    }


def summarize(rows):
    summaries = {}
    for strategy in ("max3", "max8"):
        selected = [row for row in rows if row["strategy"] == strategy]
        successful = [row for row in selected if "score" in row]
        matched = sum(len(row["score"]["matched"]) for row in successful)
        expected = sum(
            len(row["score"]["matched"]) + len(row["score"]["missing"])
            for row in successful
        )
        emitted = sum(len(row["changes"]) for row in successful)
        summaries[strategy] = {
            "calls": len(selected),
            "errors": len(selected) - len(successful),
            "strict_recall_successful_calls": matched / expected if expected else None,
            "strict_precision_successful_calls": matched / emitted if emitted else None,
            "duplicates": sum(row["score"]["duplicates"] for row in successful),
            "total_tokens": sum(
                row.get("usage", {}).get("total_tokens", 0) for row in selected
            ),
            "completion_tokens": sum(
                row.get("usage", {}).get("completion_tokens", 0) or 0
                for row in selected
            ),
            "elapsed_ms_sum": sum(row["elapsed_ms"] for row in selected),
            "batches_all_pending_submitted": sum(
                set(row["pending_ids"]) <= set(row["submitted_ids"]) for row in selected
            ),
            "successful_batches_with_missed_claims_and_empty_next_batch": sum(
                bool(row["score"]["missing"]) and not row["next_batch_ids"]
                for row in successful
            ),
            "cost_usd": None,
        }
    return summaries


async def run(args):
    os.environ.update(credentials())
    fixture = json.loads(DATASET.read_text())
    cases = [make_case(count, fixture["claims"]) for count in (1, 3, 5, 8)]
    frozen = json.loads(FROZEN.read_text())
    prompt_baseline = frozen["baseline_prompt"]
    report = {
        "experiment": "change_density",
        "model": "kimi-k3",
        "endpoint": "moonshot-kimi",
        "concurrency": 1,
        "synthetic": True,
        "fixture_sha256": digest(fixture),
        "cases": cases,
        "cases_sha256": digest(cases),
        "baseline_prompt": prompt_baseline,
        "prompt_sha256": hashlib.sha256(prompt_baseline.encode()).hexdigest(),
        "frozen_definition_sha256": hashlib.sha256(FROZEN.read_bytes()).hexdigest(),
        "scope": "Historical frozen payload/schema comparison, not current production service batching.",
        "timing_note": "Concurrent unrelated experiments may run; timings are descriptive, not isolated comparisons.",
        "cost_note": "Provider billing/rates unavailable; token counts recorded, no invented dollar estimate.",
        "rows": [],
    }
    for repetition in range(1, args.repeat + 1):
        for case in cases:
            strategies = ("max3", "max8") if repetition % 2 else ("max8", "max3")
            for strategy in strategies:
                session = {
                    "detector": {"processed_revision": 0},
                    "revision": len(case["segments"]),
                    "segments": case["segments"],
                }
                definition = next(
                    row
                    for row in frozen["rows"]
                    if row["case"] == case["id"] and row["strategy"] == strategy
                )
                payload = definition["payload"]
                schema = definition["schema"]
                revision = len(case["segments"])
                prompt, budget = prompt_baseline, 1200
                if strategy == "max8":
                    schema["properties"]["changes"]["maxItems"] = 8
                    prompt = prompt_baseline.replace(
                        "Produce at most three changes.",
                        "Produce at most eight changes.",
                    )
                    budget = 3200
                row = {
                    "case": case["id"],
                    "strategy": strategy,
                    "repetition": repetition,
                    "payload": payload,
                    "schema": schema,
                    "schema_sha256": digest(schema),
                    "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                    "max_tokens": budget,
                    "pending_ids": [segment["id"] for segment in case["segments"]],
                    "submitted_ids": [
                        segment["id"] for segment in payload["transcript"]
                    ],
                    "cursor_before": 0,
                    "batch_revision": revision,
                }
                raw_responses = []

                started = time.monotonic()
                try:
                    with patch(
                        "server.meeting_model.RunpodJobClient",
                        recording_jobs(raw_responses),
                    ):
                        content, usage = await KimiModel().complete(
                            prompt, payload, schema, "assumptions", max_tokens=budget
                        )
                    row.update(raw_content=content, usage=usage)
                    contract = BaselineResult if strategy == "max3" else ExpandedResult
                    changes = contract.model_validate_json(content).model_dump()[
                        "changes"
                    ]
                    supplied = set(row["submitted_ids"])
                    if any(
                        not set(change["evidence_ids"]) <= supplied
                        for change in changes
                    ):
                        raise ValueError("Unknown evidence")
                    row.update(changes=changes, score=score(case, changes))
                    session["detector"]["processed_revision"] = revision
                    row.update(
                        cursor_after=revision,
                        next_batch_ids=[],
                        next_revision=revision,
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
                    row["error_type"] = type(error).__name__
                row.update(
                    elapsed_ms=round((time.monotonic() - started) * 1000),
                    raw_responses=raw_responses,
                    raw_responses_sha256=digest(raw_responses),
                )
                report["rows"].append(row)
                save_private_json(args.output, report)
                print(
                    json.dumps(
                        {
                            key: row[key]
                            for key in ("case", "strategy", "repetition", "elapsed_ms")
                        }
                        | {"score": row.get("score"), "error": row.get("error_type")}
                    ),
                    flush=True,
                )
    report["output_rows_sha256"] = digest(report["rows"])
    report["summary"] = summarize(report["rows"])
    save_private_json(args.output, report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeat", type=int, choices=(1, 2, 3), default=2)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
