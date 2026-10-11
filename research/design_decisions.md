# Design decisions

- Official GEPA owns proposal, Pareto selection and search recovery; the adapter only builds real native episodes and reflection rows.
- Thin subclasses append strategy overlays. Retail has no user tools; pass None exactly as upstream.
- A single direct HTTP transport replaces the completion binding in native llm_utils, preserving native message conversion, tools, orchestration and evaluation. This makes each attempted send auditable and disables unobservable client retries.
- SQLite BEGIN IMMEDIATE reservations survive concurrency/crashes; raw responses contain no credentials. Costs stay null because reliable gateway prices were not provided.
- Pin the already installable official revision after verifying upstream Retail/scorer files did not change through HEAD. Copied data match upstream Git blobs, not historical results.
- Customer reviews are one independent call after actual exposure, not an analyst/mutator/validator pipeline. Unsupported evidence is unresolved.
- Verified repair is a scientific prerequisite. Retaining a Service state is stagnation, never a fabricated evolution.
- Budget feasibility uses a small E/V panel, three search repeats, two alternating cycles and full official H Service accuracy. Statistical power is explicitly limited.
