"""Transactional relational meeting history; never stores PCM or credentials."""

import json
import os
import sqlite3
from pathlib import Path

from server import audio_receipts
from server.history_schema import statements
from server.history_store import RelationalStore


class MeetingHistory:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        path.chmod(0o600)
        self.database = sqlite3.connect(path, timeout=2, check_same_thread=False)
        self.database.row_factory = sqlite3.Row
        self.database.execute("PRAGMA foreign_keys=ON")
        self.database.execute("PRAGMA synchronous=FULL")
        self.store = RelationalStore(self.database)
        try:
            self._migrate()
            with self.database:
                self.database.execute(
                    "UPDATE audio_receipts SET state='failed' WHERE state='pending'"
                )
        except BaseException:
            self.database.close()
            raise

    def _migrate(self):
        version = self.database.execute("PRAGMA user_version").fetchone()[0]
        if version == 3:
            return
        if version in (1, 2):
            self._migrate_segment_precision(version)
            return
        if version != 0:
            raise ValueError("Unsupported meeting history schema version")
        with self.database:
            self.database.execute("BEGIN IMMEDIATE")
            existing = self.database.execute("PRAGMA table_info(meetings)").fetchall()
            if existing and {row["name"] for row in existing} != {
                "id",
                "created_at",
                "updated_at",
                "payload",
                "summary",
            }:
                raise ValueError("Unrecognized legacy meeting schema")
            if existing:
                self.database.execute("ALTER TABLE meetings RENAME TO legacy_meetings")
                self.database.execute("DROP INDEX IF EXISTS meetings_created")
            for statement in statements():
                self.database.execute(statement)
            if existing:
                for row in self.database.execute(
                    "SELECT * FROM legacy_meetings"
                ).fetchall():
                    snapshot = json.loads(row["payload"])
                    if (
                        snapshot["id"] != row["id"]
                        or snapshot["created_at"] != row["created_at"]
                    ):
                        raise ValueError("Legacy meeting identity mismatch")
                    self.store.save(snapshot)
                    self.database.execute(
                        "UPDATE meetings SET updated_at=? WHERE id=?",
                        (row["updated_at"], row["id"]),
                    )
                self.database.execute("DROP TABLE legacy_meetings")
            if self.database.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("Meeting migration failed referential integrity")
            self.database.execute(audio_receipts.SCHEMA)
            self.database.execute("PRAGMA user_version=3")

    def _migrate_segment_precision(self, version):
        self.database.execute("PRAGMA foreign_keys=OFF")
        try:
            with self.database:
                self.database.execute("BEGIN IMMEDIATE")
                statement = next(
                    item
                    for item in statements()
                    if item.startswith("CREATE TABLE transcript_segments ")
                )
                self.database.execute(
                    statement.replace(
                        "CREATE TABLE transcript_segments ",
                        "CREATE TABLE transcript_segments_v3 ",
                        1,
                    )
                )
                self.database.execute(
                    "INSERT INTO transcript_segments_v3 SELECT * FROM transcript_segments"
                )
                self.database.execute("DROP TABLE transcript_segments")
                self.database.execute(
                    "ALTER TABLE transcript_segments_v3 RENAME TO transcript_segments"
                )
                if version == 1:
                    self.database.execute(audio_receipts.SCHEMA)
                if self.database.execute("PRAGMA foreign_key_check").fetchone():
                    raise ValueError(
                        "Meeting precision migration failed referential integrity"
                    )
                self.database.execute("PRAGMA user_version=3")
        finally:
            self.database.execute("PRAGMA foreign_keys=ON")

    def save(self, snapshot):
        with self.database:
            self.store.save(snapshot)

    def save_audio_result(self, snapshot, chunk, state):
        with self.database:
            self.store.save(snapshot)
            audio_receipts.transition(
                self.database, audio_receipts.identity(chunk), state
            )

    def receipt(self, key):
        return audio_receipts.lookup(self.database, key)

    def unfinished_audio_before(self, identifier, epoch, source, sequence):
        return (
            self.database.execute(
                "SELECT 1 FROM audio_receipts WHERE meeting_id=? AND epoch=? AND source=? "
                "AND sequence<? AND state IN ('pending','failed') LIMIT 1",
                (identifier, epoch, source, sequence),
            ).fetchone()
            is not None
        )

    def has_unfinished_audio(self, identifier):
        return (
            self.database.execute(
                "SELECT 1 FROM audio_receipts WHERE meeting_id=? AND state IN ('pending','failed') LIMIT 1",
                (identifier,),
            ).fetchone()
            is not None
        )

    def receive_audio(self, chunk):
        key = audio_receipts.identity(chunk)
        with self.database:
            self.database.execute(
                "INSERT INTO audio_receipts VALUES (?,?,?,?,?,?,?,?,'pending') "
                "ON CONFLICT(meeting_id,epoch,source,sequence) DO UPDATE SET state='pending'",
                (
                    *key,
                    chunk["sha256"],
                    chunk["start_ms"],
                    chunk["end_ms"],
                    chunk["speech_end_ms"],
                ),
            )

    def fail_audio(self, chunk):
        with self.database:
            audio_receipts.transition(
                self.database, audio_receipts.identity(chunk), "failed"
            )

    def load(self, identifier):
        snapshot = self.store.load(identifier)
        if snapshot is None:
            return None
        snapshot["archived"] = True
        snapshot["interrupted"] = (
            snapshot.get("interrupted", False) or snapshot["state"] != "stopped"
        )
        if snapshot["state"] != "stopped":
            snapshot["finalization"] = {
                "state": "incomplete",
                "message": "Processing was interrupted by a service restart. Saved results are available; audio is not replayed.",
            }
        snapshot["state"] = "stopped"
        snapshot["error"] = None
        for candidate in snapshot["candidates"]:
            if candidate.get("research", {}).get("state") in (
                "pending",
                "queued",
                "searching",
                "analyzing",
            ):
                candidate["research"] = {
                    "state": "interrupted",
                    "message": "The session ended before this check completed.",
                }
        return snapshot

    def list(self, offset=0, limit=50):
        rows = self.database.execute(
            """SELECT meetings.id,title,created_at,ended_at,elapsed_ms,state,interrupted,
                finalization_state,finalization_message,finalization_mask,
                (SELECT count(*) FROM transcript_segments WHERE meeting_id=meetings.id) AS transcript_count,
                (SELECT count(*) FROM claims JOIN claim_revisions ON
                    claim_revisions.meeting_id=claims.meeting_id AND claim_id=claims.id AND revision=current_revision
                    WHERE claims.meeting_id=meetings.id AND claim_revisions.state!='ignored') AS candidate_count
                FROM meetings ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?""",
            (limit + 1, offset),
        ).fetchall()
        items = []
        for row in rows[:limit]:
            item = {
                key: row[key]
                for key in dict(row)
                if not key.startswith("finalization_")
            }
            item["finalization"] = {
                name: row[f"finalization_{name}"]
                for index, name in enumerate(("state", "message"))
                if row["finalization_mask"] & (1 << index)
            }
            item["interrupted"] = bool(item["interrupted"])
            items.append(item)
        for item in items:
            if item.get("state", "stopped") != "stopped":
                item.update(
                    state="stopped",
                    interrupted=True,
                    finalization={
                        "state": "incomplete",
                        "message": "Processing was interrupted. Saved results remain available.",
                    },
                )
        return {
            "meetings": items,
            "next_offset": offset + limit if len(rows) > limit else None,
        }

    def close(self):
        self.database.close()
