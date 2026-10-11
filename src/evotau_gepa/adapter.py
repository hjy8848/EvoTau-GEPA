"""Thin official GEPA adapter; scores do not replace native rewards."""

import json
from concurrent.futures import ThreadPoolExecutor

from gepa.core.adapter import EvaluationBatch
from tau2.domains.retail.environment import get_environment

from .customer import BOUNDARY, public_trace, review, valid_discovery
from .repair import reflection_context
from .retail import digest
from .transport import RUN_ID


class RetailAdapter:
    propose_new_texts = None

    def __init__(
        self,
        retail,
        role,
        run_id,
        service="",
        customer="",
        condition="free",
        feedback=None,
        history=None,
    ):
        self.retail, self.role, self.run_id = retail, role, run_id
        self.service, self.customer, self.condition = service, customer, condition
        self.feedback, self.history = feedback or [], history or []
        self.directory = retail.root / "results/searches" / run_id
        self.directory.mkdir(parents=True, exist_ok=True)

    def one(self, unit, candidate):
        token = RUN_ID.set(self.run_id)
        try:
            return self._one(unit, candidate)
        finally:
            RUN_ID.reset(token)

    def _one(self, unit, candidate):
        task_id, seed, split = unit["task"], unit["seed"], unit["split"]
        if split == "H":
            raise ValueError("H forbidden in GEPA")
        prompt = candidate[self.role + "_instructions"]
        if self.role == "service":
            episode = self.retail.run(task_id, seed, prompt, self.customer, self.run_id, split)
            return {"episode": episode, "score": episode["native_reward"]}
        path = self.directory / (digest({"candidate": candidate, "unit": unit}) + ".json")
        if path.exists():
            return json.loads(path.read_text())
        baseline = self.retail.run(
            task_id, seed, self.service, "", "reference-native-customer", split
        )
        challenged = self.retail.run(task_id, seed, self.service, prompt, self.run_id, split)
        task = self.retail.task(task_id, split)
        audit = review(
            self.retail.gateway, task, prompt, challenged, get_environment().get_policy()
        )
        result = {"episode": challenged, "baseline": baseline["key"], "audit": audit, "score": 0.0}
        if (
            baseline["native_reward"] == 1
            and challenged["native_reward"] == 0
            and all(audit[k] for k in ("legal", "exposed", "failure_verified"))
        ):
            repeat_seed = 202 if seed == 101 else 101
            repeated = self.retail.run(
                task_id, repeat_seed, self.service, prompt, self.run_id + "-repeat", split
            )
            repeat_baseline = self.retail.run(
                task_id, repeat_seed, self.service, "", "reference-native-customer", split
            )
            repeat_audit = review(
                self.retail.gateway, task, prompt, repeated, get_environment().get_policy()
            )
            result.update(
                repeated=repeated["key"],
                repeat_audit=repeat_audit,
                repeat_baseline=repeat_baseline["key"],
            )
            result["score"] = float(
                repeat_baseline["native_reward"] == 1
                and valid_discovery(baseline, challenged, repeated, audit, repeat_audit)
            )
        path.write_text(json.dumps(result))
        return result

    def evaluate(self, batch, candidate, capture_traces=False):
        if any(x["split"] == "H" for x in batch):
            raise ValueError("H forbidden in GEPA")
        with ThreadPoolExecutor(max_workers=4) as pool:
            outputs = list(pool.map(lambda unit: self.one(unit, candidate), batch))
        record = {
            "run_id": self.run_id,
            "role": self.role,
            "condition": self.condition,
            "candidate": candidate,
            "candidate_sha256": digest(candidate),
            "batch": batch,
            "scores": [x["score"] for x in outputs],
            "episodes": [x["episode"]["key"] for x in outputs],
        }
        with (self.directory / "candidate_history.jsonl").open("a") as f:
            f.write(json.dumps(record) + "\n")
        return EvaluationBatch(
            outputs=outputs,
            scores=record["scores"],
            trajectories=outputs if capture_traces else None,
        )

    def make_reflective_dataset(self, candidate, eval_batch, components_to_update):
        rows = []
        for output in eval_batch.trajectories or []:
            episode = output["episode"]
            if episode["split"] != "E":
                raise ValueError("Only E trajectories may enter reflection")
            task = self.retail.task(episode["task"], "E")
            feedback = {
                "native_reward": episode["native_reward"],
                "objective_score": output["score"],
            }
            if self.role == "customer":
                feedback.update(
                    review=output["audit"],
                    constraints=BOUNDARY,
                    search_context=reflection_context(self.condition, self.history, self.feedback),
                )
            rows.append(
                {
                    "Inputs": {"scenario": str(task.user_scenario)},
                    "Generated Outputs": public_trace(episode),
                    "Feedback": feedback,
                }
            )
        return {name: rows for name in components_to_update}
