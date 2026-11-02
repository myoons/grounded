"""Operator resource envelope, snapshotted for each meeting."""

import os

from pydantic import BaseModel, ConfigDict, Field


class CapacityPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    max_segments: int = Field(default=768, ge=1, le=768)
    max_duration_seconds: int = Field(default=3600, ge=1, le=3600)
    max_candidates: int = Field(default=128, ge=1, le=128)
    detection_requests: int = Field(default=180, ge=0, le=2000)
    research_requests: int = Field(default=60, ge=0, le=500)

    @classmethod
    def from_environment(cls):
        values = {}
        for name in cls.model_fields:
            raw = os.environ.get(f"GROUNDED_{name.upper()}")
            if raw is not None:
                try:
                    values[name] = int(raw)
                except ValueError:
                    raise ValueError(f"Invalid GROUNDED_{name.upper()}") from None
        return cls(**values)


LEGACY_POLICY = CapacityPolicy(
    max_segments=256,
    max_duration_seconds=3600,
    detection_requests=60,
    research_requests=20,
)


def meeting_policy(session):
    if "capacity_policy" not in session:
        session["capacity_policy"] = LEGACY_POLICY.model_dump()
    return CapacityPolicy.model_validate(session["capacity_policy"])
