# Persistent meeting history — Grounded 0.7

History is stored in normalized relational tables, not a meeting JSON blob.
See [the schema and migration design](HISTORY_SCHEMA.md) for the entity diagram,
keys, revision ownership, query strategy and deployment procedure. A failed write
retains unsaved changes in memory, but process termination can still lose them.
Reopening saved history is read-only, not a new capture session.

## User behavior

Detected meetings are live rooms. Past meetings are saved research sessions, ordered
newest first with date, title and check count. The two lists are independent: leaving
Google Meet removes the live room but never deletes its saved session.

Opening a past meeting reconstructs the same script and compact claim grid. Original
utterance navigation, exact quotations, source buttons, conditions, recommendations
and full-card expansion still work. The last expanded card and both scroll positions
are restored. Capture controls and cloud retry/dismiss controls are not enabled for
an archived session: reading history must not unexpectedly record or incur inference.

The Meetings button saves viewing state and detaches the stopped session. It does
not delete history. Multiple calls to the same Meet URL become separate research
sessions with distinct IDs and dates.

End closes audio collection but does not cancel accepted work. While remaining
transcription, extraction and research finish, the meeting remains in Past meetings
with `finishing` status. Opening it reads live progress without opening inputs.
Returning to the list does not detach or cancel a finishing session. The single
active-session restriction prevents starting a new meeting until finishing ends.
The final summary distinguishes complete from incomplete processing; complete
means the bounded pipeline drained, not that every statement was accurately checked.

## Storage and ownership

The connected FastAPI server owns `meetings.sqlite` under its private data directory
(by default `~/Library/Application Support/Roomshift/meetings.sqlite` on macOS).
When using an SSH tunnel, the database lives on the server Mac, not the participant
MacBook. No new external storage service is involved.

Typed meeting, transcript, claim/revision, evidence-link, research, source, citation
and presentation rows commit in one transaction using `synchronous=FULL`. The list
reads meeting columns and child counts, not a duplicate summary blob. Composite
foreign keys prevent cross-meeting and cross-revision links. A creation-time index
supports bounded 50-item pages. Unchanged rows are not rewritten at every checkpoint.
The database is mode 600. This is OS-user access control, not application-level
encryption; use normal device/storage encryption for the host.

Persisted: timestamps, title, transcript, echo references, candidates, research
versions/results, quote/source metadata and presentation state. Not persisted:
PCM buffers, raw recordings or provider credentials. Durable audio receipts are
stored separately and committed with transcript outcomes. Encrypted pending audio
lives on the participant Mac; see [audio ownership](DURABLE_AUDIO.md). Source pages
remain transient; saved quotations and metadata remain inspectable, while an external
website can independently change or disappear.

## Checkpoint and failure rules

- Checkpoint before starting capture, after finalized transcription, claim changes,
  research completion, review changes, Pause/End and graceful shutdown.
- Opening history never launches capture, detector or research tasks.
- An unfinished session recovered after a server crash is shown as interrupted;
  incomplete checks are marked interrupted, not falsely completed or automatically retried.
- Finalization status is stored on the meeting and read directly by the list. A restart cannot
  leave a saved row permanently showing `finishing`: without its runtime owner it
  is presented as stopped/incomplete. Completed results remain readable.
- Viewing state is debounced during scrolling and flushed before returning to the
  list. An abrupt process kill can lose the last unacknowledged viewing-state update.
- A failed transaction preserves the previous committed relational state. New results stay
  in memory and a compact save warning appears. Detach/new-session replacement is
  blocked until a required checkpoint succeeds; subsequent reads retry failed saves.
- A complete disk failure plus process termination can still lose uncommitted data.
  No system can guarantee recovery of a write that never committed; no such claim is made.
- The 30-minute stopped-session cleanup releases RAM only after saving. It no longer
  deletes the durable history. Server restart leaves the saved rows available.

## Retention and deployment

History is retained locally; no automatic deletion or retention expiry is introduced.
There is no per-meeting Delete UI in this release. The existing API DELETE operation
means release the stopped runtime session after saving, not erase durable records.
Do not include the database or journal files in Git, release ZIPs or public artifacts.
Back up the database with SQLite's backup API or while the server is stopped.

Meetings cleared before this version were never stored and cannot be reconstructed.
An in-memory meeting present at upgrade must be preserved separately before restarting
the old memory-only server. Never claim historical recovery for previously lost data.

## Validation

Tests cover actual SQLite close/reopen, API lifespan restart, transcript/result/source
round trips, source revision guards, presentation restoration, unknown-card rejection,
transaction failure rollback, failed-save detach blocking, automatic transcript
checkpointing, private file permissions, and pagination. The synthetic Electron flow
ends a meeting, returns to Past meetings, reopens all 16 cards, restores expanded
detail, follows its original utterance/source, and verifies no new capture/upload.

Deterministic finalization tests cover End during ASR, extraction and research,
repeated End, queue refill beyond eight items, budget/error/timeout outcomes,
history progress and interrupted restart. The End-time final-utterance flush and
Meet-departure path still require device-specific audio validation after installation;
synthetic tests are not a claim of new MacBook live verification.
