import base64
import binascii
import json
import subprocess
import time
from pathlib import Path

MODEL = "Systran/faster-whisper-large-v3"
REVISION = "edaa852ec7e145841d8ffdb056a99866b5f0a478"
SAMPLE_RATE = 16000
MAX_BYTES = SAMPLE_RATE * 7 * 2


def validate_model_files(directory):
    directory = Path(directory)
    for name in (
        "config.json",
        "model.bin",
        "tokenizer.json",
        "vocabulary.json",
        "preprocessor_config.json",
    ):
        if not (directory / name).is_file():
            raise ValueError("Speech model files are incomplete.")
    config = json.loads((directory / "preprocessor_config.json").read_text())
    if config.get("feature_size") != 128 or config.get("sampling_rate") != SAMPLE_RATE:
        raise ValueError(
            "Whisper large-v3 requires the pinned 128-bin feature extractor."
        )


def decode_request(request):
    if not isinstance(request, dict) or set(request) - {"pcm", "language"}:
        raise ValueError("Invalid speech request fields.")
    if request.get("language") != "en":
        raise ValueError("Only English transcription is supported.")
    encoded = request.get("pcm")
    if not isinstance(encoded, str) or len(encoded) > 300000:
        raise ValueError("Invalid PCM payload.")
    try:
        pcm = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("Invalid PCM encoding.") from None
    if not 3200 <= len(pcm) <= MAX_BYTES or len(pcm) % 2:
        raise ValueError("PCM must be 0.1 to 7 seconds of mono 16 kHz PCM16.")
    return pcm


class SpeechEngine:
    def __init__(self):
        import numpy as np
        from faster_whisper import WhisperModel

        validate_model_files("/models/whisper")
        self.model = WhisperModel(
            "/models/whisper",
            device="cuda",
            compute_type="float16",
            local_files_only=True,
            num_workers=1,
        )
        try:
            self.gpu_name = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                text=True,
                timeout=3,
            ).splitlines()[0][:100]
        except (OSError, subprocess.SubprocessError, IndexError):
            self.gpu_name = "unavailable"
        segments, _info = self.model.transcribe(
            np.zeros(SAMPLE_RATE, dtype=np.float32),
            language="en",
            beam_size=5,
            temperature=0,
            word_timestamps=True,
            vad_filter=False,
            condition_on_previous_text=False,
        )
        list(segments)

    def transcribe(self, request):
        import numpy as np

        started = time.monotonic()
        pcm = decode_request(request)
        audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        words = []
        uncertain = False
        if np.sqrt(np.mean(audio * audio)) >= 0.003:
            segments, _info = self.model.transcribe(
                audio,
                language="en",
                task="transcribe",
                beam_size=5,
                temperature=0,
                word_timestamps=True,
                vad_filter=True,
                condition_on_previous_text=False,
            )
            for segment in segments:
                if segment.no_speech_prob >= 0.75:
                    continue
                if segment.avg_logprob < -1 or segment.compression_ratio > 2.4:
                    uncertain = True
                    continue
                words.extend(
                    {"text": word.word, "start": word.start, "end": word.end}
                    for word in segment.words or []
                )
        return {
            "words": words,
            "language": "en",
            "uncertain": uncertain,
            "model": MODEL,
            "revision": REVISION,
            "device": "cuda",
            "gpu_name": self.gpu_name,
            "inference_ms": round((time.monotonic() - started) * 1000, 2),
        }
