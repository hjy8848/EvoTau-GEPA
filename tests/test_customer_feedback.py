"""Offline semantic regressions using real v1 cases, never paid model calls."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from gepa.core.adapter import EvaluationBatch
from gepa.strategies.instruction_proposal import InstructionProposalSignature
from tau2.domains.retail.environment import get_environment

from evotau_gepa.adapter import RetailAdapter
from evotau_gepa.customer import (
    BOUNDARY,
    CUSTOMER_REFLECTION_TEMPLATE,
    customer_role_error,
    deterministic_findings,
    exploration_feedback,
    reconcile_review,
    review,
    valid_discovery,
)


def case(task_id):
    value = json.loads(
        (Path(__file__).parent / f"fixtures/customer_case_{task_id}.json").read_text()
    )
    return SimpleNamespace(user_scenario=value["scenario"]), value["episode"]


def judgment(**changes):
    value = {
        "legal": True,
        "exposed": False,
        "failure_verified": False,
        "goal_fidelity": "preserved",
        "strategy_exposure": "not_observed",
        "service_failure": "not_supported",
        "strategy_evidence_indices": [],
        "evidence_indices": [],
        "goal_reason": "Native goals preserved",
        "exposure_reason": "No concrete strategy exposure established",
        "failure_reason": "No failure established",
        "reason": "review",
    }
    value.update(changes)
    return value


@pytest.mark.parametrize(
    "text",
    [
        "You are an AI assistant acting as a customer service agent in a retail domain.",
        "You are a retail customer service assistant. Help users with orders.",
        "You are a customer service agent. Authenticate users and call get_user_details.",
    ],
)
def test_service_identity_cannot_be_evaluated_as_customer(text):
    assert customer_role_error(text)


def test_customer_can_discuss_service_without_being_rejected():
    assert customer_role_error(BOUNDARY) is None
    assert customer_role_error("You are a Customer speaking to a support agent.") is None
    assert customer_role_error("Ask Service to clarify the order before you confirm.") is None


def test_rejected_role_has_no_fabricated_native_episode(tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("Role rejection must happen before any native/model call")

    adapter = RetailAdapter(SimpleNamespace(root=tmp_path, run=forbidden), "customer", "v2-free")
    batch = adapter.evaluate(
        [{"task": "13", "seed": 101, "split": "E"}],
        {"customer_instructions": "You are a customer service agent."},
        True,
    )
    assert batch.outputs[0]["episode"] is None
    assert batch.outputs[0]["evaluation_status"] == "rejected_role"
    record = json.loads((adapter.directory / "candidate_history.jsonl").read_text())
    assert record["episodes"] == []
    assert record["candidate_errors"]


def test_customer_reflection_separates_behavior_and_environment(tmp_path):
    task, episode = case("107")
    episode.update(split="E", key="fixture", seed=101)
    retail = SimpleNamespace(root=tmp_path, task=lambda *args: task)
    adapter = RetailAdapter(
        retail, "customer", "v2-free", condition="free", feedback=[{"secret_diff": "RC_ONLY"}]
    )
    output = {"episode": episode, "score": 0.0, "audit": judgment(), "baseline_native_reward": 1}
    rows = adapter.make_reflective_dataset(
        {}, EvaluationBatch([output], [0], [output]), ["customer_instructions"]
    )
    row = rows["customer_instructions"][0]
    assert row["Inputs"]["optimized_role"] == "Customer"
    assert all(m["role"] == "user" for m in row["Generated Outputs"]["customer_utterances"])
    assert all(m["role"] != "user" for m in row["Environment Responses"]["service_and_tools"])
    assert len(row["Environment Responses"]["full_chronological_trace"]) == len(
        episode["simulation"]["messages"]
    )
    assert "repair_feedback" not in row["Feedback"]["search_context"]
    assert "RC_ONLY" not in json.dumps(row)
    InstructionProposalSignature.validate_prompt_template(CUSTOMER_REFLECTION_TEMPLATE)
    rendered = InstructionProposalSignature.prompt_renderer(
        {
            "current_instruction_doc": BOUNDARY,
            "dataset_with_feedback": [row],
            "prompt_template": CUSTOMER_REFLECTION_TEMPLATE,
        }
    )
    assert "NOT optimizing the Service agent" in rendered
    assert "<curr_instructions>" not in rendered
    assert "<inputs_outputs_feedback>" not in rendered


def test_zero_discovery_retains_different_stage_feedback():
    absent = exploration_feedback(judgment(), 1, 1, 0)
    exposed = exploration_feedback(judgment(exposed=True, strategy_exposure="observed"), 1, 1, 0)
    assert absent["final_discovery_score"] == exposed["final_discovery_score"] == 0
    assert absent["strategy_exposure"] != exposed["strategy_exposure"]
    assert "baseline" in exposed["native_outcomes"]


def test_mouse_return_overrides_false_legal_review():
    task, episode = case("13")
    findings = deterministic_findings(task, episode, get_environment().get_policy())
    violation = next(x for x in findings if x["kind"] == "return_protected_item")
    assert violation["item_id"] == "5796612084"
    result = reconcile_review(judgment(), episode, findings)
    assert result["legal"] is False
    assert result["goal_fidelity"] == "violated"
    assert result["model_review"]["legal"] is True
    assert result["review_conflicts"]


def test_service_return_does_not_alone_make_customer_illegal():
    task, episode = case("13")
    for message in episode["simulation"]["messages"]:
        if message["role"] == "user":
            message["content"] = "Keep the Gaming Mouse; do not return it."
    findings = deterministic_findings(task, episode, get_environment().get_policy())
    assert not any(x["kind"] == "return_protected_item" for x in findings)


def test_same_id_exchange_has_policy_evidence_not_automatic_discovery():
    task, episode = case("107")
    findings = deterministic_findings(task, episode, get_environment().get_policy())
    violation = next(x for x in findings if x["kind"] == "same_variant_exchange")
    assert violation["confirmed"]
    result = reconcile_review(judgment(), episode, findings)
    assert result["failure_verified"] is True
    assert result["model_review"]["failure_verified"] is False
    assert result["evidence_indices"]
    # A policy finding is not proof of candidate exposure, novelty or repetition.
    assert result["exposed"] is False
    assert not valid_discovery(
        {"native_reward": 1}, {"native_reward": 0}, {"native_reward": 0}, result, result
    )


def test_rejected_same_id_exchange_is_not_completed_violation():
    task, episode = case("107")
    bad_ids = {
        c["id"]
        for m in episode["simulation"]["messages"]
        for c in m.get("tool_calls") or []
        if c["name"] == "exchange_delivered_order_items"
        and c["arguments"]["item_ids"] == c["arguments"]["new_item_ids"]
    }
    for message in episode["simulation"]["messages"]:
        if message.get("id") in bad_ids and message["role"] == "tool":
            message.update(error=True, content=json.dumps({"error": "different variant required"}))
    findings = deterministic_findings(task, episode, get_environment().get_policy())
    assert findings and not any(f["confirmed"] for f in findings)
    assert not reconcile_review(judgment(), episode, findings)["failure_verified"]


def test_conflicting_review_fields_and_noncustomer_exposure_are_unresolved():
    _, episode = case("107")
    result = reconcile_review(judgment(exposed=False, strategy_exposure="observed"), episode, [])
    assert result["review_conflicts"] and not result["exposed"]
    result = reconcile_review(
        judgment(exposed=True, strategy_exposure="observed", strategy_evidence_indices=[0]),
        episode,
        [],
    )
    assert not result["exposed"]  # index 0 is Service, not Customer


def test_review_uses_rules_even_with_incorrect_model_judgment():
    task, episode = case("107")
    captured = []

    def chat(messages, role):
        captured.append(messages)
        return {"choices": [{"message": {"content": json.dumps(judgment())}}]}

    gateway = SimpleNamespace(chat=chat)
    result = review(gateway, task, BOUNDARY, episode, get_environment().get_policy())
    assert result["failure_verified"]
    assert "deterministic_findings" in captured[0][0]["content"]


def test_v2_cannot_reuse_frozen_v1_search_directory(tmp_path):
    directory = tmp_path / "results/searches/M2-free-17"
    directory.mkdir(parents=True)
    (directory / "candidate_history.jsonl").write_text("{}\n")
    with pytest.raises(ValueError, match="immutable"):
        RetailAdapter(SimpleNamespace(root=tmp_path), "customer", "M2-free-17")


def test_reconciliation_does_not_mutate_original_judgment():
    task, episode = case("13")
    original = judgment()
    preserved = copy.deepcopy(original)
    reconcile_review(
        original, episode, deterministic_findings(task, episode, get_environment().get_policy())
    )
    assert original == preserved


def test_explicit_exposure_reason_contradiction_is_flagged():
    _, episode = case("107")
    result = reconcile_review(
        judgment(exposure_reason="The interaction strategy was fully exposed and successful."),
        episode,
        [],
    )
    assert result["strategy_exposure"] == "uncertain"
    assert not result["exposed"]
    assert any("explanation" in conflict for conflict in result["review_conflicts"])


def test_experiment_wires_official_customer_template(tmp_path, monkeypatch):
    import evotau_gepa.experiment as module
    from evotau_gepa.experiment import Experiment

    captured = []

    def optimize(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(
            best_candidate={"customer_instructions": BOUNDARY},
            val_aggregate_scores=[0],
            best_idx=0,
            num_candidates=1,
            total_metric_calls=12,
            to_dict=lambda: {"test": True},
        )

    monkeypatch.setattr(module.gepa, "optimize", optimize)
    experiment = object.__new__(Experiment)
    experiment.root = tmp_path
    experiment.manifest = {"E": ["22"], "V": ["13"], "customer_metric_calls": 12}
    experiment.h_request_reserve = 13000
    experiment.gateway = SimpleNamespace(
        budget=SimpleNamespace(summary=lambda: {"remaining": 20000}),
        reflection=lambda _: None,
    )
    experiment.retail = SimpleNamespace(root=tmp_path)
    result = experiment.search("customer", "v2-free", 17, BOUNDARY)
    assert captured[0]["reflection_prompt_template"] == CUSTOMER_REFLECTION_TEMPLATE
    assert captured[0]["max_metric_calls"] == 12
    assert result["metric_budget_completed"]
