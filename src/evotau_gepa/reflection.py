"""Deterministic Customer evidence extraction; original trajectories stay intact."""

import hashlib
import json
import re


def trace_id(messages):
    canonical = [
        {
            "index": m.get("index", i),
            "role": m["role"],
            "content": m.get("content"),
            "tool_calls": m.get("tool_calls"),
        }
        for i, m in enumerate(messages)
    ]
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


def tool_result(content, referenced):
    """Project observed fields, never substitute a guessed result or policy verdict."""
    try:
        value = json.loads(content)
    except (TypeError, ValueError):
        return {"text": content}
    if not isinstance(value, dict):
        return {"value": value}
    # Root identities/status and business facts remain available. Only repetitive
    # catalogue variants are projected; unknown structures are preserved.
    result = {}
    omitted = {}
    for key, field in value.items():
        if key == "variants" and isinstance(field, dict):
            selected = {k: v for k, v in field.items() if k in referenced}
            result[key] = selected if selected else field
            if selected and len(selected) != len(field):
                omitted[key] = {
                    "omitted_records": len(field) - len(selected),
                    "selection": "item IDs mentioned in the actual dialogue or tool arguments",
                }
        else:
            result[key] = field
    if omitted:
        result["reflection_omissions"] = omitted
    return result


def extract_evidence(messages, audit=None, historical=False):
    """Keep all Customer turns and decision/response witnesses in original order."""
    indexed = [{**m, "index": m.get("index", i)} for i, m in enumerate(messages)]
    referenced = set()
    for m in indexed:
        if m["role"] in {"user", "assistant"}:
            referenced.update(re.findall(r"\b\d{8,}\b", m.get("content") or ""))
        for call in m.get("tool_calls") or []:
            referenced.update(re.findall(r"\b\d{8,}\b", json.dumps(call.get("arguments", {}))))
    selected = set()
    # Every Customer turn receives its immediate surrounding Service response.
    # This also retains correctly handled challenges and absent exposure, not just
    # failures chosen by an unreliable automatic judge.
    customer_positions = [i for i, m in enumerate(indexed) if m["role"] == "user"]
    for i in [] if historical else customer_positions:
        if i:
            selected.add(i - 1)
        if i + 1 < len(indexed):
            selected.add(i + 1)
    # Current cases and repair witnesses retain every tool decision/result. Older
    # history retains Customer behavior and all tool decisions, plus write/error
    # results and their context. Routine read payloads remain in the source archive.
    for i, m in enumerate(indexed):
        calls = m.get("tool_calls") or []
        if calls or (m["role"] == "tool" and not historical):
            selected.add(i)
        if historical and calls and any(not c["name"].startswith(("get_", "find_")) for c in calls):
            selected.update(range(max(0, i - 2), min(len(indexed), i + 3)))
        if historical and m["role"] == "tool":
            try:
                body = json.loads(m.get("content") or "null")
            except (ValueError, TypeError):
                body = None
            if m.get("error") or (isinstance(body, dict) and "error" in body):
                selected.update(range(max(0, i - 1), min(len(indexed), i + 2)))
    anchors = set()
    if audit:
        anchors.update(i for i in audit.get("evidence_indices", []) if type(i) is int)
        anchors.update(i for i in audit.get("strategy_evidence_indices", []) if type(i) is int)
        for finding in audit.get("deterministic_findings", []):
            anchors.update(i for i in finding.get("evidence_indices", []) if type(i) is int)
    for i, m in enumerate(indexed):
        if m["index"] in anchors:
            selected.update(range(max(0, i - 1), min(len(indexed), i + 2)))
    customer = [
        {"index": m["index"], "content": m.get("content")} for m in indexed if m["role"] == "user"
    ]
    environment = []
    last_calls = []
    for i, m in enumerate(indexed):
        if m.get("tool_calls"):
            last_calls = m["tool_calls"]
        if i not in selected or m["role"] == "user":
            continue
        row = {"index": m["index"], "role": m["role"]}
        if m["role"] == "tool":
            # A JSON string prevents GEPA's recursive Markdown renderer from
            # expanding every database field into its own heading.
            row["result_json"] = json.dumps(
                tool_result(m.get("content"), referenced), ensure_ascii=False, separators=(",", ":")
            )
            row["responds_to_call_ids"] = (
                [m["id"]] if m.get("id") else [c["id"] for c in last_calls if "id" in c]
            )
            if "error" in m:
                row["error"] = m["error"]
        else:
            row["content"] = m.get("content")
            if m.get("tool_calls"):
                row["tool_calls"] = m["tool_calls"]
        environment.append(row)
    return {
        "evidence_id": trace_id(messages),
        "representation": "historical_excerpt" if historical else "case_or_repair_witness",
        "customer_utterances": customer,
        "environment_events": environment,
        "event_order": sorted([m["index"] for m in customer + environment]),
        "source_message_count": len(indexed),
        "omitted_message_indices": [
            m["index"] for i, m in enumerate(indexed) if m["role"] != "user" and i not in selected
        ],
        "interpretation": "Excerpt, not a complete transcript. Original indices preserve order and expose gaps; tool-result omissions are explicit. Historical excerpts omit routine read results; absence is not proof of availability, correctness or failure. Full originals remain in saved episodes/history.",
    }


def shared_context(condition, history, feedback, current_evidence):
    """One batch-level history registry, shared identically by Free/RC."""
    if condition not in {"free", "rc"}:
        raise ValueError(condition)
    locations = {e["evidence_id"]: f"case:{i}" for i, e in enumerate(current_evidence) if e}
    registry = []

    def pack(value, witness=False):
        if (
            isinstance(value, list)
            and value
            and all(isinstance(m, dict) and "role" in m for m in value)
        ):
            key = trace_id(value)
            if key not in locations and not witness:
                key += ":history"
            if key not in locations:
                locations[key] = f"shared:{len(registry)}"
                excerpt = extract_evidence(value, historical=not witness)
                excerpt["source_trace_sha256"] = excerpt["evidence_id"]
                excerpt["evidence_id"] = key
                registry.append(excerpt)
            return {"evidence_ref": key, "location": locations[key]}
        if isinstance(value, list):
            return [pack(v, witness) for v in value]
        if isinstance(value, dict):
            return {
                k: pack(v, witness or k in {"before", "after", "repeat_before", "repeat_after"})
                for k, v in value.items()
            }
        return value

    unique_history = {}
    for entry in history:
        unique_history.setdefault(json.dumps(entry, sort_keys=True), entry)
    common = pack(list(unique_history.values()))
    # The common portion is built before RC additions, ensuring equal shared history.
    context = {"history": common, "history_evidence": list(registry)}
    if condition == "rc":
        repair = pack(feedback, witness=True)
        context.update(
            repair_feedback=repair,
            repair_only_evidence=registry[len(context["history_evidence"]) :],
        )
    return context
