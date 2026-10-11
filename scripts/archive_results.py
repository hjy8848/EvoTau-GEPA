"""Package raw results into verified, portable archives without deleting originals."""

import hashlib
import json
import tarfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
archive = root / "results/raw_results.tar.gz"
manifest = []
with tarfile.open(archive, "w:gz", compresslevel=6) as tar:
    for directory in ["results/episodes", "results/requests", "results/searches"]:
        for path in sorted((root / directory).rglob("*")):
            if not path.is_file() or path.suffix in {".lock", ".tmp"}:
                continue
            relative = str(path.relative_to(root))
            manifest.append(
                {
                    "path": relative,
                    "bytes": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
            tar.add(path, arcname=relative)
payload = {
    "archive": "results/raw_results.tar.gz",
    "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    "files": manifest,
}
(root / "results/raw_archive_manifest.json").write_text(json.dumps(payload, indent=2))
print(
    json.dumps(
        {
            "archive": str(archive),
            "bytes": archive.stat().st_size,
            "files": len(manifest),
            "sha256": payload["archive_sha256"],
        }
    )
)
