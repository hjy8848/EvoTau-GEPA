import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from evotau_gepa.budget import Budget, BudgetExceeded


def test_budget_concurrent_and_resume(tmp_path):
    path = tmp_path / "budget.sqlite3"
    budget = Budget(path, limit=13)

    def attempt(_):
        try:
            return budget.reserve("test", "qwen")
        except BudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = [x for x in pool.map(attempt, range(50)) if x is not None]
    assert len(ids) == len(set(ids)) == 13
    recovered = Budget(path, limit=13)
    assert recovered.summary()["remaining"] == 0
    with pytest.raises(BudgetExceeded):
        recovered.reserve("test", "qwen")
    with pytest.raises(ValueError):
        Budget(path, limit=30_000)


def test_failure_consumes_budget(tmp_path):
    budget = Budget(tmp_path / "b.sqlite3", limit=1)
    rid = budget.reserve("test", "qwen")
    budget.finish(rid, False, 0.1, {"error": "timeout"})
    assert budget.summary()["states"] == {"failed": 1}
    with pytest.raises(BudgetExceeded):
        budget.reserve("retry", "qwen")


def test_manifest_disjoint():
    manifest = json.loads(
        (Path(__file__).parents[1] / "configs/experiment_manifest.json").read_text()
    )
    assert not (set(manifest["E"]) & set(manifest["V"]))
    assert not (set(manifest["E"] + manifest["V"]) & set(manifest["H"]))
    assert manifest["request_limit"] == 30_000


def test_h_never_enters_adapter():
    from evotau_gepa.adapter import RetailAdapter

    adapter = object.__new__(RetailAdapter)
    with pytest.raises(ValueError):
        adapter.evaluate([{"task": "5", "seed": 1, "split": "H"}], {})


def test_overlay_isolation():
    from tau2.agent.llm_agent import LLMAgent
    from tau2.user.user_simulator import UserSimulator

    from evotau_gepa.retail import Customer, Service

    agent = Service(tools=[], domain_policy="POLICY", llm="openai/dashscope/qwen3.7-plus")
    native = LLMAgent(tools=[], domain_policy="POLICY", llm=agent.llm)
    assert agent.system_prompt == native.system_prompt
    agent.overlay = "SERVICE_ONLY"
    user = Customer(llm=agent.llm, instructions="SCENARIO")
    user.overlay = "CUSTOMER_ONLY"
    assert agent.system_prompt.startswith(native.system_prompt)
    assert "SERVICE_ONLY" not in user.system_prompt
    assert "CUSTOMER_ONLY" not in agent.system_prompt
    user.overlay = ""
    assert user.system_prompt == UserSimulator(llm=agent.llm, instructions="SCENARIO").system_prompt


def test_repair_attribution_and_information_isolation():
    from evotau_gepa.repair import make_feedback, reflection_context

    base = {
        "task": "0",
        "seed": 101,
        "split": "E",
        "customer": "",
        "service": "",
        "native_reward": 0,
        "simulation": {"messages": []},
    }
    after = {**base, "service": "repair", "native_reward": 1}
    feedback = make_feedback("", "repair", base, after)
    assert "repair_feedback" not in reflection_context("free", [], [feedback])
    assert reflection_context("rc", [], [feedback])["repair_feedback"] == [feedback]
    with pytest.raises(ValueError):
        make_feedback("", "repair", base, {**after, "seed": 202})
    with pytest.raises(ValueError):
        make_feedback("", "repair", base, {**after, "native_reward": 0})


def test_invalid_discovery_scores_zero():
    from evotau_gepa.customer import valid_discovery

    success, failure = {"native_reward": 1}, {"native_reward": 0}
    valid = {"legal": True, "exposed": True, "failure_verified": True}
    assert valid_discovery(success, failure, failure, valid, valid)
    assert not valid_discovery(success, failure, failure, {**valid, "legal": False}, valid)
    assert not valid_discovery(failure, failure, failure, valid, valid)
