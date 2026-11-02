"""Relational meeting schema and explicit DTO field definitions."""

MEETING = {
    "title": "TEXT",
    "created_at": "TEXT NOT NULL",
    "ended_at": "TEXT",
    "elapsed_ms": "INTEGER",
    "state": "TEXT NOT NULL CHECK(state IN ('capturing','paused','finishing','stopped'))",
    "epoch": "INTEGER",
    "language": "TEXT",
    "interrupted": "INTEGER",
    "cloud_detection": "INTEGER",
    "detection_consent_version": "TEXT",
    "research_enabled": "INTEGER",
    "research_consent_version": "TEXT",
    "error": "TEXT",
    "retention": "TEXT",
    "search_enabled": "INTEGER",
}
SEGMENT = {
    "source": "TEXT CHECK(source IN ('microphone','system'))",
    "text": "TEXT NOT NULL",
    "start_ms": "REAL",
    "end_ms": "REAL",
    "speech_end_ms": "REAL",
    "final": "INTEGER",
    "sequence": "INTEGER",
    "epoch": "INTEGER",
    "possible_echo_of": "TEXT",
    "continuation": "INTEGER",
    "finalized_at": "REAL",
    "processing_ms": "INTEGER",
    "queue_wait_ms": "INTEGER",
}
CLAIM = {"edited": "INTEGER", "research_attempts": "INTEGER"}
REVISION = {
    "state": "TEXT NOT NULL CHECK(state IN ('candidate','deferred','retracted','ignored'))",
    "category": "TEXT CHECK(category IN ('capability','limits_pricing','policy_licensing','performance_comparison','technical_behavior','current_status'))",
    "statement": "TEXT",
    "question": "TEXT",
    "topic_key": "TEXT",
    "research_key": "TEXT",
}
RESEARCH = {
    "state": "TEXT NOT NULL CHECK(state IN ('not_requested','pending','queued','searching','analyzing','ready','error','interrupted','budget_limit'))",
    "verdict": "TEXT CHECK(verdict IN ('supported','contradicted','conditional','insufficient'))",
    "summary": "TEXT",
    "recommendation": "TEXT",
    "query": "TEXT",
    "completed_at": "TEXT",
    "elapsed_ms": "INTEGER",
    "message": "TEXT",
    "comparison_basis": "TEXT",
}
SOURCE = {
    "url": "TEXT NOT NULL",
    "title": "TEXT",
    "retrieved_at": "TEXT",
    "content_hash": "TEXT",
    "excerpt_chars": "INTEGER",
    "kind": "TEXT",
}
EVIDENCE = {
    "source": "TEXT",
    "text": "TEXT",
    "start_ms": "REAL",
    "end_ms": "REAL",
}
RUNTIME = {
    "sources",
    "detector",
    "research",
    "queue_depth",
    "research_queue_depth",
    "capacity_policy",
}


def columns(fields):
    return ", ".join(f'"{name}" {kind}' for name, kind in fields.items())


