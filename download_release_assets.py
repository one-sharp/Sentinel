"""Download and verify this private release's resumable binary parts using gh."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
DEVELOPMENT = ("Sentinel-dev-wheels-py312-win64.zip", "Sentinel-models-v0.3.1.zip")
ALLOWED = {*DEVELOPMENT, "Sentinel-Setup.exe", "Sentinel-portable-v0.3.1.zip", "model.tar.bz2"}


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def read_manifest(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != 1 or value.get("repo") != "one-sharp/Sentinel" or value.get("tag") != "v0.3.1-logic":
        raise ValueError("Unsupported release manifest")
    files = value.get("files", [])
    if {item.get("name") for item in files} != ALLOWED or len(files) != len(ALLOWED):
        raise ValueError("Unexpected release files")
    for item in files:
        if item.get("delivery") not in ("file", "parts"):
            raise ValueError("Invalid delivery type")
        parts = item.get("parts", [])
        if not 0 < len(parts) <= 256 or not isinstance(item.get("size"), int) or item["size"] <= 0:
            raise ValueError("Invalid file size or parts")
        for index, part in enumerate(parts, 1):
            if part.get("name") != f'{item["name"]}.part{index:03d}' or not isinstance(part.get("size"), int) or not 0 < part["size"] <= 8 * 1024 * 1024:
                raise ValueError("Invalid part name or size")
        if sum(part["size"] for part in parts) != item["size"]:
            raise ValueError("Part sizes do not match file")
        if any(not re.fullmatch(r"[0-9a-f]{64}", entry.get("sha256", "")) for entry in [item, *parts]):
            raise ValueError("Invalid SHA256")
    return value


def assemble(item: dict, directory: Path) -> Path:
    target = directory / item["name"]
    if target.exists():
        if target.stat().st_size == item["size"] and digest(target) == item["sha256"]:
            return target
        raise ValueError(f"Existing file differs; move it aside first: {target}")
    for part in item["parts"]:
        path = directory / part["name"]
        if not path.is_file() or path.stat().st_size != part["size"] or digest(path) != part["sha256"]:
            raise ValueError(f"Missing or corrupt part: {path}")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=target.name + ".", suffix=".assembling", dir=directory, delete=False) as output:
            temporary = Path(output.name)
            for part in item["parts"]:
                with (directory / part["name"]).open("rb") as source:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        output.write(block)
        if temporary.stat().st_size != item["size"] or digest(temporary) != item["sha256"]:
            raise ValueError(f"Assembled file failed verification: {target.name}")
        temporary.rename(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="Also restore installer, portable app and original ASR archive")
    parser.add_argument("--asset", choices=sorted(ALLOWED), action="append", help="Restore selected file(s)")
    parser.add_argument("--directory", type=Path, default=ROOT / ".downloads")
    args = parser.parse_args()
    manifest = read_manifest(ROOT / "release" / "assets-manifest.json")
    selected = ALLOWED if args.all else set(args.asset or DEVELOPMENT)
    args.directory.mkdir(parents=True, exist_ok=True)
    for item in manifest["files"]:
        if item["name"] not in selected:
            continue
        target = args.directory / item["name"]
        if target.exists():
            print(f"Verified: {assemble(item, args.directory)}", flush=True)
            continue
        if item["delivery"] == "file":
            subprocess.run(["gh", "release", "download", manifest["tag"], "--repo", manifest["repo"], "--dir", str(args.directory), "--pattern", item["name"]], check=True)
            print(f"Verified: {assemble(item, args.directory)}", flush=True)
            continue
        command = ["gh", "release", "download", manifest["tag"], "--repo", manifest["repo"], "--dir", str(args.directory), "--skip-existing"]
        for part in item["parts"]:
            path = args.directory / part["name"]
            if path.exists() and (path.stat().st_size != part["size"] or digest(path) != part["sha256"]):
                raise ValueError(f"Corrupt downloaded part; remove it and retry: {path}")
            command += ["--pattern", part["name"]]
        subprocess.run(command, check=True)
        print(f"Verified: {assemble(item, args.directory)}", flush=True)


if __name__ == "__main__":
    main()
