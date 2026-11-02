"""Verify the actual chunker submits no overlap-only tail to GPU ASR."""

import asyncio
import base64
import json
import os
import subprocess
from pathlib import Path

from scripts.runpod_access import credentials
from server.meeting_gpu_speech import RunpodTranscriber


async def main():
    os.environ.update(credentials())
    deployment = json.loads(
        (Path.home() / ".config/roomshift/speech-deployment.json").read_text()
    )
    os.environ["ROOMSHIFT_SPEECH_ENDPOINT_ID"] = deployment["endpoint_id"]
    root = Path(__file__).resolve().parents[1]
    fixture = (
        Path.home()
        / "Library/Application Support/Roomshift/artifacts/research-fixtures/microphone.wav"
    )
    code = """
import {readFileSync} from 'node:fs';
import {UtteranceChunker} from './desktop/meeting/chunker.mjs';
import {wavSamples} from './desktop/tests/native-fixture.mjs';
const samples=wavSamples(readFileSync(process.argv[1]));
const chunks=[];
const chunker=new UtteranceChunker(chunk=>chunks.push({...chunk,pcm:Buffer.from(chunk.pcm).toString('base64')}));
for(let offset=0;offset+3200<=samples.length;offset+=3200)chunker.push(samples.slice(offset,offset+3200));
process.stdout.write(JSON.stringify(chunks));
"""
    raw = await asyncio.to_thread(
        subprocess.check_output,
        ["node", "--input-type=module", "-e", code, str(fixture)],
        cwd=root,
        text=True,
    )
    chunks = json.loads(raw)
    if len(chunks) != 1:
        raise AssertionError(
            "The completed fixture must not create an overlap-only request."
        )
    result = await RunpodTranscriber().transcribe(
        base64.b64decode(chunks[0]["pcm"]), "en"
    )
    text = "".join(word["text"] for word in result["words"]).strip()
    passed = (
        "sqlite" in text.lower()
        and "servers" in text.lower()
        and not result["uncertain"]
    )
    report = {
        "passed": passed,
        "synthetic": True,
        "chunk_count": len(chunks),
        "text": text,
        "uncertain": result["uncertain"],
    }
    destination = (
        fixture.parent.parent / "speech-noise-evaluation/unprimed/chunker-result.json"
    )
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.write_text(json.dumps(report, indent=2))
    print(json.dumps(report))
    if not passed:
        raise SystemExit("GPU did not transcribe the retained complete utterance.")


if __name__ == "__main__":
    asyncio.run(main())
