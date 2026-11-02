import asyncio
import copy
import hashlib
import json
import re
import sqlite3
import time
from collections import OrderedDict
from datetime import UTC, datetime
from difflib import SequenceMatcher
from uuid import uuid4

import httpx

from server.capacity_policy import CapacityPolicy, meeting_policy
from server.meeting_detector import MeetingDetector, build_context
from server.meeting_gpu_speech import RunpodTranscriber
from server.meeting_research import MeetingResearcher
from server.observability import EventLog, error_cause


class MeetingError(Exception):
    def __init__(self, code, message, status=409):
        self.code, self.message, self.status = code, message, status


class DetectionContractError(ValueError):
    pass


class ClaimCapacityError(DetectionContractError):
    pass


def normalized(value):
    return re.sub(r"[\W_]+", "", value.casefold())


class MeetingService:
    def __init__(
        self,
        speech=None,
        detector=None,
        researcher=None,
        history=None,
        events=None,
        capacity=None,
    ):
        self.capacity = capacity or CapacityPolicy.from_environment()
        self.events = events or EventLog()
        self.history = history
        self.storage_error = None
        self.speech = speech or RunpodTranscriber()
        self.detector = detector or MeetingDetector()
        self.researcher = researcher or MeetingResearcher()
        self.research_queue = asyncio.Queue(maxsize=8)
        self.research_active = {}
        self.current = None
        self.queue = asyncio.Queue(maxsize=4)
        self.changed = asyncio.Event()
        self.tasks = []
        self.lock = asyncio.Lock()
        self.watchdog = None
        self.finalizer = None
        self.finalization_timeout = 300
        self.audio_pending = set()

    def _event(self, stage, status, session=None, chunk=None, **fields):
        session = session or self.current or {}
        identity = {"session_id": session.get("id"), "epoch": session.get("epoch")}
        if chunk:
            identity.update(
                {
                    key: chunk.get(key)
                    for key in ("session_id", "epoch", "source", "sequence")
                }
            )
        self.events.emit(
            stage=stage,
            status=status,
            **identity,
            queue_depth=self.queue.qsize(),
            research_queue_depth=self.research_queue.qsize(),
            **fields,
        )

    def capabilities(self):
        return {
            "speech_ready": self.speech.ready(),
            "detector_ready": self.detector.ready(),
            "audio_destination": "Runpod GPU via the connected server; Whisper large-v3, English only",
            "audio_consent_version": "runpod-audio-v2",
            "detection_consent_version": "runpod-kimi-k3-v1",
            "text_destination": "Runpod Public Kimi K3 API, only when cloud detection is enabled",
            "retention": "Meeting transcripts and findings are saved privately on the connected server. The desktop retains encrypted pending audio until durable acknowledgement or explicit deletion. The server does not store original audio. Provider retention policies also apply.",
            "storage_error": self.storage_error,
            "search_enabled": self.researcher.ready(),
            "research_consent_version": "firecrawl-kimi-v1",
            "search_destination": "Firecrawl receives verification queries and reads public sources; Runpod Kimi analyzes the retrieved excerpts.",
            "existing_session": {
                "id": self.current["id"],
                "state": self.current["state"],
            }
            if self.current
            else None,
        }

    async def start(self, command):
        async with self.lock:
            if self.current and self.current["state"] in (
                "capturing",
                "paused",
                "finishing",
            ):
                raise MeetingError(
                    "SESSION_ACTIVE",
                    "End the current session and wait for its remaining checks to finish before starting another.",
                )
            if not self.speech.ready():
                raise MeetingError(
                    "SPEECH_UNAVAILABLE",
                    "Configure and warm the Runpod speech worker on the connected server.",
                    503,
                )
            if command.cloud_detection and not self.detector.ready():
                raise MeetingError(
                    "DETECTOR_UNAVAILABLE", "Runpod detection is not configured.", 503
                )
            if command.research_enabled and not self.researcher.ready():
                raise MeetingError(
                    "SEARCH_UNAVAILABLE", "External search is not configured.", 503
                )
            await self._halt()
            self._persist(required=True)
            now = time.monotonic()
            self.current = {
                "id": str(uuid4()),
                "title": command.title,
                "created_at": datetime.now(UTC).isoformat(),
                "ended_at": None,
                "interrupted": False,
                "finalization": {"state": "not_started"},
                "presentation": {
                    "expanded": [],
                    "transcript_scroll": 0,
                    "cards_scroll": 0,
                },
                "epoch": 1,
                "state": "capturing",
                "language": command.language,
                "cloud_detection": command.cloud_detection,
                "detection_consent_version": command.detection_consent_version,
                "research_enabled": command.research_enabled,
                "research_consent_version": command.research_consent_version,
                "capacity_policy": self.capacity.model_dump(),
                "research": {"requests": 0, "limit": self.capacity.research_requests},
                "created": now,
                "last_seen": now,
                "stopped_at": None,
                "revision": 0,
                "segments": [],
                "candidates": [],
                "receipts": OrderedDict(),
                "sources": {
                    source: {
                        "sequence": -1,
                        "last_word_ms": -1,
                        "state": "waiting",
                        "gaps": 0,
                    }
                    for source in ("microphone", "system")
                },
                "detector": {
                    "state": "waiting" if command.cloud_detection else "disabled",
                    "requests": 0,
                    "tokens": 0,
                    "latency_ms": None,
                    "blocked": False,
                    "processed_revision": 0,
                },
                "error": None,
            }
            try:
                self._persist(required=True)
            except MeetingError:
                self.current = None
                raise
            self._launch()
            self._event("session", "started")
            if self.watchdog is None:
                self.watchdog = asyncio.create_task(self._watch())
            return self.view(self.current["id"])

    def session(self, identifier):
        if not self.current or self.current["id"] != identifier:
            raise MeetingError(
                "SESSION_NOT_FOUND", "This meeting session is no longer available.", 404
            )
        return self.current

    def view(self, identifier):
        if not self.current or self.current["id"] != identifier:
            archived = self.history.load(identifier) if self.history else None
            if archived is not None:
                return archived
            self.session(identifier)
        session = self.session(identifier)
        session["last_seen"] = time.monotonic()
        if self.storage_error:
            self._persist()
        return self._snapshot(session) | {
            "archived": False,
            "storage_error": self.storage_error,
        }

    def _snapshot(self, session):
        return {
            key: session[key]
            for key in (
                "id",
                "title",
                "created_at",
                "ended_at",
                "interrupted",
                "finalization",
                "presentation",
                "epoch",
                "state",
                "language",
                "cloud_detection",
                "detection_consent_version",
                "research_enabled",
                "research_consent_version",
                "research",
                "capacity_policy",
                "segments",
                "candidates",
                "sources",
                "detector",
                "error",
            )
        } | {
            "queue_depth": self.queue.qsize(),
            "elapsed_ms": session.get(
                "duration_ms", round((time.monotonic() - session["created"]) * 1000)
            ),
            "retention": "Saved transcript and findings on the server; encrypted pending audio on the desktop until acknowledgement or explicit deletion",
            "search_enabled": session["research_enabled"],
            "research_queue_depth": self.research_queue.qsize(),
        }

    def _persist(self, required=False):
        if not self.current or not self.history:
            return
        try:
            self.history.save(self._snapshot(self.current))
            self.storage_error = None
        except (sqlite3.Error, OSError, ValueError):
            self._event("storage", "failed", cause="save_failed")
            self.storage_error = (
                "Meeting could not be saved. Keep this window open and retry."
            )
            if required:
                raise MeetingError(
                    "HISTORY_UNAVAILABLE", self.storage_error, 503
                ) from None

    def history_list(self, offset=0):
        page = (
            self.history.list(offset)
            if self.history
            else {"meetings": [], "next_offset": None}
        )
        if self.current:
            for item in page["meetings"]:
                if item["id"] == self.current["id"]:
                    item.update(
                        state=self.current["state"],
                        finalization=self.current["finalization"],
                        interrupted=self.current["interrupted"],
                    )
        return page

    def update_presentation(self, identifier, command):
        snapshot = self.view(identifier)
        known = {candidate["id"] for candidate in snapshot["candidates"]}
        if not set(command.expanded) <= known:
            raise MeetingError("INVALID_PRESENTATION", "Unknown card reference.", 422)
        if self.current and self.current["id"] == identifier:
            self.current["presentation"] = command.model_dump()
            self._persist(required=True)
        elif self.history:
            snapshot["presentation"] = command.model_dump()
            try:
                self.history.save(snapshot)
            except (sqlite3.Error, OSError):
                raise MeetingError(
                    "HISTORY_UNAVAILABLE",
                    "Could not save the meeting view. Retry before leaving.",
                    503,
                ) from None
        return {"saved": True}

    def accept(
        self,
        identifier,
        epoch,
        source,
        sequence,
        start_ms,
        end_ms,
        speech_end_ms,
        age_ms,
        pcm,
        durable=False,
    ):
        session = self.session(identifier)
        if not durable and any(
            state.get("processing_only") for state in session["sources"].values()
        ):
            raise MeetingError(
                "PROCESSING_ONLY",
                "Recovered meetings accept only explicitly retained durable audio.",
            )
        if session["error"] and not durable:
            raise MeetingError(
                "PIPELINE_INTERRUPTED",
                "Pause and reconnect the interrupted meeting pipeline.",
                503,
            )
        if session["state"] != "capturing" or epoch != session["epoch"]:
            raise MeetingError(
                "STALE_EPOCH",
                "Capture is paused or this audio belongs to an older session epoch.",
            )
        if (
            source not in session["sources"]
            or sequence < 0
            or not 0
            <= start_ms
            < end_ms
            <= meeting_policy(session).max_duration_seconds * 1000
        ):
            raise MeetingError(
                "INVALID_AUDIO", "Invalid source or audio interval.", 422
            )
        if (
            not start_ms <= speech_end_ms <= end_ms
            or age_ms < 0
            or (not durable and age_ms > 15000)
        ):
            raise MeetingError(
                "EXPIRED_AUDIO", "Audio is too old or has invalid timing.", 422
            )
        if (
            not 3200 <= len(pcm) <= 224000
            or len(pcm) % 2
            or abs((end_ms - start_ms) - len(pcm) / 32) > 2
        ):
            raise MeetingError(
                "INVALID_PCM", "Use bounded 16 kHz mono PCM16 audio.", 422
            )
        key = (epoch, source, sequence)
        digest = hashlib.sha256(pcm).hexdigest()
        metadata = (start_ms, end_ms, speech_end_ms, digest)
        previous = None
        if durable:
            if not self.history:
                raise MeetingError(
                    "HISTORY_UNAVAILABLE",
                    "Durable audio requires persistent history.",
                    503,
                )
            previous = self.history.receipt((identifier, *key))
            if previous:
                if (
                    tuple(
                        previous[name]
                        for name in ("start_ms", "end_ms", "speech_end_ms", "sha256")
                    )
                    != metadata
                ):
                    raise MeetingError(
                        "CHUNK_CONFLICT",
                        "A chunk identifier was reused with different audio.",
                    )
                if previous["state"] != "failed":
                    return {
                        "accepted": True,
                        "duplicate": True,
                        "state": previous["state"],
                    }
        if not durable and key in session["receipts"]:
            if session["receipts"][key] != metadata:
                raise MeetingError(
                    "CHUNK_CONFLICT",
                    "A chunk identifier was reused with different audio.",
                )
            return {"accepted": True, "duplicate": True}
        if len(session["segments"]) >= meeting_policy(session).max_segments:
            raise MeetingError(
                "AUDIO_CAPACITY_REACHED",
                "This meeting reached its transcript capacity. Pending audio is retained; retrying this meeting cannot increase its capacity.",
                409,
            )
        state = session["sources"][source]
        if durable and self.history.unfinished_audio_before(
            identifier, epoch, source, sequence
        ):
            raise MeetingError(
                "BACKPRESSURE",
                "Complete earlier audio from this input before sending the next chunk.",
                429,
            )
        if sequence <= state["sequence"] and not (durable and previous):
            raise MeetingError("OUT_OF_ORDER", "Audio chunk arrived out of order.")
        if self.queue.full():
            raise MeetingError(
                "BACKPRESSURE",
                "Transcription is behind; pause capture rather than buffering more audio.",
                429,
            )
        chunk = {
            "session_id": identifier,
            "epoch": epoch,
            "source": source,
            "sequence": sequence,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "speech_end_ms": speech_end_ms,
            "age_ms": age_ms,
            "received": time.monotonic(),
            "pcm": pcm,
            "durable": durable,
            "sha256": digest,
        }
        if durable:
            try:
                self.history.receive_audio(chunk)
            except (sqlite3.Error, OSError):
                self._event("storage", "failed", chunk=chunk, cause="save_failed")
                raise MeetingError(
                    "HISTORY_UNAVAILABLE",
                    "Audio receipt could not be saved; retain and retry.",
                    503,
                ) from None
        state["gaps"] += max(0, sequence - state["sequence"] - 1)
        state.update(sequence=sequence, state="queued")
        session["last_seen"] = time.monotonic()
        session["receipts"][key] = metadata
        while len(session["receipts"]) > 512:
            session["receipts"].popitem(last=False)
        self.queue.put_nowait(chunk)
        self._event("audio", "admitted", chunk=chunk)
        if durable:
            self.audio_pending.add((identifier, *key))
            state["durable_audio"] = True
        return {"accepted": True, "duplicate": False, "state": "pending"}

    def audio_status(self, identifier, epoch, source, sequence, digest):
        if not self.history:
            raise MeetingError(
                "HISTORY_UNAVAILABLE", "Audio receipts are unavailable.", 503
            )
        receipt = self.history.receipt((identifier, epoch, source, sequence))
        if receipt is None:
            if not self.history.store.load(identifier):
                raise MeetingError("SESSION_NOT_FOUND", "Meeting does not exist.", 404)
            return {"status": "missing", "retryable": True}
        if receipt["sha256"] != digest:
            raise MeetingError(
                "CHUNK_CONFLICT", "Audio digest does not match the receipt."
            )
        key = (identifier, epoch, source, sequence)
        if receipt["state"] == "pending" and key not in self.audio_pending:
            try:
                self.history.fail_audio(
                    dict(
                        zip(
                            ("session_id", "epoch", "source", "sequence"),
                            key,
                            strict=True,
                        )
                    )
                )
            except (sqlite3.Error, OSError):
                raise MeetingError(
                    "HISTORY_UNAVAILABLE", "Could not reconcile audio receipt.", 503
                ) from None
            receipt["state"] = "failed"
        if receipt["state"] == "failed":
            session = (
                self.current
                if self.current and self.current["id"] == identifier
                else self.history.store.load(identifier)
            )
            if (
                session
                and len(session["segments"]) >= meeting_policy(session).max_segments
            ):
                return {
                    "status": "failed",
                    "retryable": False,
                    "code": "AUDIO_CAPACITY_REACHED",
                }
        return {"status": receipt["state"], "retryable": receipt["state"] == "failed"}

    async def cancel_processing(self, identifier):
        self.events.emit(
            stage="session", status="cancel_requested", session_id=identifier
        )
        async with self.lock:
            if self.current and self.current["id"] == identifier:
                session = self.current
                session["state"] = "stopped"
                session["epoch"] += 1
                session["stopped_at"] = time.monotonic()
                session["ended_at"] = (
                    session["ended_at"] or datetime.now(UTC).isoformat()
                )
                session["finalization"] = {
                    "state": "cancelled",
                    "message": "Remaining audio processing was explicitly cancelled.",
                }
                if self.finalizer:
                    self.finalizer.cancel()
                    await asyncio.gather(self.finalizer, return_exceptions=True)
                    self.finalizer = None
                await self._halt()
                self._persist(required=True)
                return self.view(identifier)
            snapshot = self.history.store.load(identifier) if self.history else None
            if snapshot is None:
                raise MeetingError("SESSION_NOT_FOUND", "Meeting does not exist.", 404)
            snapshot.update(
                state="stopped",
                epoch=snapshot["epoch"] + 1,
                finalization={
                    "state": "cancelled",
                    "message": "Remaining audio processing was explicitly cancelled.",
                },
            )
            try:
                self.history.save(snapshot)
            except (sqlite3.Error, OSError):
                raise MeetingError(
                    "HISTORY_UNAVAILABLE",
                    "Cancellation could not be saved. Retain local audio.",
                    503,
                ) from None
            return self.view(identifier)

    async def resume_processing(self, identifier):
        self.events.emit(
            stage="session", status="resume_requested", session_id=identifier
        )
        async with self.lock:
            if (
                self.current
                and self.current["id"] != identifier
                and self.current["state"] != "stopped"
            ):
                raise MeetingError(
                    "SESSION_ACTIVE",
                    "Finish the current meeting before recovering audio.",
                )
            if self.current and self.current["id"] == identifier:
                session = self.current
                if session["state"] == "capturing":
                    if (
                        session["error"]
                        or not self.tasks
                        or any(task.done() for task in self.tasks)
                    ):
                        await self._halt()
                        session["error"] = None
                        self._persist(required=True)
                        self._launch()
                    return self.view(identifier)
                if (
                    session["state"] not in ("stopped", "paused")
                    or session["finalization"].get("state") != "incomplete"
                ):
                    raise MeetingError(
                        "SESSION_CLOSED",
                        "Only interrupted processing can be recovered.",
                    )
            else:
                session = self.history.store.load(identifier) if self.history else None
                if not session:
                    raise MeetingError(
                        "SESSION_NOT_FOUND", "Meeting does not exist.", 404
                    )
                session["restored_segment_ids"] = {
                    segment["id"] for segment in session["segments"]
                }
                if (
                    session["state"] in ("stopped", "paused")
                    and session.get("finalization", {}).get("state") != "incomplete"
                ):
                    raise MeetingError(
                        "SESSION_CLOSED", "This meeting is not recoverable."
                    )
            await self._halt()
            self._persist(required=True)
            meeting_policy(session)
            now = time.monotonic()
            elapsed_ms = session.get(
                "elapsed_ms",
                session.get(
                    "duration_ms", round((now - session.get("created", now)) * 1000)
                ),
            )
            session.update(
                state="capturing",
                error=None,
                interrupted=True,
                created=now - elapsed_ms / 1000,
                last_seen=now,
                stopped_at=None,
                receipts=OrderedDict(),
                revision=len(session["segments"]),
                finalization={"state": "pending"},
            )
            session.pop("duration_ms", None)
            for candidate in session["candidates"]:
                if candidate.get("research", {}).get("state") in (
                    "queued",
                    "searching",
                    "analyzing",
                ):
                    candidate["research"] = {
                        "state": "interrupted",
                        "message": "Explicit recovery requeues unfinished research.",
                    }
            if (
                session["detector"]["requests"]
                < meeting_policy(session).detection_requests
            ):
                session["detector"].update(blocked=False, state="waiting")
                session["detector"].pop("error", None)
            for source in session["sources"].values():
                source.setdefault("last_word_ms", -1)
                source.setdefault("sequence", -1)
                source["processing_only"] = True
            self.current = session
            self._persist(required=True)
            self._launch()
            if self.watchdog is None:
                self.watchdog = asyncio.create_task(self._watch())
            return self.view(identifier)

    async def action(self, identifier, action, finalization_error=None):
        async with self.lock:
            session = self.session(identifier)
            self._event("session", "action_requested", session, cause=action)
            if action == "stop":
                if session["state"] in ("finishing", "stopped"):
                    return self.view(identifier)
                was_paused = session["state"] == "paused"
                session["state"] = "finishing"
                session["ended_at"] = datetime.now(UTC).isoformat()
                session["duration_ms"] = round(
                    (time.monotonic() - session["created"]) * 1000
                )
                session["finalization"] = {"state": "pending"}
                if finalization_error:
                    session["finalization"]["message"] = finalization_error[:400]
                if was_paused:
                    self._launch()
                self.changed.set()
                self._persist()
                self.finalizer = asyncio.create_task(self._finish(session))
                return self.view(identifier)
            if session["state"] in ("finishing", "stopped"):
                raise MeetingError(
                    "SESSION_CLOSED",
                    "This meeting is no longer accepting capture or changes.",
                )
            if action == "retry_detection":
                if (
                    session["detector"]["requests"]
                    >= meeting_policy(session).detection_requests
                ):
                    raise MeetingError(
                        "DETECTION_BUDGET",
                        "Detection budget reached. End this meeting and start a new session.",
                    )
                if session["state"] != "capturing" or not session["cloud_detection"]:
                    raise MeetingError(
                        "NOT_CAPTURING", "Resume a cloud-enabled session first."
                    )
                session["detector"].update(blocked=False, state="waiting")
                session["detector"].pop("error", None)
                self.changed.set()
                return self.view(identifier)
            if action == "resume":
                if session["state"] != "paused":
                    raise MeetingError("NOT_PAUSED", "Only paused sessions can resume.")
                if (
                    len(session["segments"]) >= meeting_policy(session).max_segments
                    or time.monotonic() - session["created"]
                    >= meeting_policy(session).max_duration_seconds
                ):
                    raise MeetingError(
                        "SESSION_LIMIT", "End this meeting and start a new session."
                    )
                session["state"] = "capturing"
                session["error"] = None
                session["last_seen"] = time.monotonic()
                self._launch()
            else:
                session["state"] = "paused"
                session["epoch"] += 1
                session["stopped_at"] = None
                await self._halt()
                for state in session["sources"].values():
                    state.update(
                        sequence=-1, state=session["state"], last_chunk_end_ms=-1
                    )
                if session["cloud_detection"]:
                    session["detector"]["state"] = session["state"]
            self._persist()
            return self.view(identifier)

    async def clear(self, identifier):
        async with self.lock:
            session = self.session(identifier)
            if session["state"] == "finishing":
                raise MeetingError(
                    "SESSION_FINISHING",
                    "Remaining checks are finishing. Wait before leaving this meeting.",
                )
            if session["state"] != "stopped":
                session["ended_at"] = datetime.now(UTC).isoformat()
                session["duration_ms"] = round(
                    (time.monotonic() - session["created"]) * 1000
                )
            session["state"] = "stopped"
            session["epoch"] += 1
            await self._halt()
            self._persist(required=True)
            self.current = None

    def review(self, identifier, candidate_id, command):
        session = self.session(identifier)
        if session["state"] in ("finishing", "stopped"):
            raise MeetingError(
                "SESSION_CLOSED", "This meeting no longer accepts card changes."
            )
        candidate = next(
            (item for item in session["candidates"] if item["id"] == candidate_id), None
        )
        if not candidate:
            raise MeetingError("CANDIDATE_NOT_FOUND", "Candidate not found.", 404)
        if command.action == "ignore":
            candidate["state"] = "ignored"
            self._refresh_research(session, candidate)
        elif command.action == "edit":
            if not command.question:
                raise MeetingError("QUESTION_REQUIRED", "A question is required.", 422)
            candidate.update(
                question=command.question,
                edited=True,
                state="candidate",
                conditions=[],
                missing_information=[],
            )
            self._refresh_research(session, candidate)
        elif command.action == "retry_research":
            if not session["research_enabled"] or session["state"] != "capturing":
                raise MeetingError(
                    "RESEARCH_DISABLED", "Resume a research-enabled session first."
                )
            if (
                candidate["state"] != "candidate"
                or candidate.get("research_attempts", 0) >= 3
            ):
                raise MeetingError(
                    "RESEARCH_LIMIT",
                    "This candidate cannot be retried; clarify the question or start a new session.",
                )
            research = candidate.get("research", {})
            if research.get("state") not in ("error", "interrupted") and not (
                research.get("state") == "ready"
                and research.get("verdict") == "insufficient"
            ):
                raise MeetingError(
                    "RESEARCH_ACTIVE", "Research is already pending or complete."
                )
            candidate["research"] = {"state": "pending"}
            candidate["research_revision"] += 1
            self._schedule_research(session)
        self._persist()
        return candidate

    async def _finish(self, session):
        self._event("session", "finishing", session)
        finish_started = time.monotonic()
        reason = session["finalization"].get("message")
        try:
            async with asyncio.timeout(self.finalization_timeout):
                await self.queue.join()
                self.changed.set()
                while (
                    session["cloud_detection"]
                    and session["detector"]["processed_revision"] < session["revision"]
                    and not session["detector"]["blocked"]
                ):
                    if any(task.done() for task in self.tasks):
                        raise RuntimeError("Processing worker stopped")
                    await asyncio.sleep(0.05)
                self._schedule_research(session)
                await self.research_queue.join()
        except asyncio.CancelledError:
            raise
        except (TimeoutError, RuntimeError):
            reason = "Finalization could not finish within its processing limit. Unfinished work was not replayed."
        if self.current is not session or session["state"] != "finishing":
            return
        if not reason and (
            session["error"]
            or (self.history and self.history.has_unfinished_audio(session["id"]))
            or session["detector"]["blocked"]
            or any(not item["final"] for item in session["segments"])
            or any(
                item.get("research", {}).get("state")
                in ("error", "budget_limit", "interrupted")
                for item in session["candidates"]
                if item["state"] == "candidate"
            )
        ):
            reason = "Some audio or checks could not be completed. Saved results remain available; inspect incomplete cards and transcript."
        async with self.lock:
            if self.current is not session or session["state"] != "finishing":
                return
            session["state"] = "stopped"
            session["stopped_at"] = time.monotonic()
            session["finalization"] = (
                {"state": "incomplete", "message": reason}
                if reason
                else {"state": "complete"}
            )
            await self._halt()
            for source in session["sources"].values():
                if source["state"] != "error":
                    source["state"] = "stopped"
            self._persist()

            self._event(
                "session",
                "incomplete" if reason else "complete",
                session,
                duration_ms=round((time.monotonic() - finish_started) * 1000),
            )

    def _launch(self):
        self.tasks = [
            asyncio.create_task(self._transcribe()),
            asyncio.create_task(self._detect()),
            asyncio.create_task(self._research()),
            asyncio.create_task(self._research()),
        ]
        session = self.current
        self._schedule_research(session)
        if session["revision"] > session["detector"]["processed_revision"]:
            self.changed.set()

        def finished(task):
            if (
                not task.cancelled()
                and task.exception() is not None
                and self.current is session
            ):
                session["error"] = (
                    "A processing worker stopped. Pause and reconnect; buffered audio will not be replayed."
                )

        for task in self.tasks:
            task.add_done_callback(finished)

    async def _halt(self):
        tasks, self.tasks = self.tasks, []
        for task in tasks:
            task.cancel()
        while not self.queue.empty():
            chunk = self.queue.get_nowait()
            self._fail_audio(chunk)
            self.audio_pending.discard(
                (
                    chunk["session_id"],
                    chunk["epoch"],
                    chunk["source"],
                    chunk["sequence"],
                )
            )
            chunk["pcm"] = b""
            self.queue.task_done()
        while not self.research_queue.empty():
            self.research_queue.get_nowait()
            self.research_queue.task_done()
        if self.current:
            for candidate in self.current["candidates"]:
                if candidate.get("research", {}).get("state") in (
                    "queued",
                    "searching",
                    "analyzing",
                    "pending",
                ):
                    candidate["research"] = {
                        "state": "interrupted",
                        "message": "Research stopped; resume explicitly to continue.",
                    }
        self.changed.clear()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.speech.stop()
        self.research_active.clear()

    async def _transcribe(self):
        while True:
            chunk = await self.queue.get()
            audio_started = time.monotonic()
            self._event(
                "transcription",
                "started",
                chunk=chunk,
                wait_ms=round((audio_started - chunk["received"]) * 1000),
            )
            before = None
            try:
                session = self.session(chunk["session_id"])
                source = session["sources"][chunk["source"]]
                if chunk["epoch"] != session["epoch"]:
                    self._event("transcription", "stale", session, chunk)
                    self._fail_audio(chunk)
                    continue
                if (
                    not chunk.get("durable")
                    and session["state"] != "finishing"
                    and time.monotonic() - chunk["received"] > 12
                ):
                    raise TimeoutError("Audio queue expired")
                if len(session["segments"]) >= meeting_policy(session).max_segments:
                    raise RuntimeError("Transcript capacity reached; retain audio")
                source["state"] = "transcribing"
                started = time.monotonic()
                language = session["language"]
                transcription = await self.speech.transcribe(chunk["pcm"], language)
                self._event(
                    "transcription",
                    "provider_returned",
                    session,
                    chunk,
                    duration_ms=round((time.monotonic() - started) * 1000),
                    inference_ms=transcription.get("inference_ms"),
                    round_trip_ms=transcription.get("round_trip_ms"),
                )
                if chunk.get("durable") and transcription.get("uncertain"):
                    raise RuntimeError(
                        "Uncertain transcription requires retained audio"
                    )
                words = transcription["words"]
                if transcription.get("language"):
                    source["language"] = transcription["language"]
                if (
                    self.current is not session
                    or chunk["epoch"] != session["epoch"]
                    or session["state"] not in ("capturing", "finishing")
                ):
                    self._fail_audio(chunk)
                    continue
                if chunk.get("durable"):
                    before = (
                        copy.deepcopy(source),
                        len(session["segments"]),
                        session["revision"],
                    )
                fresh = [
                    word
                    for word in words
                    if chunk["start_ms"] + word["end"] * 1000
                    > source["last_word_ms"] + 40
                ]
                previous_chunk_end = source.get("last_chunk_end_ms", -1)
                source["last_chunk_end_ms"] = chunk["end_ms"]
                previous_text = next(
                    (
                        item["text"]
                        for item in reversed(session["segments"])
                        if item["source"] == chunk["source"]
                    ),
                    "",
                )
                overlap_count = 0
                if chunk["start_ms"] < previous_chunk_end:
                    for count in range(1, min(8, len(fresh)) + 1):
                        prefix = normalized(
                            "".join(word["text"] for word in fresh[:count])
                        )
                        if (
                            len(prefix) >= 2
                            and normalized(previous_text).endswith(prefix)
                            and chunk["start_ms"] + fresh[count - 1]["end"] * 1000
                            <= previous_chunk_end + 200
                        ):
                            overlap_count = count
                if overlap_count:
                    source["last_word_ms"] = max(
                        source["last_word_ms"],
                        chunk["start_ms"] + fresh[overlap_count - 1]["end"] * 1000,
                    )
                    source["boundary_duplicates"] = (
                        source.get("boundary_duplicates", 0) + overlap_count
                    )
                    fresh = fresh[overlap_count:]
                text = "".join(word["text"] for word in fresh).strip()
                source.update(
                    state="uncertain"
                    if transcription.get("uncertain")
                    else "no_speech"
                    if not text
                    else "transcribed",
                    transcription_ms=round((time.monotonic() - started) * 1000),
                    gpu_inference_ms=transcription.get("inference_ms"),
                    gpu_round_trip_ms=transcription.get("round_trip_ms"),
                    queue_wait_ms=round((started - chunk["received"]) * 1000),
                )
                if transcription.get("uncertain"):
                    source["uncertain_chunks"] = source.get("uncertain_chunks", 0) + 1
                if not text:
                    self._commit_audio(session, chunk, "no_speech")
                    continue
                if len(session["segments"]) >= meeting_policy(session).max_segments:
                    raise RuntimeError("Transcript limit reached")
                start_ms = chunk["start_ms"] + fresh[0]["start"] * 1000
                end_ms = chunk["start_ms"] + fresh[-1]["end"] * 1000
                source["last_word_ms"] = end_ms
                echo = next(
                    (
                        item["id"]
                        for item in reversed(session["segments"][-6:])
                        if item["source"] != chunk["source"]
                        and item["final"]
                        and not transcription.get("uncertain", False)
                        and abs(item["end_ms"] - end_ms) < 1500
                        and len(normalized(text)) > 8
                        and SequenceMatcher(
                            None, normalized(text), normalized(item["text"])
                        ).ratio()
                        > 0.92
                    ),
                    None,
                )
                segment = {
                    "id": str(uuid4()),
                    "source": chunk["source"],
                    "text": text[:2000],
                    "start_ms": round(start_ms),
                    "end_ms": round(end_ms),
                    "speech_end_ms": chunk["speech_end_ms"],
                    "final": not transcription.get("uncertain", False),
                    "sequence": chunk["sequence"],
                    "epoch": chunk["epoch"],
                    "possible_echo_of": echo,
                    "continuation": chunk["start_ms"] < previous_chunk_end,
                    "finalized_at": time.monotonic(),
                    "processing_ms": source["transcription_ms"],
                    "queue_wait_ms": source["queue_wait_ms"],
                }
                session["segments"].append(segment)
                session["revision"] += 1
                self._commit_audio(session, chunk, "processed")
                self.changed.set()
            except asyncio.CancelledError:
                self._fail_audio(chunk)
                self._event("transcription", "cancelled", chunk=chunk)
                raise
            except (
                MeetingError,
                ValueError,
                RuntimeError,
                TimeoutError,
                OSError,
                TypeError,
                KeyError,
                IndexError,
                sqlite3.Error,
                httpx.HTTPError,
            ) as error:
                self._event(
                    "transcription",
                    "failed",
                    chunk=chunk,
                    cause=error_cause(error),
                    duration_ms=round((time.monotonic() - audio_started) * 1000),
                )
                if before is not None:
                    source.clear()
                    source.update(before[0])
                    del session["segments"][before[1] :]
                    session["revision"] = before[2]
                self._fail_audio(chunk)
                if self.current and self.current["id"] == chunk["session_id"]:
                    if not chunk.get("durable"):
                        self.current["error"] = (
                            "Transcription interrupted ("
                            + type(error).__name__
                            + "). Pause and reconnect inputs; no automatic audio replay."
                        )
                    self.current["sources"][chunk["source"]]["state"] = "error"
            finally:
                self.audio_pending.discard(
                    (
                        chunk["session_id"],
                        chunk["epoch"],
                        chunk["source"],
                        chunk["sequence"],
                    )
                )
                chunk["pcm"] = b""
                self.queue.task_done()

    def _commit_audio(self, session, chunk, state):
        if chunk.get("durable"):
            self.history.save_audio_result(self._snapshot(session), chunk, state)
            self.storage_error = None
        else:
            self._persist()
        self._event(
            "audio",
            "committed" if chunk.get("durable") else "processed",
            session,
            chunk,
            cause=state,
        )

    def _fail_audio(self, chunk):
        if chunk.get("durable") and self.history:
            try:
                self.history.fail_audio(chunk)
            except (sqlite3.Error, OSError):
                self.storage_error = "Audio acknowledgement could not be saved. Retain local audio and retry recovery."
                self._event("storage", "failed", chunk=chunk, cause="save_failed")

    async def _detect(self):
        last_request = -100
        while True:
            await self.changed.wait()
            self.changed.clear()
            await asyncio.sleep(max(0.25, 5 - (time.monotonic() - last_request)))
            session = self.current
            if (
                not session
                or not session["cloud_detection"]
                or session["state"] not in ("capturing", "finishing")
            ):
                continue
            state = session["detector"]
            if state["blocked"]:
                continue
            if state["processed_revision"] == session["revision"]:
                continue
            if state["requests"] >= meeting_policy(session).detection_requests:
                self._event("detection", "blocked", session, cause="budget_limit")
                state.update(
                    state="budget_limit",
                    blocked=True,
                    error="Detection budget reached. Transcription can continue; start a new session for more detection.",
                )
                self._persist()
                continue
            candidates = [dict(item) for item in session["candidates"]]
            try:
                segments, revision = self._detection_batch(session, candidates)
            except ValueError:
                self._event("detection", "blocked", session, cause="invalid_context")
                state.update(
                    state="error",
                    blocked=True,
                    error="An utterance exceeds the bounded detection context. Remaining extraction is incomplete.",
                )
                self._persist()
                continue
            if not segments:
                try:
                    self.apply_changes(
                        session,
                        None,
                        [],
                        candidates,
                        detector_updates={"processed_revision": revision},
                    )
                except MeetingError:
                    state.update(state="error", blocked=True, error=self.storage_error)
                continue
            epoch = session["epoch"]
            state.update(state="checking", requests=state["requests"] + 1)
            last_request = time.monotonic()
            self._event(
                "detection",
                "started",
                session,
                revision=revision,
                processed_revision=state["processed_revision"],
                attempt=state["requests"],
            )
            try:
                result, usage = await self.detector.detect(segments, candidates)
                if (
                    self.current is not session
                    or session["epoch"] != epoch
                    or session["state"] not in ("capturing", "finishing")
                ):
                    self._event("detection", "stale", session, revision=revision)
                    continue
                state["tokens"] += max(0, int(usage.get("total_tokens", 0)))
                if (
                    len(result.changes) == 8
                    and revision > state["processed_revision"] + 1
                ):
                    state["batch_limit"] = 1
                    state["state"] = "catching_up"
                    self._persist()
                    self.changed.set()
                    continue
                detector_updates = {
                    "batch_limit": 4,
                    "coverage_warning": "A dense utterance reached the extraction output limit; additional claims may be omitted."
                    if len(result.changes) == 8
                    else None,
                    "state": "catching_up"
                    if session["revision"] != revision
                    else "ready",
                    "processed_revision": revision,
                    "context_segments": usage.get("context_segments"),
                    "context_candidates": usage.get("context_candidates"),
                    "context_chars": usage.get("context_chars"),
                    "omitted_segments": usage.get("omitted_segments"),
                    "timings": {
                        key: usage.get(key)
                        for key in (
                            "round_trip_ms",
                            "queue_delay_ms",
                            "provider_execution_ms",
                        )
                    },
                    "latency_ms": (
                        round((time.monotonic() - segments[-1]["finalized_at"]) * 1000)
                        if segments[-1].get("finalized_at") is not None
                        and segments[-1]["id"]
                        not in session.get("restored_segment_ids", set())
                        else None
                    ),
                }
                self.apply_changes(
                    session,
                    result,
                    segments,
                    candidates,
                    detector_updates=detector_updates,
                )
                if session["revision"] != revision:
                    self.changed.set()
                self._event(
                    "detection",
                    "complete",
                    session,
                    revision=revision,
                    processed_revision=state["processed_revision"],
                    duration_ms=round((time.monotonic() - last_request) * 1000),
                )
            except asyncio.CancelledError:
                raise
            except MeetingError:
                self._event("detection", "blocked", session, cause="save_failed")
                state.update(state="error", blocked=True, error=self.storage_error)
            except ClaimCapacityError:
                state.update(
                    state="budget_limit",
                    blocked=True,
                    error="Claim capacity reached. Transcription continues; remaining extraction is incomplete.",
                )
                self._persist()
            except (
                httpx.HTTPError,
                ValueError,
                KeyError,
                IndexError,
                TypeError,
                RuntimeError,
                TimeoutError,
            ) as error:
                self._event(
                    "detection",
                    "blocked",
                    session,
                    cause=error_cause(error),
                    duration_ms=round((time.monotonic() - last_request) * 1000),
                )
                state.update(
                    state="error",
                    blocked=True,
                    error="Detection paused ("
                    + type(error).__name__
                    + "). Review connection/model readiness, then retry. No search was run.",
                )
                self._persist()

    def _detection_batch(self, session, candidates):
        processed = session["detector"]["processed_revision"]
        selected = []
        revision = processed
        stop = min(
            session["revision"], processed + session["detector"].get("batch_limit", 4)
        )
        for position in range(processed + 1, stop + 1):
            eligible = [
                dict(item)
                for item in session["segments"][:position]
                if item["final"] and not item["possible_echo_of"]
            ]
            pending = {
                item["id"]
                for item in session["segments"][processed:position]
                if item["final"] and not item["possible_echo_of"]
            }
            try:
                context = build_context(eligible, candidates)
            except ValueError:
                if revision == processed:
                    raise
                break
            supplied = {item["id"] for item in context["transcript"]}
            if not pending <= supplied:
                if revision == processed:
                    raise ValueError("Pending utterance exceeds context")
                break
            selected, revision = eligible, position
        return selected, revision

    def apply_changes(
        self, session, result, segments, supplied_candidates, *, detector_updates=None
    ):
        previous_candidates = list(session["candidates"])
        previous_values = copy.deepcopy(previous_candidates)
        previous_detector = copy.deepcopy(session["detector"])
        previous_error = session["error"]
        refreshed = set()
        try:
            if result is not None:
                refreshed = self._apply_changes(
                    session, result, segments, supplied_candidates
                )
            session["detector"].update(detector_updates or {})
            self._persist(required=True)
        except Exception:
            for candidate, values in zip(
                previous_candidates, previous_values, strict=True
            ):
                candidate.clear()
                candidate.update(values)
            session["candidates"][:] = previous_candidates
            session["detector"].clear()
            session["detector"].update(previous_detector)
            session["error"] = previous_error
            raise
        for identifier in refreshed:
            active = self.research_active.get(identifier)
            if active:
                active.cancel()
        self._schedule_research(session)

    def _apply_changes(self, session, result, segments, supplied_candidates):
        refreshed = set()
        evidence = {item["id"]: item for item in segments}
        known = {item["id"] for item in supplied_candidates}
        for change in result.changes:
            if (
                not set(change.evidence_ids) <= evidence.keys()
                or not set(change.related_ids) <= known
            ):
                raise DetectionContractError("Unknown evidence or candidate reference")
            if (
                change.action in ("update", "retract", "link")
                and change.candidate_id not in known
            ):
                raise DetectionContractError("Unknown candidate reference")
            if any(
                len(value) > 240
                for value in change.conditions + change.missing_information
            ):
                raise DetectionContractError("Unbounded condition")
        for change in result.changes:
            candidate = next(
                (
                    item
                    for item in session["candidates"]
                    if item["id"] == change.candidate_id
                ),
                None,
            )
            if candidate is None:
                matches = [
                    item
                    for item in session["candidates"]
                    if normalized(item["question"]) == normalized(change.question)
                    or normalized(item["topic_key"]) == normalized(change.topic_key)
                ]
                matches.sort(
                    key=lambda item: (
                        item["state"] in ("ignored", "retracted"),
                        normalized(item["question"]) != normalized(change.question),
                    )
                )
                candidate = next(iter(matches), None)
            if candidate and candidate["state"] == "ignored":
                continue
            fields = change.model_dump(exclude={"action", "candidate_id"})
            if candidate and candidate["state"] == "retracted":
                fresh_evidence = set(change.evidence_ids) - set(
                    candidate["evidence_ids"]
                )
                changed_scope = normalized(candidate["statement"]) != normalized(
                    change.statement
                ) or sorted(candidate.get("conditions", [])) != sorted(
                    change.conditions
                )
                if (
                    change.action not in ("create", "update")
                    or not fresh_evidence
                    or not changed_scope
                ):
                    continue
                fields["related_ids"] = list(
                    dict.fromkeys([candidate["id"], *change.related_ids])
                )[:4]
                candidate = None
            fields["evidence"] = [
                {
                    key: evidence[identifier][key]
                    for key in ("id", "source", "text", "start_ms", "end_ms")
                }
                for identifier in change.evidence_ids
            ]
            fields["state"] = (
                "retracted"
                if change.action == "retract"
                else "deferred"
                if change.action == "defer"
                else "candidate"
            )
            if candidate:
                merged = {
                    item["id"]: item
                    for item in candidate["evidence"] + fields["evidence"]
                }
                values = list(merged.values())
                fields["evidence"] = (
                    values if len(values) <= 4 else [values[0], *values[-3:]]
                )
                fields["evidence_ids"] = [item["id"] for item in fields["evidence"]]
                if change.action == "link":
                    candidate["evidence"] = fields["evidence"]
                    candidate["evidence_ids"] = fields["evidence_ids"]
                    continue
                for name in ("conditions", "missing_information"):
                    if sorted(candidate.get(name, [])) == sorted(fields[name]):
                        fields[name] = candidate.get(name, [])
                scope_changed = any(
                    candidate.get(name) != fields.get(name)
                    for name in (
                        "state",
                        "category",
                        "statement",
                        "conditions",
                        "missing_information",
                    )
                )
                if (
                    change.action != "update"
                    or candidate.get("edited")
                    or not scope_changed
                ):
                    fields.pop("question", None)
                fields.pop("topic_key", None)
                candidate.update(fields)
            elif len(session["candidates"]) < meeting_policy(session).max_candidates:
                candidate = {
                    "id": str(uuid4()),
                    "edited": False,
                    **fields,
                }
                session["candidates"].append(candidate)
            else:
                raise ClaimCapacityError(
                    "Claim capacity reached; extraction checkpoint was not advanced."
                )
            if candidate and self._refresh_research(session, candidate, dispatch=False):
                refreshed.add(candidate["id"])
        return refreshed

    def _refresh_research(self, session, candidate, *, dispatch=True):
        signature = hashlib.sha256(
            json.dumps(
                {
                    key: candidate.get(key)
                    for key in ("question", "state", "category", "statement")
                }
                | {
                    key: sorted(candidate.get(key, []))
                    for key in ("conditions", "missing_information")
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        if candidate.get("research_key") == signature:
            return
        active = self.research_active.get(candidate["id"])
        if active and dispatch:
            active.cancel()
        candidate["research_key"] = signature
        candidate["research_revision"] = candidate.get("research_revision", 0) + 1
        candidate["research"] = {
            "state": "pending"
            if candidate["state"] == "candidate"
            and session["research_enabled"]
            and session["state"] in ("capturing", "finishing")
            else "interrupted"
            if candidate["state"] == "candidate"
            and session["research_enabled"]
            and session["state"] == "paused"
            else "not_requested"
        }
        if dispatch:
            self._schedule_research(session)
        return True

    def _schedule_research(self, session):
        if (
            not session
            or session["state"] not in ("capturing", "finishing")
            or not session["research_enabled"]
        ):
            return
        for candidate in session["candidates"]:
            if candidate["state"] != "candidate" or candidate.get("research", {}).get(
                "state"
            ) not in ("pending", "interrupted"):
                continue
            if (
                session["research"]["requests"] >= session["research"]["limit"]
                or candidate.get("research_attempts", 0) >= 3
            ):
                candidate["research"] = {
                    "state": "budget_limit",
                    "message": "Research budget reached; start a new session to continue.",
                }
                continue
            if self.research_queue.full():
                return
            self.research_queue.put_nowait(
                (
                    session["id"],
                    session["epoch"],
                    candidate["id"],
                    candidate["research_revision"],
                    time.monotonic(),
                )
            )
            candidate["research"] = {"state": "queued"}
            self._event(
                "research",
                "queued",
                session,
                claim_id=candidate["id"],
                research_revision=candidate["research_revision"],
            )

    async def _research(self):
        while True:
            (
                identifier,
                epoch,
                candidate_id,
                revision,
                queued_at,
            ) = await self.research_queue.get()
            queue_wait_ms = round((time.monotonic() - queued_at) * 1000)
            research_started = time.monotonic()

            def log_research(
                status,
                identifier=identifier,
                epoch=epoch,
                candidate_id=candidate_id,
                revision=revision,
                queue_wait_ms=queue_wait_ms,
                **fields,
            ):
                self.events.emit(
                    stage="research",
                    status=status,
                    session_id=identifier,
                    epoch=epoch,
                    claim_id=candidate_id,
                    research_revision=revision,
                    wait_ms=queue_wait_ms,
                    **fields,
                )

            task = None
            try:
                session = self.current
                if (
                    not session
                    or session["id"] != identifier
                    or session["epoch"] != epoch
                    or session["state"] not in ("capturing", "finishing")
                ):
                    log_research("stale")
                    continue
                candidate = next(
                    (
                        item
                        for item in session["candidates"]
                        if item["id"] == candidate_id
                    ),
                    None,
                )
                if (
                    not candidate
                    or candidate["state"] != "candidate"
                    or candidate.get("research_revision") != revision
                ):
                    log_research("stale")
                    continue
                if session["research"]["requests"] >= session["research"]["limit"]:
                    candidate["research"] = {
                        "state": "budget_limit",
                        "message": "Research budget reached.",
                    }
                    continue
                session["research"]["requests"] += 1
                candidate["research_attempts"] = (
                    candidate.get("research_attempts", 0) + 1
                )
                log_research("started", attempt=candidate["research_attempts"])

                def current(
                    session=session, candidate=candidate, epoch=epoch, revision=revision
                ):
                    return (
                        self.current is session
                        and session["state"] in ("capturing", "finishing")
                        and session["epoch"] == epoch
                        and candidate["state"] == "candidate"
                        and candidate.get("research_revision") == revision
                    )

                def progress(
                    state,
                    current=current,
                    candidate=candidate,
                    log_research=log_research,
                ):
                    if current():
                        candidate["research"] = {"state": state}
                        log_research("progress", cause=state)

                finalized_at = max(
                    (
                        item.get("finalized_at", 0)
                        for item in session["segments"]
                        if item["id"] in candidate["evidence_ids"]
                        and item["id"] not in session.get("restored_segment_ids", set())
                    ),
                    default=0,
                )
                task = asyncio.create_task(
                    self.researcher.research(copy.deepcopy(candidate), progress)
                )
                self.research_active[candidate_id] = task
                try:
                    async with asyncio.timeout(100):
                        result = await task
                    if current():
                        result.setdefault("timings", {})["queue_wait_ms"] = (
                            queue_wait_ms
                        )
                        if finalized_at:
                            result["timings"]["final_transcript_to_answer_ms"] = round(
                                (time.monotonic() - finalized_at) * 1000
                            )
                        candidate["research"] = result
                        self._persist()
                        log_research(
                            "complete",
                            duration_ms=round(
                                (time.monotonic() - research_started) * 1000
                            ),
                            **{
                                key: result.get("timings", {}).get(key)
                                for key in (
                                    "search_ms",
                                    "synthesis_ms",
                                    "validation_ms",
                                )
                            },
                        )
                    else:
                        log_research("stale")
                except asyncio.CancelledError:
                    log_research("cancelled")
                    if asyncio.current_task().cancelling():
                        raise
                except (
                    ValueError,
                    RuntimeError,
                    TimeoutError,
                    KeyError,
                    TypeError,
                    httpx.HTTPError,
                ) as error:
                    log_research(
                        "failed",
                        cause=error_cause(error),
                        duration_ms=round((time.monotonic() - research_started) * 1000),
                    )
                    if current():
                        candidate["research"] = {
                            "state": "error",
                            "message": "Research could not be completed or its evidence could not be validated. Retry when ready.",
                        }
            finally:
                if task and self.research_active.get(candidate_id) is task:
                    self.research_active.pop(candidate_id, None)
                self.research_queue.task_done()
                self._schedule_research(self.current)
                self._persist()

    def source(self, identifier, candidate_id, source_id, revision):
        session = self.view(identifier)
        candidate = next(
            (item for item in session["candidates"] if item["id"] == candidate_id), None
        )
        if candidate is None or candidate.get("research_revision") != revision:
            raise MeetingError(
                "SOURCE_CHANGED",
                "Research changed; review the latest sources before opening.",
            )
        source = next(
            (
                item
                for item in (candidate or {}).get("research", {}).get("sources", [])
                if item["id"] == source_id
            ),
            None,
        )
        if source is None:
            raise MeetingError(
                "SOURCE_NOT_FOUND", "This research source is no longer available.", 404
            )
        return {"url": source["url"], "title": source["title"]}

    async def _watch(self):
        while True:
            await asyncio.sleep(5)
            session = self.current
            if not session:
                continue
            now = time.monotonic()
            if session["state"] == "capturing" and (
                now - session["last_seen"] > 15
                or now - session["created"]
                > meeting_policy(session).max_duration_seconds
            ):
                try:
                    if any(
                        source.get("durable_audio")
                        for source in session["sources"].values()
                    ):
                        async with self.lock:
                            session["state"] = "paused"
                            session["interrupted"] = True
                            session["finalization"] = {
                                "state": "incomplete",
                                "message": "Capture lease expired. Resume saved audio processing explicitly.",
                            }
                            await self._halt()
                            self._persist()
                    else:
                        await self.action(session["id"], "pause")
                except MeetingError as error:
                    if error.code == "SESSION_NOT_FOUND":
                        continue
                    raise
                session["error"] = (
                    "Capture lease expired or session duration limit reached. Reconnect explicitly."
                )
            elif session["state"] == "stopped" and now - session["stopped_at"] > 1800:
                self._persist()
                if not self.storage_error:
                    self.current = None

    async def close(self):
        if self.finalizer:
            self.finalizer.cancel()
            await asyncio.gather(self.finalizer, return_exceptions=True)
            self.finalizer = None
        if self.watchdog:
            self.watchdog.cancel()
            await asyncio.gather(self.watchdog, return_exceptions=True)
            self.watchdog = None
        if self.current:
            if self.current["state"] != "stopped":
                self.current["interrupted"] = True
                self.current["finalization"] = {
                    "state": "incomplete",
                    "message": "The service stopped before processing completed. Unsaved audio is not replayed.",
                }
                self.current["duration_ms"] = round(
                    (time.monotonic() - self.current["created"]) * 1000
                )
            self.current["state"] = "stopped"
        await self._halt()
        self._persist(required=True)
        self.current = None
