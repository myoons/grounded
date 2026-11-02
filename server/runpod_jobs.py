import asyncio
import json
import re
import time

import httpx


class RunpodJobClient:
    def __init__(
        self,
        endpoint,
        key,
        transport=None,
        timeout=25,
        execution_timeout_ms=20000,
        ttl_ms=60000,
    ):
        self.endpoint = endpoint
        self.key = key
        self.transport = transport
        self.timeout = timeout
        self.execution_timeout_ms = execution_timeout_ms
        self.ttl_ms = ttl_ms
        self.active = None
        self.busy = False
        self.generation = 0

    def ready(self):
        return bool(self.key and re.fullmatch(r"[a-zA-Z0-9-]+", self.endpoint))

    def connection(self):
        return httpx.AsyncClient(
            base_url=f"https://api.runpod.ai/v2/{self.endpoint}/",
            headers={"Authorization": f"Bearer {self.key}"},
            timeout=httpx.Timeout(8, connect=5),
            follow_redirects=False,
            transport=self.transport,
        )

    async def cancel(self, identifier):
        try:
            async with self.connection() as connection:
                async with asyncio.timeout(3):
                    await connection.post(f"cancel/{identifier}")
        except (TimeoutError, httpx.HTTPError):
            pass

    async def response(self, connection, method, path, **options):
        async with connection.stream(method, path, **options) as response:
            response.raise_for_status()
            chunks = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > 1_000_000:
                    raise ValueError("Runpod response exceeds the size limit.")
                chunks.append(chunk)
        payload = json.loads(b"".join(chunks))
        if not isinstance(payload, dict):
            raise TypeError("Invalid Runpod response.")
        return payload

    async def run(self, data):
        if not self.ready():
            raise RuntimeError("Runpod endpoint is not configured.")
        if not isinstance(data, dict) or len(json.dumps(data).encode()) > 400000:
            raise ValueError("Invalid or oversized Runpod input.")
        if self.busy:
            raise RuntimeError("A Runpod request is already active.")
        self.busy = True
        generation = self.generation
        started = time.monotonic()
        identifier = None
        complete = False
        try:
            async with asyncio.timeout(self.timeout):
                async with self.connection() as connection:
                    payload = await self.response(
                        connection,
                        "POST",
                        "run",
                        json={
                            "input": data,
                            "policy": {
                                "executionTimeout": self.execution_timeout_ms,
                                "ttl": self.ttl_ms,
                            },
                        },
                    )
                    identifier = payload.get("id")
                    if not isinstance(identifier, str) or not re.fullmatch(
                        r"[a-zA-Z0-9_-]{1,150}", identifier
                    ):
                        raise ValueError("Invalid Runpod job identifier.")
                    self.active = identifier
                    while True:
                        if generation != self.generation:
                            raise asyncio.CancelledError()
                        if payload.get("id") != identifier:
                            raise ValueError("Runpod job identity changed.")
                        state = payload.get("status")
                        if state == "COMPLETED":
                            payload["round_trip_ms"] = round(
                                (time.monotonic() - started) * 1000, 2
                            )
                            complete = True
                            return payload
                        if state not in ("IN_QUEUE", "IN_PROGRESS"):
                            raise RuntimeError("Runpod job failed or was cancelled.")
                        await asyncio.sleep(0.2)
                        payload = await self.response(
                            connection, "GET", f"status/{identifier}"
                        )
        finally:
            if self.active == identifier:
                self.active = None
            try:
                if identifier and not complete:
                    await self.cancel(identifier)
            finally:
                self.busy = False

    async def stop(self):
        self.generation += 1
        identifier, self.active = self.active, None
        if identifier:
            await self.cancel(identifier)
