from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MeetingContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


EnglishText = Annotated[
    str, Field(min_length=1, max_length=240, pattern=r"^[^\x00-\x1F\x7F]+$")
]


class MeetingStart(MeetingContract):
    title: str = Field(default="Google Meet", min_length=1, max_length=160)
    consent: Literal[True]
    audio_consent_version: Literal["runpod-audio-v1", "runpod-audio-v2"]
    cloud_detection: bool = False
    detection_consent_version: Literal["runpod-kimi-k3-v1"] | None = None
    research_enabled: bool = False
    research_consent_version: Literal["firecrawl-kimi-v1"] | None = None
    language: Literal["en"] = "en"

    @model_validator(mode="after")
    def validate_cloud_consent(self):
        if (
            self.cloud_detection
            and self.detection_consent_version != "runpod-kimi-k3-v1"
        ):
            raise ValueError(
                "Explicit consent for the Runpod Kimi K3 public API is required."
            )
        if self.research_enabled and (
            not self.cloud_detection
            or self.research_consent_version != "firecrawl-kimi-v1"
        ):
            raise ValueError(
                "Explicit consent for external search and evidence analysis is required."
            )
        return self


class MeetingAction(MeetingContract):
    action: Literal["pause", "resume", "stop", "retry_detection"]
    finalization_error: str | None = Field(default=None, min_length=1, max_length=400)

    @model_validator(mode="after")
    def validate_finalization_error(self):
        if self.finalization_error and self.action != "stop":
            raise ValueError("Only End meeting accepts an upload finalization error.")
        return self


class MeetingPresentation(MeetingContract):
    expanded: list[str] = Field(default_factory=list, max_length=128)
    transcript_scroll: int = Field(default=0, ge=0, le=10000000)
    cards_scroll: int = Field(default=0, ge=0, le=10000000)


ClaimCategory = Literal[
    "capability",
    "limits_pricing",
    "policy_licensing",
    "performance_comparison",
    "technical_behavior",
    "current_status",
]


class CandidateChange(MeetingContract):
    action: Literal["create", "update", "retract", "defer", "link"]
    category: ClaimCategory = "technical_behavior"
    statement: EnglishText = "Claim awaiting verification"
    candidate_id: str | None
    evidence_ids: list[str] = Field(min_length=1, max_length=4)
    question: str = Field(
        min_length=5,
        max_length=240,
        pattern=r"^[^\x00-\x1F\x7F]+\?$",
    )
    reason: str = Field(min_length=1, max_length=500, pattern=r"^[^\x00-\x1F\x7F]+$")
    conditions: list[EnglishText] = Field(max_length=4)
    missing_information: list[EnglishText] = Field(max_length=4)
    topic_key: str = Field(min_length=1, max_length=100, pattern=r"^[\x20-\x7E]+$")
    related_ids: list[str] = Field(max_length=4)


class DetectionResult(MeetingContract):
    changes: list[CandidateChange] = Field(max_length=8)


class CandidateReview(MeetingContract):
    action: Literal["ignore", "edit", "retry_research"]
    question: str | None = Field(default=None, min_length=5, max_length=240)
