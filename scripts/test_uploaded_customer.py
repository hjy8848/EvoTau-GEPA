"""Evaluate one uploaded Customer overlay on the three saved successful E cases."""

import argparse
import fcntl
import json
import os
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
os.environ["TAU2_DATA_DIR"] = str(root / "benchmark/data")
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

from evotau_gepa.adapter import RetailAdapter
from evotau_gepa.customer import customer_role_error
from evotau_gepa.retail import Retail, digest
from evotau_gepa.transport import MODEL, Gateway


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def transcript(episode):
    text = f"Native reward: {episode['native_reward']}; seed: {episode['seed']}\n\n"
    for index, message in enumerate(episode["simulation"]["messages"]):
        text += f"### 消息 {index} · {message['role']}\n\n"
        if message.get("content"):
            text += str(message["content"]) + "\n\n"
        if message.get("tool_calls"):
            text += (
                "```json\n"
                + json.dumps(message["tool_calls"], ensure_ascii=False, indent=2)
                + "\n```\n\n"
            )
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("attachment", type=Path)
    parser.add_argument("--send", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "paper/candidate_inspection/conditional_commitment_test",
    )
    args = parser.parse_args()
    attachment = args.attachment.read_text()
    start = attachment.index("# Customer Interaction Strategy — Conditional Commitment Probing")
    end = attachment.index("## 这为什么是针对你三个案例的进化？", start)
    candidate = attachment[start:end].strip()
    assert customer_role_error(candidate) is None
    destination = args.output
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "candidate.txt").write_text(candidate)
    prior = json.loads(
        (root / "paper/candidate_inspection/customer_v4_single_call/report.json").read_text()
    )
    baselines = [
        json.loads((root / f"results/episodes/{case['baseline_episode']}.json").read_text())
        for case in prior["tasks"]
    ]
    manifest = json.loads((root / "configs/experiment_manifest.json").read_text())
    assert len(baselines) == 3 and {b["task"] for b in baselines} == {"21", "48", "112"}
    assert all(
        b["native_reward"] == 1
        and b["service"] == ""
        and b["customer"] == ""
        and b["seed"] == 101
        and b["split"] == "E"
        and b["task"] in manifest["E"]
        for b in baselines
    )
    run_id = "customer-conditional-commitment-" + digest(candidate)[:12]
    report = {
        "status": "prepared_not_sent",
        "scope": "Standalone three-case E test of uploaded candidate; not a GEPA search or frozen-study resumption",
        "model": MODEL,
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "run_id": run_id,
        "candidate_sha256": digest(candidate),
        "candidate_characters": len(candidate),
        "tasks": [b["task"] for b in baselines],
        "initial_seed": 101,
        "conditional_repeat_seed": 202,
        "native_reference_reuse": [b["key"] for b in baselines],
        "qwen_parent_evaluated": False,
        "new_proposal_calls": 0,
    }
    if not args.send:
        save(destination / "prepared.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    lock = (root / "results/execution.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    gateway = Gateway(root)
    retail = Retail(root, manifest, gateway)
    adapter = RetailAdapter(retail, "customer", run_id, service="")
    before = gateway.budget.summary()["reserved_or_sent"]
    report.update(status="running", request_count_before=before)
    save(destination / "report.json", report)
    try:
        batch = [{"task": b["task"], "seed": 101, "split": "E"} for b in baselines]
        result = adapter.evaluate(batch, {"customer_instructions": candidate}, capture_traces=True)
        outputs = result.outputs
        save(destination / "evaluations.json", outputs)
        cases = []
        text = "# Conditional Commitment 候选：原生对照与实际对话\n\n"
        text += "只测试上传候选；没有执行 Qwen 父候选。原生对照复用缓存，候选与审查为本次执行。\n\n"
        for baseline, output in zip(baselines, outputs, strict=True):
            assert output["baseline"] == baseline["key"]
            episode = output["episode"]
            audit = output["audit"]
            case = {
                "task": baseline["task"],
                "native_baseline_reward": baseline["native_reward"],
                "challenged_native_reward": episode["native_reward"],
                "discovery_score": output["score"],
                "audit": audit,
                "episode_key": episode["key"],
                "repeat_executed": "repeated" in output,
            }
            text += f"## 任务 {baseline['task']}\n\n"
            text += "### 本次自动审查（保留不确定性）\n\n```json\n"
            text += json.dumps(audit, ensure_ascii=False, indent=2) + "\n```\n\n"
            text += "## 原生 Customer 对照\n\n" + transcript(baseline)
            text += "## 上传候选 Customer\n\n" + transcript(episode)
            if "repeated" in output:
                repeated = json.loads(
                    (root / f"results/episodes/{output['repeated']}.json").read_text()
                )
                repeat_baseline = json.loads(
                    (root / f"results/episodes/{output['repeat_baseline']}.json").read_text()
                )
                case.update(
                    repeat_native_reward=repeated["native_reward"],
                    repeat_baseline_reward=repeat_baseline["native_reward"],
                    repeat_audit=output["repeat_audit"],
                )
                text += "## 第二 seed 原生对照\n\n" + transcript(repeat_baseline)
                text += "## 第二 seed 上传候选\n\n" + transcript(repeated)
            cases.append(case)
        (destination / "dialogue_comparisons.md").write_text(text)
        report.update(
            status="completed",
            cases=cases,
            valid_discoveries=sum(o["score"] == 1 for o in outputs),
        )
    except Exception as exc:
        report.update(status="failed", error=str(exc))
        raise
    finally:
        after = gateway.budget.summary()["reserved_or_sent"]
        report.update(request_count_after=after, new_model_requests=after - before)
        save(destination / "report.json", report)
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    print(
        json.dumps(
            {
                "status": report["status"],
                "new_model_requests": report["new_model_requests"],
                "valid_discoveries": report.get("valid_discoveries"),
                "cases": [
                    {k: v for k, v in case.items() if k not in {"audit", "repeat_audit"}}
                    for case in report.get("cases", [])
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
