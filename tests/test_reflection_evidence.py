"""Semantic checks on reflection excerpts, chronology, references and parity."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

from gepa.core.adapter import EvaluationBatch

from evotau_gepa.adapter import RetailAdapter
from evotau_gepa.customer import public_trace
from evotau_gepa.reflection import extract_evidence, shared_context, tool_result


def test_nonfailure_keeps_customer_order_and_correct_service_response():
    messages = [
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "Address first, then items."},
        {"role": "assistant", "content": "I will confirm the address before changing items."},
        {"role": "assistant", "content": "unrelated housekeeping"},
        {"role": "assistant", "content": "another unrelated note"},
        {"role": "user", "content": "Yes, this address is correct."},
        {"role": "assistant", "content": "Address confirmed. Now confirm the items."},
    ]
    saved = copy.deepcopy(messages)
    evidence = extract_evidence(messages, {"failure_verified": False})
    assert messages == saved
    assert [m["index"] for m in evidence["customer_utterances"]] == [1, 5]
    assert evidence["event_order"] == [0, 1, 2, 4, 5, 6]
    assert evidence["omitted_message_indices"] == [3]
    assert evidence["environment_events"][-1]["content"] == messages[-1]["content"]


def test_audit_anchor_adds_required_surrounding_evidence():
    messages = [{"role": "assistant", "content": str(i)} for i in range(7)]
    evidence = extract_evidence(messages, {"evidence_indices": [3]})
    assert evidence["event_order"] == [2, 3, 4]
    assert evidence["omitted_message_indices"] == [0, 1, 5, 6]


def test_catalogue_projection_preserves_chosen_variants_and_marks_omissions():
    value = {
        "product_id": "p",
        "variants": {
            "1111111111": {"price": 10, "available": True},
            "2222222222": {"price": 20, "available": False},
        },
        "fulfillments": ["shipment"],
    }
    result = tool_result(json.dumps(value), {"1111111111"})
    assert result["variants"] == {"1111111111": value["variants"]["1111111111"]}
    assert result["reflection_omissions"]["variants"]["omitted_records"] == 1
    assert result["fulfillments"] == ["shipment"]
    assert tool_result(json.dumps(value), set()) == value
    assert tool_result("unparsed tool output", set()) == {"text": "unparsed tool output"}


def test_tool_arguments_results_errors_and_original_indices_survive():
    messages = [
        {"role": "user", "content": "Use item 1111111111."},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c", "name": "modify", "arguments": {"item_ids": ["1111111111"]}}
            ],
        },
        {"role": "tool", "id": "c", "error": True, "content": '{"error":"rejected"}'},
    ]
    evidence = extract_evidence(messages)
    assert evidence["event_order"] == [0, 1, 2]
    assert evidence["environment_events"][0]["tool_calls"] == messages[1]["tool_calls"]
    assert evidence["environment_events"][1]["error"] is True
    assert json.loads(evidence["environment_events"][1]["result_json"]) == {"error": "rejected"}
    assert evidence["environment_events"][1]["responds_to_call_ids"] == ["c"]


def test_shared_history_parity_dedup_and_rc_diff_retention():
    before = [{"role": "user", "content": "before"}, {"role": "assistant", "content": "failed"}]
    after = [{"role": "user", "content": "after"}, {"role": "assistant", "content": "repaired"}]
    history = [before, before, {"task": "22", "before": before, "after": after}]
    repairs = [
        {
            "text_diff": "ACTUAL_DIFF",
            "before": before,
            "after": after,
            "scope": "observed only",
            "repeat": {
                "before": before,
                "after": after,
                "native_reward_before": 0,
                "native_reward_after": 1,
            },
        }
    ]
    free = shared_context("free", history, repairs, [extract_evidence(before)])
    rc = shared_context("rc", history, repairs, [extract_evidence(before)])
    assert free["history"] == rc["history"]
    assert free["history_evidence"] == rc["history_evidence"]
    assert len(free["history"]) == 2
    assert len(free["history_evidence"]) == 1
    assert free["history"][0]["location"] == "case:0"
    assert "ACTUAL_DIFF" not in json.dumps(free)
    assert rc["repair_feedback"][0]["text_diff"] == "ACTUAL_DIFF"
    assert rc["repair_feedback"][0]["scope"] == "observed only"
    assert rc["repair_only_evidence"] == []
    registry = {e["evidence_id"] for e in free["history_evidence"]}
    registry.add(extract_evidence(before)["evidence_id"])
    for phase in ["before", "after"]:
        assert free["history"][1][phase]["evidence_ref"] in registry


def test_shared_context_appears_once_in_minibatch(tmp_path):
    fixture = json.loads((Path(__file__).parent / "fixtures/customer_case_107.json").read_text())
    episode = fixture["episode"]
    episode.update(split="E", seed=101, key="fixture")
    history = [public_trace(episode)]
    retail = SimpleNamespace(
        root=tmp_path, task=lambda *args: SimpleNamespace(user_scenario=fixture["scenario"])
    )
    adapter = RetailAdapter(retail, "customer", "evidence-free", history=history)
    output = {
        "episode": episode,
        "score": 0,
        "audit": {"legal": True, "exposed": False, "failure_verified": False},
        "baseline_native_reward": 1,
    }
    rows = adapter.make_reflective_dataset(
        {}, EvaluationBatch([output, output], [0, 0], [output, output]), ["customer_instructions"]
    )["customer_instructions"]
    assert sum("Shared Context" in row for row in rows) == 1
    assert not rows[0]["Shared Context"]["history_evidence"]
    assert rows[0]["Inputs"]["scenario"] == str(fixture["scenario"])
    assert "full_chronological_trace" not in json.dumps(rows)


def test_history_omits_routine_reads_but_keeps_customer_and_write_evidence():
    messages = [
        {"role": "user", "content": "Change the address before changing items."},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "read", "name": "get_user_details", "arguments": {"user_id": "u"}}
            ],
        },
        {"role": "tool", "id": "read", "content": '{"user_id":"u","orders":["o"]}'},
        {"role": "assistant", "content": "Please confirm."},
        {"role": "user", "content": "Yes."},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "write",
                    "name": "modify_pending_order_address",
                    "arguments": {"order_id": "o", "state": "NY"},
                }
            ],
        },
        {"role": "tool", "id": "write", "content": '{"order_id":"o","address":{"state":"NY"}}'},
    ]
    history = extract_evidence(messages, historical=True)
    assert [m["index"] for m in history["customer_utterances"]] == [0, 4]
    assert 2 in history["omitted_message_indices"]
    assert any(
        m["index"] == 6 and '"state":"NY"' in m["result_json"]
        for m in history["environment_events"]
    )
    witness = extract_evidence(messages)
    assert 2 in witness["event_order"]
