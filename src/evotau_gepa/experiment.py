"""Alternating scheduling; all proposal/selection remains in official GEPA."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import gepa

from .adapter import RetailAdapter
from .budget import BudgetExceeded
from .customer import BOUNDARY, public_trace, review, valid_discovery
from .repair import make_feedback
from .retail import Retail, digest
from .transport import RUN_ID, Gateway


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2))
    temporary.replace(path)


class Experiment:
    def __init__(self, root):
        self.root = Path(root)
        self.manifest = json.loads((self.root / "configs/experiment_manifest.json").read_text())
        self.gateway = Gateway(root)
        self.retail = Retail(root, self.manifest, self.gateway)

    def units(self, split, seed=101):
        return [{"task": task, "seed": seed, "split": split} for task in self.manifest[split]]

    def search(self, role, run_id, seed, initial="", **kwargs):
        directory = self.root / "results/searches" / run_id
        path = directory / "selection.json"
        if path.exists():
            return json.loads(path.read_text())
        if self.gateway.budget.summary()["remaining"] <= self.manifest["H_request_reserve"]:
            raise BudgetExceeded("Evolution allowance exhausted; held-out allowance protected")
        adapter = RetailAdapter(self.retail, role, run_id, **kwargs)
        token = RUN_ID.set(run_id)
        try:
            result = gepa.optimize(
                seed_candidate={role + "_instructions": initial},
                trainset=self.units("E"),
                valset=self.units("V"),
                adapter=adapter,
                reflection_lm=self.gateway.reflection,
                max_metric_calls=self.manifest[role + "_metric_calls"],
                seed=seed,
                reflection_minibatch_size=2,
                skip_perfect_score=False,
                run_dir=str(directory / "gepa"),
                raise_on_exception=True,
                stop_callbacks=lambda state: (
                    self.gateway.budget.summary()["remaining"] <= self.manifest["H_request_reserve"]
                ),
            )
        finally:
            RUN_ID.reset(token)
        save(directory / "gepa_result.json", result.to_dict())
        selection = {
            "run_id": run_id,
            "role": role,
            "prompt": result.best_candidate[role + "_instructions"],
            "initial_score": result.val_aggregate_scores[0],
            "best_score": result.val_aggregate_scores[result.best_idx],
            "accepted_candidates": result.num_candidates,
            "metric_calls": result.total_metric_calls,
            "metric_budget_completed": result.total_metric_calls
            >= self.manifest[role + "_metric_calls"],
            "result_sha256": digest(result.to_dict()),
        }
        # A tie is stagnation, rather than a mandatory promotion.
        if selection["best_score"] <= selection["initial_score"]:
            selection["prompt"] = initial
            selection["stagnated"] = True
        save(path, selection)
        return selection

    def evaluate_service(self, prompt, customer, condition, split="E", final=False):
        units = self.units(split)
        with ThreadPoolExecutor(max_workers=4) as pool:
            return list(
                pool.map(
                    lambda x: self.retail.run(
                        x["task"], x["seed"], prompt, customer, condition, split, final
                    ),
                    units,
                )
            )

    def verified_repair(self, before_prompt, after_prompt, customer, label):
        if before_prompt == after_prompt:
            return []
        before = self.evaluate_service(before_prompt, customer, label + "-before")
        after = self.evaluate_service(after_prompt, customer, label + "-after")
        evidence = []
        for left, right in zip(before, after):
            if left["native_reward"] != 0 or right["native_reward"] != 1:
                continue
            repeat_left = self.retail.run(
                left["task"], 202, before_prompt, customer, label + "-before"
            )
            repeat_right = self.retail.run(
                left["task"], 202, after_prompt, customer, label + "-after"
            )
            if repeat_left["native_reward"] == 0 and repeat_right["native_reward"] == 1:
                audits = [
                    self.repair_fidelity_review(episode, customer, label)
                    for episode in [left, right, repeat_left, repeat_right]
                ]
                if not all(audit["legal"] for audit in audits):
                    continue
                first = make_feedback(before_prompt, after_prompt, left, right)
                first["repeat"] = make_feedback(
                    before_prompt, after_prompt, repeat_left, repeat_right
                )
                first["customer_fidelity_reviews"] = audits
                evidence.append(first)
        save(self.root / f"results/repairs/{label}.json", evidence)
        return evidence

    def repair_fidelity_review(self, episode, customer, label):
        from tau2.domains.retail.environment import get_environment

        path = self.root / f"results/repair_reviews/{label}-{episode['key']}.json"
        if path.exists():
            return json.loads(path.read_text())
        token = RUN_ID.set(label + "-fidelity")
        try:
            audit = review(
                self.gateway,
                self.retail.task(episode["task"], "E"),
                customer,
                episode,
                get_environment().get_policy(),
            )
            save(path, audit)
            return audit
        finally:
            RUN_ID.reset(token)

    def run(self):
        if self.manifest["status"] != "frozen":
            raise ValueError("Freeze and verify protocol before searching")
        try:
            with ThreadPoolExecutor(max_workers=3) as pool:
                return list(pool.map(self._run_repeat, self.manifest["search_seeds"]))
        finally:
            completed = [
                json.loads(path.read_text())
                for seed in self.manifest["search_seeds"]
                if (path := self.root / f"results/repeats/{seed}.json").exists()
            ]
            save(self.root / "results/states.json", completed)

    def _run_repeat(self, seed):
        state_path = self.root / f"results/repeats/{seed}.json"
        if state_path.exists():
            return json.loads(state_path.read_text())
        service_result = self.search("service", f"M1-{seed}", seed)
        s1 = service_result["prompt"]
        feedback = self.verified_repair("", s1, "", f"M1-{seed}")
        history = [public_trace(x) for x in self.evaluate_service(s1, "", f"common-E-{seed}")]
        free = self.search(
            "customer", f"M2-free-{seed}", seed, BOUNDARY, service=s1, history=history
        )
        # If M1 provides no verified repair, use actual M2 failures to attempt one.
        if not feedback:
            repaired = self.search(
                "service", f"M2-repair-{seed}", seed, s1, customer=free["prompt"]
            )
            feedback = self.verified_repair(s1, repaired["prompt"], free["prompt"], f"M2-{seed}")
            if feedback:
                s1 = repaired["prompt"]
                history = [
                    public_trace(x) for x in self.evaluate_service(s1, "", f"paired-E-{seed}")
                ]
        repeat = {
            "search_seed": seed,
            "M1": service_result,
            "fixed_service": s1,
            "feedback": feedback,
            "M2": free,
            "branches": {},
        }
        if not feedback:
            repeat["core_status"] = "unidentifiable_no_repeated_verified_repair"
        # Both arms receive all the same past failure and follow-up dialogue evidence.
        # Only RC receives the attributed text diff and measured repair summary.
        history += [
            {
                "task": f["task"],
                "before": f["before"],
                "after": f["after"],
                "repeat_before": f["repeat"]["before"],
                "repeat_after": f["repeat"]["after"],
            }
            for f in feedback
        ]
        # Same S1 and history, exactly equal GEPA settings and metric budgets.
        if feedback:
            self.prepare_references(s1)
        for condition in ["free", "rc"]:
            current_service = s1
            customer_result = self.search(
                "customer",
                f"{'M3' if feedback else 'M4-start'}-{condition}-{seed}",
                seed,
                BOUNDARY,
                service=s1,
                condition=condition,
                feedback=feedback,
                history=history,
            )
            current_customer = customer_result["prompt"]
            branch = {
                "fixed_search": customer_result,
                "fixed_search_eligible": bool(feedback),
                "cycles": [],
            }
            current_feedback = feedback
            for cycle in range(self.manifest["cycles"]):
                label = f"M4-{condition}-{seed}-{cycle}"
                pre = self.evaluate_service(current_service, current_customer, label + "-challenge")
                evolved = self.search(
                    "service",
                    label + "-service",
                    seed + cycle,
                    current_service,
                    customer=current_customer,
                )
                proposed = evolved["prompt"]
                repair = self.verified_repair(current_service, proposed, current_customer, label)
                previous_service = current_service
                if repair:
                    current_service, current_feedback = proposed, repair
                common = history + [public_trace(x) for x in pre]
                common += [
                    {
                        "task": f["task"],
                        "before": f["before"],
                        "after": f["after"],
                        "repeat_before": f["repeat"]["before"],
                        "repeat_after": f["repeat"]["after"],
                    }
                    for f in current_feedback
                ]
                updated = self.search(
                    "customer",
                    label + "-customer",
                    seed + cycle,
                    current_customer,
                    service=current_service,
                    condition=condition,
                    feedback=current_feedback,
                    history=common,
                )
                branch["cycles"].append(
                    {
                        "cycle": cycle,
                        "service_before": digest(previous_service),
                        "service_after": digest(current_service),
                        "repair": repair,
                        "service_search": evolved,
                        "customer_search": updated,
                        "service_promoted": bool(repair),
                        "customer_changed": current_customer != updated["prompt"],
                    }
                )
                current_customer = updated["prompt"]
                save(self.root / f"results/branches/{condition}-{seed}.json", branch)
            branch.update(final_service=current_service, final_customer=current_customer)
            repeat["branches"][condition] = branch
        if not feedback:
            # Still attempt the two planned alternating cycles. If they yield
            # a repair, use the first in fixed branch/cycle order for M3;
            # never select a pair by its Free/RC discovery outcome.
            repaired_cycle = next(
                (
                    cycle
                    for condition in ["free", "rc"]
                    for cycle in repeat["branches"][condition]["cycles"]
                    if cycle["repair"]
                ),
                None,
            )
            if repaired_cycle:
                pair = repaired_cycle["repair"]
                fixed_service = repaired_cycle["service_search"]["prompt"]
                common = [
                    public_trace(x)
                    for x in self.evaluate_service(fixed_service, "", f"late-paired-E-{seed}")
                ]
                common += [
                    {
                        "task": f["task"],
                        "before": f["before"],
                        "after": f["after"],
                        "repeat_before": f["repeat"]["before"],
                        "repeat_after": f["repeat"]["after"],
                    }
                    for f in pair
                ]
                self.prepare_references(fixed_service)
                for condition in ["free", "rc"]:
                    selected = self.search(
                        "customer",
                        f"M3-{condition}-{seed}",
                        seed,
                        BOUNDARY,
                        service=fixed_service,
                        condition=condition,
                        feedback=pair,
                        history=common,
                    )
                    repeat["branches"][condition].update(
                        fixed_search=selected, fixed_search_eligible=True
                    )
                repeat.update(
                    fixed_service=fixed_service,
                    feedback=pair,
                    core_status="identified_from_first_repeated_verified_M4_repair",
                )
        save(state_path, repeat)
        return repeat

    def prepare_references(self, service):
        for split in ["E", "V"]:
            for seed in self.manifest["simulation_seeds"]:
                with ThreadPoolExecutor(max_workers=4) as pool:
                    list(
                        pool.map(
                            lambda task, split=split, seed=seed: self.retail.run(
                                task, seed, service, "", "reference-native-customer", split
                            ),
                            self.manifest[split],
                        )
                    )

    def heldout(self, states):
        selections = {"native": {"service": "", "customer": ""}}
        for state in states:
            seed = state["search_seed"]
            selections[f"GEPA-Service-{seed}"] = {"service": state["M1"]["prompt"], "customer": ""}
            for condition, branch in state["branches"].items():
                selections[f"{condition}-{seed}"] = {
                    "service": branch["final_service"],
                    "customer": "",
                }
                if branch["fixed_search_eligible"]:
                    selections[f"fixed-challenge-{condition}-{seed}"] = {
                        "service": state["fixed_service"],
                        "customer": branch["fixed_search"]["prompt"],
                    }
        frozen = {"protocol_sha256": digest(self.manifest), "selections": selections}
        path = self.root / "results/final_selection.json"
        if path.exists() and json.loads(path.read_text()) != frozen:
            raise ValueError("H selections cannot change")
        save(path, frozen)
        # No results from H ever return to optimize().
        units = [
            (task, seed, pair, label)
            for label, pair in selections.items()
            for seed in self.manifest["heldout_seeds"]
            for task in (
                self.manifest["H_challenge"]
                if label.startswith("fixed-challenge")
                else self.manifest["H"]
            )
        ]
        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(self.heldout_unit, units))
        save(self.root / "results/heldout_complete.json", {"selection_sha256": digest(frozen)})

    def heldout_unit(self, unit):
        from tau2.domains.retail.environment import get_environment

        task, seed, pair, label = unit
        episode = self.retail.run(
            task, seed, pair["service"], pair["customer"], "H-" + label, "H", True
        )
        if not label.startswith("fixed-challenge"):
            return episode
        path = self.root / f"results/heldout_reviews/{label}-{task}-{seed}.json"
        if path.exists():
            return episode
        baseline_label = "H-fixed-reference-" + label.rsplit("-", 1)[-1]
        baseline = self.retail.run(task, seed, pair["service"], "", baseline_label, "H", True)
        token = RUN_ID.set("H-" + label)
        try:
            scenario = self.retail.task(task, "H", True)
            policy = get_environment().get_policy()
            audit = review(self.gateway, scenario, pair["customer"], episode, policy)
            record = {
                "episode": episode["key"],
                "baseline": baseline["key"],
                "task": task,
                "condition": label,
                "audit": audit,
                "valid_discovery": False,
            }
            if (
                baseline["native_reward"] == 1
                and episode["native_reward"] == 0
                and all(audit[k] for k in ("legal", "exposed", "failure_verified"))
            ):
                repeated = self.retail.run(
                    task,
                    404,
                    pair["service"],
                    pair["customer"],
                    "H-" + label + "-repeat",
                    "H",
                    True,
                )
                repeated_baseline = self.retail.run(
                    task, 404, pair["service"], "", baseline_label, "H", True
                )
                repeated_audit = review(self.gateway, scenario, pair["customer"], repeated, policy)
                record.update(
                    repeated=repeated["key"],
                    repeated_baseline=repeated_baseline["key"],
                    repeated_audit=repeated_audit,
                    valid_discovery=repeated_baseline["native_reward"] == 1
                    and valid_discovery(baseline, episode, repeated, audit, repeated_audit),
                )
            save(path, record)
        finally:
            RUN_ID.reset(token)
        return episode
