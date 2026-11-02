"""Keep the speech worker warm for a bounded demo, then restore its settings."""

import argparse
import fcntl
import json
import signal
import time
from pathlib import Path
from uuid import uuid4

from scripts.private_state import save_private_json
from scripts.runpod_access import client

ROOT = Path.home() / ".config/roomshift"
JOURNAL = ROOT / "warm-session.json"


def save(value):
    save_private_json(JOURNAL, value)


def restore(record):
    current = json.loads(JOURNAL.read_text())
    if current.get("session_id") != record.get("session_id"):
        raise RuntimeError(
            "A newer warm session owns these endpoints; refusing to change its settings."
        )
    for endpoint in record["endpoints"]:
        if (
            not isinstance(endpoint.get("id"), str)
            or not endpoint["id"].replace("-", "").isalnum()
            or type(endpoint.get("workersMin")) is not int
            or endpoint["workersMin"] not in (0, 1)
        ):
            raise ValueError("Invalid saved endpoint settings.")
    with client() as connection:
        for endpoint in record["endpoints"]:
            response = connection.patch(
                f"endpoints/{endpoint['id']}",
                json={"workersMin": endpoint["workersMin"]},
            )
            response.raise_for_status()
    record["state"] = "restored"
    save(record)


def main():
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (ROOT / "warm-session.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(
                "A warm-session process is active; stop it before restoring or starting another."
            ) from None
        run()


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=1200)
    parser.add_argument("--restore", action="store_true")
    args = parser.parse_args()
    if args.restore:
        restore(json.loads(JOURNAL.read_text()))
        print("Original worker minimums restored.")
        return
    if not 30 <= args.seconds <= 10800:
        parser.error("Warm sessions must last 30–10800 seconds.")
    if JOURNAL.exists() and json.loads(JOURNAL.read_text()).get("state") != "restored":
        parser.error(
            "An earlier warm session needs review. Use --restore before starting another."
        )
    identifiers = {
        json.loads((ROOT / name).read_text())["endpoint_id"]
        for name in ["speech-deployment.json"]
    }
    if any(not identifier.replace("-", "").isalnum() for identifier in identifiers):
        raise ValueError("Invalid deployment identifiers.")
    with client() as connection:
        endpoints = []
        for identifier in sorted(identifiers):
            response = connection.get(f"endpoints/{identifier}")
            response.raise_for_status()
            current = response.json()
            if current["workersMax"] != 1:
                raise ValueError(
                    "Expected one-worker Roomshift endpoints; refusing to change other configurations."
                )
            endpoints.append({"id": identifier, "workersMin": current["workersMin"]})
        record = {
            "session_id": str(uuid4()),
            "state": "activating",
            "endpoints": endpoints,
            "started_at": time.time(),
            "seconds": args.seconds,
        }
        save(record)

        def terminate(_signal, _frame):
            raise SystemExit("Stopping warm session.")

        signal.signal(signal.SIGTERM, terminate)
        try:
            for endpoint in endpoints:
                response = connection.patch(
                    f"endpoints/{endpoint['id']}", json={"workersMin": 1}
                )
                response.raise_for_status()
            record["state"] = "active"
            save(record)
            print(
                f"Warming {len(endpoints)} endpoints, maximum one GPU each, for {args.seconds} seconds. Idle GPU time is billed. Ctrl-C restores original minimums.",
                flush=True,
            )
            time.sleep(args.seconds)
        finally:
            restore(record)
            print("Original worker minimums restored.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
