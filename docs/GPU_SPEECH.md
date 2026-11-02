# English GPU transcription

## Data path

AudioWorklet PCM16 (16 kHz, mono) -> trusted local API -> Runpod queue ->
Whisper large-v3 CUDA worker -> validated word timestamps -> meeting transcript.

## Recorded endpoint demonstration

The custom Runpod worker processed synthetic English audio saying:

> The export feature is available only on the enterprise plan if the administrator enables it.

The [recorded validated response](evaluations/speech/recorded-response.json) contains
the recognized words and timestamps, the same reconstructed sentence,
`uncertain: false`, 1,388.73 ms worker inference and 2,584.08 ms client round trip.
This is a real-provider observation from the audio-boundary experiment on November 2,
2026, not a mocked response or a latency guarantee. Audio was synthesized with macOS
Samantha at 185 words/minute; its PCM SHA256 is included in the result.

The model was `Systran/faster-whisper-large-v3`, pinned to revision
`edaa852ec7e145841d8ffdb056a99866b5f0a478`. The response shown is the validated
application-adapter result with reconstructed text and timing metadata, not the raw
Runpod job envelope. Credentials, endpoint identifiers and audio bytes are omitted.

The request contract is an input object containing `language: "en"` and base64
mono 16 kHz PCM16 audio in `pcm`. To exercise the endpoint with generated audio,
follow [setup](RUNNING.md), then run `scripts.prepare_meeting_fixtures` and
`scripts.evaluate_chunk_boundary` as Python modules. The latter makes a real GPU
request and validates the retained complete utterance; it incurs provider charges.

The application has no local fallback. Retired local small-model code has been
removed; earlier baseline measurements remain historical evidence. English is fixed
at both API and worker boundaries.

## Deployment

The private worker repository contains only the speech worker and build workflow.
Its image is published in the existing public `ghcr.io/myoons/roomshift-worker`
package. No application database, meeting audio, credential file or personal image
is in the build context. Dependencies are hash-locked; model revision is pinned.
The CUDA runtime uses a managed Python 3.12.13 environment, installed with pinned uv
using its [official Docker integration](https://docs.astral.sh/uv/guides/integration/docker/).
Python 3.10 could not resolve the patched dnspython/filelock versions, so the runtime
and lockfile target were upgraded together rather than forcing incompatible packages.
Deploy only a verified image digest using `scripts.runpod_speech create --image ...`.
The deployment journal is outside the repository and refuses duplicate creation.

- Maximum one GPU worker; minimum zero outside a bounded warm session.
- Worker startup loads the model and consumes a synthetic inference to initialize
  CUDA before accepting requests; `speech_ready` means configuration is present,
  not that a GPU has finished starting. Check endpoint health before capture.
- `scripts.runpod_warm_session --seconds 3600` keeps speech warm for a bounded
  session and restores its previous minimum on exit. Up to three hours is allowed
  for an active session; idle time is billed. K3 uses a public API, not a dedicated worker.
- If the warm-up process is killed abruptly, review its private journal and run
  `--restore`; do not start another lease or assume idle billing has stopped.
- Known jobs are cancelled best-effort on failure/stop. The desktop durable spool uses bounded retries; a crash after inference but before commit can cause duplicate inference billing.
- Per-job execution timeout is 20 seconds; TTL is 60 seconds. Client deadline is
  25 seconds. Cold workers should be warmed to reduce latency; retained audio protects against transient delays.

## Privacy and integrity

The current desktop uses `audio_consent_version: runpod-audio-v2`, disclosing
encrypted temporary local retention. The server accepts legacy v1 clients only for
compatibility; those clients do not gain durable replay guarantees.
The worker accepts bounded inline PCM, never user-supplied download URLs. Audio is
decoded in memory, not written to disk. The application does not log transcripts
or audio. Runpod still processes inputs and retains results under its policies;
in-memory audio handling is not a cloud zero-retention claim. The resulting meeting
transcript and findings are retained locally in private SQLite, as described in MEETING_HISTORY.md.
This worker's in-memory handling is distinct from the desktop's encrypted pending
audio files; see [durable audio](DURABLE_AUDIO.md).

The server verifies model revision, CUDA device, English language, finite word
times, word count, and output shape. Unsupported or malformed outputs never become
confirmed transcript. GPU time and round-trip time are recorded separately.

## Evaluation

`python -m scripts.prepare_meeting_fixtures` creates synthetic English input.
`python -m scripts.evaluate_speech_noise` exercises complete speech, non-speech and
artificially cut-off tails twice each. `python -m scripts.evaluate_chunk_boundary`
checks the production chunker against the real GPU endpoint.
Artifacts are outside Git. Synthetic success does not establish native MacBook
capture, real accent/noise accuracy, meeting usability, or speaker identification.

References: [Runpod execution policies](https://docs.runpod.io/serverless/endpoints/send-requests),
[faster-whisper](https://github.com/SYSTRAN/faster-whisper).
