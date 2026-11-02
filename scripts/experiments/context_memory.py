"""Frozen synthetic long-dialog stress probes; not a representative accuracy eval."""

import argparse
import asyncio
import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

import httpx

from scripts.private_state import save_private_json
from scripts.runpod_access import credentials
from server.meeting_detector import (
    MAX_CONTEXT_CHARS,
    PROMPT,
    MeetingDetector,
    build_context,
    detection_schema,
    encoded_size,
)
from server.meeting_model import MODEL


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def identifier(name):
    return str(uuid5(NAMESPACE_URL, "roomshift-context-probe/" + name))


def segment(name, text):
    return {"id": identifier(name), "source": "system", "text": text}


def fixtures():
    old = segment(
        "old",
        "SQLite WAL supports concurrent writers on a network filesystem, so we can deploy our database there.",
    )
    candidate = {
        "id": identifier("claim"),
        "topic_key": "sqlite-wal-network-filesystem",
        "state": "candidate",
        "question": "Does SQLite WAL support concurrent writers on a network filesystem?",
        "statement": "SQLite WAL supports concurrent writers on a network filesystem.",
        "category": "technical_behavior",
        "evidence_ids": [old["id"]],
        "conditions": [],
        "missing_information": [],
    }
    noise = [
        segment(
            f"noise-{index}",
            "Internal workshop housekeeping: I prefer the blue room layout and the quiet seats. "
            * 8,
        )
        for index in range(45)
    ]
    candidates = [candidate] + [
        {
            **candidate,
            "id": identifier(f"noise-claim-{index}"),
            "topic_key": f"private-layout-{index}",
            "question": "Does the private workshop layout match our internal seating preference?",
            "statement": "Our private workshop seating layout matches internal preferences.",
            "evidence_ids": [noise[index]["id"]],
        }
        for index in range(25)
    ]
    specifications = [
        (
            "repeat",
            "Returning to SQLite WAL: it supports concurrent writers on a network filesystem, which our deployment still depends on.",
            "link",
            True,
            "technical_behavior",
        ),
        (
            "correction",
            "Correction to the SQLite WAL network filesystem claim: our deployment uses a local disk, not a network filesystem; keep checking concurrent writers on that local disk.",
            "update",
            True,
            "technical_behavior",
        ),
        (
            "reintroduced",
            "Returning to SQLite WAL and the network filesystem plan: we withdraw that premise and our deployment no longer depends on it.",
            "retract",
            True,
            "technical_behavior",
        ),
        (
            "pronoun_gap",
            "Returning to the database assumption: it supports that, so our deployment still depends on it.",
            "link",
            True,
            "technical_behavior",
        ),
        ("unresolved", "That vendor supports it, so choose them.", "none", False, None),
        (
            "excluded_preference",
            "I prefer the blue room layout; it feels calmer.",
            "none",
            False,
            None,
        ),
    ]
    cases = []
    for name, text, action, has_claim, category in specifications:
        newest = segment(name, text)
        cases.append(
            {
                "name": name,
                "segments": ([old] if has_claim else []) + noise + [newest],
                "candidates": candidates if has_claim else [],
                "expected": {
                    "action": action,
                    "candidate_id": candidate["id"] if has_claim else None,
                    "category": category,
                    "recent_evidence": newest["id"],
                    "antecedent": old["id"] if has_claim else None,
                },
                "ambiguity": "Pronoun-only return has weak lexical overlap; no assumption that retrieval resolves it."
                if name == "pronoun_gap"
                else None,
            }
        )
    return cases


def terms(text):
    stop = {
        "the",
        "and",
        "that",
        "this",
        "our",
        "for",
        "with",
        "supports",
        "claim",
        "returning",
        "from",
        "does",
        "which",
        "still",
        "depends",
    }
    return {
        word
        for word in re.findall(r"[a-z]+", text.lower())
        if len(word) > 2 and word not in stop
    }


def lexical_context(segments, candidates):
    query = terms(segments[-1]["text"])
    ranked = sorted(
        enumerate(candidates),
        key=lambda pair: (
            len(
                query & terms(pair[1].get("statement", "") + " " + pair[1]["question"])
            ),
            pair[0],
        ),
    )
    selected = build_context([], [item for _, item in ranked])["candidates"]
    original_order = {item["id"]: index for index, item in enumerate(candidates)}
    selected.sort(key=lambda item: original_order[item["id"]])
    payload = {
        "transcript": [],
        "candidates": selected,
        "omitted_segments": len(segments),
        "omitted_candidates": len(candidates) - len(selected),
    }
    ranked_segments = sorted(
        enumerate(segments[:-1]),
        key=lambda pair: (len(query & terms(pair[1]["text"])), pair[0]),
        reverse=True,
    )
    picked = [(len(segments) - 1, segments[-1])]
    for index, item in ranked_segments:
        proposed = sorted(picked + [(index, item)])
        payload["transcript"] = [entry for _, entry in proposed]
        if encoded_size(payload) <= MAX_CONTEXT_CHARS:
            picked = proposed
    payload["transcript"] = [item for _, item in sorted(picked)]
    payload["omitted_segments"] = len(segments) - len(picked)
    if encoded_size(payload) > MAX_CONTEXT_CHARS:
        raise ValueError("Newest utterance exceeds context budget")
    return payload


