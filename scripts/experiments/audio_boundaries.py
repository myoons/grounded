"""Frozen synthetic chunk-boundary experiment; never changes production tuning."""

import argparse
import array
import asyncio
import base64
import csv
import hashlib
import json
import os
import random
import re
import subprocess
import time
import wave
from pathlib import Path

import httpx

from scripts.runpod_access import credentials
from server.meeting_gpu_speech import MODEL, REVISION, RunpodTranscriber

ROOT = Path(__file__).resolve().parents[2]
GRID = {
    "baseline": (600, 6000, 1200),
    "silence_short": (200, 6000, 1200),
    "silence_long": (1000, 6000, 1200),
    "chunk_short": (600, 4000, 1200),
    "chunk_long": (600, 7000, 1200),
    "overlap_none": (600, 6000, 0),
    "overlap_short": (600, 6000, 600),
}
LIVE = {
    "pause": ("baseline", "silence_short", "silence_long"),
    "boundary": (
        "baseline",
        "overlap_none",
        "overlap_short",
        "chunk_short",
        "chunk_long",
    ),
    "quiet": ("baseline",),
    "noise": ("baseline",),
}
NODE = """
import {readFileSync} from 'node:fs';
import {wavSamples} from './desktop/tests/native-fixture.mjs';
let source=readFileSync('./desktop/meeting/chunker.mjs','utf8');
const [silence,maximum,overlap]=JSON.parse(process.argv[2]);
for(const [before,after] of [
 ['this.blocks.length >= 30',`this.blocks.length >= ${maximum/200}`],
 ['this.quietMs >= 600',`this.quietMs >= ${silence}`],
 ['blocks.slice(-6)',overlap ? `blocks.slice(-${overlap/200})` : '[]']
]) {
 if(source.split(before).length!==2) throw Error('Chunker source changed: '+before);
 source=source.replace(before,after);
}
const {UtteranceChunker}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const samples=wavSamples(readFileSync(process.argv[1]));
const chunks=[];
const chunker=new UtteranceChunker(chunk=>chunks.push({...chunk,emitted_at_ms:chunker.samples/16,pcm:Buffer.from(chunk.pcm).toString('base64')}));
for(let offset=0;offset+3200<=samples.length;offset+=3200)chunker.push(samples.slice(offset,offset+3200));
chunker.flush();
process.stdout.write(JSON.stringify(chunks));
"""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def tokens(text):
    return re.findall(r"[a-z]+", text.lower())


def word_error(reference, hypothesis):
    expected, actual = tokens(reference), tokens(hypothesis)
    previous = list(range(len(actual) + 1))
    for index, token in enumerate(expected, 1):
        current = [index]
        for position, candidate in enumerate(actual, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[position] + 1,
                    previous[position - 1] + (token != candidate),
                )
            )
        previous = current
    return previous[-1] / max(1, len(expected))


def synthesize(directory, name, text):
    original, output = directory / f"{name}.aiff", directory / f"{name}.wav"
    subprocess.run(
        ["say", "-v", "Samantha", "-r", "185", "-o", str(original), text], check=True
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(original),
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(output),
        ],
        check=True,
    )
    with wave.open(str(output)) as audio:
        samples = array.array("h", audio.readframes(audio.getnframes()))
    active = [index for index, sample in enumerate(samples) if abs(sample) > 150]
    return samples[max(0, active[0] - 160) : active[-1] + 161]


def write_wav(path, samples):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(samples.tobytes())


