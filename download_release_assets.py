"""Download and verify this private release's resumable binary parts using gh."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener, getproxies

ROOT = Path(__file__).resolve().parent
DEVELOPMENT = ("Sentinel-dev-wheels-py312-win64.zip", "Sentinel-models-v0.3.1.zip")
ALLOWED = {*DEVELOPMENT, "Sentinel-Setup.exe", "Sentinel-portable-v0.3.1.zip", "model.tar.bz2"}


class GithubRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        target = urlsplit(newurl)
        if target.scheme != "https" or target.hostname not in {
            "api.github.com", "github.com", "release-assets.githubusercontent.com",
            "objects.githubusercontent.com", "github-releases.githubusercontent.com",
        }:
            raise ValueError("Unexpected release download destination")
        return super().redirect_request(request, response, code, message, headers, newurl)


def download_verified(asset: dict, expected: dict, directory: Path, token: str, proxies: dict) -> Path:
    target = directory / expected["name"]
    if target.exists():
        if target.stat().st_size == expected["size"] and digest(target) == expected["sha256"]:
            return target
        raise ValueError(f"Existing download differs; move it aside first: {target}")
    if asset.get("state") != "uploaded" or asset.get("size") != expected["size"] or asset.get("digest") != "sha256:" + expected["sha256"]:
        raise ValueError(f"Release asset is missing or unverified: {target.name}")
    for attempt in range(4):
        temporary = None
        try:
            opener = build_opener(ProxyHandler(proxies), GithubRedirects())
            request = Request(f'https://api.github.com/repos/one-sharp/Sentinel/releases/assets/{int(asset["id"])}?sentinel={time.time_ns()}',
                              headers={"Accept": "application/octet-stream", "Cache-Control": "no-cache", "User-Agent": "Sentinel-migration"})
            # urllib does not forward unredirected headers to the signed asset URL.
            request.add_unredirected_header("Authorization", "Bearer " + token)
            count = 0
            checksum = hashlib.sha256()
            with opener.open(request, timeout=45) as response, tempfile.NamedTemporaryFile(prefix=".sentinel-download-", dir=directory, delete=False) as output:
                temporary = Path(output.name)
                while block := response.read(128 * 1024):
                    count += len(block)
                    if count > expected["size"]:
                        raise ValueError("Download exceeds expected size")
                    checksum.update(block)
                    output.write(block)
            if count != expected["size"] or checksum.hexdigest() != expected["sha256"]:
                raise ValueError("Downloaded content failed SHA256 verification")
            if target.exists():
                raise ValueError(f"Destination appeared during download: {target}")
            temporary.rename(target)
            print(f"Downloaded: {target.name}", flush=True)
            return target
        except (OSError, ValueError):
            if attempt == 3:
                raise
            time.sleep(2 * (attempt + 1))
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    raise RuntimeError("Download retries exhausted")


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
    environment = os.environ.copy()
    # gh follows environment proxies; Windows users may configure only WinINET.
    if proxy := getproxies().get("https"):
        environment.setdefault("HTTPS_PROXY", proxy)
    manifest = read_manifest(ROOT / "release" / "assets-manifest.json")
    selected = ALLOWED if args.all else set(args.asset or DEVELOPMENT)
    args.directory.mkdir(parents=True, exist_ok=True)
    def gh_json(arguments):
        result = subprocess.run(["gh", *arguments], check=True, capture_output=True, text=True, env=environment)
        return json.loads(result.stdout)
    release = gh_json(["release", "view", manifest["tag"], "--repo", manifest["repo"], "--json", "databaseId"])
    pages = gh_json(["api", "--paginate", "--slurp", f'repos/{manifest["repo"]}/releases/{int(release["databaseId"])}/assets?per_page=100'])
    assets = {asset["name"]: asset for page in pages for asset in page}
    token = subprocess.run(["gh", "auth", "token"], check=True, capture_output=True, text=True).stdout.strip()
    proxies = getproxies()
    for item in manifest["files"]:
        if item["name"] not in selected:
            continue
        target = args.directory / item["name"]
        if target.exists():
            print(f"Verified: {assemble(item, args.directory)}", flush=True)
            continue
        if item["delivery"] == "file":
            download_verified(assets.get(item["name"], {}), item, args.directory, token, proxies)
            print(f"Verified: {assemble(item, args.directory)}", flush=True)
            continue
        for part in item["parts"]:
            path = args.directory / part["name"]
            if path.exists() and (path.stat().st_size != part["size"] or digest(path) != part["sha256"]):
                raise ValueError(f"Corrupt downloaded part; remove it and retry: {path}")
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs = [pool.submit(download_verified, assets.get(part["name"], {}), part, args.directory, token, proxies) for part in item["parts"]]
            for job in jobs:
                job.result()
        print(f"Verified: {assemble(item, args.directory)}", flush=True)


if __name__ == "__main__":
    main()
