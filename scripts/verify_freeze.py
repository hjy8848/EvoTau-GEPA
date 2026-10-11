"""Read-only source, config, data and request-cap verification."""

import hashlib
import json
from pathlib import Path

from evotau_gepa.budget import Budget

root = Path(__file__).resolve().parents[1]
freeze = json.loads((root / "research/protocol_freeze.json").read_text())
for relative, sha in freeze.items():
    assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == sha, relative
manifest = json.loads((root / "configs/experiment_manifest.json").read_text())
for relative, sha in manifest["data_sha256"].items():
    path = root / manifest["data_dir"] / relative
    assert hashlib.sha256(path.read_bytes()).hexdigest() == sha, relative
assert Budget(root / "results/request_budget.sqlite3").summary()["reserved_or_sent"] <= 30000
print("Frozen source/config, official data hashes and request cap verified")
