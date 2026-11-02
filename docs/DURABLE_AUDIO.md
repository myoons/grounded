# Durable audio ownership and recovery

## Purpose and boundary

Slow transcription or a network outage must not discard an audio chunk that the
desktop has acknowledged as durably saved. This is not a zero-loss microphone:
samples in the device, worklet, utterance chunker, IPC, or pending disk write have
not yet crossed that boundary. A process crash can lose that short, pre-durable
window. If local storage cannot keep up, capture stops visibly rather than silently
deleting previously accepted audio.

The participant Mac owns encrypted audio until the connected server confirms a
durable terminal transcription outcome. An HTTP upload acknowledgement means
only admission to processing. It is never permission to delete local audio.

## Data path

```text
Microphone / system playback
    -> bounded utterance chunk
    -> encrypted atomic file on participant Mac
    -> serial upload / receipt polling
    -> Runpod Whisper
    -> SQLite transaction: transcript snapshot + completed receipt
    -> confirmed receipt -> delete encrypted local chunk
```

There is one serial processing pump. It retains source-local order rather than
trying to parallelize overlapping speech windows. Claim extraction and research
remain independent of capture. Increasing GPU throughput is an optimization, not
a substitute for durable ownership.

## Local storage and privacy

Runtime location:
`~/Library/Application Support/Roomshift Research/audio-spool/`.

- Directory mode `0700`; files mode `0600`.
- Each immutable chunk contains metadata and PCM encrypted with AES-256-GCM and
  a fresh nonce. The application format identifier is authenticated as AAD.
- A random 256-bit data key is wrapped with Electron `safeStorage`; on macOS this
  relies on OS Keychain-backed protection. The wrapped key is stored as `key.enc`.
- If OS encryption is unavailable, the insecure `basic_text` backend is selected,
  or an existing key cannot be recovered, capture fails closed. There is no
  plaintext fallback. Encryption at rest does not protect against a compromised
  logged-in account or a running application that can decrypt the files.
- Writes use an exclusive temporary file, file `fsync`, atomic rename, and directory
  `fsync`. The desktop acknowledges durability only after those operations.
- Limits: 256 MiB of encrypted chunks, four hours of cumulative chunk duration
  including overlapping regions, and 128 MiB of filesystem reserve. The first
  exhausted limit stops new intake; no old pending chunks are evicted to make room.
- The renderer disk-write queue is bounded separately. A failed write remains in
  memory for an explicit retry while the app stays open; it is not called durable.

The capture confirmation explicitly discloses temporary encrypted audio storage
and uploads to the connected server and Runpod. Successfully completed chunks are
deleted after the durable receipt is confirmed. Failed or abandoned chunks have
**no silent age-based expiry**: they remain until explicit recovery or deletion,
subject to the fixed storage quota. This is an intentional retention trade-off,
not a claim that all temporary data disappears automatically. No PCM is written to
logs or committed to the repository. Providers have their own retention policies.

Unreadable ciphertext is preserved and blocks capture. The UI reports the condition;
it does not pretend to recover a lost key or silently erase corrupt files. Resolving
lost-key/corruption cases currently requires operator assistance. Deleting the
application data or Keychain material can make retained audio unrecoverable.

## Identity, receipts, and commit

The immutable logical identifier is `(meeting_id, epoch, source, sequence)`.
Content SHA-256 and capture timing bind retries to the original chunk. Reusing an
identifier with different audio or timing is rejected. Receipt status lookup also
checks the expected digest. Receipt rows have a foreign key to the meeting.

```text
Local:     captured -> writing -> retained -> uploading -> awaiting outcome
                                          ^ retryable failure |
                                          +-------------------+
           committed processed/no_speech -> local deletion
           permanent failure / retries exhausted -> retained, explicit recovery

Server:    missing -> pending -> processed | no_speech
                          \-> failed (retryability explicit)
```

The server stores receipt metadata, not durable PCM. It can therefore lose an
in-memory queued job on restart without losing ownership of the captured audio:
the desktop still holds its encrypted copy. Pending receipts are made retryable
after server restart. Completed receipts and the transcript update commit in the
same SQLite transaction. A failed transaction cannot produce a successful durable
acknowledgement. In-memory transcript/cursor changes are restored on commit failure.

This provides idempotent transcript publication, **not exactly-once GPU inference**.
An explicitly uncertain transcription is not a terminal success, even when some
words were returned. Its receipt fails and the encrypted chunk remains available
for bounded retry; partial words are not committed as a completed transcription.
A confident empty response still depends on the speech model's no-speech decision,
which can be wrong. Storage acknowledgement does not prove recognition accuracy.

