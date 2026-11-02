from huggingface_hub import snapshot_download

from speech_worker.engine import MODEL, REVISION

snapshot_download(
    MODEL,
    revision=REVISION,
    local_dir="/models/whisper",
    allow_patterns=[
        "config.json",
        "preprocessor_config.json",
        "model.bin",
        "tokenizer.json",
        "vocabulary.json",
    ],
)
