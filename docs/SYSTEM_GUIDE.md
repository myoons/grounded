# Grounded: explain the system in six steps

## 1. The product in one sentence

Grounded listens to an authorized English meeting and turns decision-relevant factual
claims into concise findings with inspectable source evidence, without interrupting
the conversation.

It is not a meeting bot or a truth oracle. It saves text/results and temporarily
retains encrypted, unresolved audio on the participant Mac for explicit recovery. The live experience
is a transcript beside compact research cards. Detection lists a meeting; only the
operator opening that room starts collection. Pause and End remain explicit.

## 2. Three execution locations

| Location | Components | Why here? |
| --- | --- | --- |
| Participant's Mac | Electron, Recall detection, microphone AudioWorklet, Swift Core Audio helper | Native permissions and audio access must happen on the meeting device. |
| Trusted local service (same Mac or SSH-connected Mac mini) | FastAPI, active session, private SQLite history, bounded queues, validators, credentials | Own lifecycle, durability and provenance independently of the UI and model. |
| Cloud providers | Our Runpod Whisper endpoint, Runpod public Kimi K3, Firecrawl | GPU inference and external evidence retrieval without loading models on the participant's laptop. |

The speech endpoint is **our deployed handler, container and pinned model**. Kimi is
a **Runpod public endpoint**; we do not control its GPU allocation. Firecrawl is
external search, not a Runpod product. This distinction matters in the presentation.

The desktop-to-service connection is localhost, optionally forwarded over SSH.
The service-to-provider connections use HTTPS. This is a single-operator demo, not
a publicly hosted, authenticated multi-tenant service.

## 3. Follow one sentence

Example: “SQLite WAL works across servers on a network filesystem.”

1. **Capture.** The microphone arrives through an AudioWorklet. System playback
   arrives through Core Audio Tap, AVAudioConverter and a bounded child-process pipe.
   Both become 200 ms blocks of 16 kHz mono samples. The native route does not capture
   screen pixels. System playback can contain sounds from other applications.
2. **Chunk.** An energy gate keeps pre-roll, emits after 600 ms quiet or six seconds,
   and retains 1.2 seconds overlap for continuing speech. This is not a semantic VAD.
   The GPU worker additionally uses Whisper's VAD. An overlap-only tail is not sent twice.
3. **Transcribe.** PCM16 is submitted to our Runpod endpoint. Whisper large-v3 runs
   CUDA float16, English-only, beam size five, word timestamps, no prior-text prompt.
   Server checks model identity, revision, device, finite timings and output bounds.
4. **Extract.** K3 proposes a typed claim with transcript evidence IDs, category,
   verification question, conditions and stable topic key. It cannot assign database
   identity or directly execute tools. The server validates all references.
5. **Research.** A queue schedules Firecrawl search and readable page excerpts.
   K3 selects a verdict and server-generated quote IDs. A page title is not a quote.
   Source IDs, exact text, quote lengths and result shape are checked before publication.
6. **Present.** A card shows the original claim and the direct answer. Expanded detail
   contains implication, original speech, caveat and source links. Links resolve from
   server metadata, not a model-generated URL. Speech continues while research runs.

No stage needs the retired camera, segmentation, virtual objects or OBS code.

## 4. Understand the data model

These records form the active in-memory session and are checkpointed together into
normalized SQLite records. The server owns mutations; models only propose validated changes.

| Record | Important fields | Invariant |
| --- | --- | --- |
| Session | `id`, `epoch`, `state`, `created`, `last_seen` | One active meeting; an old epoch cannot submit new audio. |
| Audio chunk | source, sequence, interval, PCM digest | Same identity and bytes is an idempotent duplicate; changed bytes is a conflict. |
| Transcript segment | `id`, source, text, word-derived interval, `final`, echo reference | A source is an input channel, not a named person. Uncertain and duplicate rows remain internal. |
| Candidate | `id`, category, statement, question, evidence IDs, conditions, state | Evidence must point to known transcript; repeated claims link rather than multiply. |
| Research version | fingerprint, `research_revision`, attempts, state | Material scope changes invalidate older work and source mappings. |
| Finding | verdict, summary, recommendation, citations, caveats | Unsupported or malformed model output is not shown as a completed answer. |
| Source | ID, URL, title, retrieval time, excerpt hash | Citation provenance is tied to this job's fetched content. |

Four result verdicts are distinct: **supported**, **contradicted**, **conditional**,
and **insufficient**. “Insufficient” is an answer about evidence availability, not
a synonym for false or an infrastructure error. Searching, analyzing and failed
are processing states; withdrawn and deferred are conversational states.