def prepare(directory):
    directory.mkdir(parents=True, mode=0o700, exist_ok=False)
    os.chmod(directory, 0o700)
    first = "The export feature is available"
    second = "only on the enterprise plan if the administrator enables it."
    lead = synthesize(directory, "lead", first)
    condition = synthesize(directory, "condition", second)
    natural = synthesize(directory, "natural", first + " " + second)
    pause = lead + array.array("h", [0] * 12800) + condition
    boundary_text = "For our deployment we have confirmed that the export feature is available only on the enterprise plan if the administrator enables it."
    boundary = synthesize(directory, "continuous_boundary", boundary_text)
    generator = random.Random(42)
    noise = array.array(
        "h",
        (
            max(-32768, min(32767, round(sample * 0.8 + generator.uniform(-450, 450))))
            for sample in natural
        ),
    )
    fixtures = {"pause": pause, "boundary": boundary, "quiet": natural, "noise": noise}
    records = []
    for name, samples in fixtures.items():
        path = directory / f"{name}.wav"
        write_wav(path, samples)
        records.append(
            {
                "name": name,
                "sha256": digest(path.read_bytes()),
                "duration_ms": len(samples) / 16,
                "reference": boundary_text
                if name == "boundary"
                else first + " " + second,
            }
        )
    protocol = {
        "synthetic_only": True,
        "voice": "macOS Samantha",
        "rate_wpm": 185,
        "generation": "say then ffmpeg mono PCM16 16kHz; trim outside abs(sample)>150 with 10ms margin",
        "pause_ms": 800,
        "boundary_design": "Continuous longer sentence crossing6s; condition location depends on generated speech",
        "noise": "seed42 uniform +/-450 PCM units; speech gain0.8; noise includes pauses",
        "fixture_parser": "actual native-fixture.mjs appends15s silence; actual chunker flush after blocks",
        "grid": GRID,
        "live_subset": LIVE,
        "paid_call_limit": 24,
        "concurrency": 1,
        "model": MODEL,
        "revision": REVISION,
        "chunker_sha256": digest((ROOT / "desktop/meeting/chunker.mjs").read_bytes()),
        "fixtures": records,
        "limitations": "One synthetic voice, four related clips, no optimality claim. Token WER uses raw concatenated chunks and counts overlap duplicates; not production merge quality. Critical flags are lexical presence, not semantic accuracy.",
    }
    (directory / "protocol.json").write_text(json.dumps(protocol, indent=2))
    rows = []
    for fixture in records:
        for variant, settings in GRID.items():
            raw = subprocess.check_output(
                [
                    "node",
                    "--input-type=module",
                    "-e",
                    NODE,
                    str(directory / f"{fixture['name']}.wav"),
                    json.dumps(settings),
                ],
                cwd=ROOT,
            )
            chunks = json.loads(raw)
            for chunk in chunks:
                chunk["pcm_sha256"] = digest(base64.b64decode(chunk["pcm"]))
            rows.append(
                {
                    "fixture": fixture["name"],
                    "variant": variant,
                    "chunks": chunks,
                    "chunk_count": len(chunks),
                    "audio_ms_submitted": sum(
                        (chunk["end_ms"] - chunk["start_ms"]) for chunk in chunks
                    ),
                }
            )
    (directory / "deterministic.json").write_text(json.dumps(rows, indent=2))
    with (directory / "deterministic.csv").open("w") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=["fixture", "variant", "chunk_count", "audio_ms_submitted"],
        )
        writer.writeheader()
        writer.writerows({key: row[key] for key in writer.fieldnames} for row in rows)
    return protocol, rows


async def run_live(directory):
    if (directory / "live.json").exists() or any(directory.glob("call-*.json")):
        raise ValueError("Live results already exist; artifacts are immutable.")
    protocol = json.loads((directory / "protocol.json").read_text())
    rows = json.loads((directory / "deterministic.json").read_text())
    selected = [
        row for row in rows if row["variant"] in protocol["live_subset"][row["fixture"]]
    ]
    unique = {chunk["pcm_sha256"] for row in selected for chunk in row["chunks"]}
    if len(unique) > 24:
        raise ValueError(
            f"Frozen selection requires {len(unique)} calls, above budget."
        )
    os.environ.update(credentials())
    deployment = json.loads(
        (Path.home() / ".config/roomshift/speech-deployment.json").read_text()
    )
    os.environ["ROOMSHIFT_SPEECH_ENDPOINT_ID"] = deployment["endpoint_id"]
    provider = RunpodTranscriber()
    results, cache = [], {}
    for row in selected:
        outputs = []
        for chunk in row["chunks"]:
            key = chunk["pcm_sha256"]
            if key not in cache:
                started = time.monotonic()
                try:
                    outcome = await provider.transcribe(
                        base64.b64decode(chunk["pcm"]), "en"
                    )
                    outcome["text"] = "".join(
                        word["text"] for word in outcome["words"]
                    ).strip()
                except (
                    httpx.HTTPError,
                    ValueError,
                    TypeError,
                    KeyError,
                    RuntimeError,
                    OSError,
                ) as error:
                    outcome = {"error_type": type(error).__name__}
                outcome["wall_ms_excluding_tts"] = round(
                    (time.monotonic() - started) * 1000, 2
                )
                outcome["pcm_sha256"] = key
                cache[key] = outcome
                (directory / f"call-{len(cache):02d}.json").write_text(
                    json.dumps(outcome, indent=2)
                )
            outputs.append(cache[key])
        text = " ".join(output.get("text", "") for output in outputs)
        reference = next(
            item["reference"]
            for item in protocol["fixtures"]
            if item["name"] == row["fixture"]
        )
        results.append(
            {
                "fixture": row["fixture"],
                "variant": row["variant"],
                "text": text,
                "raw_concatenation_wer": word_error(reference, text),
                "critical_tokens_present": all(
                    token in tokens(text) for token in ("only", "enterprise", "if")
                ),
                "successful_chunks": sum("error" not in output for output in outputs),
                "chunk_count": len(outputs),
                "outputs": outputs,
            }
        )
    report = {
        "real_provider": True,
        "synthetic_audio": True,
        "attempted_calls": len(cache),
        "results": results,
        "protocol_sha256": digest((directory / "protocol.json").read_bytes()),
    }
    (directory / "live.json").write_text(json.dumps(report, indent=2))
    with (directory / "live.csv").open("w") as stream:
        fields = [key for key in results[0] if key != "outputs"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: row[key] for key in fields} for row in results)
    print(
        json.dumps(
            {
                "attempted_calls": len(cache),
                "results": [
                    {key: value for key, value in row.items() if key != "outputs"}
                    for row in results
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "live"))
    parser.add_argument("directory", type=Path)
    arguments = parser.parse_args()
    if arguments.mode == "prepare":
        prepare(arguments.directory)
    else:
        asyncio.run(run_live(arguments.directory))
