"""Small reproducible retrieval/passage diagnostic; all network data stays private."""

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from scripts.private_state import save_private_json
from scripts.search_access import credentials
from server.meeting_search import public_url, readable_text, select_passages

FIXTURE = Path(__file__).resolve().parents[2] / "tests/data/retrieval_recall.json"
DEFAULT_OUTPUT = Path.home() / ".local/state/roomshift/retrieval-experiment"


def normalized(text):
    return re.sub(r"\s+", " ", text).strip().casefold()


def canonical(url):
    parsed = urlsplit(public_url(url))
    return parsed.hostname.removeprefix("www."), parsed.path.rstrip("/")


def inclusion(text, case):
    text = normalized(text)
    decisive = normalized(case["decisive"]) in text
    condition = normalized(case["condition"]) in text
    return {
        "decisive": decisive,
        "condition": condition,
        "both": decisive and condition,
    }


def adjacent_passages(text, query, limit):
    terms = set(re.findall(r"[a-z][a-z0-9_-]{2,}", query.lower()))
    paragraphs = re.split(r"\n\s*\n", text)
    ranked = sorted(
        range(len(paragraphs)),
        key=lambda index: sum(term in paragraphs[index].lower() for term in terms),
        reverse=True,
    )
    chosen = set()
    for index in ranked:
        candidate = chosen | set(
            range(max(0, index - 1), min(len(paragraphs), index + 2))
        )
        if (
            len("\n\n".join(paragraphs[position] for position in sorted(candidate)))
            <= limit
        ):
            chosen = candidate
    return "\n\n".join(paragraphs[index] for index in sorted(chosen))


def passage_rows(text, case):
    return [
        {
            "selector": name,
            "budget": budget,
            "input_chars": len(text),
            "output_chars": len(selected),
            **inclusion(selected, case),
        }
        for name, selector in [
            ("production", select_passages),
            ("paragraph_neighbors", adjacent_passages),
        ]
        for budget in (3000, 6000, 10000)
        for selected in [selector(text, case["question"], budget)]
    ]


def synthetic_cases():
    case = {
        "question": "Does LumaBox archive export require an enterprise plan?",
        "decisive": "Archive export is supported.",
        "condition": "Only on the enterprise plan; free accounts cannot use it.",
    }
    filler = "General navigation and unrelated installation examples. " * 20
    decisive = case["decisive"] + "\n\n" + case["condition"]
    return [
        ("late_decisive", case, (filler + "\n\n") * 12 + decisive),
        (
            "keyword_decoys",
            case,
            ((case["question"] + " ") * 20 + "\n\n") * 8 + decisive,
        ),
        ("window_split", case, "x" * 4190 + "\n\n" + decisive + "\n\n" + filler * 10),
    ]


def summarize_search(body, case):
    web = body.get("data", {}).get("web", []) if body.get("success") else []
    rows = []
    for rank, item in enumerate(web[:5], 1):
        try:
            matches = canonical(item.get("url", "")) == canonical(case["url"])
            valid = True
        except ValueError:
            matches, valid = False, False
        markdown = item.get("markdown")
        available = valid and isinstance(markdown, str) and len(markdown.strip()) >= 100
        text = readable_text(markdown[:200000]) if available else ""
        rows.append(
            {
                "rank": rank,
                "url": item.get("url"),
                "canonical_match": matches,
                "available": available,
                "input_chars": len(text),
                "full_evidence": inclusion(text, case),
                "passages": passage_rows(text, case) if available and matches else [],
                "cookie_boilerplate": "cookie preferences" in text.lower(),
                "server_error_boilerplate": "server load is currently too high"
                in text.lower(),
            }
        )
    return {
        "id": case["id"],
        "success": body.get("success", False),
        "sources": rows,
        "prefixes": [
            {
                "limit": limit,
                "canonical_url_found": any(
                    row["canonical_match"] for row in rows[:limit]
                ),
                "canonical_source_available": any(
                    row["canonical_match"] and row["available"] for row in rows[:limit]
                ),
                "canonical_full_evidence": any(
                    row["canonical_match"] and row["full_evidence"]["both"]
                    for row in rows[:limit]
                ),
            }
            for limit in (3, 5)
        ],
    }


def run(output, live=False):
    fixture = json.loads(FIXTURE.read_text())
    cases = fixture["cases"]
    search_rows = []
    key = credentials().get("FIRECRAWL_API_KEY") if live else None
    if live and not key:
        raise RuntimeError("Private Firecrawl credential unavailable")
    for case in cases:
        query = case["question"].rstrip("?") + " official documentation"
        cache = output / (case["id"] + "-search.json")
        if not cache.exists() and live:
            payload = {
                "query": query,
                "limit": 5,
                "scrapeOptions": {
                    "formats": ["markdown"],
                    "onlyMainContent": True,
                    "maxAge": 3600000,
                    "timeout": 20000,
                    "skipTlsVerification": False,
                },
            }
            started = datetime.now(UTC).isoformat()
            try:
                response = httpx.post(
                    "https://api.firecrawl.dev/v2/search",
                    headers={"Authorization": f"Bearer {key}"},
                    json=payload,
                    timeout=35,
                )
                body = (
                    response.json()
                    if response.status_code == 200
                    else {"success": False, "http_status": response.status_code}
                )
            except (httpx.HTTPError, ValueError) as error:
                body = {"success": False, "error_type": type(error).__name__}
            save_private_json(
                cache, {"started_at": started, "payload": payload, "body": body}
            )
        if cache.exists():
            record = json.loads(cache.read_text())
            search_rows.append(
                {
                    "started_at": record["started_at"],
                    **summarize_search(record["body"], case),
                }
            )
        else:
            search_rows.append({"id": case["id"], "missing_cache": True})
    primary = (
        json.loads((output / "primary.json").read_text())
        if (output / "primary.json").exists()
        else {}
    )
    if live and not primary:
        for case in cases:
            response = httpx.post(
                "https://api.firecrawl.dev/v2/scrape",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "url": case["url"],
                    "formats": ["markdown"],
                    "onlyMainContent": True,
                    "maxAge": 0,
                },
                timeout=35,
            )
            response.raise_for_status()
            body = response.json()
            if body.get("success") and isinstance(
                body.get("data", {}).get("markdown"), str
            ):
                primary[case["url"]] = body["data"]
        save_private_json(output / "primary.json", primary)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "fixture_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        "method": "Same limit5 response prefixes3/5; not independently issued limit3 vs limit5 requests. Fixed gold URL recall only. No LLM or answer accuracy metric.",
        "search": search_rows,
        "primary_passages": [
            {
                "id": case["id"],
                "full_evidence": inclusion(text, case),
                "rows": passage_rows(text, case),
            }
            for case in cases
            if case["url"] in primary
            for text in [readable_text(primary[case["url"]]["markdown"][:200000])]
        ],
        "synthetic_passages": [
            {"id": name, "rows": passage_rows(text, case)}
            for name, case, text in synthetic_cases()
        ],
    }
    save_private_json(output / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Fetch only missing cache entries (four requests maximum)",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = run(args.output, args.live)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
