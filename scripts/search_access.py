import shlex
from pathlib import Path


def credentials():
    path = Path.home() / ".config/roomshift/search.env"
    if not path.exists():
        return {}
    if path.stat().st_mode & 0o077:
        raise ValueError("Search credential file must have mode 600.")
    values = {}
    for line in path.read_text().splitlines():
        name, separator, value = line.removeprefix("export ").partition("=")
        if separator and name.strip() == "FIRECRAWL_API_KEY":
            parts = shlex.split(value, comments=True)
            if len(parts) != 1:
                raise ValueError("Invalid search credential format.")
            values[name.strip()] = parts[0]
    return values
