"""Create synthetic English test audio outside the repository on macOS."""

import subprocess
from pathlib import Path


def main():
    directory = (
        Path.home()
        / "Library/Application Support/Roomshift/artifacts/research-fixtures"
    )
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    statements = {
        "microphone": "SQLite write ahead logging works on a network file system. We can share the database file between our servers.",
        "system": "I prefer a clean white interface. That is just my personal preference.",
    }
    for name, text in statements.items():
        original = directory / f"{name}.aiff"
        output = directory / f"{name}.wav"
        subprocess.run(
            ["say", "-v", "Samantha", "-r", "205", "-o", str(original), text],
            check=True,
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
    print(f"Synthetic research fixtures: {directory}")


if __name__ == "__main__":
    main()
