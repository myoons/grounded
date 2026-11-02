"""Local synthetic HTTP fixture for the desktop durable-audio integration test."""

import asyncio
import json
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from server.api import RequestBoundary
from server.meeting_api import meeting_error, router
from server.meeting_history import MeetingHistory
from server.meeting_service import MeetingError, MeetingService
from tests.test_meeting_finalization import ControlledDetector, ControlledResearcher


class SyntheticSpeech:
    def ready(self):
        return True

    async def stop(self):
        pass

    async def transcribe(self, _pcm, _language):
        await asyncio.sleep(0.02)
        return {"words": [{"text": "Synthetic speech", "start": 0.01, "end": 0.18}]}


def fixture(directory):
    def restore(app):
        app.state.history = MeetingHistory(directory / "meetings.sqlite")
        app.state.meetings = MeetingService(
            speech=SyntheticSpeech(),
            detector=ControlledDetector(),
            researcher=ControlledResearcher(),
            history=app.state.history,
        )

    @asynccontextmanager
    async def lifespan(app):
        restore(app)
        try:
            yield
        finally:
            await app.state.meetings.close()
            app.state.history.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    app.add_exception_handler(MeetingError, meeting_error)
    app.add_middleware(RequestBoundary)

    @app.post("/__synthetic_restart")
    async def restart():
        await app.state.meetings.close()
        app.state.history.close()
        restore(app)
        return {"restarted": True}

    @app.post("/__synthetic_unextracted")
    async def unextracted():
        service = app.state.meetings
        current = service.current
        current.update(cloud_detection=True, research_enabled=True)
        current["detector"]["blocked"] = True
        service.accept(
            current["id"],
            current["epoch"],
            "microphone",
            0,
            0,
            200,
            190,
            0,
            bytes(6400),
            durable=True,
        )
        await service.queue.join()
        return {
            "segments": len(current["segments"]),
            "candidates": len(current["candidates"]),
        }

    return app


if __name__ == "__main__":
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    print(json.dumps({"port": listener.getsockname()[1]}), flush=True)
    server = uvicorn.Server(
        uvicorn.Config(fixture(Path(sys.argv[1])), log_level="error")
    )
    server.run(sockets=[listener])
