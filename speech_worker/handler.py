import traceback

import runpod

from speech_worker.engine import SpeechEngine

engine = SpeechEngine()


def handler(job):
    try:
        return engine.transcribe(job["input"])
    except (ValueError, TypeError, KeyError, RuntimeError, OSError) as error:
        frames = traceback.extract_tb(error.__traceback__)
        return {
            "error": "Speech inference failed.",
            "error_type": type(error).__name__,
            "locations": [
                {"function": frame.name, "line": frame.lineno} for frame in frames[-4:]
            ],
        }


runpod.serverless.start({"handler": handler})
