"""Start the meeting service using private Runpod credentials."""

import argparse
import json
import os
import re
from pathlib import Path

import uvicorn

from scripts.runpod_access import credentials
from scripts.search_access import credentials as search_credentials


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8877)
    args = parser.parse_args()
    os.environ.update(credentials())
    os.environ.update(search_credentials())
    speech_path = Path.home() / ".config/roomshift/speech-deployment.json"
    if speech_path.exists():
        speech = json.loads(speech_path.read_text())
        endpoint = speech.get("endpoint_id", "")
        if not re.fullmatch(r"[a-zA-Z0-9-]+", endpoint):
            raise ValueError("Invalid speech endpoint configuration.")
        os.environ["ROOMSHIFT_SPEECH_ENDPOINT_ID"] = endpoint
    uvicorn.run(
        "server.api:create_app",
        factory=True,
        host="127.0.0.1",
        port=args.port,
        access_log=False,
    )


if __name__ == "__main__":
    main()
