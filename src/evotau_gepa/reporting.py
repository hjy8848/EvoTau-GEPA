"""Offline paper artifacts computed from actual saved records only."""

import csv
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from .budget import Budget


def clustered_difference(free, rc, samples=10000, seed=1729):
    tasks = sorted(set(free) & set(rc))
    if len(tasks) < 2:
        return {
            "effect": None,
            "ci95": None,
            "task_count": len(tasks),
            "status": "insufficient data",
        }
    delta = np.array([np.mean(rc[t]) - np.mean(free[t]) for t in tasks])
    rng = np.random.default_rng(seed)
    boots = rng.choice(delta, size=(samples, len(tasks)), replace=True).mean(axis=1)
    return {
        "effect": float(delta.mean()),
        "ci95": list(np.quantile(boots, [0.025, 0.975])),
        "task_count": len(tasks),
        "unit": "task clusters; seed repetitions averaged within task",
        "independent_search_repeats": "reported separately",
        "multiple_comparisons": "one preregistered primary comparison",
    }


def build(root):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    root = Path(root)
    episodes = [json.loads(p.read_text()) for p in (root / "results/episodes").glob("*.json")]
    grouped = defaultdict(list)
    for e in episodes:
        grouped[(e["condition"], e["split"])].append(e)
    rows = [
        {
            "condition": condition,
            "split": split,
            "episodes": len(group),
            "tasks": len({x["task"] for x in group}),
            "accuracy": np.mean([x["native_reward"] for x in group]),
        }
        for (condition, split), group in sorted(grouped.items())
    ]
    with (root / "results/results.csv").open("w") as f:
        writer = csv.DictWriter(
            f, fieldnames=["condition", "split", "episodes", "tasks", "accuracy"]
        )
        writer.writeheader()
        writer.writerows(rows)
    audits, discoveries = [], []
    for p in (root / "results/searches").glob("*/*.json"):
        item = json.loads(p.read_text())
        if "audit" not in item:
            continue
        item["run_id"] = p.parent.name
        audits.append(item)
        if item["score"] == 1:
            discoveries.append(item)
    run_candidates, run_audits = defaultdict(set), defaultdict(list)
    for p in (root / "results/searches").glob("*/candidate_history.jsonl"):
        for line in p.read_text().splitlines():
            h = json.loads(line)
            run_candidates[h["run_id"]].add(h["candidate_sha256"])
    for item in audits:
        run_audits[item["run_id"]].append(item)
    manifest = json.loads((root / "configs/experiment_manifest.json").read_text())
    tasks = manifest["E"] + manifest["V"]
    free, rc = defaultdict(list), defaultdict(list)
    paired = []
    # One conservative discovery per task per independent search; never multiply a
    # repeated mechanism by counting all of its episodes/candidates as discoveries.
    for seed in manifest["search_seeds"]:
        labels = [f"M3-{condition}-{seed}" for condition in ["free", "rc"]]
        if not all(
            (root / f"results/searches/{label}/selection.json").exists() for label in labels
        ):
            continue
        paired.append(seed)
        for label, target in zip(labels, [free, rc]):
            found = {x["episode"]["task"] for x in run_audits[label] if x["score"] == 1}
            denominator = len(run_candidates[label])
            for task in tasks:
                target[task].append(float(task in found) * 100 / denominator)
    statistics = clustered_difference(free, rc)
    statistics.update(
        paired_search_seeds=paired,
        rate_unit="task discoveries per 100 distinct evaluated candidates",
        status="exploratory" if paired else "unidentifiable / not evaluated",
    )
    if statistics["effect"] is not None:
        statistics["effect"] *= len(tasks)
        statistics["ci95"] = [x * len(tasks) for x in statistics["ci95"]]
    search_rows = []
    for label, items in sorted(run_audits.items()):
        search_rows.append(
            {
                "run_id": label,
                "evaluated_candidates": len(run_candidates[label]),
                "audited_episodes": len(items),
                "legal_episodes": sum(x["audit"]["legal"] for x in items),
                "exposed_episodes": sum(x["audit"]["exposed"] for x in items),
                "novel_task_discoveries": len(
                    {x["episode"]["task"] for x in items if x["score"] == 1}
                ),
                "automatic_mechanism_labels": sorted(
                    {x["audit"].get("mechanism", "unresolved") for x in items if x["score"] == 1}
                ),
            }
        )
    (root / "results/search_metrics.json").write_text(json.dumps(search_rows, indent=2))
    usage = Budget(root / "results/request_budget.sqlite3").summary()
    summary = {
        "episodes": len(episodes),
        "rows": rows,
        "automatic_valid_discovery_records": len(discoveries),
        "discovery_tasks": len({x["episode"]["task"] for x in discoveries}),
        "primary_statistics": statistics,
        "api_usage": usage,
        "heldout_complete": (root / "results/heldout_complete.json").exists(),
        "complete": (root / "results/heldout_complete.json").exists()
        and len(paired) == len(manifest["search_seeds"]),
    }
    for name, value in [
        ("summary", summary),
        ("statistical_analysis", statistics),
        ("discovery_cases", discoveries),
        ("api_usage", usage),
    ]:
        (root / f"results/{name}.json").write_text(json.dumps(value, indent=2))
    (root / "results/episodes.jsonl").write_text(
        "".join(
            json.dumps(
                {k: e[k] for k in ["key", "task", "seed", "condition", "split", "native_reward"]}
            )
            + "\n"
            for e in episodes
        )
    )
    history = []
    for p in (root / "results/searches").glob("*/candidate_history.jsonl"):
        history.extend(p.read_text().splitlines())
    (root / "results/candidate_history.jsonl").write_text(
        "\n".join(history) + ("\n" if history else "")
    )
    table = "| Condition | Split | Episodes | Tasks | Native accuracy |\n|---|---|---:|---:|---:|\n"
    table += "".join(
        f"| {x['condition']} | {x['split']} | {x['episodes']} | {x['tasks']} | {x['accuracy']:.3f} |\n"
        for x in rows
    )
    (root / "paper/tables/native_accuracy.md").write_text(table)
    plots = {
        "service_accuracy": (
            [x["condition"] for x in rows if x["split"] == "H"],
            [x["accuracy"] for x in rows if x["split"] == "H"],
        ),
        "failure_discovery": (
            ["Free", "RC"],
            [sum(map(sum, free.values())), sum(map(sum, rc.values()))],
        )
        if free and rc
        else ([], []),
        "validity_exposure": (
            ["legal", "exposed"],
            [np.mean([x["audit"][k] for x in audits]) for k in ["legal", "exposed"]],
        )
        if audits
        else ([], []),
    }
    states_path = root / "results/states.json"
    states = json.loads(states_path.read_text()) if states_path.exists() else []
    method_rows = []
    for name, prefix in [
        ("Native", "H-native"),
        ("GEPA-Service", "H-GEPA-Service-"),
        ("Free-GEPA", "H-free-"),
        ("Open-RC branch", "H-rc-"),
    ]:
        selected = [
            e
            for e in episodes
            if e["split"] == "H"
            and (
                e["condition"] == prefix if name == "Native" else e["condition"].startswith(prefix)
            )
        ]
        method_rows.append(
            {
                "method": name,
                "episodes": len(selected),
                "tasks": len({e["task"] for e in selected}),
                "accuracy": float(np.mean([e["native_reward"] for e in selected]))
                if selected
                else None,
            }
        )
    (root / "results/method_summary.json").write_text(json.dumps(method_rows, indent=2))
    method_table = "| Method | H episodes | H tasks | Native accuracy |\n|---|---:|---:|---:|\n"
    for row in method_rows:
        value = f"{row['accuracy']:.3f}" if row["accuracy"] is not None else "not evaluated"
        method_table += f"| {row['method']} | {row['episodes']} | {row['tasks']} | {value} |\n"
    (root / "paper/tables/method_summary.md").write_text(method_table)
    overlap = set(manifest.get("historical_H_overlap", []))
    strata = []
    for label in sorted({e["condition"] for e in episodes if e["split"] == "H"}):
        for exposure in [True, False]:
            selected = [
                e
                for e in episodes
                if e["condition"] == label
                and e["split"] == "H"
                and (e["task"] in overlap) == exposure
            ]
            if selected:
                strata.append(
                    {
                        "condition": label,
                        "historical_ID_overlap": exposure,
                        "episodes": len(selected),
                        "tasks": len({e["task"] for e in selected}),
                        "accuracy": float(np.mean([e["native_reward"] for e in selected])),
                    }
                )
    (root / "results/heldout_exposure_strata.json").write_text(json.dumps(strata, indent=2))
    cycle_rows = [
        dict(x, branch=c, seed=s["search_seed"])
        for s in states
        for c, b in s.get("branches", {}).items()
        for x in b["cycles"]
    ]
    plots["repair_cycles"] = (
        [f"{x['branch']}-{x['seed']}-{x['cycle']}" for x in cycle_rows],
        [len(x["repair"]) for x in cycle_rows],
    )
    plots["service_cycles"] = (
        [f"{x['branch']}-{x['seed']}-{x['cycle']}" for x in cycle_rows],
        [x["service_search"]["best_score"] for x in cycle_rows],
    )
    plots["customer_cycles"] = (
        [f"{x['branch']}-{x['seed']}-{x['cycle']}" for x in cycle_rows],
        [x["customer_search"]["best_score"] for x in cycle_rows],
    )
    plots["seed_distribution"] = (
        [f"{condition}-{seed}" for seed in paired for condition in ["free", "rc"]],
        [
            sum((free if condition == "free" else rc)[task][i] for task in tasks)
            for i, seed in enumerate(paired)
            for condition in ["free", "rc"]
        ],
    )
    plots["primary_ci"] = (
        (
            ["lower95", "effect", "upper95"],
            [statistics["ci95"][0], statistics["effect"], statistics["ci95"][1]],
        )
        if statistics["effect"] is not None
        else ([], [])
    )
    # Native response ids join exact requests to completed episodes; auxiliary and
    # interrupted calls remain explicitly unassigned rather than estimated.
    response_requests = {}
    request_rows = []
    for path in (root / "results/requests").glob("*.json"):
        request = json.loads(path.read_text())
        request_rows.append(request)
        response_requests[request.get("response", {}).get("id")] = request["id"]
    condition_requests = defaultdict(set)
    for episode in episodes:
        for message in episode["simulation"]["messages"]:
            raw = message.get("raw_data") or {}
            request_id = response_requests.get(raw.get("id"))
            if request_id:
                condition_requests[episode["condition"]].add(request_id)
    attributed = set().union(*condition_requests.values()) if condition_requests else set()
    cost_rows = [
        {
            "condition": label,
            "native_requests_exact": len(ids),
            "auxiliary_attribution": "not available; global ledger retains all auxiliary requests",
            "cost": None,
        }
        for label, ids in sorted(condition_requests.items())
    ]
    run_request_ids = defaultdict(set)
    context_lengths = defaultdict(list)
    for request in request_rows:
        run_id = request.get("run_id", "unattributed")
        run_request_ids[run_id].add(request["id"])
        if request.get("role") == "reflection":
            context_lengths[run_id].append(len(json.dumps(request["request"]["messages"])))
    exact_search_costs = [
        {
            "run_id": run_id,
            "all_attempts": len(ids),
            "reflection_context_chars": context_lengths[run_id],
            "cost": None,
        }
        for run_id, ids in sorted(run_request_ids.items())
    ]
    (root / "results/condition_costs.json").write_text(
        json.dumps(
            {
                "rows": cost_rows,
                "all_attempts_by_run": exact_search_costs,
                "unattributed_requests": len(request_rows) - len(attributed),
                "note": "All-attempt attribution is exact for tagged requests; legacy M0/M1 requests without tags are unassigned. Shared native-reference costs are reported separately. Reflection context characters supplement exact usage tokens in raw responses.",
            },
            indent=2,
        )
    )
    plots["cost_benefit"] = (
        [x["condition"] for x in cost_rows],
        [x["native_requests_exact"] for x in cost_rows],
    )
    for name, (labels, values) in plots.items():
        fig, ax = plt.subplots(figsize=(max(6, len(labels) * 0.4), 4))
        if labels:
            ax.bar(labels, values)
            ax.tick_params(axis="x", rotation=70)
        else:
            ax.text(
                0.5,
                0.5,
                "Not evaluated / no eligible evidence",
                ha="center",
                va="center",
                transform=ax.transAxes,
            )
        ax.set_title(name.replace("_", " "))
        fig.tight_layout()
        fig.savefig(root / f"paper/figures/{name}.png", dpi=160)
        plt.close(fig)
    report = "# EvoTau-GEPA execution report\n\n"
    report += (
        "Frozen execution completed.\n\n"
        if summary["complete"]
        else "**Full research execution is incomplete. Missing stages are not negative results.**\n\n"
    )
    report += table + "\nPrimary comparison: " + json.dumps(statistics) + "\n\n"
    report += "## Final H methods\n\n" + method_table + "\n"
    report += "## Service search\n\n"
    for path in sorted((root / "results/searches").glob("M1-*/selection.json")):
        selection = json.loads(path.read_text())
        report += f"- {selection['run_id']}: V {selection['initial_score']:.3f} → {selection['best_score']:.3f}; stagnant={selection.get('stagnated', False)}; official accepted candidates={selection['accepted_candidates']}.\n"
    report += "\n## Customer discovery and validity\n\n"
    report += "All measured search metrics are in results/search_metrics.json. Per-task discoveries are deduplicated within a search; repeated records remain available as evidence. Mechanism strings are automatically proposed labels, not human-adjudicated independent failure mechanisms.\n\n"
    report += "## Alternating state transitions\n\n"
    for state in states:
        report += f"Search seed {state['search_seed']}: {state.get('core_status', 'initial repeated repair available')}.\n\n"
        for condition, branch in state.get("branches", {}).items():
            for cycle in branch["cycles"]:
                report += f"- {condition} cycle {cycle['cycle']}: Service {cycle['service_before'][:12]} → {cycle['service_after'][:12]}; repeated repairs={len(cycle['repair'])}; Customer changed={cycle['customer_changed']}.\n"
    report += "\n## Ablations, repeats and uncertainty\n\n"
    report += f"Frozen search seeds: {manifest['search_seeds']}. Eligible completed fixed-S1 pairs: {paired}. Free/RC starting without evidence is not a repair-conditioned causal experiment. Shuffled/length-matched repair controls were secondary and were not executed unless separately recorded. One primary comparison, task-cluster bootstrap; other comparisons descriptive. H only reports the actually executed seed plan, not unrun repetitions.\n\n"
    report += "## Evidence and reproducibility\n\n"
    report += "Native messages, tool calls/results and reward decomposition: results/episodes/. Exact raw transport requests/responses: results/requests/. Candidate history includes rejected proposals. Frozen dependency, source/config and official-data hashes are verifiable with scripts/verify_freeze.py. Offline reconstruction: `.venv/bin/python scripts/build_paper_results.py`. Full execution/resume: set EVOTAU_API_BASE and credentials, then `.venv/bin/python scripts/reproduce.py`.\n\n"
    report += "API usage: " + json.dumps(usage) + "\n\n"
    report += "Only actual episodes are included. Evidence review is automatic, not independent human validation. H has historical exposure; no claim of entirely unseen blind testing. Costs are unknown. Pass^k is not reported without actual k repetitions.\n"
    interruption = root / "results/interruption.json"
    if interruption.exists():
        report += "\nExecution interruption: " + interruption.read_text() + "\n"
    (root / "paper/experiment_report.md").write_text(report)
    return summary
