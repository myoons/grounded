import copy
import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from server import audio_receipts
from server.history_schema import statements
from server.history_store import RelationalStore
from server.meeting_history import MeetingHistory


def fixture(identifier="meeting-a"):
    segment = {
        "id": "utterance-a",
        "source": "system",
        "text": "Free has a daily limit.",
        "start_ms": 0,
        "end_ms": 1000,
        "final": True,
    }
    return {
        "id": identifier,
        "title": "A synthetic meeting",
        "created_at": "2026-11-02T00:00:00+00:00",
        "ended_at": "2026-11-02T00:01:00+00:00",
        "elapsed_ms": 60000,
        "state": "stopped",
        "interrupted": False,
        "finalization": {"state": "complete"},
        "segments": [segment],
        "candidates": [
            {
                "id": "claim-a",
                "state": "candidate",
                "category": "limits_pricing",
                "statement": "Free allows 100 calls daily.",
                "question": "What is Free's daily limit?",
                "research_revision": 1,
                "conditions": ["Free plan"],
                "missing_information": [],
                "related_ids": [],
                "evidence_ids": [segment["id"]],
                "evidence": [
                    {
                        key: segment[key]
                        for key in ("id", "source", "text", "start_ms", "end_ms")
                    }
                ],
                "research": {
                    "state": "ready",
                    "verdict": "supported",
                    "summary": "Free permits 100 calls daily.",
                    "caveats": ["A synthetic fixture, not a real product."],
                    "sources": [
                        {
                            "id": "S1",
                            "url": "https://example.com/limits",
                            "title": "Limits",
                        }
                    ],
                    "citations": [
                        {"source_id": "S1", "quote": "Free permits 100 calls daily."}
                    ],
                },
            }
        ],
        "presentation": {
            "expanded": ["claim-a"],
            "transcript_scroll": 120,
            "cards_scroll": 40,
        },
    }


def legacy(path, snapshots):
    with closing(sqlite3.connect(path)) as database, database:
        database.execute(
            "CREATE TABLE meetings (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, payload TEXT NOT NULL, summary TEXT NOT NULL)"
        )
        database.execute(
            "CREATE INDEX meetings_created ON meetings(created_at DESC,id DESC)"
        )
        for snapshot in snapshots:
            database.execute(
                "INSERT INTO meetings VALUES (?,?,?,?,?)",
                (
                    snapshot["id"],
                    snapshot["created_at"],
                    snapshot["created_at"],
                    json.dumps(snapshot),
                    json.dumps({"id": snapshot["id"]}),
                ),
            )


class RelationalHistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.path = Path(self.directory.name) / "history.sqlite"
        self.history = MeetingHistory(self.path)
        self.snapshot = fixture()
        self.history.save(self.snapshot)

    def tearDown(self):
        self.history.close()
        self.directory.cleanup()

    def test_relational_roundtrip_no_domain_blobs_and_noop_has_zero_writes(self):
        self.assertEqual(self.history.store.load("meeting-a"), self.snapshot)
        before = self.history.database.total_changes
        self.history.save(copy.deepcopy(self.snapshot))
        self.assertEqual(self.history.database.total_changes, before)
        columns = {
            row["name"]
            for row in self.history.database.execute("PRAGMA table_info(meetings)")
        }
        self.assertNotIn("payload", columns)
        self.assertNotIn("summary", columns)
        self.assertEqual(
            self.history.database.execute("PRAGMA foreign_keys").fetchone()[0], 1
        )
        self.assertEqual(
            self.history.database.execute("PRAGMA foreign_key_check").fetchall(), []
        )

    def test_presentation_change_does_not_rewrite_domain_rows(self):
        database = self.history.database
        database.execute("CREATE TABLE writes (entity TEXT NOT NULL)")
        for table in (
            "transcript_segments",
            "claims",
            "claim_revisions",
            "research_results",
            "research_sources",
            "research_citations",
        ):
            for operation in ("INSERT", "UPDATE", "DELETE"):
                database.execute(
                    f"CREATE TRIGGER audit_{table}_{operation} AFTER {operation} ON {table} BEGIN INSERT INTO writes VALUES ('{table}'); END"
                )
        self.snapshot["presentation"]["cards_scroll"] = 500
        self.history.save(self.snapshot)
        self.assertEqual(database.execute("SELECT * FROM writes").fetchall(), [])

    def test_revisions_keep_old_results_and_same_source_ids_are_scoped(self):
        candidate = self.snapshot["candidates"][0]
        candidate["research_revision"] = 2
        candidate["statement"] = "Pro allows 1000 calls daily."
        candidate["conditions"] = ["Pro plan"]
        candidate["research"]["sources"][0]["url"] = "https://example.com/pro"
        candidate["research"]["citations"][0]["quote"] = "Pro permits 1000 calls daily."
        self.history.save(self.snapshot)
        rows = self.history.database.execute(
            "SELECT revision,quote FROM research_citations ORDER BY revision"
        ).fetchall()
        self.assertEqual(
            [tuple(row) for row in rows],
            [
                (1, "Free permits 100 calls daily."),
                (2, "Pro permits 1000 calls daily."),
            ],
        )
        self.assertEqual(self.history.store.load("meeting-a"), self.snapshot)
        candidate["statement"] = "Changed without revision"
        with self.assertRaises(ValueError):
            self.history.save(self.snapshot)
        self.assertEqual(
            self.history.store.load("meeting-a")["candidates"][0]["statement"],
            "Pro allows 1000 calls daily.",
        )

    def test_cross_meeting_links_and_unknown_sources_are_rejected_atomically(self):
        other = fixture("meeting-b")
        other["segments"][0]["id"] = "other-utterance"
        other["candidates"] = []
        other["presentation"]["expanded"] = []
        self.history.save(other)
        with self.assertRaises(sqlite3.IntegrityError), self.history.database:
            self.history.database.execute(
                "UPDATE claim_evidence SET segment_id='other-utterance' WHERE meeting_id='meeting-a'"
            )
        changed = copy.deepcopy(self.snapshot)
        changed["title"] = "Must roll back"
        changed["candidates"][0]["research"]["citations"][0]["source_id"] = "S404"
        with self.assertRaises(sqlite3.IntegrityError):
            self.history.save(changed)
        self.assertEqual(self.history.store.load("meeting-a"), self.snapshot)

    def test_list_uses_relational_counts_without_reading_transcript_content(self):
        statements = []
        self.history.database.set_trace_callback(statements.append)
        listing = self.history.list()
        self.history.database.set_trace_callback(None)
        self.assertEqual(listing["meetings"][0]["candidate_count"], 1)
        self.assertEqual(listing["meetings"][0]["transcript_count"], 1)
        self.assertEqual(len(statements), 1)
        self.assertNotIn("runtime_json", statements[0])
        self.assertIn("JOIN claim_revisions", statements[0])

    def test_unknown_fields_and_nonfinite_values_are_not_silently_lost(self):
        for change in (
            {"new_domain_field": "must handle"},
            {"elapsed_ms": float("nan")},
        ):
            with self.assertRaises(ValueError):
                self.history.save(self.snapshot | change)
            self.assertEqual(self.history.store.load("meeting-a"), self.snapshot)

    def test_transcript_and_claim_scope_require_explicit_identity_or_revision(self):
        changed = copy.deepcopy(self.snapshot)
        changed["segments"][0]["text"] = "Rewrite history"
        with self.assertRaises(ValueError):
            self.history.save(changed)
        changed = copy.deepcopy(self.snapshot)
        changed["candidates"][0]["conditions"] = ["Pro plan"]
        with self.assertRaises(ValueError):
            self.history.save(changed)
        changed = copy.deepcopy(self.snapshot)
        changed["candidates"] = []
        with self.assertRaises(ValueError):
            self.history.save(changed)
        self.assertEqual(self.history.store.load("meeting-a"), self.snapshot)

    def test_repeated_claim_links_new_utterance_without_new_research_revision(self):
        segment = {
            "id": "utterance-b",
            "source": "microphone",
            "text": "I repeat the same claim.",
            "final": True,
        }
        self.snapshot["segments"].append(segment)
        candidate = self.snapshot["candidates"][0]
        candidate["evidence_ids"].append(segment["id"])
        candidate["evidence"].append(
            {key: segment[key] for key in ("id", "source", "text")}
        )
        self.history.save(self.snapshot)
        self.assertEqual(self.history.store.load("meeting-a"), self.snapshot)
        self.assertEqual(
            self.history.database.execute(
                "SELECT count(*) FROM claim_revisions"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.history.database.execute(
                "SELECT count(*) FROM research_results"
            ).fetchone()[0],
            1,
        )

    def test_strict_columns_and_state_constraints_reject_invalid_rows(self):
        for column, value in (("epoch", "not an integer"), ("state", "invented")):
            with self.assertRaises(sqlite3.IntegrityError), self.history.database:
                self.history.database.execute(
                    f"UPDATE meetings SET {column}=?", (value,)
                )
        self.assertEqual(self.history.store.load("meeting-a"), self.snapshot)


class HistoryMigrationTests(unittest.TestCase):
    def test_precision_upgrade_failure_rolls_back_schema_and_version(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite"
            with closing(sqlite3.connect(path)) as database, database:
                database.row_factory = sqlite3.Row
                for statement in statements():
                    if statement.startswith("CREATE TABLE transcript_segments "):
                        for field in ("start_ms", "end_ms", "speech_end_ms"):
                            statement = statement.replace(
                                f'"{field}" REAL', f'"{field}" INTEGER'
                            )
                    database.execute(statement)
                RelationalStore(database).save(fixture())
                database.execute(audio_receipts.SCHEMA)
                database.execute(
                    "UPDATE claim_evidence SET segment_id='missing-utterance'"
                )
                database.execute("PRAGMA user_version=2")
            with self.assertRaises(ValueError):
                MeetingHistory(path)
            with closing(sqlite3.connect(path)) as database:
                self.assertEqual(
                    database.execute("PRAGMA user_version").fetchone()[0], 2
                )
                columns = {
                    row[1]: row[2]
                    for row in database.execute(
                        "PRAGMA table_info(transcript_segments)"
                    )
                }
                self.assertEqual(columns["speech_end_ms"], "INTEGER")
                self.assertEqual(
                    database.execute(
                        "SELECT count(*) FROM transcript_segments"
                    ).fetchone()[0],
                    1,
                )
                self.assertIsNone(
                    database.execute(
                        "SELECT name FROM sqlite_master WHERE name='transcript_segments_v3'"
                    ).fetchone()
                )

    def test_fractional_legacy_timestamps_are_preserved_exactly(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite"
            snapshot = fixture()
            snapshot["segments"][0].update(
                start_ms=0.125, end_ms=999.875, speech_end_ms=912.375
            )
            snapshot["candidates"][0]["evidence"][0].update(
                start_ms=0.125, end_ms=999.875
            )
            legacy(path, [snapshot])
            history = MeetingHistory(path)
            self.assertEqual(history.store.load("meeting-a"), snapshot)
            self.assertEqual(
                history.database.execute("PRAGMA foreign_key_check").fetchall(), []
            )
            history.close()

    def test_relational_precision_upgrade_preserves_links_receipts_and_accepts_fractions(
        self,
    ):
        for version in (1, 2):
            with self.subTest(version=version), TemporaryDirectory() as directory:
                path = Path(directory) / "history.sqlite"
                snapshot = fixture()
                with closing(sqlite3.connect(path)) as database, database:
                    database.row_factory = sqlite3.Row
                    for statement in statements():
                        if statement.startswith("CREATE TABLE transcript_segments "):
                            for field in ("start_ms", "end_ms", "speech_end_ms"):
                                statement = statement.replace(
                                    f'"{field}" REAL', f'"{field}" INTEGER'
                                )
                        database.execute(statement)
                    RelationalStore(database).save(snapshot)
                    if version == 2:
                        database.execute(audio_receipts.SCHEMA)
                        database.execute(
                            "INSERT INTO audio_receipts VALUES ('meeting-a',1,'system',0,?,0.125,999.875,912.375,'processed')",
                            ("a" * 64,),
                        )
                    database.execute(f"PRAGMA user_version={version}")
                history = MeetingHistory(path)
                self.assertEqual(history.store.load("meeting-a"), snapshot)
                self.assertEqual(
                    history.database.execute("PRAGMA user_version").fetchone()[0], 3
                )
                self.assertEqual(
                    history.database.execute("PRAGMA foreign_keys").fetchone()[0], 1
                )
                self.assertEqual(
                    history.database.execute("PRAGMA foreign_key_check").fetchall(), []
                )
                if version == 2:
                    receipt = history.receipt(("meeting-a", 1, "system", 0))
                    self.assertEqual(
                        (
                            receipt["start_ms"],
                            receipt["end_ms"],
                            receipt["speech_end_ms"],
                        ),
                        (0.125, 999.875, 912.375),
                    )
                snapshot["segments"].append(
                    {
                        "id": "fractional",
                        "text": "Fractional sample boundary",
                        "source": "system",
                        "start_ms": 1000.125,
                        "end_ms": 1999.875,
                        "speech_end_ms": 1912.375,
                        "final": True,
                    }
                )
                history.save(snapshot)
                history.close()
                history = MeetingHistory(path)
                self.assertEqual(history.store.load("meeting-a"), snapshot)
                history.close()

    def test_legacy_migration_preserves_current_data_and_is_idempotent(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite"
            snapshot = fixture()
            snapshot["candidates"][0]["research_revision"] = 7
            legacy(path, [snapshot])
            history = MeetingHistory(path)
            self.assertEqual(history.store.load("meeting-a"), snapshot)
            self.assertEqual(
                history.database.execute(
                    "SELECT revision FROM claim_revisions"
                ).fetchone()[0],
                7,
            )
            self.assertEqual(
                history.database.execute(
                    "SELECT count(*) FROM claim_revisions"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                history.database.execute("PRAGMA user_version").fetchone()[0], 3
            )
            history.close()
            history = MeetingHistory(path)
            self.assertEqual(history.store.load("meeting-a"), snapshot)
            self.assertEqual(
                history.database.execute("PRAGMA foreign_key_check").fetchall(), []
            )
            history.close()

    def test_unknown_legacy_field_rolls_back_entire_schema_and_all_meetings(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite"
            broken = fixture("meeting-b") | {"unrecognized": "never silently discard"}
            legacy(path, [fixture(), broken])
            with self.assertRaises(ValueError):
                MeetingHistory(path)
            with closing(sqlite3.connect(path)) as database:
                self.assertEqual(
                    database.execute("PRAGMA user_version").fetchone()[0], 0
                )
                self.assertEqual(
                    database.execute("SELECT count(*) FROM meetings").fetchone()[0], 2
                )
                self.assertEqual(
                    json.loads(
                        database.execute(
                            "SELECT payload FROM meetings WHERE id='meeting-b'"
                        ).fetchone()[0]
                    ),
                    broken,
                )
                self.assertIsNone(
                    database.execute(
                        "SELECT name FROM sqlite_master WHERE name='claim_revisions'"
                    ).fetchone()
                )

    def test_broken_legacy_foreign_reference_rolls_back(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite"
            snapshot = fixture()
            snapshot["candidates"][0]["related_ids"] = ["missing-claim"]
            legacy(path, [snapshot])
            with self.assertRaises(ValueError):
                MeetingHistory(path)
            with closing(sqlite3.connect(path)) as database:
                self.assertEqual(
                    database.execute("PRAGMA user_version").fetchone()[0], 0
                )
                self.assertIsNotNone(
                    database.execute("SELECT payload FROM meetings").fetchone()
                )
