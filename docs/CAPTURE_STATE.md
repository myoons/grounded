# Capture state ownership

This is a focused lifecycle controller, not a global store framework rewrite.
The server, operating-system resources and renderer view have different owners.

| Owner | Authoritative state |
| --- | --- |
| `MeetingService` | Active meeting, processing queues, current claims and durable checkpoints |
| Electron main | Selected meeting, capture authorization, native audio process, encrypted spool |
| Renderer `CaptureLifecycle` | Current start, pause or end transition and its identity token |
| Renderer view | Polled server snapshot, navigation and presentation preferences |

The renderer no longer independently toggles `starting`, `pausing` and `ending`
flags. An exclusive operation token owns the transition. A late completion can
finish only its own token, not release a newer operation. Saved-history opening
is navigation, not microphone startup.

Status labels derive from the capture transition, server processing state, actual
input presence and archive state. A server still accepting retained audio does not
mean a microphone is listening. An interrupted End keeps the backlog and does not
pretend to have stopped the server before upload completion.

## Startup interruption

Meet departure and application Quit can arrive while permission or backend startup
is pending, even if the UI button is disabled. End preempts startup; startup checks
its token after asynchronous boundaries and cannot open new inputs afterward.
End waits for the pending start so an already-created backend session can be
identified and finalized. Late microphone tracks are closed without upload.

Main-process shutdown also waits while meeting selection is pending. If the meeting
disappears after the backend creates a session, its identity is returned for cleanup
rather than thrown away in an exception. An unacknowledged timeout preserves the
window; it is not permission to discard never-durable audio.

## Validation and limits

- `capture-lifecycle.test.mjs`: exclusivity, stale-token completion and derived labels.
- `startup-end-ui-smoke.mjs`: actual renderer with delayed backend startup and delayed
  microphone permission; two stop acknowledgements, zero uploads, late tracks closed.
- `meeting-shutdown.test.mjs`: actual main-function code under controlled dependencies;
  pending Quit acknowledgement, preserved window after timeout and late session identity.
- `finalization-ui-smoke.mjs`: failed End/Pause, retained backlog, explicit recovery,
  history navigation and no capture during recovery.

These tests control dependencies and are not physical-device or real OS permission
acceptance. The backend remains a single-process owner; multi-worker deployment
would require a different concurrency contract. Renderer caches and navigation
values still exist: the claim is explicit ownership of capture transitions, not
that every application value has been moved into one global object.
