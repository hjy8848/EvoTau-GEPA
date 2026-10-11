from pathlib import Path

from evotau_gepa.reporting import build

print(build(Path(__file__).resolve().parents[1]))
