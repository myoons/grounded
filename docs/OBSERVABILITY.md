# Pipeline observability

Grounded keeps content-free structured diagnostics on the trusted backend. The
UI remains focused on transcripts and findings. These logs are diagnostic hints,
not the durable work ledger, billing records, or a complete distributed trace.

## Location and retention

The API constructs `EventLog(data_dir / "logs")`. With the default data directory,
the active file is `~/Library/Application Support/Roomshift/logs/pipeline.jsonl`.
The directory is restricted to mode `700`; files are `600`. Rotation occurs before
an append exceeds 1 MiB. There are at most three backups (`pipeline.jsonl.1`
through `.3`) plus the active file: at most 4 MiB under this writer. The oldest
backup is replaced. This is size-based retention, not a promised number of days.
Logs are not shipped to a telemetry provider or exposed in the meeting UI.

Desktop capture metadata remains in Electron's user-data directory as
`capture-diagnostics.jsonl` plus one `.previous` file, each bounded to 64,000 bytes.
Its pending writer queue is limited to 32 events. Overflow/failure emits a bounded
stderr warning. Raw error messages and arbitrary renderer strings are excluded;
only known routes, codes, error names, and sanitized track states are retained.
Desktop events currently have no meeting/audio-chunk correlation identifier.

Writes are synchronous, small, and bounded in count by production events; there
is no unbounded background logging queue. Filesystem failure does not fail the
meeting pipeline. A fixed, content-free stderr warning is emitted at most once
per minute per logger when diagnostics fail. Operators must not infer complete
traces from missing events. No fsync durability is promised for diagnostic logs.

## Schema and correlation

Each JSON line has `schema_version: 1` and a UTC ISO `at` timestamp. Allowed fields:

| Fields | Meaning |
|---|---|
| `stage`, `status`, `cause` | Fixed vocabulary; no raw exception messages |
| `session_id`, `epoch` | Meeting identity and capture generation |
| `source`, `sequence` | Audio channel and chunk number within its generation |
| `claim_id`, `research_revision` | Claim and exact scope of the research |
| `revision`, `processed_revision` | Transcript checkpoint being extracted/applied |
| `attempt` | Detector request counter or research attempt counter, not provider retries |
| `queue_depth`, `research_queue_depth` | Queue snapshots at selected service events |
| `wait_ms`, `duration_ms` | Local monotonic wait/work duration for the named stage |
| `inference_ms`, `round_trip_ms` | Optional speech adapter/provider measurements |
| `search_ms`, `synthesis_ms`, `validation_ms` | Research component timings on successful return |

Unknown fields and invalid/nonfinite/negative durations are discarded. Identifiers
are bounded hex/hyphen tokens. No transcript, prompt, audio, question, source URL,
credential, provider response body, or exception text belongs in this schema.
Even identifier-only logs reveal activity times and should remain private.

`at` orders observations approximately across processes; it is not a stopwatch.
Local durations use `time.monotonic()` inside the current process. Do not subtract
serialized monotonic values from a prior process or calculate cross-machine
latency from them. A provider-reported interval has its provider's definition.

## Follow one meeting

1. Find `session/started` and its `session_id`. Filter lines by that ID using a
   local JSON reader (for example `jq 'select(.session_id == "ID")' pipeline.jsonl`).
2. For an audio chunk, match **session + epoch + source + sequence**:
   `audio/admitted` → `transcription/started` → `provider_returned` →
   `audio/committed` for durable audio. Admission follows receipt persistence;
   commit follows successful transcript-and-receipt persistence. The legacy
   non-durable route emits `processed`, not a durable acknowledgement.
3. Compare `wait_ms` at transcription start with its provider-return duration.
   A large wait indicates local queue delay; a large round trip does not by itself
   prove GPU inference is slow. Worker startup, provider queueing and transport
   are not separately resolved by this logger.
4. Match `detection/started` and `complete` using transcript `revision` and the
   request `attempt`. `blocked` identifies budget, context, storage or processing
   failure; `stale` means the returned work no longer matches live state.
5. Match research by **claim + research revision**. `queued` → `started` →
   `progress` (`searching`/`analyzing`) → `complete`, `failed`, `cancelled` or
   `stale`. Completion records available search/synthesis/validation durations.
   These are computation completion events, not proof of durable storage; consult
   storage failures and the database work state before declaring recovery done.
6. `session/finishing` → `complete` or `incomplete` records drain outcome.
   Cancel/resume events record **requests**, not proof the operation succeeded.

After a restart, use the persisted database work state as truth, not a missing
log tail. Query rotated files as well as the active file during investigation.

## Deliberate limits

- No end-to-end desktop speech-end-to-painted-card trace is claimed.
- Detector scheduling wait is not separately timed from model work yet.
- Research component times may be unavailable on failures; stage progress still
  identifies the last observed step, not an exact root cause.
- Provider cancellation success, billing, and individual transport retries are
  not inferred from service-level events.
- Local hostile users with the same OS account can alter logs; these are not
  tamper-evident audit records or a replacement for application authorization.
- Pipeline logging does not sanitize unrelated libraries' stdout/stderr.

## Validation rationale

`tests/test_observability.py` exercises the production service with deterministic
fake providers, not merely formatted example lines. It checks audio correlation,
research failure identity, content exclusion, bounded rotation and permissions,
finite timing fields, and continued transcription when the sink fails. Research
failure lifecycle runs with the injected sink. These demonstrate diagnostic
behavior, not model quality, live device latency, or provider availability.
