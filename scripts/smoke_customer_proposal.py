"""Reuse three successful E cases and optionally make exactly one proposal call."""

import argparse
import copy
import json
import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

root = Path(__file__).resolve().parents[1]
os.environ["TAU2_DATA_DIR"] = str(root / "benchmark/data")
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

from gepa.core.adapter import EvaluationBatch
from gepa.core.data_loader import ListDataLoader
from gepa.strategies.instruction_proposal import InstructionProposalSignature
from tau2.data_model.tasks import Task
from tau2.domains.retail.environment import get_environment

from evotau_gepa.adapter import RetailAdapter
from evotau_gepa.customer import (
    CUSTOMER_REFLECTION_TEMPLATE,
    CUSTOMER_REVISION,
    customer_role_error,
    deterministic_findings,
    public_trace,
    reconcile_review,
)
from evotau_gepa.experiment import RandomCustomerBatchSampler
from evotau_gepa.retail import digest
from evotau_gepa.transport import MODEL, RUN_ID, Gateway


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))


def build(destination):
    source_run = "M2-free-43"
    source = root / "results/searches" / source_run
    candidate = json.loads((source / "selection.json").read_text())["prompt"]
    outputs = []
    baselines = []
    service = None
    source_paths = []
    for path in sorted(source.glob("*.json")):
        saved = json.loads(path.read_text())
        if "audit" not in saved:
            continue
        episode = saved["episode"]
        if episode["split"] != "E" or episode["seed"] != 101 or episode["customer"] != candidate:
            continue
        baseline = json.loads((root / f"results/episodes/{saved['baseline']}.json").read_text())
        if baseline["native_reward"] != 1:
            continue
        assert episode["native_reward"] == 1
        assert baseline["service"] == episode["service"]
        assert baseline["customer"] == "" and baseline["split"] == "E"
        if service is None:
            service = episode["service"]
        assert service == episode["service"]
        outputs.append(copy.deepcopy(saved))
        baselines.append(baseline)
        source_paths.append(str(path.relative_to(root)))
    # Use the production sampler on the eligible pool, with no model evaluations.
    loader = ListDataLoader(list(range(len(outputs))))
    ids = RandomCustomerBatchSampler(43).next_minibatch_ids(loader, SimpleNamespace(i=0))
    assert len(ids) == 3 and len({outputs[i]["episode"]["task"] for i in ids}) == 3
    outputs = [outputs[i] for i in ids]
    baselines = [baselines[i] for i in ids]
    source_paths = [source_paths[i] for i in ids]
    raw_tasks = json.loads((root / "benchmark/data/tau2/domains/retail/tasks.json").read_text())
    manifest = json.loads((root / "configs/experiment_manifest.json").read_text())

    def task(task_id, split):
        assert split == "E" and task_id in manifest["E"]
        return Task.model_validate(next(t for t in raw_tasks if t["id"] == task_id))

    policy = get_environment().get_policy()
    for output, baseline in zip(outputs, baselines, strict=True):
        episode = output["episode"]
        output["baseline_native_reward"] = baseline["native_reward"]
        output["audit"] = reconcile_review(
            output["audit"],
            episode,
            deterministic_findings(task(episode["task"], "E"), episode, policy),
        )
    # A minimal generation smoke test: shared history is only these three references.
    with tempfile.TemporaryDirectory() as temporary:
        adapter = RetailAdapter(
            SimpleNamespace(root=Path(temporary), task=task),
            "customer",
            "single-proposal-preview",
            service=service,
            history=[public_trace(baseline) for baseline in baselines],
        )
        rows = adapter.make_reflective_dataset(
            {"customer_instructions": candidate},
            EvaluationBatch(outputs, [o["score"] for o in outputs], outputs),
            ["customer_instructions"],
        )["customer_instructions"]
    for row in rows:
        row["Feedback"]["audit_provenance"] = (
            "Saved v1 review, reconciled by current deterministic rules only. Missing newer "
            "semantic assessments remain uncertain. No new LLM audit was run. Native "
            "baseline and challenged completion are both 1 in this saved episode."
        )
    prompt = InstructionProposalSignature.prompt_renderer(
        {
            "current_instruction_doc": candidate,
            "dataset_with_feedback": rows,
            "prompt_template": CUSTOMER_REFLECTION_TEMPLATE,
        }
    )
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "reflection_input.txt").write_text(prompt)
    (destination / "current_customer_strategy.txt").write_text(candidate)
    save(destination / "reflection_cases.json", rows)
    report = {
        "mode": "single proposal only; no candidate simulation, selection or effectiveness test",
        "revision": CUSTOMER_REVISION,
        "model": MODEL,
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "source_run": source_run,
        "search_seed": 43,
        "iteration": 0,
        "service_sha256": digest(service),
        "current_strategy_provenance": "Actual saved strategy that produced these episodes",
        "shared_history_scope": "Only the three selected native references; Free, no repair metadata",
        "tasks": [
            {
                "task": o["episode"]["task"],
                "seed": o["episode"]["seed"],
                "baseline_native_reward": b["native_reward"],
                "challenged_native_reward": o["episode"]["native_reward"],
                "baseline_episode": b["key"],
                "challenged_episode": o["episode"]["key"],
                "source_evaluation": path,
            }
            for o, b, path in zip(outputs, baselines, source_paths, strict=True)
        ],
        "input_characters": len(prompt),
        "input_sha256": digest(prompt),
        "new_model_requests": 0,
        "status": "prepared_not_sent",
    }
    save(destination / "report.json", report)
    return prompt, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send", action="store_true")
    parser.add_argument(
        "--output", type=Path, default=root / "paper/candidate_inspection/customer_v4_single_call"
    )
    args = parser.parse_args()
    destination = args.output
    if (destination / "send_started.json").exists():
        raise ValueError("This smoke attempt has already started; refusing a second model call")
    prompt, report = build(destination)
    if not args.send:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    gateway = Gateway(root)
    before = gateway.budget.summary()["reserved_or_sent"]
    run_id = "customer-v4-single-proposal-" + destination.name
    # An exclusive marker survives interruptions and prevents accidental re-sending.
    with (destination / "send_started.json").open("x") as marker:
        json.dump({"run_id": run_id, "request_count_before": before}, marker)
    report.update(status="request_started", run_id=run_id, request_count_before=before)
    save(destination / "report.json", report)
    token = RUN_ID.set(run_id)
    try:
        raw = gateway.reflection(prompt)
        (destination / "model_response.txt").write_text(raw)
        strategy = InstructionProposalSignature.output_extractor(raw)["new_instruction"]
        (destination / "candidate.txt").write_text(strategy)
        report.update(
            status="completed",
            candidate_characters=len(strategy),
            role_guard_error=customer_role_error(strategy),
        )
    except Exception as exc:
        report.update(status="failed_no_retry", error=str(exc))
        raise
    finally:
        RUN_ID.reset(token)
        after = gateway.budget.summary()["reserved_or_sent"]
        with sqlite3.connect(gateway.budget.path) as db:
            matching = db.execute(
                "SELECT id,state,metadata FROM requests WHERE id>? ORDER BY id", (before,)
            ).fetchall()
        records = [
            {"id": rid, "state": state, "metadata": json.loads(metadata or "{}")}
            for rid, state, metadata in matching
            if json.loads(metadata or "{}").get("run_id") == run_id
        ]
        report.update(
            request_count_after=after,
            new_model_requests=after - before,
            ledger_records=records,
            automatic_retries=0,
            candidate_evaluations=0,
        )
        save(destination / "report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
