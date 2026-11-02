import fcntl
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from server.meeting_api import meeting_error, router
from server.meeting_history import MeetingHistory
from server.meeting_service import MeetingError, MeetingService
from server.observability import EventLog
from server.paths import default_data_dir

MAX_BYTES = 256 * 1024


class RequestBoundary:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        mutation = scope["method"] in ("POST", "PUT", "PATCH", "DELETE")
        if not mutation and not scope["path"].startswith("/api/meetings"):
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        origin = headers.get(b"origin")
        expected = b"http://" + headers.get(b"host", b"")
        if (origin and origin != expected) or headers.get(
            b"x-roomshift-client"
        ) != b"1":
            return await JSONResponse(
                {
                    "error": {
                        "code": "ORIGIN_REJECTED",
                        "message": "Use the local Grounded application.",
                    }
                },
                status_code=403,
            )(scope, receive, send)
        if not mutation:
            return await self.app(scope, receive, send)
        chunks = []
        total = 0
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                return
            total += len(event.get("body", b""))
            if total > MAX_BYTES:
                return await JSONResponse(
                    {
                        "error": {
                            "code": "BODY_TOO_LARGE",
                            "message": "Request exceeds the 256 KB limit.",
                        }
                    },
                    status_code=413,
                )(scope, receive, send)
            chunks.append(event.get("body", b""))
            if not event.get("more_body", False):
                break
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {
                    "type": "http.request",
                    "body": b"".join(chunks),
                    "more_body": False,
                }
            return await receive()

        await self.app(scope, replay, send)


def create_app(root: Path | None = None):
    data_dir = root or default_data_dir()

    @asynccontextmanager
    async def lifespan(app):
        data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (data_dir / "server.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            history = MeetingHistory(data_dir / "meetings.sqlite")
            app.state.meetings = MeetingService(
                history=history, events=EventLog(data_dir / "logs")
            )
            try:
                yield
            finally:
                try:
                    await app.state.meetings.close()
                finally:
                    history.close()

    app = FastAPI(title="Grounded", lifespan=lifespan)
    app.include_router(router)
    app.add_exception_handler(MeetingError, meeting_error)
    app.add_middleware(RequestBoundary)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"]
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, _error):
        return JSONResponse(
            {
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "Check the request fields and try again.",
                }
            },
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def unexpected(_request, _error):
        return JSONResponse(
            {
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "The operation could not be completed.",
                }
            },
            status_code=500,
        )

    @app.get("/api/health")
    async def health():
        return {"service": "roomshift-research", "status": "ok"}

    return app
