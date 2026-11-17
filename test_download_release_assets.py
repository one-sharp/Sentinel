import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import Request

from download_release_assets import ALLOWED, GithubRedirects, assemble, download_verified, read_manifest


def entry(name="Sentinel-models-v0.3.1.zip"):
    blocks = [b"first model block", b"second model block"]
    return {"name": name, "delivery": "parts", "size": sum(map(len, blocks)),
            "sha256": hashlib.sha256(b"".join(blocks)).hexdigest(),
            "parts": [{"name": f"{name}.part{i:03d}", "size": len(block), "sha256": hashlib.sha256(block).hexdigest()}
                      for i, block in enumerate(blocks, 1)]}, blocks


class ReleaseRecoveryTests(unittest.TestCase):
    def test_redirect_does_not_forward_github_token(self):
        request = Request("https://api.github.com/repos/one-sharp/Sentinel/releases/assets/1")
        request.add_unredirected_header("Authorization", "Bearer test-placeholder")
        redirects = GithubRedirects()
        redirected = redirects.redirect_request(request, None, 302, "Found", {}, "https://release-assets.githubusercontent.com/example")
        self.assertIsNone(redirected.get_header("Authorization"))
        for destination in ("http://release-assets.githubusercontent.com/example", "https://unexpected.example/file"):
            with self.assertRaises(ValueError):
                redirects.redirect_request(request, None, 302, "Found", {}, destination)

    def test_download_verifies_payload_before_publishing_file(self):
        item, blocks = entry()
        expected = item["parts"][0]
        asset = {"id": 1, "state": "uploaded", "size": expected["size"], "digest": "sha256:" + expected["sha256"]}
        with tempfile.TemporaryDirectory() as directory, patch("download_release_assets.build_opener") as factory:
            factory.return_value.open.return_value = io.BytesIO(blocks[0])
            result = download_verified(asset, expected, Path(directory), "test-placeholder", {})
            self.assertEqual(result.read_bytes(), blocks[0])
            request = factory.return_value.open.call_args.args[0]
            self.assertIn("Authorization", request.unredirected_hdrs)
            self.assertNotIn("Authorization", request.headers)
            self.assertFalse(list(Path(directory).glob(".sentinel-download-*")))

    def test_corrupt_download_never_publishes_a_partial_file(self):
        item, _ = entry()
        expected = item["parts"][0]
        asset = {"id": 1, "state": "uploaded", "size": expected["size"], "digest": "sha256:" + expected["sha256"]}
        with tempfile.TemporaryDirectory() as directory, patch("download_release_assets.build_opener") as factory, patch("download_release_assets.time.sleep"):
            factory.return_value.open.side_effect = lambda *a, **kw: io.BytesIO(b"corrupt")
            with self.assertRaisesRegex(ValueError, "SHA256"):
                download_verified(asset, expected, Path(directory), "test-placeholder", {})
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_assemble_verifies_and_reuses_complete_file(self):
        item, blocks = entry()
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            for part, block in zip(item["parts"], blocks):
                (directory / part["name"]).write_bytes(block)
            result = assemble(item, directory)
            self.assertEqual(result.read_bytes(), b"".join(blocks))
            self.assertEqual(assemble(item, directory), result)
            self.assertFalse(list(directory.glob("*.assembling")))

    def test_corrupt_part_does_not_create_output(self):
        item, blocks = entry()
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            for part, block in zip(item["parts"], blocks):
                (directory / part["name"]).write_bytes(block)
            (directory / item["parts"][0]["name"]).write_bytes(b"corrupt")
            with self.assertRaisesRegex(ValueError, "corrupt part"):
                assemble(item, directory)
            self.assertFalse((directory / item["name"]).exists())

    def test_existing_different_file_is_preserved(self):
        item, _ = entry()
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            result = directory / item["name"]
            result.write_bytes(b"keep this")
            with self.assertRaisesRegex(ValueError, "Existing file differs"):
                assemble(item, directory)
            self.assertEqual(result.read_bytes(), b"keep this")

    def test_whole_checksum_failure_removes_only_temporary_file(self):
        item, blocks = entry()
        item["sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            for part, block in zip(item["parts"], blocks):
                (directory / part["name"]).write_bytes(block)
            with self.assertRaisesRegex(ValueError, "failed verification"):
                assemble(item, directory)
            self.assertFalse((directory / item["name"]).exists())
            self.assertFalse(list(directory.glob("*.assembling")))
            self.assertTrue(all((directory / part["name"]).exists() for part in item["parts"]))

    def test_manifest_rejects_paths_size_errors_and_unknown_files(self):
        original = {"schema": 1, "repo": "one-sharp/Sentinel", "tag": "v0.3.1-logic",
                    "files": [entry(name)[0] for name in sorted(ALLOWED)]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(original), encoding="utf-8")
            read_manifest(path)
            for mutation in ("path", "size", "unknown"):
                value = copy.deepcopy(original)
                if mutation == "path":
                    value["files"][0]["parts"][0]["name"] = "../private.env"
                elif mutation == "size":
                    value["files"][0]["size"] += 1
                else:
                    value["files"][0]["name"] = "private.env"
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(ValueError):
                    read_manifest(path)


if __name__ == "__main__":
    unittest.main()