def score(result, expected):
    changes = result["changes"]
    if expected["action"] == "none":
        return {
            "action_and_identity": not changes,
            "category": not changes,
            "recent_evidence": not changes,
        }
    matches = [
        item
        for item in changes
        if item["action"] == expected["action"]
        and item["candidate_id"] == expected["candidate_id"]
    ]
    return {
        "action_and_identity": len(matches) == 1 and len(changes) == 1,
        "category": any(item["category"] == expected["category"] for item in matches),
        "recent_evidence": any(
            expected["recent_evidence"] in item["evidence_ids"] for item in matches
        ),
    }


async def run(output, live):
    cases = fixtures()
    frozen = {
        "provenance": "Author-created synthetic long-dialog stress cases; held out from production tuning, no natural meeting accuracy inference.",
        "cases": cases,
    }
    save_private_json(output / "frozen.json", frozen)
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "fixture_sha256": digest(frozen),
        "model": MODEL,
        "prompt_sha256": digest(PROMPT),
        "max_completion_tokens_per_call": 1200,
        "limitations": [
            "Six deliberately constructed cases, one completion per arm; no statistical accuracy claim.",
            "Other experiments may run concurrently; latency is contaminated, not an isolated benchmark.",
            "Lexical retrieval is experimental, not RAG quality evidence; pronouns may remain unresolved.",
            "Private seating candidates are deliberate distractors, not typical production eligible claims.",
        ],
        "records": [],
    }
    if live:
        os.environ.update(credentials())
    for case_index, case in enumerate(cases):
        arms = [("baseline", build_context), ("lexical_recency", lexical_context)]
        if case_index % 2:
            arms.reverse()
        for arm, selector in arms:
            payload = selector(case["segments"], case["candidates"])
            expected = case["expected"]
            record = {
                "case": case["name"],
                "arm": arm,
                "payload": payload,
                "payload_sha256": digest(payload),
                "context_chars": encoded_size(payload),
                "schema_sha256": digest(
                    detection_schema(payload["transcript"], payload["candidates"])
                ),
                "retention": {
                    "antecedent": expected["antecedent"]
                    in {item["id"] for item in payload["transcript"]}
                    if expected["antecedent"]
                    else None,
                    "candidate": expected["candidate_id"]
                    in {item["id"] for item in payload["candidates"]}
                    if expected["candidate_id"]
                    else None,
                },
            }
            report["records"].append(record)
            save_private_json(output / "report.json", report)
            if live:
                detector = MeetingDetector()
                complete = detector.model.complete

                async def capture(*args, complete=complete, record=record, **kwargs):
                    content, usage = await complete(*args, **kwargs)
                    record["raw_output"] = content
                    record["usage"] = usage
                    return content, usage

                detector.model.complete = capture
                try:
                    with patch(
                        "server.meeting_detector.build_context", return_value=payload
                    ):
                        result, usage = await detector.detect(
                            case["segments"], case["candidates"]
                        )
                    record["output"] = result.model_dump(mode="json")
                    record["usage"] = usage
                    record["input_tokens_derived"] = (
                        usage["total_tokens"] - usage["completion_tokens"]
                        if isinstance(usage.get("completion_tokens"), int)
                        else None
                    )
                    record["fidelity"] = score(record["output"], expected)
                except (
                    ValueError,
                    TypeError,
                    RuntimeError,
                    TimeoutError,
                    httpx.HTTPError,
                ) as error:
                    record["failure"] = type(error).__name__
                save_private_json(output / "report.json", report)
            print(
                json.dumps(
                    {
                        "case": case["name"],
                        "arm": arm,
                        "retention": record["retention"],
                        "fidelity": record.get("fidelity"),
                        "failure": record.get("failure"),
                    }
                ),
                flush=True,
            )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if (args.output / "frozen.json").exists():
        parser.error("Choose a fresh output directory to preserve the frozen run.")
    asyncio.run(run(args.output, args.live))


if __name__ == "__main__":
    main()
