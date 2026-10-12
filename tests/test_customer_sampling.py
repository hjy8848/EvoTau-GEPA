"""Offline checks of native-success filtering and random Customer minibatches."""

import json
from types import SimpleNamespace

import pytest
from gepa.core.data_loader import ListDataLoader

from evotau_gepa.customer import BOUNDARY
from evotau_gepa.experiment import Experiment, RandomCustomerBatchSampler


def experiment_stub(tmp_path, rewards, calls):
    experiment = object.__new__(Experiment)
    experiment.root = tmp_path
    experiment.manifest = {
        "E": ["22", "48", "112", "21"],
        "V": ["13", "107"],
        "customer_metric_calls": 12,
        "service_metric_calls": 12,
    }
    experiment.h_request_reserve = 13000
    experiment.gateway = SimpleNamespace(
        budget=SimpleNamespace(summary=lambda: {"remaining": 20000}),
        reflection=lambda _: pytest.fail("An offline test must not call a model"),
    )

    def run(task, seed, service, customer, condition, split):
        calls.append((task, seed, service, customer, condition, split))
        return {"key": f"native-{service}-{task}", "native_reward": rewards[service][task]}

    experiment.retail = SimpleNamespace(root=tmp_path, run=run)
    return experiment


def capture_optimize(monkeypatch, captured):
    def optimize(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(
            best_candidate=kwargs["seed_candidate"],
            val_aggregate_scores=[0],
            best_idx=0,
            num_candidates=1,
            total_metric_calls=12,
            to_dict=lambda: {"offline_stub": True},
        )

    monkeypatch.setattr("evotau_gepa.experiment.gepa.optimize", optimize)


def test_sampler_has_three_distinct_cases_and_reproducible_iteration_draws():
    loader = ListDataLoader(["a", "b", "c", "d"])
    sampler = RandomCustomerBatchSampler(17)
    draws = [sampler.next_minibatch_ids(loader, SimpleNamespace(i=i)) for i in range(12)]
    assert all(len(draw) == len(set(draw)) == 3 for draw in draws)
    assert all(set(draw) <= set(loader.all_ids()) for draw in draws)
    assert len({frozenset(draw) for draw in draws}) > 1
    # No mutable RNG state: restarting at an iteration reproduces that draw.
    assert (
        RandomCustomerBatchSampler(17).next_minibatch_ids(loader, SimpleNamespace(i=7)) == draws[7]
    )


@pytest.mark.parametrize("size", [1, 2, 3])
def test_small_pool_is_used_once_without_padding(size):
    loader = ListDataLoader(list(range(size)))
    draw = RandomCustomerBatchSampler(17).next_minibatch_ids(loader, SimpleNamespace(i=0))
    assert len(draw) == size
    assert set(draw) == set(range(size))


def test_customer_filters_native_failures_with_same_pool_and_draws_for_free_rc(
    tmp_path, monkeypatch
):
    calls, captured = [], []
    rewards = {"S1": {"22": 1, "48": 0, "112": 1, "21": 1}}
    experiment = experiment_stub(tmp_path, rewards, calls)
    capture_optimize(monkeypatch, captured)
    for condition in ["free", "rc"]:
        experiment.search(
            "customer", f"new-{condition}", 17, BOUNDARY, service="S1", condition=condition
        )
    for kwargs in captured:
        assert [unit["task"] for unit in kwargs["trainset"]] == ["22", "112", "21"]
        assert kwargs["valset"] == experiment.units("V")
        assert "reflection_minibatch_size" not in kwargs
        assert kwargs["max_metric_calls"] == 12
    assert all(call[1:] == (101, "S1", "", "reference-native-customer", "E") for call in calls)
    draws = [
        kwargs["batch_sampler"].next_minibatch_ids(
            ListDataLoader(kwargs["trainset"]), SimpleNamespace(i=2)
        )
        for kwargs in captured
    ]
    assert draws[0] == draws[1]
    records = [
        json.loads(
            (tmp_path / f"results/searches/new-{condition}/reflection_sampling.json").read_text()
        )
        for condition in ["free", "rc"]
    ]
    assert records[0] == records[1]
    assert records[0]["eligible_tasks"] == ["22", "112", "21"]
    assert len(records[0]["references"]) == 4


def test_service_update_changes_eligibility_and_service_search_is_unchanged(tmp_path, monkeypatch):
    calls, captured = [], []
    experiment = experiment_stub(
        tmp_path,
        {
            "S1": {"22": 1, "48": 0, "112": 0, "21": 0},
            "S2": {"22": 0, "48": 1, "112": 0, "21": 0},
        },
        calls,
    )
    capture_optimize(monkeypatch, captured)
    for service in ["S1", "S2"]:
        experiment.search("customer", f"new-{service}", 17, BOUNDARY, service=service)
    assert [unit["task"] for unit in captured[0]["trainset"]] == ["22"]
    assert [unit["task"] for unit in captured[1]["trainset"]] == ["48"]
    native_call_count = len(calls)
    experiment.search("service", "service-test", 17, "S2")
    assert len(calls) == native_call_count
    assert captured[2]["trainset"] == experiment.units("E")
    assert captured[2]["reflection_minibatch_size"] == 2
    assert "batch_sampler" not in captured[2]


def test_no_native_success_stops_before_gepa_without_fabricating_scores(tmp_path, monkeypatch):
    calls, captured = [], []
    experiment = experiment_stub(
        tmp_path, {"S1": {task: 0 for task in ["22", "48", "112", "21"]}}, calls
    )
    capture_optimize(monkeypatch, captured)
    with pytest.raises(ValueError, match="No native-success E tasks"):
        experiment.search("customer", "none-pass", 17, BOUNDARY, service="S1")
    assert not captured
    directory = tmp_path / "results/searches/none-pass"
    assert json.loads((directory / "reflection_sampling.json").read_text())["eligible_tasks"] == []
    assert not (directory / "selection.json").exists()


def test_completed_v3_selection_cannot_bypass_revision_guard(tmp_path):
    directory = tmp_path / "results/searches/old-v3"
    directory.mkdir(parents=True)
    (directory / "customer_revision.json").write_text(json.dumps("customer-evidence-v3"))
    saved_selection = '{"prompt": "old strategy"}'
    (directory / "selection.json").write_text(saved_selection)
    calls = []
    experiment = experiment_stub(tmp_path, {}, calls)
    with pytest.raises(ValueError, match="revision mismatch"):
        experiment.search("customer", "old-v3", 17, BOUNDARY)
    assert not calls
    assert (directory / "selection.json").read_text() == saved_selection
