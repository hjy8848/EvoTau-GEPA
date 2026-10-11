# EvoTau-GEPA

Independent research apparatus for repair-conditioned customer–service co-evolution on native Retail. Official GEPA does all prompt search; native tau2 owns policies, tools, tasks, dialogue and scoring. Every LLM role uses the same Qwen3.7-Plus gateway.

## Install and verify

Python 3.12 is required. The checked-in requirements lock pins the executed environment, including the official tau2 Git revision. No old EvoTau runtime dependency.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python -m pip install -e '.[dev]'
TAU2_DATA_DIR="$PWD/benchmark/data" .venv/bin/python -m pytest -q
.venv/bin/ruff check src scripts tests
.venv/bin/ruff format --check src scripts tests
```

The fixed official benchmark data match upstream Git blob hashes (research/data_provenance.json), with SHA256 in the experiment manifest. They are bundled so the repository is independently runnable. H scenarios must not be inspected for prompt development.

## Frozen real API execution

```bash
export EVOTAU_API_BASE=http://10.130.138.46:8010/v1
# Export EVOTAU_API_KEY through your secret manager, or use the macOS Keychain
# account litellm-api-key / service litellm.
.venv/bin/python scripts/reproduce.py
```

This sends real paid API requests. It resumes completed episodes and official GEPA state. The persistent global ledger results/request_budget.sqlite3 never resets, and the hard cap is 30,000 including probes/errors. Do not delete the ledger to resume. Credentials are read at runtime and are not stored in outputs. Actual costs remain unknown because the gateway rate is not verified.

Frozen configuration: configs/experiment_manifest.json. Protocol: research/protocol.md; source/config snapshot: research/protocol_freeze.json. Original user requirements: research/user_request.md. Execution state and failures: results/states.json, results/interruption.json and results/execution.log, where present.

## Offline paper reconstruction

```bash
.venv/bin/python scripts/build_paper_results.py
```

This makes no API calls. Output: paper/experiment_report.md, paper/tables/, paper/figures/, results/results.csv, summary.json, statistical_analysis.json, discovery_cases.json, api_usage.json. Raw transcripts and scoring decomposition: results/episodes/; full gateway payloads/responses: results/requests/; official search state and candidates: results/searches/.

Candidate histories retain even evaluated candidates that GEPA did not accept. Automatic customer evidence reviews are explicitly marked; unresolved cases get zero discovery credit. Observed task success is never replaced by a guessed native reward.

The frozen study is exploratory: small E/V panels and three search repeats. H includes historically exposed task IDs, documented in research/historical_overlap.json. A real, repeat-verified repair is required to test repair conditioning. No verified repair means the primary RC question is unidentifiable for that repeat, not a negative causal result.

When raw results are archived for Git delivery, first restore and verify them:

```bash
.venv/bin/python scripts/unpack_results.py
.venv/bin/python scripts/verify_freeze.py
```

The archive manifest verifies every original file, including official GEPA checkpoint state. Unpacking restores completed-episode caches so frozen execution can resume without rebilling completed episodes. Keep the durable SQLite ledger alongside the archive. `scripts/archive_results.py` packages originals without deleting them; run only after the experiment process exits.
