"""Create one digest-pinned GPU speech endpoint without warming it implicitly."""

import argparse
import json
import re
from pathlib import Path

from scripts.private_state import save_private_json
from scripts.runpod_access import client
from speech_worker.engine import MODEL, REVISION


def save(path, value):
    save_private_json(path, value)


def configuration(template):
    return {
        "name": "roomshift-english-speech",
        "templateId": template,
        "gpuTypeIds": ["NVIDIA GeForce RTX 4090", "NVIDIA L40S"],
        "gpuCount": 1,
        "workersMin": 0,
        "workersMax": 1,
        "idleTimeout": 30,
        "executionTimeoutMs": 20000,
        "minCudaVersion": "12.3",
        "scalerType": "QUEUE_DELAY",
        "scalerValue": 4,
        "flashboot": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create", "status", "pause"])
    parser.add_argument("--image")
    parser.add_argument(
        "--record",
        type=Path,
        default=Path.home() / ".config/roomshift/speech-deployment.json",
    )
    args = parser.parse_args()
    with client() as connection:
        if args.action == "create":
            if args.record.exists():
                parser.error(
                    "Deployment record exists; inspect it before creating resources."
                )
            if not args.image or not re.fullmatch(
                r"ghcr\.io/[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*@sha256:[a-f0-9]{64}",
                args.image,
            ):
                parser.error("An audited immutable speech image digest is required.")
            record = {
                "state": "template_submission_unconfirmed",
                "image": args.image,
                "model": MODEL,
                "revision": REVISION,
            }
            save(args.record, record)
            response = connection.post(
                "templates",
                json={
                    "name": "roomshift-english-speech",
                    "imageName": args.image,
                    "isServerless": True,
                    "isPublic": False,
                    "containerDiskInGb": 20,
                    "volumeInGb": 0,
                    "env": {"HF_HUB_OFFLINE": "1", "RUNPOD_LOG_LEVEL": "WARN"},
                },
            )
            response.raise_for_status()
            record.update(
                template_id=response.json()["id"],
                state="endpoint_submission_unconfirmed",
            )
            save(args.record, record)
            settings = configuration(record["template_id"])
            response = connection.post("endpoints", json=settings)
            response.raise_for_status()
            record.update(
                endpoint_id=response.json()["id"],
                state="created",
                configuration=settings,
            )
            save(args.record, record)
        else:
            record = json.loads(args.record.read_text())
        endpoint = record["endpoint_id"]
        if not re.fullmatch(r"[a-zA-Z0-9-]+", endpoint):
            raise ValueError("Invalid speech endpoint ID.")
        if args.action == "pause":
            response = connection.patch(
                f"endpoints/{endpoint}", json={"workersMin": 0, "workersMax": 0}
            )
            response.raise_for_status()
        response = connection.get(f"endpoints/{endpoint}")
        response.raise_for_status()
        print(
            json.dumps(
                {
                    key: response.json().get(key)
                    for key in ("id", "name", "workersMin", "workersMax", "gpuTypeIds")
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
