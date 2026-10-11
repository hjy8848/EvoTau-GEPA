"""Verify and restore cached episodes, raw requests and official GEPA state."""

import hashlib
import json
import tarfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / "results/raw_archive_manifest.json").read_text())
archive = root / manifest["archive"]
assert hashlib.sha256(archive.read_bytes()).hexdigest() == manifest["archive_sha256"]
with tarfile.open(archive) as tar:
    for item in tar.getmembers():
        if not item.isfile() or not (root / item.name).resolve().is_relative_to(root):
            raise ValueError("Unexpected archive member")
    tar.extractall(root, filter="data")
for file in manifest["files"]:
    assert hashlib.sha256((root / file["path"]).read_bytes()).hexdigest() == file["sha256"], file[
        "path"
    ]
print(f"Verified and restored {len(manifest['files'])} raw result files")
