import contextlib
import json
import math
import os
import re
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path


class EventLog:
    """Bounded, content-free diagnostic events; never a durable work queue."""

    numbers = frozenset(
        {
            "epoch",
            "sequence",
            "revision",
            "processed_revision",
            "research_revision",
            "attempt",
            "queue_depth",
            "research_queue_depth",
            "duration_ms",
            "wait_ms",
            "search_ms",
            "synthesis_ms",
            "validation_ms",
            "inference_ms",
            "round_trip_ms",
        }
    )
    labels = frozenset({"stage", "status", "cause", "source"})
    identifiers = frozenset({"session_id", "claim_id"})
    vocabulary = frozenset(
        {
            "session",
            "storage",
            "audio",
            "transcription",
            "detection",
            "research",
            "started",
            "failed",
            "admitted",
            "cancel_requested",
            "resume_requested",
            "action_requested",
            "finishing",
            "incomplete",
            "complete",
            "stale",
            "provider_returned",
            "cancelled",
            "committed",
            "processed",
            "blocked",
            "queued",
            "progress",
            "searching",
            "analyzing",
            "synthesizing",
            "validating",
            "no_speech",
            "save_failed",
            "timeout",
            "io_error",
            "invalid_result",
            "processing_error",
            "stop",
            "pause",
            "resume",
            "retry_detection",
            "microphone",
            "system",
            "budget_limit",
            "invalid_context",
        }
    )

    def __init__(self, directory=None, *, sink=None, max_bytes=1_048_576, backups=3):
        if max_bytes < 1024 or not 1 <= backups <= 10:
            raise ValueError("Invalid diagnostic retention limits")
        self.directory = Path(directory) if directory is not None else None
        self.sink = sink
        self.max_bytes = max_bytes
        self.backups = backups
        self.lock = threading.Lock()
        self.last_warning = -math.inf

    def emit(self, **fields):
        if self.directory is None and self.sink is None:
            return
        try:
            record = {"schema_version": 1, "at": datetime.now(UTC).isoformat()}
            for key, value in fields.items():
                if key in self.numbers and type(value) in (int, float):
                    if math.isfinite(value) and 0 <= value <= 1e15:
                        record[key] = value
                elif key in self.labels and isinstance(value, str):
                    if value in self.vocabulary:
                        record[key] = value
                elif (
                    key in self.identifiers
                    and isinstance(value, str)
                    and re.fullmatch(r"[0-9a-fA-F-]{1,64}", value)
                ):
                    record[key] = value
            with self.lock:
                if self.sink is not None:
                    self.sink(record)
                else:
                    self._append(record)
        except (OSError, ValueError, TypeError, RuntimeError):
            now = time.monotonic()
            if now - self.last_warning >= 60:
                self.last_warning = now
                with contextlib.suppress(OSError, ValueError, RuntimeError):
                    sys.stderr.write(
                        "Grounded diagnostics unavailable; trace may be incomplete.\n"
                    )

    def _append(self, record):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        target = self.directory / "pipeline.jsonl"
        line = (json.dumps(record, separators=(",", ":")) + "\n").encode()
        if target.exists() and target.stat().st_size + len(line) > self.max_bytes:
            for index in range(self.backups, 0, -1):
                source = (
                    target if index == 1 else target.with_suffix(f".jsonl.{index - 1}")
                )
                destination = target.with_suffix(f".jsonl.{index}")
                if source.exists():
                    source.replace(destination)
                    destination.chmod(0o600)
        descriptor = os.open(
            target, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "ab", closefd=False) as output:
                output.write(line)
        finally:
            os.close(descriptor)


def error_cause(error):
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, OSError):
        return "io_error"
    if isinstance(error, (ValueError, TypeError, KeyError, IndexError)):
        return "invalid_result"
    return "processing_error"
