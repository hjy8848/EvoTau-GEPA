# Design decisions

- Official GEPA owns proposal, Pareto selection and search recovery; the adapter only builds real native episodes and reflection rows.
- Thin subclasses append strategy overlays. Retail has no user tools; pass None exactly as upstream.
- A single direct HTTP transport replaces the completion binding in native llm_utils, preserving native message conversion, tools, orchestration and evaluation. This makes each attempted send auditable and disables unobservable client retries.
- SQLite BEGIN IMMEDIATE reservations survive concurrency/crashes; raw responses contain no credentials. Costs stay null because reliable gateway prices were not provided.
- Pin the already installable official revision after verifying upstream Retail/scorer files did not change through HEAD. Copied data match upstream Git blobs, not historical results.
- Customer reviews are one independent call after actual exposure, not an analyst/mutator/validator pipeline. Unsupported evidence is unresolved.
- Verified repair is a scientific prerequisite. Retaining a Service state is stagnation, never a fabricated evolution.
- Budget feasibility uses a small E/V panel, three search repeats, two alternating cycles and full official H Service accuracy. Statistical power is explicitly limited.

- Independent search repeats execute in a plain three-thread pool; native episode batches use four workers. Native LLM seeds are participant-local (verified upstream LLMConfigMixin), not global RNG mutations. Per-episode advisory locks prevent duplicate billing for shared reference/cache identities. This is concurrency tuning within the authorized task, not a new agent framework or queue service.

- M1 has two distinct outputs: the official GEPA candidate selected by V (the standalone GEPA-Service baseline) and repeat-verified E repair evidence. A V-selected candidate is not itself a verified repair. The frozen strict repair acceptance rule gates repair feedback and alternating Service promotions; M1's standalone baseline follows official V selection. Unsuccessful E repair verification remains an explicit zero eligible-pair result, never feedback labeled effective.
