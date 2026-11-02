# Rejecting fetched error pages before synthesis

## Motivation and scope

The retrieval experiment exposed a concrete failure: a SQLite forum search hit
returned a 212-character server-overload page. Its search title described the
intended forum discussion, while its scraped metadata reported HTTP 503 and its
body described server load. The former minimum-length check accepted it as a
source. A search hit is not proof that its requested document was retrieved.

`server/meeting_search.py` now filters the fetched document before passage
selection, source numbering, and synthesis:

1. Reject integer `metadata.statusCode` values from 400 through 599. This field
   was present in the cached Firecrawl response; numbers inside prose are not
   interpreted as HTTP status metadata.
2. Keep the existing minimum of 100 characters, now also checking readable text
   after Markdown cleanup.
3. When metadata is missing or reports success, reject narrowly recognized short
   overload, error, and browser-verification templates. Error headings require
   accompanying failure language. Body-template matching is limited to 1,600
   characters, avoiding broad rejection of longer technical documents.
4. Mark a URL as seen only after accepting its document. A failed fetch must not
   hide a later readable result for the same URL.

All-filtered results follow the existing no-readable-sources path:
`Insufficient`, no citations, and no synthesis model call. This is not evidence
that the meeting claim is false.

## Why deterministic filtering

This is fetch-quality hygiene, not source authority scoring or truth detection.
A model call would add latency and uncertainty to recognizing explicit HTTP
failures. The implementation does not reject documents merely for mentioning
errors, retries, CAPTCHA, or status codes. It does not add a trust-score system.

## Verification

Run:

```sh
uv run python -m unittest tests.test_search_page_quality tests.test_meeting_research -q
```

The seven focused tests cover HTTP metadata, a minimal overload regression,
short 404/503/access-denied/challenge templates, ordinary error documentation,
short factual content, long content with challenge-like introductory text,
duplicate URLs, and the real researcher path with a model that fails if called.
Combined with the research suite, 33 tests passed on 2026-11-02. Fixtures are
synthetic or minimally paraphrased; private cached full pages are not published.

### Post-hoc replay, not a new live benchmark

The four existing private cached Firecrawl responses were replayed through the
classifier without new provider requests. Each original request returned five
hits; production still requests three. This replay examines all cached hits,
not a controlled measurement of production search recall.

| Query fixture | Cached hits | Previously readable | Newly rejected |
| --- | ---: | ---: | ---: |
| PostgreSQL row-level security | 5 | 5 | 0 |
| S3 object size | 5 | 4 | 0 |
| SQLite foreign keys | 5 | 5 | 1 (HTTP 503) |
| SQLite WAL | 5 | 5 | 0 |

The one removed page was the observed overload response. Keeping the other 18
readable pages does not prove that they are authoritative or answer the question.

## Limits

- The text rules are deliberately conservative, English-oriented heuristics.
  Long, localized, unfamiliar, or disguised challenge pages can pass.
- A short genuine document that exactly resembles a failure template can be
  rejected. A genuine document returned with HTTP error metadata is also excluded.
- Missing or malformed metadata is not interpreted as a trustworthy success.
  Text checks still run, but they cannot establish retrieval validity generally.
- This does not detect prompt injection or guarantee semantic relevance.
- The existing 100-character requirement can still exclude concise valid pages;
  changing that policy requires separate retrieval evaluation.
- No output-quality or answer-accuracy percentage is claimed from this replay.
