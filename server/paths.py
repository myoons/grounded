import os
import sys
from pathlib import Path


def default_data_dir() -> Path:
    override = os.environ.get("ROOMSHIFT_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Roomshift"
    return (
        Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
        / "roomshift"
    )
