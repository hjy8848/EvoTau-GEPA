# Customer feedback v2: offline fixes

The fixed Open-RC idea is unchanged. This revision fixes the Customer search role,
descriptive feedback, and two evidence-review errors. It does not implement shaped
numeric rewards, alter the strict final discovery predicate, increase search budgets,
change the native scorer, or restart paid experiments.

## Changes

- Official GEPA's supported `reflection_prompt_template` identifies Customer as the
  optimized role. Only Customer utterances occupy Generated Outputs; Service/tools
  appear as environment feedback, with the original indexed chronological trace retained.
- Initial Customer instructions explicitly distinguish lawful testing from Service
  duties and from obstructing task completion. Obvious explicit Service identities
  are rejected before simulation, recorded separately with no fabricated episode or
  native reward, and fed back as invalid proposals. Rejected proposals still consume
  GEPA metric evaluations; they do not count as actual evaluated native candidates.
- Reflection receives separate goal-fidelity, strategy-exposure, and Service-failure
  evidence descriptions, alongside both native outcomes and the unchanged discovery
  score. Free does not receive RC repair metadata.
- Structured review assessments must agree with their booleans; exposure citations
  must reference Customer messages. Clear exposure explanation contradictions are
  marked unresolved. General semantic prose consistency is not guaranteed by these
  checks and still requires examination.
- A narrow deterministic check detects an explicitly requested, executed return of
  an item protected by the native scenario's explicit “everything except …” clause.
  Item identities come from observed tool results, not gold evaluation actions.
  A unilateral Service return does not by itself make Customer illegal.
- Identical old/new exchange item IDs violate the frozen policy's different-option
  requirement when a successful order response confirms execution. Rejected tool
  attempts are recorded but not counted as completed violations. This finding alone
  does not establish strategy exposure, novelty, repetition, or a valid discovery.
- Model judgments are retained under `model_review`; corrections and conflicts have
  indexed evidence. These limited checks are not a comprehensive policy validator.

## Historical separation

The v1 source/config freeze is `research/protocol_freeze.json`, at parent commit
`19963ce`. It is not overwritten to legitimize changed algorithms. The old generated
candidates, audits, native scores and discovery counts are unchanged. Customer v2
adapter records use a revision-specific cache key and refuse legacy search directories.
An eventual new experiment must use distinct run IDs and its own frozen protocol;
it must retain the existing global request ledger and the original 30,000 cap.
`scripts/reproduce.py` for v1 intentionally fails source verification on this v2
branch, before constructing the model gateway or sending requests.

## Offline verification

```bash
TAU2_DATA_DIR="$PWD/benchmark/data" .venv/bin/python -m pytest -q
.venv/bin/ruff check src scripts tests
.venv/bin/ruff format --check src scripts tests
```

Regression fixtures reproduce the actual v1 task 13 and task 107 conversations,
without raw model payloads. Tests verify the role boundary, official GEPA template
wiring, separated reflective data, textual feedback under zero discovery credit,
positive and negative tool checks, review contradictions, and historical run isolation.
Tests and fixture replay require no model requests. Candidate-generation or discovery
improvement has not yet been measured on this revision.
