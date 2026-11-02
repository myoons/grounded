"""Explicit relational mapping; JSON is restricted to runtime diagnostics."""

import json
from datetime import UTC, datetime

from server import history_schema as schema


def validate_keys(value, allowed, entity):
    unknown = set(value) - set(allowed)
    if unknown:
        raise ValueError(f"Unsupported {entity} fields: {', '.join(sorted(unknown))}")


def encode(value, fields):
    return {name: value.get(name) for name in fields} | {
        "field_mask": sum(
            1 << index for index, name in enumerate(fields) if name in value
        )
    }


def decode(row, fields):
    return {
        name: bool(row[name])
        if name
        in {
            "interrupted",
            "cloud_detection",
            "research_enabled",
            "search_enabled",
            "final",
            "continuation",
            "edited",
        }
        and row[name] is not None
        else row[name]
        for index, name in enumerate(fields)
        if row["field_mask"] & (1 << index)
    }


def json_text(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True)


class RelationalStore:
    def __init__(self, database):
        self.database = database

    def upsert(self, table, keys, values):
        record = keys | values
        names = list(record)
        changes = list(values)
        assignments = ",".join(f'"{name}"=excluded."{name}"' for name in changes)
        distinct = " OR ".join(f'"{name}" IS NOT excluded."{name}"' for name in changes)
        self.database.execute(
            f"INSERT INTO {table} ({','.join(chr(34) + name + chr(34) for name in names)}) "
            f"VALUES ({','.join('?' for _ in names)}) ON CONFLICT ({','.join(keys)}) "
            + (
                f"DO UPDATE SET {assignments} WHERE {distinct}"
                if changes
                else "DO NOTHING"
            ),
            list(record.values()),
        )

    def rows(self, table, keys, order=None):
        return self.database.execute(
            f"SELECT * FROM {table} WHERE "
            + " AND ".join(f"{key}=?" for key in keys)
            + (f" ORDER BY {order}" if order else ""),
            list(keys.values()),
        ).fetchall()

    def sync(self, table, scope, records, identity):
        existing = {
            tuple(row[name] for name in identity) for row in self.rows(table, scope)
        }
        desired = set()
        for record in records:
            key = tuple(record[name] for name in identity)
            if key in desired:
                raise ValueError(f"Duplicate {table} identity")
            desired.add(key)
            keys = scope | {name: record[name] for name in identity}
            self.upsert(
                table,
                keys,
                {name: value for name, value in record.items() if name not in identity},
            )
        for key in existing - desired:
            keys = scope | dict(zip(identity, key, strict=True))
            self.database.execute(
                f"DELETE FROM {table} WHERE "
                + " AND ".join(f"{name}=?" for name in keys),
                list(keys.values()),
            )

    def save(self, snapshot):
        json_text(snapshot)
        changes_before = self.database.total_changes
        validate_keys(
            snapshot,
            set(schema.MEETING)
            | schema.RUNTIME
            | {
                "id",
                "segments",
                "candidates",
                "finalization",
                "presentation",
                "archived",
                "storage_error",
            },
            "meeting",
        )
        identifier = snapshot["id"]
        scope = {"meeting_id": identifier}
        finalization = snapshot.get("finalization", {})
        validate_keys(finalization, {"state", "message"}, "finalization")
        current = self.rows("meetings", {"id": identifier})
        values = encode(snapshot, schema.MEETING) | {
            "finalization_state": finalization.get("state"),
            "finalization_message": finalization.get("message"),
            "finalization_mask": sum(
                1 << index
                for index, name in enumerate(("state", "message"))
                if name in finalization
            ),
            "runtime_json": json_text(
                {name: snapshot[name] for name in schema.RUNTIME if name in snapshot}
            ),
        }
        if current and current[0]["created_at"] != snapshot["created_at"]:
            raise ValueError("Meeting creation time is immutable")
        self.upsert(
            "meetings",
            {"id": identifier},
            values
            | {
                "updated_at": current[0]["updated_at"]
                if current
                else datetime.now(UTC).isoformat(),
            },
        )
        self._segments(scope, snapshot["segments"])
        identifiers = [candidate["id"] for candidate in snapshot["candidates"]]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Duplicate claim identity")
        existing = {row["id"] for row in self.rows("claims", scope)}
        if existing - set(identifiers):
            raise ValueError("Persisted claims cannot be silently removed")
        for position, candidate in enumerate(snapshot["candidates"]):
            self._claim(scope, candidate, position)
        presentation = snapshot.get("presentation")
        if presentation is not None:
            validate_keys(
                presentation,
                {"expanded", "transcript_scroll", "cards_scroll"},
                "presentation",
            )
            self.upsert(
                "meeting_presentation",
                scope,
                {
                    "transcript_scroll": presentation.get("transcript_scroll", 0),
                    "cards_scroll": presentation.get("cards_scroll", 0),
                },
            )
            self.sync(
                "expanded_claims",
                scope,
                [
                    {"claim_id": claim, "position": position}
                    for position, claim in enumerate(presentation.get("expanded", []))
                ],
                ["claim_id"],
            )

        if current and self.database.total_changes != changes_before:
            self.database.execute(
                "UPDATE meetings SET updated_at=? WHERE id=?",
                (datetime.now(UTC).isoformat(), identifier),
            )

    def _segments(self, scope, segments):
        identifiers = [item["id"] for item in segments]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Duplicate transcript identity")
        existing = {row["id"]: row for row in self.rows("transcript_segments", scope)}
        if set(existing) - set(identifiers):
            raise ValueError("Persisted transcript cannot be silently removed")
        for position, segment in enumerate(segments):
            validate_keys(segment, set(schema.SEGMENT) | {"id"}, "transcript")
            values = encode(segment, schema.SEGMENT) | {"position": position}
            if segment["id"] in existing and any(
                existing[segment["id"]][name] != value for name, value in values.items()
            ):
                raise ValueError("Saved transcript segments are immutable")
            self.upsert(
                "transcript_segments",
                scope | {"id": segment["id"]},
                values,
            )

    def _claim(self, scope, candidate, position):
        collections = (
            "conditions",
            "missing_information",
            "related_ids",
            "evidence_ids",
            "evidence",
        )
        validate_keys(
            candidate,
            set(schema.CLAIM)
            | set(schema.REVISION)
            | set(collections)
            | {"id", "reason", "research", "research_revision"},
            "claim",
        )
        revision = candidate.get("research_revision", 0)
        claim_scope = scope | {"id": candidate["id"]}
        previous = self.rows("claims", claim_scope)
        if previous and revision < previous[0]["current_revision"]:
            raise ValueError("Claim revision cannot move backwards")
        self.upsert(
            "claims",
            claim_scope,
            encode(candidate, schema.CLAIM)
            | {
                "position": position,
                "current_revision": revision,
                "revision_present": int("research_revision" in candidate),
            },
        )
        revision_scope = scope | {"claim_id": candidate["id"], "revision": revision}
        immutable = encode(candidate, schema.REVISION)
        previous_revision = self.rows("claim_revisions", revision_scope)
        if previous_revision and any(
            previous_revision[0][name] != value for name, value in immutable.items()
        ):
            raise ValueError("Substantive claim changes require a new revision")
        attributes = [
            {"kind": kind, "position": index, "value": value}
            for kind in ("conditions", "missing_information")
            for index, value in enumerate(candidate.get(kind, []))
        ]
        if previous_revision:
            old = [
                {name: row[name] for name in ("kind", "position", "value")}
                for row in self.rows(
                    "claim_attributes", revision_scope, "kind,position"
                )
            ]
            if old != sorted(
                attributes, key=lambda item: (item["kind"], item["position"])
            ):
                raise ValueError("Claim scope changes require a new revision")
        self.upsert(
            "claim_revisions",
            revision_scope,
            immutable
            | {
                "reason": candidate.get("reason"),
                "reason_present": int("reason" in candidate),
                "collection_mask": sum(
                    1 << index
                    for index, name in enumerate(collections)
                    if name in candidate
                ),
            },
        )
        self.sync("claim_attributes", revision_scope, attributes, ["kind", "position"])
        self.sync(
            "claim_relations",
            revision_scope,
            [
                {"position": index, "related_id": identifier}
                for index, identifier in enumerate(candidate.get("related_ids", []))
            ],
            ["position"],
        )
        evidence_ids = candidate.get("evidence_ids", [])
        evidence = candidate.get("evidence", [])
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("Duplicate evidence reference")
        evidence_by_id = {item["id"]: item for item in evidence}
        if len(evidence_by_id) != len(evidence):
            raise ValueError("Duplicate evidence record")
        evidence_order = list(
            dict.fromkeys([*(item["id"] for item in evidence), *evidence_ids])
        )
        records = []
        for index, identifier in enumerate(evidence_order):
            item = evidence_by_id.get(identifier, {})
            validate_keys(item, set(schema.EVIDENCE) | {"id"}, "evidence")
            segments = self.rows("transcript_segments", scope | {"id": identifier})
            if not segments or any(
                segments[0][name] != value
                for name, value in item.items()
                if name != "id"
            ):
                raise ValueError(
                    "Evidence must reference the matching stored utterance"
                )
            records.append(
                {"field_mask": encode(item, schema.EVIDENCE)["field_mask"]}
                | {
                    "segment_id": identifier,
                    "position": index,
                    "id_position": evidence_ids.index(identifier)
                    if identifier in evidence_ids
                    else None,
                    "evidence_present": int(identifier in evidence_by_id),
                }
            )
        self.sync("claim_evidence", revision_scope, records, ["segment_id"])
        if "research" in candidate:
            self._research(revision_scope, candidate["research"])

    def _research(self, scope, research):
        validate_keys(
            research,
            set(schema.RESEARCH)
            | {"sources", "citations", "caveats", "usage", "timings"},
            "research",
        )
        self.upsert(
            "research_results",
            scope,
            encode(research, schema.RESEARCH)
            | {
                "collection_mask": sum(
                    1 << index
                    for index, name in enumerate(("sources", "citations", "caveats"))
                    if name in research
                ),
                "telemetry_json": json_text(
                    {
                        name: research[name]
                        for name in ("usage", "timings")
                        if name in research
                    }
                ),
            },
        )
        citations = research.get("citations", [])
        for citation in citations:
            validate_keys(citation, {"source_id", "quote"}, "citation")
        source_records = []
        for position, source in enumerate(research.get("sources", [])):
            validate_keys(source, set(schema.SOURCE) | {"id"}, "source")
            source_records.append(
                encode(source, schema.SOURCE)
                | {"id": source["id"], "position": position}
            )
        self.sync("research_sources", scope, source_records, ["id"])
        self.sync(
            "research_citations",
            scope,
            [
                {"position": index, **citation}
                for index, citation in enumerate(citations)
            ],
            ["position"],
        )
        self.sync(
            "research_caveats",
            scope,
            [
                {"position": index, "value": value}
                for index, value in enumerate(research.get("caveats", []))
            ],
            ["position"],
        )

    def load(self, identifier):
        rows = self.rows("meetings", {"id": identifier})
        if not rows:
            return None
        row = rows[0]
        snapshot = {
            "id": identifier,
            **decode(row, schema.MEETING),
            **json.loads(row["runtime_json"]),
        }
        snapshot["finalization"] = {
            name: row[f"finalization_{name}"]
            for index, name in enumerate(("state", "message"))
            if row["finalization_mask"] & (1 << index)
        }
        scope = {"meeting_id": identifier}
        snapshot["segments"] = [
            {"id": item["id"], **decode(item, schema.SEGMENT)}
            for item in self.rows("transcript_segments", scope, "position")
        ]
        snapshot["candidates"] = [
            self._load_claim(scope, claim)
            for claim in self.rows("claims", scope, "position")
        ]
        presentation = self.rows("meeting_presentation", scope)
        if presentation:
            snapshot["presentation"] = {
                "transcript_scroll": presentation[0]["transcript_scroll"],
                "cards_scroll": presentation[0]["cards_scroll"],
                "expanded": [
                    item["claim_id"]
                    for item in self.rows("expanded_claims", scope, "position")
                ],
            }
        return snapshot

    def _load_claim(self, scope, claim):
        revision_scope = scope | {
            "claim_id": claim["id"],
            "revision": claim["current_revision"],
        }
        revision = self.rows("claim_revisions", revision_scope)[0]
        result = {
            "id": claim["id"],
            **decode(claim, schema.CLAIM),
            **decode(revision, schema.REVISION),
        }
        if claim["revision_present"]:
            result["research_revision"] = claim["current_revision"]
        if revision["reason_present"]:
            result["reason"] = revision["reason"]
        for index, name in enumerate(
            (
                "conditions",
                "missing_information",
                "related_ids",
                "evidence_ids",
                "evidence",
            )
        ):
            if revision["collection_mask"] & (1 << index):
                result[name] = []
        for attribute in self.rows("claim_attributes", revision_scope, "kind,position"):
            result[attribute["kind"]].append(attribute["value"])
        if "related_ids" in result:
            result["related_ids"] = [
                item["related_id"]
                for item in self.rows("claim_relations", revision_scope, "position")
            ]
        links = self.database.execute(
            """SELECT claim_evidence.*,transcript_segments.source,transcript_segments.text,
                transcript_segments.start_ms,transcript_segments.end_ms FROM claim_evidence
                JOIN transcript_segments ON transcript_segments.meeting_id=claim_evidence.meeting_id
                AND transcript_segments.id=claim_evidence.segment_id
                WHERE claim_evidence.meeting_id=? AND claim_id=? AND revision=? ORDER BY claim_evidence.position""",
            list(revision_scope.values()),
        ).fetchall()
        if "evidence_ids" in result:
            result["evidence_ids"] = [
                item["segment_id"]
                for item in sorted(
                    (link for link in links if link["id_position"] is not None),
                    key=lambda item: item["id_position"],
                )
            ]
        if "evidence" in result:
            result["evidence"] = [
                {
                    "id": item["segment_id"],
                    **decode(item, schema.EVIDENCE),
                }
                for item in links
                if item["evidence_present"]
            ]
        research = self.rows("research_results", revision_scope)
        if research:
            result["research"] = self._load_research(revision_scope, research[0])
        return result

    def _load_research(self, scope, row):
        research = decode(row, schema.RESEARCH) | json.loads(row["telemetry_json"])
        if row["collection_mask"] & 1:
            research["sources"] = [
                {"id": item["id"], **decode(item, schema.SOURCE)}
                for item in self.rows("research_sources", scope, "position")
            ]
        if row["collection_mask"] & 2:
            research["citations"] = [
                {name: item[name] for name in ("source_id", "quote")}
                for item in self.rows("research_citations", scope, "position")
            ]
        if row["collection_mask"] & 4:
            research["caveats"] = [
                item["value"]
                for item in self.rows("research_caveats", scope, "position")
            ]
        return research
