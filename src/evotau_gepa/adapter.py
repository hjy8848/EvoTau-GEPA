"""Thin official GEPA adapter; scores do not replace native rewards."""

import json
from concurrent.futures import ThreadPoolExecutor

from gepa.core.adapter import EvaluationBatch
from tau2.domains.retail.environment import get_environment

from .customer import (
    CUSTOMER_REVISION,
    customer_role_error,
    exploration_feedback,
    public_trace,
    review,
    valid_discovery,
)
from .reflection import extract_evidence, shared_context
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
        if role == "customer":
            revision_path = self.directory / "customer_revision.json"
            if (self.directory / "candidate_history.jsonl").exists() and not revision_path.exists():
                raise ValueError("Frozen v1 Customer run is immutable; use a new run_id")
            if (
                revision_path.exists()
                and json.loads(revision_path.read_text()) != CUSTOMER_REVISION
            ):
                raise ValueError("Customer run revision mismatch; use a new run_id")
            revision_path.write_text(json.dumps(CUSTOMER_REVISION))

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
        role_error = customer_role_error(prompt)
        if role_error:
            return {
                "episode": None,
                "unit": unit,
                "score": 0.0,
                "evaluation_status": "rejected_role",
                "candidate_error": role_error,
                "proposed_strategy": prompt,
            }
        path = self.directory / (
            digest({"candidate": candidate, "unit": unit, "revision": CUSTOMER_REVISION}) + ".json"
        )
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
        result = {
            "episode": challenged,
            "baseline": baseline["key"],
            "baseline_native_reward": baseline["native_reward"],
            "audit": audit,
            "score": 0.0,
            "evaluation_status": "evaluated",
            "customer_revision": CUSTOMER_REVISION,
        }
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
            "episodes": [x["episode"]["key"] for x in outputs if x.get("episode")],
            "evaluation_statuses": [x.get("evaluation_status", "evaluated") for x in outputs],
            "candidate_errors": [x["candidate_error"] for x in outputs if x.get("candidate_error")],
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
        evidence = []
        for output in eval_batch.trajectories or []:
            episode = output["episode"]
            unit = episode or output["unit"]
            if unit["split"] != "E":
                raise ValueError("Only E trajectories may enter reflection")
            task = self.retail.task(unit["task"], "E")
            if episode is None:
                evidence.append(None)
                rows.append(
                    {
                        "Inputs": {
                            "scenario": str(task.user_scenario),
                            "optimized_role": "Customer",
                        },
                        "Generated Outputs": {
                            "rejected_strategy": output["proposed_strategy"],
                            "customer_utterances": [],
                        },
                        "Feedback": {
                            "evaluation_status": "rejected_before_simulation",
                            "candidate_error": output["candidate_error"],
                            "objective": "Rewrite only lawful Customer behavior, not Service duties. No native episode or reward exists for this rejected proposal.",
                        },
                    }
                )
                continue
            feedback = {
                "native_reward": episode["native_reward"],
                "objective_score": output["score"],
            }
            if self.role == "customer":
                excerpt = extract_evidence(episode["simulation"]["messages"], output["audit"])
                evidence.append(excerpt)
                feedback.update(
                    exploration=exploration_feedback(
                        output["audit"],
                        episode["native_reward"],
                        output.get("baseline_native_reward"),
                        output["score"],
                    ),
                    evidence_indices=output["audit"].get("evidence_indices", []),
                    strategy_evidence_indices=output["audit"].get("strategy_evidence_indices", []),
                    objective="Find lawful, evidenced Service decision errors by changing Customer interaction strategy. Preserve native goals. Do not optimize Service task completion.",
                )
                rows.append(
                    {
                        "Inputs": {
                            "scenario": str(task.user_scenario),
                            "optimized_role": "Customer",
                            "available_backend_tools": [],
                        },
                        "Generated Outputs": {
                            "customer_utterances": excerpt["customer_utterances"]
                        },
                        "Environment Responses": {
                            k: v for k, v in excerpt.items() if k != "customer_utterances"
                        },
                        "Feedback": feedback,
                    }
                )
                continue
            rows.append(
                {
                    "Inputs": {"scenario": str(task.user_scenario)},
                    "Generated Outputs": public_trace(episode),
                    "Feedback": feedback,
                }
            )
        if self.role == "customer" and rows:
            for i, row in enumerate(rows):
                row["Inputs"]["case_id"] = i
            rows[0]["Shared Context"] = shared_context(
                self.condition, self.history, self.feedback, evidence
            )
        return {name: rows for name in components_to_update}
