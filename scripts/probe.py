"""Secret-safe discovery and one budgeted Qwen call; no fallback or retries."""

import json
import os
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from evotau_gepa.budget import Budget

root = Path(__file__).resolve().parents[1]
budget = Budget(root / "results/request_budget.sqlite3")
base = os.environ.get("EVOTAU_API_BASE", "http://10.130.138.46:8010/v1")
key = (
    os.environ.get("EVOTAU_API_KEY")
    or subprocess.check_output(
        ["security", "find-generic-password", "-a", "litellm-api-key", "-s", "litellm", "-w"],
        text=True,
    ).strip()
)
opener = build_opener(ProxyHandler({}))
headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
record = {"base": base, "date": "2026-10-12", "backend_weights_verified": False}
try:
    with opener.open(Request(base + "/models", headers=headers), timeout=20) as response:
        model_ids = [x["id"] for x in json.load(response)["data"]]
    record["qwen_aliases"] = [x for x in model_ids if "qwen3.7-plus" in x]
except (OSError, ValueError) as exc:
    record["discovery_error"] = type(exc).__name__ + ": " + str(exc)[:300]
model = "dashscope/qwen3.7-plus"
payload = {
    "model": model,
    "messages": [{"role": "user", "content": "Reply exactly OK."}],
    "temperature": 0,
    "max_tokens": 32,
    "enable_thinking": False,
}
request_id = budget.reserve("smoke", "openai/" + model)
started = time.monotonic()
try:
    with opener.open(
        Request(base + "/chat/completions", headers=headers, data=json.dumps(payload).encode()),
        timeout=30,
    ) as response:
        body = json.load(response)
        record.update(status=response.status, response=body)
    budget.finish(request_id, True, time.monotonic() - started, {"usage": body.get("usage", {})})
except (OSError, ValueError) as exc:
    record.update(
        status=getattr(exc, "code", None), error=type(exc).__name__ + ": " + str(exc)[:300]
    )
    if isinstance(exc, HTTPError):
        record["error_body"] = exc.read().decode()[:2000].replace(key, "[REDACTED]")
    budget.finish(request_id, False, time.monotonic() - started, {"error": record["error"]})
record["budget"] = budget.summary()
(root / "results/gateway_probes").mkdir(exist_ok=True)
(root / f"results/gateway_probes/{request_id:06d}.json").write_text(json.dumps(record, indent=2))
(root / "results/gateway_probe.json").write_text(json.dumps(record, indent=2))
print(json.dumps(record, indent=2))
