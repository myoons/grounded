"""Inspect the tracked submission without printing any discovered credential value."""

import argparse
import hashlib
import json
import re
import subprocess
import tomllib
from pathlib import Path

from scripts.runpod_access import credentials
from scripts.search_access import credentials as search_credentials

PATTERNS = (
    rb"(?:rpa_|ghp_|github_pat_|sk-)[A-Za-z0-9_-]{20,}",
    rb"fc-[a-f0-9]{24,}",
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
)
SOURCE_SUFFIXES = {".py", ".mjs", ".cjs", ".js", ".swift"}
PRIVATE_SUFFIXES = {
    ".wav",
    ".aiff",
    ".mp3",
    ".m4a",
    ".mp4",
    ".mov",
    ".sqlite",
    ".db",
    ".asar",
    ".zip",
    ".pdf",
    ".pem",
    ".key",
}


def private_artifact(name):
    path = Path(name)
    return path.suffix.lower() in PRIVATE_SUFFIXES or (
        path.name != ".env.example"
        and (
            path.name == ".env"
            or path.name.startswith(".env.")
            or path.suffix == ".env"
        )
    )


def inspect_history(root, known_secrets=()):
    entries = (
        subprocess.check_output(["git", "rev-list", "--objects", "--all"], cwd=root)
        .decode()
        .splitlines()
    )
    findings = []
    scanned = 0
    for entry in entries:
        identifier, _, name = entry.partition(" ")
        kind = subprocess.check_output(
            ["git", "cat-file", "-t", identifier], cwd=root
        ).strip()
        if kind not in (b"blob", b"commit", b"tag"):
            continue
        data = subprocess.check_output(
            ["git", "cat-file", kind.decode(), identifier], cwd=root
        )
        scanned += 1
        location = {"object": identifier, "path": name or kind.decode()}
        if any(secret and secret in data for secret in known_secrets):
            findings.append({**location, "kind": "configured_secret"})
        if any(re.search(pattern, data) for pattern in PATTERNS):
            findings.append({**location, "kind": "credential_pattern"})
        if kind == b"blob" and private_artifact(name):
            findings.append({**location, "kind": "private_or_generated_artifact"})
    return {"passed": not findings, "objects_scanned": scanned, "findings": findings}


def inspect(root, known_secrets=()):
    paths = (
        subprocess.check_output(["git", "ls-files", "-z"], cwd=root)
        .decode()
        .split("\0")
    )
    inventory = []
    findings = []
    for name in filter(None, paths):
        path = root / name
        if not path.exists():
            continue
        if path.is_symlink() or not path.is_file():
            findings.append({"path": name, "kind": "unexpected_file_type"})
            continue
        data = path.read_bytes()
        if any(secret and secret in data for secret in known_secrets):
            findings.append({"path": name, "kind": "configured_secret"})
        if any(re.search(pattern, data) for pattern in PATTERNS):
            findings.append({"path": name, "kind": "credential_pattern"})
        if private_artifact(name):
            findings.append({"path": name, "kind": "private_or_generated_artifact"})
        is_png = path.suffix.lower() == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n")
        if not is_png and any(
            "\uac00" <= character <= "\ud7a3"
            for character in data.decode(errors="replace")
        ):
            findings.append({"path": name, "kind": "non_english_hangul"})
        inventory.append(
            {
                "path": name,
                "bytes": len(data),
                "lines": len(data.splitlines()),
                "sha256": hashlib.sha256(data).hexdigest(),
                "first_party_code": path.suffix in SOURCE_SUFFIXES,
            }
        )
    tomllib.loads((root / "uv.lock").read_text())
    npm = json.loads((root / "desktop/package-lock.json").read_text())
    for name, package in npm["packages"].items():
        if name and not package.get("integrity"):
            findings.append({"path": name, "kind": "missing_npm_integrity"})
    return {"passed": not findings, "files": inventory, "findings": findings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args()
    secrets = {}
    for loader in (credentials, search_credentials):
        try:
            secrets.update(loader())
        except FileNotFoundError:
            continue
    report = inspect(args.root, [value.encode() for value in secrets.values()])
    report["configured_secret_count"] = len(secrets)
    if args.history:
        report["history"] = inspect_history(
            args.root, [value.encode() for value in secrets.values()]
        )
        report["passed"] = report["passed"] and report["history"]["passed"]
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.output.write_text(json.dumps(report, indent=2))
    args.output.chmod(0o600)
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "files": len(report["files"]),
                "findings": report["findings"],
                "history": report.get("history"),
            }
        )
    )
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
