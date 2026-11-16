import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from download_release_assets import ALLOWED, assemble, read_manifest


def entry(name="Sentinel-models-v0.3.1.zip"):
    blocks = [b"first model block", b"second model block"]
    return {"name": name, "delivery": "parts", "size": sum(map(len, blocks)),
            "sha256": hashlib.sha256(b"".join(blocks)).hexdigest(),
            "parts": [{"name": f"{name}.part{i:03d}", "size": len(block), "sha256": hashlib.sha256(block).hexdigest()}
                      for i, block in enumerate(blocks, 1)]}, blocks


class ReleaseRecoveryTests(unittest.TestCase):
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
