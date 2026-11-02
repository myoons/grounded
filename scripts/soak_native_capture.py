"""Observe an explicitly started native meeting without refreshing its heartbeat."""

import argparse
import json
import subprocess
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=900)
    args = parser.parse_args()
    if not 60 <= args.seconds <= 1200:
        raise ValueError("Use a bounded 60–1200 second test.")
    directory = (
        Path.home() / "Library/Application Support/Roomshift/artifacts/native-soak"
    )
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fixture = directory.parent / "research-fixtures/microphone.wav"
    rows = []
    with httpx.Client(
        base_url="http://127.0.0.1:8877", headers={"X-Roomshift-Client": "1"}, timeout=5
    ) as connection:
        session = (
            connection.get("/api/meetings/capabilities").json().get("existing_session")
        )
        if not session or session["state"] != "capturing":
            raise RuntimeError("Start the test meeting explicitly first.")
        identifier = session["id"]
        started = time.monotonic()
        checkpoints = [5, args.seconds // 2, args.seconds - 20]
        failure = None
        try:
            while time.monotonic() - started < args.seconds:
                elapsed = round(time.monotonic() - started, 1)
                current = (
                    connection.get("/api/meetings/capabilities")
                    .json()
                    .get("existing_session")
                )
                if (
                    not current
                    or current["id"] != identifier
                    or current["state"] != "capturing"
                ):
                    raise RuntimeError("The test capture stopped or changed.")
                processes = subprocess.run(
                    ["pgrep", "-f", "/Contents/Resources/grounded-audio$"],
                    capture_output=True,
                    text=True,
                    check=False,
                ).stdout.split()
                if len(processes) != 1:
                    raise RuntimeError("Expected exactly one native audio helper.")
                usage = subprocess.check_output(
                    ["ps", "-p", processes[0], "-o", "rss=,pcpu="], text=True
                ).split()
                rows.append(
                    {
                        "elapsed_s": elapsed,
                        "helper_pid": int(processes[0]),
                        "rss_kib": int(usage[0]),
                        "cpu_percent_reported": float(usage[1]),
                    }
                )
                if checkpoints and elapsed >= checkpoints[0]:
                    checkpoints.pop(0)
                    subprocess.run(
                        ["afplay", "-v", "0.4", str(fixture)], timeout=15, check=True
                    )
                time.sleep(2)
        except (
            RuntimeError,
            OSError,
            httpx.HTTPError,
            subprocess.SubprocessError,
        ) as error:
            failure = str(error)
        finally:
            current = (
                connection.get("/api/meetings/capabilities")
                .json()
                .get("existing_session")
            )
            details = {}
            if current and current["id"] == identifier:
                state = connection.get(f"/api/meetings/{identifier}").json()
                details = {
                    "state": state["state"],
                    "error": state.get("error"),
                    "sources": state["sources"],
                    "transcript_count": len(state["segments"]),
                    "candidate_count": len(state["candidates"]),
                }
                connection.post(
                    f"/api/meetings/{identifier}/action", json={"action": "pause"}
                ).raise_for_status()
            report = {
                "passed": failure is None,
                "error": failure,
                "elapsed_s": round(time.monotonic() - started, 1),
                "observer_renews_heartbeat": False,
                "controlled_playback_checkpoints": 3 - len(checkpoints),
                "observations": rows,
                "final": details,
            }
            (directory / "result.json").write_text(json.dumps(report, indent=2))
            print(
                json.dumps(
                    {
                        key: report[key]
                        for key in [
                            "passed",
                            "error",
                            "elapsed_s",
                            "controlled_playback_checkpoints",
                        ]
                    }
                ),
                flush=True,
            )
        if failure:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
