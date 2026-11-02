# Implementation guide

Grounded demonstrates a custom Runpod Serverless transcription endpoint inside a
meeting workflow. The desktop captures authorized audio, the backend manages work
and durability, and source-linked cards expose researched findings.

For a concrete input/output example, start with the
[recorded custom endpoint demonstration](GPU_SPEECH.md#recorded-endpoint-demonstration).

## Read the code in this order

| Question | Entry point | Contract to inspect |
| --- | --- | --- |
| What is deployed on Runpod? | [`speech_worker/handler.py`](../speech_worker/handler.py), [`engine.py`](../speech_worker/engine.py), [`Dockerfile`](../speech_worker/Dockerfile) | Bounded English PCM input, pinned model, CUDA inference and structured output |
| Who owns meeting state? | [`server/meeting_service.py`](../server/meeting_service.py) | Session lifecycle, ordered extraction, concurrent research and stale-result guards |
| When may audio be deleted? | [`desktop/audio-spool-controller.mjs`](../desktop/audio-spool-controller.mjs), [`server/meeting_history.py`](../server/meeting_history.py) | Only after a durable terminal receipt, not upload acceptance |
| What is persisted? | [`server/history_schema.py`](../server/history_schema.py), [`history_store.py`](../server/history_store.py) | Normalized meeting/claim/evidence relations, scoped foreign keys and incremental writes |
| How are claims selected? | [`server/meeting_detector.py`](../server/meeting_detector.py) | Exclusions, six categories, lifecycle actions and strict structured references |
| How are answers grounded? | [`server/meeting_search.py`](../server/meeting_search.py), [`meeting_research.py`](../server/meeting_research.py) | Readable evidence, server-owned quotation choices and insufficient-evidence handling |
| How does capture stop safely? | [`desktop/meeting/capture-lifecycle.mjs`](../desktop/meeting/capture-lifecycle.mjs), [`app.mjs`](../desktop/meeting/app.mjs), [`meeting-main.mjs`](../desktop/meeting-main.mjs) | Exclusive operations, delayed permissions, shutdown acknowledgement and no hidden restart |

## Engineering decisions

1. **Durable ownership:** the device retains encrypted audio until a committed
   transcription outcome is acknowledged. Stable identities make retries idempotent
   at the database boundary, without promising exactly-once cloud billing.
2. **Atomic progress:** extracted changes and the processing cursor commit together.
   A failed save rolls back live state before dispatching dependent research.
3. **Versioned evidence:** a finding belongs to a specific claim revision; obsolete
   results cannot overwrite corrected premises.
4. **Explicit recovery:** restart does not authorize recording or paid processing.
   Accepted work can be resumed without opening a microphone.
5. **Measured choices:** density and retrieval experiments exposed actionable defects;
   settings without demonstrated superiority were retained rather than arbitrarily tuned.

The tests in [validation](VALIDATION.md) map these contracts to injected failures
and reproducible evaluations. Kimi is a managed Runpod API; Whisper is the custom
worker implemented and deployed by this project.