A crash after inference but before the durable commit can require paid inference
again. Ambiguous HTTP outcomes are resolved by querying the same receipt identity;
they are not assigned a new sequence number.

## Lifecycle rules

- **End:** stop capture, flush the final partial utterance, finish local writes,
  drain retained chunks, then ask the server to finish extraction and research.
  A bounded wait can report incomplete processing; it does not delete the backlog.
- **Pause:** use the same audio drain barrier before changing the capture epoch.
  If draining fails, local capture stays stopped and the old epoch is preserved
  for explicit recovery. A new capture cannot start with unresolved local audio.
- **Restart:** enumerate retained chunks but do not upload them or activate devices.
  `Resume processing` explicitly authorizes the matching meeting's recovery without
  starting the microphone. After audio drains, pending checks are finalized.
- **No remaining audio:** an incomplete saved meeting can still resume pending
  extraction/research through `Finish pending checks`.
- **Delete audio:** explicitly cancel server processing before deleting retained
  local chunks. Saved transcript/findings remain. If cancellation cannot be
  confirmed, audio stays retained. Normal navigation is not a deletion command.
- **Sleep / renderer failure:** do not invalidate an epoch ahead of a possible tail
  flush. A disconnected durable session can be recovered explicitly; capture never
  silently restarts.

Authorization is bound to the existing meeting and capture epoch. Durable replay
does not disable identity, timing-interval, size, source, or hash checks. Original
capture age is checked before local persistence; authorized backlog may be older
when uploaded. Recovery is processing-only, not permission to send new live audio.

## Failure policy

| Failure | Result |
| --- | --- |
| Slow STT / server queue full | Keep local chunk; bounded retry/polling, no destructive RAM backlog |
| Network fails before admission | Retry the same identity |
| Response lost after commit | Query receipt; delete only after terminal durable status |
| Server restarts | Explicit session recovery; replay missing/failed chunks, deduplicate completed ones |
| Client restarts | Load encrypted metadata; no uploads until explicit recovery |
| Disk write fails / quota exhausted | Stop new capture, preserve durable files; expose unsaved in-memory tail |
| Keychain unavailable | Disable capture, never switch to plaintext |
| Ciphertext corrupted | Preserve and block capture; native confirmation can quarantine unreadable files without deleting them |
| Repeated provider failure | At most six pump attempts per explicit authorization; retain backlog |
| User cancels retained audio | Cancel processing, then remove local files with explicit loss warning |

Polling and retry waits are bounded. Retries use exponential backoff with jitter;
polling itself does not create a new inference job. Research budgets still apply.
An explicit audio recovery grants another bounded retry window; it can incur
additional inference charges and is never performed silently after exhaustion.

### Interrupted local writes and quarantine

An interrupted encrypted write may leave a `.pending-*` file. Startup authenticates
it with the existing key and restores a valid chunk to its identity filename. A
truncated, conflicting, or unauthenticated file remains untouched and blocks new
capture. Missing key material fails closed; the app never creates a replacement
key over retained data. A failure after rename is not acknowledged as durable;
retry verifies the existing identity and syncs the directory before acknowledgement.

The recovery panel's **Resolve unreadable audio** action asks for native confirmation
before moving unreadable files to encrypted `.quarantine-*` files. Processing is
paused during the move. Quarantine does not repair speech or mark its meeting
complete, does not upload isolated files, and does not remove them. Files retain
their bytes and count against the spool quota across restarts. Missing/unopenable
key material still requires operator recovery, not this action. There is no automatic
retention expiry or secure-erasure guarantee. Healthy pending audio is preserved.

## Reproducible validation

All automated audio fixtures are synthetic; the secure-storage unit adapter is
fake and does not claim to test a real Keychain. Production spool/controller and
server service paths are exercised, not a separate demonstration implementation.

```sh
cd desktop && npm test
cd .. && uv run pytest -q tests/test_durable_audio.py
```

Focused tests cover encryption/integrity and permissions, restart enumeration,
duplicate/conflicting identity, queue pressure, atomic receipt/transcript rollback,
no-speech completion, ambiguous network outcome, bounded failure retention,
cancellation, ordered source processing, final-tail flush, explicit recovery,
pending extraction/research with no audio, and processing-only authorization.

The headless Electron finalization smoke exercises the actual renderer with a
controlled bridge; it does not exercise a physical microphone, macOS Keychain,
Runpod billing, or real Meet audio. Hardware reinstall, real Keychain behavior,
sleep/reconnect and remote-audio validation remain release checks. No production
database is migrated merely by running the synthetic tests.