def statements():
    definitions = [
        f"""CREATE TABLE meetings (id TEXT PRIMARY KEY, {columns(MEETING)},
            field_mask INTEGER NOT NULL, updated_at TEXT NOT NULL,
            finalization_state TEXT, finalization_message TEXT, finalization_mask INTEGER NOT NULL,
            runtime_json TEXT NOT NULL CHECK(json_valid(runtime_json)))""",
        "CREATE INDEX meetings_created ON meetings(created_at DESC, id DESC)",
        f"""CREATE TABLE transcript_segments (meeting_id TEXT NOT NULL, id TEXT NOT NULL,
            position INTEGER NOT NULL CHECK(position >= 0), {columns(SEGMENT)}, field_mask INTEGER NOT NULL,
            PRIMARY KEY(meeting_id,id), UNIQUE(meeting_id,position),
            FOREIGN KEY(meeting_id) REFERENCES meetings(id) ON DELETE CASCADE,
            FOREIGN KEY(meeting_id,possible_echo_of) REFERENCES transcript_segments(meeting_id,id)
                DEFERRABLE INITIALLY DEFERRED)""",
        f"""CREATE TABLE claims (meeting_id TEXT NOT NULL, id TEXT NOT NULL,
            position INTEGER NOT NULL CHECK(position >= 0), current_revision INTEGER NOT NULL CHECK(current_revision >= 0),
            revision_present INTEGER NOT NULL CHECK(revision_present IN (0,1)), {columns(CLAIM)}, field_mask INTEGER NOT NULL,
            PRIMARY KEY(meeting_id,id), UNIQUE(meeting_id,position),
            FOREIGN KEY(meeting_id) REFERENCES meetings(id) ON DELETE CASCADE,
            FOREIGN KEY(meeting_id,id,current_revision) REFERENCES claim_revisions(meeting_id,claim_id,revision)
                DEFERRABLE INITIALLY DEFERRED)""",
        f"""CREATE TABLE claim_revisions (meeting_id TEXT NOT NULL, claim_id TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK(revision >= 0), {columns(REVISION)}, field_mask INTEGER NOT NULL,
            reason TEXT, reason_present INTEGER NOT NULL, collection_mask INTEGER NOT NULL,
            PRIMARY KEY(meeting_id,claim_id,revision),
            FOREIGN KEY(meeting_id,claim_id) REFERENCES claims(meeting_id,id) ON DELETE CASCADE)""",
        """CREATE TABLE claim_attributes (meeting_id TEXT NOT NULL, claim_id TEXT NOT NULL,
            revision INTEGER NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('conditions','missing_information')),
            position INTEGER NOT NULL CHECK(position >= 0), value TEXT NOT NULL,
            PRIMARY KEY(meeting_id,claim_id,revision,kind,position),
            FOREIGN KEY(meeting_id,claim_id,revision) REFERENCES claim_revisions ON DELETE CASCADE)""",
        """CREATE TABLE claim_relations (meeting_id TEXT NOT NULL, claim_id TEXT NOT NULL,
            revision INTEGER NOT NULL, position INTEGER NOT NULL, related_id TEXT NOT NULL,
            PRIMARY KEY(meeting_id,claim_id,revision,position),
            FOREIGN KEY(meeting_id,claim_id,revision) REFERENCES claim_revisions ON DELETE CASCADE,
            FOREIGN KEY(meeting_id,related_id) REFERENCES claims(meeting_id,id) DEFERRABLE INITIALLY DEFERRED)""",
        """CREATE TABLE claim_evidence (meeting_id TEXT NOT NULL, claim_id TEXT NOT NULL,
            revision INTEGER NOT NULL, segment_id TEXT NOT NULL, position INTEGER NOT NULL,
            id_position INTEGER, evidence_present INTEGER NOT NULL, field_mask INTEGER NOT NULL,
            PRIMARY KEY(meeting_id,claim_id,revision,segment_id),
            FOREIGN KEY(meeting_id,claim_id,revision) REFERENCES claim_revisions ON DELETE CASCADE,
            FOREIGN KEY(meeting_id,segment_id) REFERENCES transcript_segments(meeting_id,id))""",
        "CREATE INDEX evidence_segment ON claim_evidence(meeting_id,segment_id)",
        f"""CREATE TABLE research_results (meeting_id TEXT NOT NULL, claim_id TEXT NOT NULL,
            revision INTEGER NOT NULL, {columns(RESEARCH)}, field_mask INTEGER NOT NULL,
            collection_mask INTEGER NOT NULL, telemetry_json TEXT NOT NULL CHECK(json_valid(telemetry_json)),
            PRIMARY KEY(meeting_id,claim_id,revision),
            FOREIGN KEY(meeting_id,claim_id,revision) REFERENCES claim_revisions ON DELETE CASCADE)""",
        f"""CREATE TABLE research_sources (meeting_id TEXT NOT NULL, claim_id TEXT NOT NULL,
            revision INTEGER NOT NULL, id TEXT NOT NULL, position INTEGER NOT NULL,
            {columns(SOURCE)}, field_mask INTEGER NOT NULL,
            PRIMARY KEY(meeting_id,claim_id,revision,id),
            FOREIGN KEY(meeting_id,claim_id,revision) REFERENCES research_results ON DELETE CASCADE)""",
        """CREATE TABLE research_citations (meeting_id TEXT NOT NULL, claim_id TEXT NOT NULL,
            revision INTEGER NOT NULL, position INTEGER NOT NULL, source_id TEXT NOT NULL, quote TEXT NOT NULL,
            PRIMARY KEY(meeting_id,claim_id,revision,position),
            FOREIGN KEY(meeting_id,claim_id,revision,source_id) REFERENCES research_sources(meeting_id,claim_id,revision,id)
                DEFERRABLE INITIALLY DEFERRED)""",
        """CREATE TABLE research_caveats (meeting_id TEXT NOT NULL, claim_id TEXT NOT NULL,
            revision INTEGER NOT NULL, position INTEGER NOT NULL, value TEXT NOT NULL,
            PRIMARY KEY(meeting_id,claim_id,revision,position),
            FOREIGN KEY(meeting_id,claim_id,revision) REFERENCES research_results ON DELETE CASCADE)""",
        "CREATE INDEX citation_source ON research_citations(meeting_id,claim_id,revision,source_id)",
        """CREATE TABLE meeting_presentation (meeting_id TEXT PRIMARY KEY,
            transcript_scroll INTEGER NOT NULL CHECK(transcript_scroll >= 0),
            cards_scroll INTEGER NOT NULL CHECK(cards_scroll >= 0),
            FOREIGN KEY(meeting_id) REFERENCES meetings(id) ON DELETE CASCADE)""",
        """CREATE TABLE expanded_claims (meeting_id TEXT NOT NULL, claim_id TEXT NOT NULL, position INTEGER NOT NULL,
            PRIMARY KEY(meeting_id,claim_id),
            FOREIGN KEY(meeting_id) REFERENCES meeting_presentation(meeting_id) ON DELETE CASCADE,
            FOREIGN KEY(meeting_id,claim_id) REFERENCES claims(meeting_id,id))""",
    ]
    return [
        statement + " STRICT" if statement.startswith("CREATE TABLE") else statement
        for statement in definitions
    ]