The six categories are capability, limits/pricing, policy/licensing, performance
comparison, technical behavior and current status. Preferences, inaccessible private
facts, speculative predictions and unresolved ambiguous statements are excluded.

## 5. Why the system stays consistent

**Epoch = capture generation.** The desktop drains retained audio before changing
the epoch on Pause. If that drain fails, capture remains stopped and the old epoch
is preserved for explicit recovery. Once the generation advances, stale requests
cannot mutate the new generation. Resume starts fresh inputs; replay of retained
audio is a separate, explicit recovery operation, never a side effect of viewing
history. See [the audio lifecycle](DURABLE_AUDIO.md).

**End = close capture, finish accepted work.** The client closes inputs, flushes the
last partial utterance and waits for bounded uploads before End reaches the server.
The server enters `finishing` without changing the epoch. Accepted audio is transcribed,
remaining transcript batches are checked, and resulting research drains under the
existing request budgets and a 300-second processing deadline. Past meetings shows
finishing progress. Failures or exhausted budgets produce an explicit incomplete
status, not a successful completion. New capture remains closed throughout.

**Revision = claim generation.** A result publishes only when session, epoch,
candidate revision and candidate state still match. A correction or withdrawal
cannot be overwritten by a slow answer to the earlier premise.

**Bounded work, not infinite buffering.** Four audio chunks can wait, eight research
jobs can wait, and two research jobs run concurrently. Audio transcription is ordered
in one consumer to preserve word-boundary and echo handling. Independent claim
research is parallel. The detector is single-flight and coalesces transcript updates;
it does not launch a new LLM request for every token.

**Failure isolation.** A search failure changes one card, not the audio stream.
A detector failure keeps transcription and exposes Retry checks. Accepted audio
waits in an encrypted, quota-bounded desktop spool when transcription is behind.
Storage failure stops new intake without evicting durable pending chunks.

**Transactional domain state.** Related meeting, transcript, claim, research and
citation rows commit in one SQLite transaction. List counts are queried from the
relational records, not copied into a separate summary blob.
The desktop temporarily retains encrypted audio until the server atomically commits
the transcript outcome and receipt. Deployment and GPU warm-session journals are
separate operational state. See [durable audio](DURABLE_AUDIO.md) for ownership,
explicit recovery, retention, and the pre-durable capture-loss boundary.

| Failure | User-visible behavior | Recovery |
| --- | --- | --- |
| Permission/device lost | Microphone only / Meeting audio only / paused | Fix permission or device, then Pause/Resume. |
| Backend disconnected | Encrypted pending audio remains; bounded processing retries | Resume processing explicitly after restart; no automatic recording. |
| Detector timeout or invalid output | Checks paused; script continues | Retry checks explicitly. |
| Search/provider error | Check failed on one card | Retry research, bounded to three attempts per card. |
| No useful source | Insufficient evidence | Inspect context, retry if appropriate; do not infer falsehood. |
| End / detected Meet departure | Capture stops; accepted work finishes and is saved | Inspect finishing progress in Past meetings; start another meeting after completion. |
| Process exits during finishing | Committed results and durable desktop audio survive; finalization is incomplete | Explicitly resume retained audio or Finish pending checks; no automatic capture. |
| Stale answer after withdrawal | Result discarded | Nothing stale is published. |

## 6. Design trade-offs

- **Desktop rather than web:** reliable system-playback capture and native meeting
  detection were the hard requirements. A shareable web dashboard is not implemented.
- **Normalized SQLite rather than a distributed database:** one trusted operator needs
  reliable local history, not multi-tenant infrastructure. Returning to the list preserves
  transcripts, findings and view state; opening history never starts cloud work.
  Incomplete work requires explicit processing recovery, without microphone capture.
- **Strict schema rather than unrestricted tool use:** model output must fit known
  actions and references. Strict JSON prevents shape errors, not reasoning errors.
- **Exact quotation rather than invented citation:** provenance is validated, but
  a real quote can still be interpreted incorrectly. This is evidence assistance.
- **GPU prewarm rather than buffering a cold start:** the speech worker loads and
  consumes a synthetic inference at startup. A bounded warm lease avoids the first
  live request paying all initialization cost. Idle warm time is billed.
- **No named-speaker claim:** mixed system playback does not identify attendees.
  Diarization and verified name mapping are outside this version.
- **No perfect-ASR claim:** complete synthetic cases passed, but isolated 0.3-second
  cut-off tails still failed. The chunker avoids redundant tail-only submissions.

Read [Setup and operations](RUNNING.md) to run it, [Current validation](VALIDATION.md)
for evidence, and [the code guide](REVIEW_GUIDE.md) for implementation entry points.
