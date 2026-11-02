# Capacity policy and dense extraction

## Product boundary, not unlimited processing

Grounded keeps one active meeting graph in memory and sends a full view to the desktop.
This release does not claim unlimited meetings or a guaranteed hour of arbitrary speech.
The former fixed 256-segment / 60-extraction / 20-research limits are replaced by an
explicit operator policy captured when each meeting starts.

| Setting | Default | Purpose |
| --- | ---: | --- |
| `GROUNDED_MAX_SEGMENTS` | 768 | Aggregate transcript resource bound |
| `GROUNDED_MAX_DURATION_SECONDS` | 3600 | Capture-duration safety bound, not a supported-hour guarantee |
| `GROUNDED_MAX_CANDIDATES` | 128 | Aggregate card resource bound |
| `GROUNDED_DETECTION_REQUESTS` | 180 | Explicit extraction request budget |
| `GROUNDED_RESEARCH_REQUESTS` | 60 | Explicit research request budget |

The 180 extraction attempts correspond to fifteen minutes at the minimum five-second
request-start interval; retries also consume attempts. Sixty research attempts are an
operator-selected spending ceiling, not an empirically optimal value or guaranteed
number of completed cards. Three attempts per card and the provider timeouts remain.
Zero AI budgets are valid: audio transcription continues while checks stay incomplete.

The 768-segment default accommodates a synthetic fifteen-minute, two-channel workload
at approximately 2.4 seconds per segment. Shorter fragments can exhaust it sooner.
This is a declared workload, not a wall-clock promise. This release rejects segment
bounds above 768 and durations above 3,600 seconds, matching the measured resource
profile and desktop audio protocol. Larger profiles require coordinated full-view,
memory, renderer, persistence and protocol validation, not an environment-variable bypass.
Reject invalid or negative configuration at service construction rather than silently
replacing it.

## Failure behavior

- Extraction/research spending exhaustion blocks that AI stage, not audio acceptance.
- Claim capacity rolls back the extraction transaction without advancing its checkpoint;
  transcription continues and finalization remains incomplete.
- Transcript capacity refuses additional chunks without acknowledging deletion. The
  desktop retains encrypted pending audio and reports the permanent capacity condition.
- Queue bounds (audio four, research eight), two research workers, disk bounds, lease,
  request-size limits and retry/time limits are unchanged.
- Restart does not refill budgets. The policy and counters are persisted with the meeting.
  Legacy rows without policy receive explicit legacy defaults (256/60/20, one-hour duration)
  on recovery, not silently changed semantics. A new policy applies to a new meeting.

## Measured local envelope

[Raw synthetic resource report](evaluations/capacity-policy/envelope.json).
The probe performs 768 sequential relational saves and reloads the transcript. It also
serializes a stress view with 768 maximum-length ASCII transcript texts and 128 cards,
four evidence snippets per card, three sources and three citations.

- Typical transcript-only response: 277,445 bytes.
- Maximum-length ASCII transcript-only response: 1,641,413 bytes.
- Full stress view: 4,430,567 bytes; Python JSON parse: 4.534 ms.
- Transcript save total: 3,446.428 ms; final save: 8.717 ms; largest: 9.135 ms.
- Peak process RSS: 72,482,816 bytes on macOS arm64 (includes interpreter/imports).

These are single-run descriptive measurements, not latency percentiles. Candidate stress
serialization is not a full-graph incremental persistence benchmark, a renderer benchmark,
or a proof of every Unicode/URL maximum. The stress graph exceeds the former desktop
2 MB response allowance; the release requires the separately tested bounded 8 MiB reader.
No raw audio, actual meeting transcript, credentials or GPU call appears in this probe.

## Dense-claim correction

The production detector now accepts eight changes with a 3,200-token output budget.
The service admits at most four new chronological transcript segments per extraction
request, with previous segments available as bounded context. A response hitting eight
changes over multiple pending segments is retried with one pending segment without
advancing the checkpoint. Retry attempts consume the same budget. A single-segment
eight-change response is applied with a coverage warning, not an infinite retry loop.

This reduces known output truncation and backlog loss; it does **not** guarantee discovery
of every fact. More than eight relevant claims in one utterance, model omissions, and
multiple dependent corrections across batch boundaries remain evaluation targets.

[Current production-adapter regression](evaluations/capacity-policy/detector-release.json):
two parallel real Kimi calls, one five-claim and one eight-claim synthetic case, each scored
1.0 strict precision/recall by the existing rule-based scorer. This is a tiny regression,
not semantic accuracy, and directly supplies all case utterances to the adapter: service
batching is tested separately with deterministic tests. Existing prompt-gold scores remain
historical; they must not be presented as scores of this new output-budget configuration.

[New production held-out regression](evaluations/capacity-policy/heldout-release.json)
ran the existing twenty synthetic held-out cases once at concurrency three using the
current prompt, eight-change schema and 3,200-token budget: **19/20** passed. The
`heldout_behavior` case returned no change despite an expected technical-behavior claim.
Median call wall time was 8,610.5 ms, maximum 16,861 ms; concurrent unrelated experiments
were not isolated. This is the existing structural/action/category/reference scorer,
not an LLM semantic judge or a claim of 95% real-meeting accuracy. All four exclusion
cases and repeat/update/retract cases passed this run. Original 19/20 and 37/40 reports
are unchanged; this separate run is not evidence of universal improvement.

The original change-density experiment now reads its committed frozen prompt/payload/schema
definitions so rerunning its `max3` arm cannot silently use today's eight-change contract.

## Reproduction

```sh
uv run python -m unittest tests.test_capacity_policy tests.test_change_density tests.test_meeting_finalization tests.test_durable_audio
uv run python -m scripts.experiments.capacity_envelope --output artifacts/experiments/capacity-policy/envelope.json
uv run python -m scripts.experiments.detector_release_check --output artifacts/experiments/capacity-policy/detector-release.json
```

The last command makes two paid Runpod API calls and reads the existing private credential
file without printing it. No deployment or operational database mutation is required.
