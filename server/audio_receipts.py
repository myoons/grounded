"""Persistent acknowledgement metadata; audio remains on the capturing device."""

SCHEMA = """CREATE TABLE audio_receipts (
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    epoch INTEGER NOT NULL CHECK(epoch >= 1),
    source TEXT NOT NULL CHECK(source IN ('microphone','system')),
    sequence INTEGER NOT NULL CHECK(sequence >= 0),
    sha256 TEXT NOT NULL CHECK(length(sha256)=64),
    start_ms REAL NOT NULL, end_ms REAL NOT NULL, speech_end_ms REAL NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending','processed','no_speech','failed')),
    PRIMARY KEY(meeting_id,epoch,source,sequence)
) STRICT"""


def identity(chunk):
    return tuple(chunk[name] for name in ("session_id", "epoch", "source", "sequence"))


def lookup(database, key):
    row = database.execute(
        "SELECT * FROM audio_receipts WHERE meeting_id=? AND epoch=? AND source=? AND sequence=?",
        key,
    ).fetchone()
    return dict(row) if row else None


def transition(database, key, state):
    database.execute(
        "UPDATE audio_receipts SET state=? WHERE meeting_id=? AND epoch=? AND source=? AND sequence=?",
        (state, *key),
    )
