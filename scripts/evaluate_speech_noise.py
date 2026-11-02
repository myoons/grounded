"""Check actual GPU ASR against generated speech and non-speech fixtures."""

import array
import asyncio
import json
import math
import os
import random
import re
import subprocess
import wave
from pathlib import Path

from scripts.runpod_access import credentials
from server.meeting_gpu_speech import RunpodTranscriber

RATE = 16000


def pcm(values):
    samples = array.array(
        "h", (round(max(-1, min(1, value)) * 32767) for value in values)
    )
    return samples.tobytes()


async def main():
    os.environ.update(credentials())
    deployment = json.loads(
        (Path.home() / ".config/roomshift/speech-deployment.json").read_text()
    )
    os.environ["ROOMSHIFT_SPEECH_ENDPOINT_ID"] = deployment["endpoint_id"]
    directory = (
        Path.home()
        / "Library/Application Support/Roomshift/artifacts/speech-noise-evaluation/unprimed"
    )
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    generator = random.Random(42)
    tone = [
        0.06 * math.sin(2 * math.pi * 440 * index / RATE) for index in range(RATE * 6)
    ]
    music = [
        sum(
            math.sin(2 * math.pi * frequency * index / RATE)
            for frequency in ((220, 277, 330) if index < RATE * 3 else (196, 247, 294))
        )
        * 0.025
        * (0.6 + 0.4 * math.sin(2 * math.pi * index / RATE))
        for index in range(RATE * 6)
    ]
    noise = [
        generator.uniform(-0.06, 0.06)
        * max(0, math.sin(2 * math.pi * 0.8 * index / RATE))
        for index in range(RATE * 6)
    ]
    cases = [
        ("silence", bytes(RATE * 12), None),
        ("tone", pcm(tone), None),
        ("instrumental", pcm(music), None),
        ("breath_like_noise", pcm(noise), None),
    ]
    for name, text in [
        ("thanks", "Thank you."),
        ("short_confirmation", "Yes, that is correct."),
        ("technical_speech", "Python three point eight has reached end of life."),
    ]:
        aiff = directory / f"{name}.aiff"
        output = directory / f"{name}.wav"
        await asyncio.to_thread(
            subprocess.run, ["say", "-v", "Samantha", "-o", str(aiff), text], check=True
        )
        await asyncio.to_thread(
            subprocess.run,
            [
                "afconvert",
                "-f",
                "WAVE",
                "-d",
                "LEI16@16000",
                "-c",
                "1",
                str(aiff),
                str(output),
            ],
            check=True,
        )
        with wave.open(str(output)) as audio:
            raw = audio.readframes(RATE * 7)
        cases.append(
            (
                name,
                raw,
                "thank"
                if name == "thanks"
                else "correct"
                if name == "short_confirmation"
                else "python",
            )
        )
        if name == "technical_speech":
            speech = array.array("h", raw)
            mixed = pcm(
                sample / 32768 * 0.8 + music[index % len(music)] * 0.5
                for index, sample in enumerate(speech)
            )
            cases.append(("speech_with_music", mixed, "python"))
    semaphore = asyncio.Semaphore(2)
    results = []
    with wave.open(
        str(directory.parent.parent / "research-fixtures/microphone.wav")
    ) as audio:
        original = audio.readframes(audio.getnframes())
    for duration in (0.3, 0.7, 1.2):
        cases.append(
            (
                f"boundary_tail_{duration}",
                original[-int(duration * 32000) :] + bytes(32000),
                {"our", "servers", "between", "the", "server"},
            )
        )

    async def evaluate(case, repetition):
        name, audio, expected = case
        async with semaphore:
            result = await RunpodTranscriber().transcribe(audio, "en")
            text = "".join(word["text"] for word in result["words"]).strip()
            if isinstance(expected, set):
                tokens = re.findall(r"[a-z]+", text.lower())
                passed = len(tokens) <= 4 and set(tokens) <= expected
            else:
                passed = (
                    not text
                    if expected is None
                    else expected in text.lower() and not result["uncertain"]
                )
            row = {
                "fixture": name,
                "repetition": repetition,
                "text": text,
                "uncertain": result["uncertain"],
                "passed": passed,
                "inference_ms": result["inference_ms"],
                "round_trip_ms": result["round_trip_ms"],
            }
            results.append(row)
            (directory / "result.json").write_text(
                json.dumps(
                    {
                        "synthetic_only": True,
                        "model": "large-v3 CUDA",
                        "results": results,
                    },
                    indent=2,
                )
            )
            print(json.dumps(row), flush=True)

    await asyncio.gather(
        *(evaluate(case, repetition) for repetition in range(2) for case in cases)
    )
    if not all(row["passed"] for row in results):
        raise SystemExit(
            "Some noise/speech checks failed; inspect evidence before changing thresholds."
        )


if __name__ == "__main__":
    asyncio.run(main())
