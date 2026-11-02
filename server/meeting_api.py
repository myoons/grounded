import math
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from server.meeting_contracts import (
    CandidateReview,
    MeetingAction,
    MeetingPresentation,
    MeetingStart,
)
from server.meeting_service import MeetingError

router = APIRouter(prefix="/api/meetings", tags=["Meeting investigation"])


@router.get("/capabilities")
async def capabilities(request: Request):
    return request.app.state.meetings.capabilities()


@router.post("")
async def start(request: Request, command: MeetingStart):
    return await request.app.state.meetings.start(command)


@router.get("")
async def history(request: Request, offset: int = Query(default=0, ge=0, le=1000000)):
    return request.app.state.meetings.history_list(offset)


@router.get("/{identifier}")
async def state(request: Request, identifier: UUID):
    return request.app.state.meetings.view(str(identifier))


@router.post("/{identifier}/action")
async def action(request: Request, identifier: UUID, command: MeetingAction):
    return await request.app.state.meetings.action(
        str(identifier), command.action, finalization_error=command.finalization_error
    )


@router.delete("/{identifier}")
async def clear(request: Request, identifier: UUID):
    await request.app.state.meetings.clear(str(identifier))
    return {"cleared": True}


@router.patch("/{identifier}/presentation")
async def presentation(
    request: Request, identifier: UUID, command: MeetingPresentation
):
    return request.app.state.meetings.update_presentation(str(identifier), command)


@router.post("/{identifier}/audio")
async def audio(request: Request, identifier: UUID):
    if request.headers.get("content-type") != "application/octet-stream":
        raise MeetingError("INVALID_CONTENT_TYPE", "Expected PCM binary data.", 415)
    try:
        epoch = int(request.headers["x-meeting-epoch"])
        sequence = int(request.headers["x-audio-sequence"])
        times = [
            float(request.headers["x-audio-" + key])
            for key in ["start", "end", "speech-end", "age"]
        ]
        if not all(math.isfinite(value) for value in times):
            raise ValueError()
    except (KeyError, ValueError):
        raise MeetingError(
            "INVALID_TIMING", "Invalid chunk timing metadata.", 422
        ) from None
    return request.app.state.meetings.accept(
        str(identifier),
        epoch,
        request.headers.get("x-audio-source"),
        sequence,
        *times,
        await request.body(),
        durable=request.headers.get("x-audio-durable") == "1",
    )


@router.get("/{identifier}/audio/{epoch}/{source}/{sequence}")
async def audio_status(
    request: Request,
    identifier: UUID,
    epoch: int,
    source: str,
    sequence: int,
    sha256: str,
):
    return request.app.state.meetings.audio_status(
        str(identifier), epoch, source, sequence, sha256
    )


@router.post("/{identifier}/resume-processing")
async def resume_processing(request: Request, identifier: UUID):
    return await request.app.state.meetings.resume_processing(str(identifier))


@router.post("/{identifier}/cancel-processing")
async def cancel_processing(request: Request, identifier: UUID):
    return await request.app.state.meetings.cancel_processing(str(identifier))


@router.patch("/{identifier}/candidates/{candidate_id}")
async def review(
    request: Request, identifier: UUID, candidate_id: UUID, command: CandidateReview
):
    return request.app.state.meetings.review(
        str(identifier), str(candidate_id), command
    )


@router.get("/{identifier}/candidates/{candidate_id}/sources/{source_id}")
async def source(
    request: Request,
    identifier: UUID,
    candidate_id: UUID,
    source_id: str,
    revision: int,
):
    return request.app.state.meetings.source(
        str(identifier), str(candidate_id), source_id, revision
    )


async def meeting_error(_request, error):
    return JSONResponse(
        {"error": {"code": error.code, "message": error.message}},
        status_code=error.status,
    )
