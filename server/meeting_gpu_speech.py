import base64
import math
import os

from server.runpod_jobs import RunpodJobClient

MODEL = "Systran/faster-whisper-large-v3"
REVISION = "edaa852ec7e145841d8ffdb056a99866b5f0a478"


def validate_output(output, duration):
    if not isinstance(output, dict) or (
        output.get("model"),
        output.get("revision"),
        output.get("device"),
        output.get("language"),
    ) != (MODEL, REVISION, "cuda", "en"):
        raise ValueError("Unexpected GPU speech model or device.")
    words = output.get("words")
    if (
        not isinstance(words, list)
        or len(words) > 500
        or type(output.get("uncertain")) is not bool
    ):
        raise ValueError("Invalid GPU speech output.")
    previous = 0.0
    for word in words:
        if not isinstance(word, dict) or set(word) != {"text", "start", "end"}:
            raise ValueError("Invalid speech word.")
        if not isinstance(word["text"], str) or not 1 <= len(word["text"]) <= 500:
            raise ValueError("Invalid speech text.")
        start, end = word["start"], word["end"]
        if any(
            type(value) not in (float, int) or not math.isfinite(value)
            for value in (start, end)
        ):
            raise ValueError("Invalid speech timestamps.")
        if not previous <= start <= end <= duration + 0.1:
            raise ValueError("Out-of-range speech timestamps.")
        previous = start
    inference = output.get("inference_ms")
    if (
        type(inference) not in (int, float)
        or not math.isfinite(inference)
        or inference < 0
    ):
        raise ValueError("Invalid inference timing.")
    gpu_name = output.get("gpu_name", "unavailable")
    if not isinstance(gpu_name, str) or len(gpu_name) > 100:
        raise ValueError("Invalid GPU name.")
    return {
        "words": words,
        "language": "en",
        "uncertain": output["uncertain"],
        "inference_ms": inference,
        "gpu_name": gpu_name,
    }


class RunpodTranscriber:
    def __init__(self, transport=None, timeout=25):
        self.jobs = RunpodJobClient(
            os.environ.get("ROOMSHIFT_SPEECH_ENDPOINT_ID", ""),
            os.environ.get("RUNPOD_API_KEY", ""),
            transport=transport,
            timeout=timeout,
        )

    def ready(self):
        return self.jobs.ready()

    async def transcribe(self, pcm, language):
        if language != "en" or not 3200 <= len(pcm) <= 224000 or len(pcm) % 2:
            raise ValueError("Expected bounded English PCM16 audio.")
        response = await self.jobs.run(
            {
                "pcm": base64.b64encode(pcm).decode(),
                "language": "en",
            }
        )
        result = validate_output(response.get("output"), len(pcm) / 32000)
        result["round_trip_ms"] = response["round_trip_ms"]
        return result

    async def stop(self):
        await self.jobs.stop()
