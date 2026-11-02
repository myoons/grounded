# Validation

Grounded separates deterministic correctness, model behavior and device integration.
A passing schema or exact quotation check does not prove semantic correctness.

## Automated implementation checks

The 0.7.2 source passed **222 Python tests and 64 desktop tests**, plus Ruff
and isolated Electron renderer checks. Reproduce the current suite with:

```sh
uv sync --locked
uv run python -m unittest discover -s tests -q
uv run ruff check server scripts tests speech_worker
npm ci --prefix desktop
npm --prefix desktop test
```

| Contract | Failure exercised | Tests |
| --- | --- | --- |
| Durable audio ownership | Upload response loss, restart, duplicate identity and failed DB commit | `test_durable_audio.py`, `durable-http.test.mjs` |
| Safe local storage | Corrupt ciphertext, failed sync, terminated writer, quota and unavailable key storage | `audio-spool.test.mjs` |
| Atomic extraction | Failure between proposed changes and checkpoint; no premature research dispatch | `test_extraction_checkpoint.py` |
| Relational consistency | Cross-meeting links, old revisions, fractional timestamps and failed migrations | `test_history_relational.py` |
| Graceful completion | End during processing, bounded failures, repeated End and zero-audio recovery | `test_meeting_finalization.py` |
| Capture lifecycle | Delayed startup/permission, external End, failed Pause and stale operation completion | `capture-lifecycle.test.mjs`, `startup-end-ui-smoke.mjs`, `meeting-shutdown.test.mjs` |
| Resource limits | AI budget exhaustion without transcription loss, frozen policies and bounded response reads | `test_capacity_policy.py`, `response-budget.test.mjs` |
| Evidence ingestion | Actual overload template, HTTP errors, ordinary error documentation and duplicate URLs | `test_search_page_quality.py` |
| Privacy of diagnostics | Allowlisted fields, bounded rotation and nonfatal logging failure | `test_observability.py`, `capture-log.test.mjs` |

Fault tests use controlled dependencies and private temporary databases. They test
the production service paths, but do not simulate every hardware or filesystem failure.

## Model evaluation

| Evaluation | Recorded result | What it measures |
| --- | --- | --- |
| Historical extraction baseline | 19/20 separate synthetic cases | Action, category and evidence-reference checks |
| Historical prompt revision | 37/40 observations of the same 20 cases | No demonstrated improvement; failures retained |
| Current eight-change extractor | 19/20 regression cases | New configuration, separate report; technical-behavior omission retained |
| Claim lifecycle | 19/20 structural; 18/20 including scope proxy | Repeat/correction/withdrawal and implementation behavior |
| Evidence-based answer synthesis | 28/30 after the passage-selector correction | Predeclared checks plus post-hoc scoped-text rubric; not semantic accuracy |

Methods, frozen inputs, raw synthetic outputs and limitations:
[extraction](CLAIM_PROMPT_EVALUATION.md), [current adapter](CAPACITY_POLICY.md),
[lifecycle](CLAIM_LIFECYCLE_EVALUATION.md), [synthesis](SYNTHESIS_GOLD_EVALUATION.md).
Some labels are ambiguous, and lexical checks can miss incorrect reasoning.
The [five design experiments](DESIGN_EXPERIMENTS.md) explain which findings led to
changes and which alternatives were not adopted.

## Integration evidence

- An isolated synthetic-audio run through the real Runpod speech endpoint, Kimi and
  Firecrawl produced two transcript channels and a cited finding. First answer:
  **26.405 seconds**. This is one recorded observation, not an SLA or a microphone test.
- A nonempty saved-meeting database copy survived schema-v3 migration with matching
  transcript, claim, evidence and presentation values; integrity and foreign-key
  checks passed.
- The actual renderer reopened that saved record, restored its expanded card after
  reload, navigated to the original utterance and resolved its source URL with zero
  capture attempts. Resolving a URL is separate from browser navigation.
- Synthetic renderer tests cover failed End/Pause, processing-only recovery and
  unavailable secure storage without falsely displaying active capture.

## Device and quality boundaries

An attended MacBook run of 0.7.1 reported functional microphone/system-audio routes,
encrypted backlog retention across a tunnel interruption and normal app restart,
explicit recovery to an empty backlog, Meet-departure completion, headset reconnect
and saved script/card/source restoration. It also exposed a completed-history label
inconsistency, corrected in 0.7.2 with unit and renderer-reload regression tests.

Attended follow-up on November 3, 2026 verified the unchanged 0.7.2 package on the
MacBook. After authorizing the exact installed app through normal macOS Keychain
and Accessibility controls, secure storage initialized without a warning and two
distinct spoken English test sentences were transcribed accurately, one before
and one after normal Quit/relaunch. Ending each test left no pending audio chunks;
the wrapped key remained. The corrected completed-history label and saved content
also survived relaunch. No code, signature, key or security-policy bypass was used.

This establishes those observed sessions, not word-perfect recognition, per-utterance
duplicate accounting, future-update permission continuity or a unique root cause
for the earlier access failure. The existing incomplete-history case had no matching
saved fixture in the attended check; that state is covered by automated tests rather
than inferred from completed meetings.

That run observed recognition errors, including the technical term “WAL”. Its first
remote-audio trial used speakers, so channel isolation was not established. Exact
tail transcription, receipt-by-receipt deletion ordering and a server-restart scenario
were not established by that attended run. Those observations are distinct from
the deterministic integration tests above; passing recovery does not imply accurate ASR.

Automated tests do not establish real macOS Keychain authorization, microphone or
headset behavior. The app requires normal OS permissions; unavailable secure storage
disables capture rather than falling back to plaintext. Follow [setup](RUNNING.md)
for the actual device and verify a short meeting before use.

English-only transcription can miss quiet speech or short cut-off clauses. The
system selects important externally checkable claims rather than guaranteeing
exhaustive extraction. System playback is mixed audio, not named-speaker attribution.
Exact quotations establish provenance, not that every conclusion follows logically.
Latency depends on worker readiness, provider load, network and document retrieval.
Local durability does not cover loss of the disk or encryption key.
