"""Replay the real E minibatch behind a v1 role error; render without model calls."""

import ast
import json
import os
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

root = Path(__file__).resolve().parents[1]
os.environ["TAU2_DATA_DIR"] = str(root / "benchmark/data")
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

from gepa.core.adapter import EvaluationBatch
from gepa.strategies.instruction_proposal import InstructionProposalSignature
from tau2.data_model.tasks import Task

from evotau_gepa.adapter import RetailAdapter
from evotau_gepa.customer import (
    BOUNDARY,
    CUSTOMER_REFLECTION_TEMPLATE,
    exploration_feedback,
    public_trace,
)

directory = root / "results/searches/M2-free-17"
history = [
    json.loads(line) for line in (directory / "candidate_history.jsonl").read_text().splitlines()
]
# The second original reflection uses the initial candidate on two E tasks.
batch = history[3]
assert [unit["split"] for unit in batch["batch"]] == ["E", "E"]
evaluations = {}
for path in directory.glob("*.json"):
    record = json.loads(path.read_text())
    if "audit" in record:
        evaluations[record["episode"]["key"]] = record
outputs = [evaluations[key] for key in batch["episodes"]]
common = []
for path in sorted((root / "results/episodes").glob("*.json")):
    episode = json.loads(path.read_text())
    if episode["condition"] == "common-E-17":
        assert episode["split"] == "E"
        common.append(public_trace(episode))
tasks = json.loads((root / "benchmark/data/tau2/domains/retail/tasks.json").read_text())


def task(task_id, split):
    assert split == "E"
    return Task.model_validate(next(t for t in tasks if t["id"] == task_id))


for output in outputs:
    baseline = json.loads((root / f"results/episodes/{output['baseline']}.json").read_text())
    output["baseline_native_reward"] = baseline["native_reward"]

with tempfile.TemporaryDirectory() as temporary:
    adapter = RetailAdapter(
        SimpleNamespace(root=Path(temporary), task=task), "customer", "preview", history=common
    )
    rows = adapter.make_reflective_dataset(
        {},
        EvaluationBatch(outputs, [o["score"] for o in outputs], outputs),
        ["customer_instructions"],
    )["customer_instructions"]
for row in rows:
    row["Feedback"]["audit_provenance"] = (
        "Saved v1 judgment reused for offline input-size preview only; not a new v3 model review or discovery result."
    )
new_prompt = InstructionProposalSignature.prompt_renderer(
    {
        "current_instruction_doc": BOUNDARY,
        "dataset_with_feedback": rows,
        "prompt_template": CUSTOMER_REFLECTION_TEMPLATE,
    }
)

# Render the immediately preceding v2 layout on exactly these same inputs.
source = subprocess.check_output(
    ["git", "show", "3e6f914:src/evotau_gepa/customer.py"], cwd=root, text=True
)
constants = {}
for node in ast.parse(source).body:
    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
        for target in node.targets:
            if isinstance(target, ast.Name):
                constants[target.id] = node.value.value
old_rows = []
for output in outputs:
    episode = output["episode"]
    trace = public_trace(episode)
    old_rows.append(
        {
            "Inputs": {
                "scenario": str(task(episode["task"], "E").user_scenario),
                "optimized_role": "Customer",
                "available_backend_tools": [],
            },
            "Generated Outputs": {"customer_utterances": [m for m in trace if m["role"] == "user"]},
            "Environment Responses": {
                "service_and_tools": [m for m in trace if m["role"] != "user"],
                "full_chronological_trace": trace,
                "interpretation": "Environment observations only; these are not Service instructions to optimize.",
            },
            "Feedback": {
                "native_reward": episode["native_reward"],
                "objective_score": output["score"],
                "review": output["audit"],
                "constraints": constants["BOUNDARY"],
                "search_context": {"history": common},
                "exploration": exploration_feedback(
                    output["audit"],
                    episode["native_reward"],
                    output["baseline_native_reward"],
                    output["score"],
                ),
                "objective": "Find lawful, evidenced Service decision errors by changing Customer interaction strategy. Preserve native goals. Do not optimize Service task completion.",
            },
        }
    )
old_prompt = InstructionProposalSignature.prompt_renderer(
    {
        "current_instruction_doc": constants["BOUNDARY"],
        "dataset_with_feedback": old_rows,
        "prompt_template": constants["CUSTOMER_REFLECTION_TEMPLATE"],
    }
)
destination = root / "paper/candidate_inspection"
destination.mkdir(parents=True, exist_ok=True)
(destination / "customer_v3_reflection_input.txt").write_text(new_prompt)
(destination / "customer_v2_same_cases_reflection_input.txt").write_text(old_prompt)
metrics = {
    "mode": "offline preview, not sent to model",
    "tasks": [unit["task"] for unit in batch["batch"]],
    "source_run": "M2-free-17",
    "source_candidate_role_error": "candidate 02",
    "v2_characters": len(old_prompt),
    "v3_characters": len(new_prompt),
    "reduction_fraction": 1 - len(new_prompt) / len(old_prompt),
    "actual_model_tokens": None,
    "new_model_requests": 0,
    "audit_provenance": "Saved v1 audits reused only to compare reflection layouts; no historical judgments changed.",
}
(destination / "customer_reflection_size_comparison.json").write_text(json.dumps(metrics, indent=2))
print(json.dumps(metrics, indent=2))
