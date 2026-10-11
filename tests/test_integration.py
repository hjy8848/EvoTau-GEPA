import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from gepa.core.adapter import EvaluationBatch

from evotau_gepa.adapter import RetailAdapter
from evotau_gepa.reporting import clustered_difference


def test_adapter_preserves_native_reward(tmp_path):
    episode = {"key": "test", "native_reward": 0.75}
    retail = SimpleNamespace(root=tmp_path, run=lambda *args: episode)
    adapter = RetailAdapter(retail, "service", "test")
    result = adapter.evaluate(
        [{"task": "0", "seed": 101, "split": "E"}], {"service_instructions": ""}, True
    )
    assert isinstance(result, EvaluationBatch)
    assert result.scores == [0.75]
    assert result.outputs[0]["episode"] is episode
    assert result.trajectories == result.outputs


def test_reflection_rejects_validation_and_h(tmp_path):
    adapter = RetailAdapter(SimpleNamespace(root=tmp_path), "service", "test")
    for split in ["V", "H"]:
        output = {"episode": {"split": split}}
        with pytest.raises(ValueError):
            adapter.make_reflective_dataset(
                {}, EvaluationBatch([output], [0], [output]), ["service_instructions"]
            )


def test_native_retail_toolkit_has_no_user_tools():
    from tau2.domains.retail.environment import get_environment

    env = get_environment()
    assert env.user_tools is None
    assert env.get_tools()
    assert env.get_policy()


def test_cluster_bootstrap_uses_tasks_not_episodes():
    free = {"a": [0] * 100, "b": [1]}
    rc = {"a": [1] * 100, "b": [0]}
    result = clustered_difference(free, rc, samples=100)
    assert result["effect"] == 0
    assert result["task_count"] == 2
    assert clustered_difference({}, {})["effect"] is None


def test_actual_smoke_preserves_scoring():
    path = Path(__file__).parents[1] / "results/smoke.json"
    if not path.exists():
        pytest.skip("Real smoke has not been executed")
    episode = json.loads(path.read_text())
    assert episode["native_reward"] == episode["simulation"]["reward_info"]["reward"]
    assert episode["simulation"]["messages"]
    assert any(x.get("tool_calls") for x in episode["simulation"]["messages"])


def test_offline_reporting_does_not_invent_results(tmp_path):
    from evotau_gepa.budget import Budget
    from evotau_gepa.reporting import build

    for folder in [
        "results/episodes",
        "results/searches",
        "results/requests",
        "paper/tables",
        "paper/figures",
        "configs",
    ]:
        (tmp_path / folder).mkdir(parents=True, exist_ok=True)
    manifest = {"E": ["0"], "V": ["1"], "H": ["5"], "search_seeds": [17, 29, 43]}
    (tmp_path / "configs/experiment_manifest.json").write_text(json.dumps(manifest))
    Budget(tmp_path / "results/request_budget.sqlite3")
    summary = build(tmp_path)
    assert summary["episodes"] == 0
    assert summary["primary_statistics"]["effect"] is None
    assert not summary["complete"]
    assert len(list((tmp_path / "paper/figures").glob("*.png"))) >= 8


def test_cached_episode_resume_and_condition_identity(tmp_path):
    from evotau_gepa.retail import Retail, digest
    from evotau_gepa.transport import MODEL

    retail = object.__new__(Retail)
    retail.root = tmp_path
    retail.manifest = {"E": ["0"]}
    retail.directory = tmp_path
    retail.task = lambda *args: None
    identity = {
        "task": "0",
        "seed": 101,
        "service": "",
        "customer": "",
        "condition": "free",
        "split": "E",
        "manifest": digest(retail.manifest),
        "model": MODEL,
    }
    key = digest(identity)
    record = {**identity, "key": key, "native_reward": 1}
    (tmp_path / f"{key}.json").write_text(json.dumps(record))
    assert retail.run("0", 101, condition="free") == record
    assert digest({**identity, "condition": "rc"}) != key


def test_stagnation_still_attempts_all_alternating_cycles(tmp_path):
    from evotau_gepa.experiment import Experiment

    experiment = object.__new__(Experiment)
    experiment.root = tmp_path
    experiment.manifest = {"status": "frozen", "search_seeds": [17], "cycles": 2}
    calls = []

    def search(role, run_id, seed, initial="", **kwargs):
        calls.append(run_id)
        return {"prompt": initial, "best_score": 0, "initial_score": 0, "stagnated": True}

    experiment.search = search
    experiment.verified_repair = lambda *args: []
    experiment.evaluate_service = lambda *args: []
    states = experiment.run()
    assert len(states) == 1
    assert states[0]["feedback"] == []
    assert not any(x.startswith("M3-") for x in calls)
    for condition in ["free", "rc"]:
        branch = states[0]["branches"][condition]
        assert not branch["fixed_search_eligible"]
        assert len(branch["cycles"]) == 2
        assert not any(c["service_promoted"] for c in branch["cycles"])
