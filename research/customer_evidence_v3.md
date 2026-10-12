# Customer evidence v3: reflection excerpts

This revision changes only what Customer GEPA reflection sees. It retains the v2
role boundary and review safeguards, official GEPA proposal/selection, native scoring,
strict final discovery predicate and all historical results. No model experiment was
restarted. The v1 source/config freeze is unchanged and blocks mixed-version resumption.
The revision marker `customer-evidence-v3` refuses reuse of v2 search checkpoints.

## Reflection input

Each minibatch case retains its full native user scenario and every Customer utterance
with original message indices. Environment evidence is stored once, separately:

- Service messages immediately surrounding Customer turns;
- every actual tool decision with unchanged arguments and all tool responses;
- indexed review evidence and its surrounding context, even when the final score is zero;
- JSON tool results serialized directly rather than recursively expanded into Markdown.

Product catalogues keep variants explicitly referenced in actual participant dialogue
or tool arguments. Unreferenced variants are omitted only when at least one referenced
variant exists. Omission counts and selection criteria are explicit; unknown result
formats and unmatched catalogues are retained. Identities, addresses, statuses,
payment facts, error results and referenced item properties are not discarded.
An omitted variant is not evidence of unavailability.

Shared history appears in the first case only. Exact duplicate entries and matching
traces are replaced by evidence references. Ordinary older history retains all Customer
utterances and all tool decisions, plus write/error results and their context; routine
read payloads and unrelated Service narration are omitted with original-index gaps
recorded. Repair before/after witnesses receive the fuller case extraction instead.
Both representations are explicitly marked and originals remain in the saved archive.

Free and RC receive byte-equal shared history/excerpts on the same inputs. Only RC
receives actual repair metadata, including the complete diff, measured outcomes,
scope, uncertain boundaries and independent-repeat evidence. Repair traces already
present in shared history or current cases are referenced, not pasted again.
Feedback provides stage descriptions and evidence indices, without duplicating the
entire raw review or introducing a shaped numeric search reward.

## Verification and preview

```bash
TAU2_DATA_DIR="$PWD/benchmark/data" .venv/bin/python -m pytest -q
.venv/bin/ruff check src scripts tests
.venv/bin/ruff format --check src scripts tests
.venv/bin/python scripts/preview_customer_reflection.py
```

The preview reuses the actual E minibatch behind `M2-free-17`'s second, Service-role
candidate. It renders the v2 and v3 layouts on identical cases and shared history.
Saved v1 audits are explicitly marked as reused for layout inspection, not new v3
judgments. Outputs are `paper/candidate_inspection/customer_v3_reflection_input.txt`,
`customer_v2_same_cases_reflection_input.txt`, and `customer_reflection_size_comparison.json`.
Character counts are reported; actual model tokens are unknown because nothing was sent.

Tests cover full Customer ordering/constraints, no-failure responses, evidence windows,
tool errors and arguments, catalogue omissions, shared-context deduplication, Free/RC
parity and preservation of true repair differences/scope. The extractor is conservative:
large legitimate Customer conversations and unknown tool payloads can remain long.
Reduced input size is verified; improved generated strategies still need a subsequent
authorized model test.
