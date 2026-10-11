"""Execute frozen real experiments, resuming completed episodes and GEPA state."""

import fcntl
import os
from pathlib import Path

root = Path(__file__).resolve().parents[1]
run_lock = (root / "results/execution.lock").open("w")
fcntl.flock(run_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
os.environ.setdefault("TAU2_DATA_DIR", str(root / "benchmark/data"))
from evotau_gepa.experiment import Experiment, save

experiment = Experiment(root)
try:
    states = experiment.run()
    experiment.heldout(states)
except Exception as exc:
    save(
        root / "results/interruption.json",
        {
            "type": type(exc).__name__,
            "error": str(exc),
            "budget": experiment.gateway.budget.summary(),
        },
    )
    raise
finally:
    save(root / "results/api_usage.json", experiment.gateway.budget.summary())
