# Customer v4: three random native-success cases

Before each new Customer search, obtain native-Customer reference results for the
current fixed Service on E at seed 101. The existing `reference-native-customer`
episode cache is reused. If a reference is absent, a future authorized search
must execute it through the normal gateway and request ledger. This change itself
does not execute model requests.

Only tasks with native reward exactly 1 enter Customer GEPA's training pool.
Each reflection iteration draws up to three different tasks uniformly without
replacement. If fewer than three qualify, use each available task once; do not
pad the batch. Draws depend only on the search seed and GEPA iteration, so identical
Free/RC inputs receive identical draws and restarting an iteration is reproducible.
No native-success task means an explicit error before GEPA, with no invented score.

Save all E reference keys/rewards, eligible task IDs, Service hash, search seed
and sampling rule in `results/searches/<run_id>/reflection_sampling.json`.
GEPA records the actual chosen minibatch IDs in its normal iteration trace.
Service updates use a new Service-specific reference identity and eligibility pool.

The sampler changes Customer E selection only. V/H splits, Service sampling,
native scoring, strict repeated discovery criteria and metric-call budgets remain
unchanged. Selected cases retain all Customer utterances and the v3 evidence
excerpts. Shared history remains historical context and is not filtered into the
native-success training pool.

The revision marker is `customer-random-three-v4`; legacy search directories,
including completed v3 selections, cannot be reused. Use new run IDs and a new
source/config freeze before a future experiment. The original freeze, stopped
runs and historical results are unchanged. The v3 input preview remains an offline
historical layout example with two cases, not a new v4 execution result.

Offline verification: 49 tests passed; Ruff lint and formatting checks passed.
Tests exercise distinct/reproducible draws, small pools, native failure exclusion,
Free/RC parity, Service-dependent eligibility, empty pools and legacy selection
rejection. The request ledger remained at 17,901 entries; no model request was made.
