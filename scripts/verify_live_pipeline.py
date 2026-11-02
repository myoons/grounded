"""Exercise a running private API with synthetic audio and real cloud research."""

import argparse
import json
import time
import wave
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8877)
    args = parser.parse_args()
    root = Path.home() / "Library/Application Support/Roomshift/artifacts"
    with httpx.Client(
        base_url=f"http://127.0.0.1:{args.port}",
        headers={"X-Roomshift-Client": "1"},
        timeout=10,
    ) as connection:
        capabilities = connection.get("/api/meetings/capabilities").json()
        if capabilities.get("existing_session"):
            raise RuntimeError("A meeting exists; do not interrupt it for this test.")
        response = connection.post(
            "/api/meetings",
            json={
                "consent": True,
                "audio_consent_version": capabilities["audio_consent_version"],
                "cloud_detection": True,
                "detection_consent_version": "runpod-kimi-k3-v1",
                "research_enabled": True,
                "research_consent_version": "firecrawl-kimi-v1",
                "language": "en",
            },
        )
        response.raise_for_status()
        session = response.json()
        identifier = session["id"]
        started = time.monotonic()
        first_answer_ms = None
        try:
            for source in ("microphone", "system"):
                with wave.open(
                    str(root / "research-fixtures" / f"{source}.wav")
                ) as audio:
                    if (
                        audio.getframerate(),
                        audio.getnchannels(),
                        audio.getsampwidth(),
                    ) != (16000, 1, 2):
                        raise ValueError("Expected synthetic mono 16 kHz PCM16 audio.")
                    pcm = audio.readframes(7 * 16000)
                response = connection.post(
                    f"/api/meetings/{identifier}/audio",
                    content=pcm,
                    headers={
                        "Content-Type": "application/octet-stream",
                        "X-Meeting-Epoch": str(session["epoch"]),
                        "X-Audio-Source": source,
                        "X-Audio-Sequence": "0",
                        "X-Audio-Start": "0",
                        "X-Audio-End": str(len(pcm) / 32),
                        "X-Audio-Speech-End": str(len(pcm) / 32),
                        "X-Audio-Age": "0",
                    },
                )
                response.raise_for_status()
            while time.monotonic() - started < 150:
                response = connection.get(f"/api/meetings/{identifier}")
                response.raise_for_status()
                state = response.json()
                if state["error"] or state["detector"]["blocked"]:
                    raise RuntimeError("Live pipeline reported an error.")
                ready = [
                    item
                    for item in state["candidates"]
                    if item.get("research", {}).get("state") == "ready"
                ]
                if ready and first_answer_ms is None:
                    first_answer_ms = round((time.monotonic() - started) * 1000)
                if ready and state["detector"]["state"] == "ready":
                    break
                if any(
                    item.get("research", {}).get("state") == "error"
                    for item in state["candidates"]
                ):
                    raise RuntimeError(
                        "Live research failed its evidence or provider checks."
                    )
                time.sleep(0.5)
            else:
                raise TimeoutError("Live research deadline exceeded.")
            candidate = ready[0]
            assert candidate["research"]["citations"]
            assert candidate["research"]["verdict"] in ("contradicted", "conditional")
            assert {item["source"] for item in state["segments"]} == {
                "microphone",
                "system",
            }
            citation = candidate["research"]["citations"][0]
            response = connection.get(
                f"/api/meetings/{identifier}/candidates/{candidate['id']}/sources/{citation['source_id']}",
                params={"revision": candidate["research_revision"]},
            )
            response.raise_for_status()
            assert response.json()["url"].startswith("https://")
            report = {
                "passed": True,
                "synthetic_audio": True,
                "port": args.port,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "first_answer_ms": first_answer_ms,
                "session": state,
            }
            directory = root / f"full-pipeline-{args.port}-smoke"
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            (directory / "result.json").write_text(json.dumps(report, indent=2))
            print(
                json.dumps(
                    {
                        "passed": True,
                        "elapsed_ms": report["elapsed_ms"],
                        "first_answer_ms": first_answer_ms,
                        "verdict": candidate["research"]["verdict"],
                        "source": response.json()["url"],
                    }
                ),
                flush=True,
            )
        finally:
            connection.post(
                f"/api/meetings/{identifier}/action", json={"action": "stop"}
            ).raise_for_status()
            deadline = time.monotonic() + 310
            while time.monotonic() < deadline:
                response = connection.get(f"/api/meetings/{identifier}")
                response.raise_for_status()
                if response.json()["state"] == "stopped":
                    break
                time.sleep(0.25)
            else:
                raise TimeoutError("Test meeting finalization did not complete.")
            connection.delete(f"/api/meetings/{identifier}").raise_for_status()


if __name__ == "__main__":
    main()
