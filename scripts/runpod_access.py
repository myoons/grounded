"""Load deployment credentials without shell evaluation or secret logging."""

import shlex
from pathlib import Path

import httpx


def credentials():
    path = Path.home() / ".config/roomshift/runpod.env"
    if path.stat().st_mode & 0o077:
        raise ValueError("Runpod credential file must have mode 600.")
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip().removeprefix("export ")
        name, separator, value = line.partition("=")
        if separator and name.strip() == "RUNPOD_API_KEY":
            parts = shlex.split(value, comments=True)
            if len(parts) != 1:
                raise ValueError("Invalid credential file format.")
            values[name.strip()] = parts[0]
    if not values.get("RUNPOD_API_KEY"):
        raise ValueError("Missing Runpod credential.")
    return values


def client(base_url="https://rest.runpod.io/v1/"):
    key = credentials()["RUNPOD_API_KEY"]
    return httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Bearer {key}"},
        timeout=30,
        follow_redirects=False,
    )
