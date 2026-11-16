"""Prepare verifiable 8 MiB release parts for unreliable upload connections."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = [ROOT / "release" / name for name in (
    "Sentinel-dev-wheels-py312-win64.zip", "Sentinel-models-v0.3.1.zip",
    "Sentinel-Setup.exe", "Sentinel-portable-v0.3.1.zip",
)] + [ROOT / "model.tar.bz2"]


def main() -> None:
    directory = ROOT / ".downloads" / "release-parts"
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {"schema": 1, "repo": "one-sharp/Sentinel", "tag": "v0.3.1-logic", "files": []}
    for path in FILES:
        whole = hashlib.sha256()
        entry = {"name": path.name, "size": 0, "delivery": "file" if path.name == "Sentinel-dev-wheels-py312-win64.zip" else "parts", "parts": []}
        with path.open("rb") as source:
            for index, block in enumerate(iter(lambda: source.read(8 * 1024 * 1024), b""), 1):
                name = f"{path.name}.part{index:03d}"
                (directory / name).write_bytes(block)
                entry["size"] += len(block)
                whole.update(block)
                entry["parts"].append({"name": name, "size": len(block), "sha256": hashlib.sha256(block).hexdigest()})
        entry["sha256"] = whole.hexdigest()
        manifest["files"].append(entry)
        print(f"Split {path.name}: {len(entry['parts'])} parts", flush=True)
    (ROOT / "release" / "assets-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
