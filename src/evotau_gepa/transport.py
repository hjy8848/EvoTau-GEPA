"""One HTTP attempt per reservation. Native tau2 receives native ModelResponse."""

import contextvars
import json
import os
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from .budget import Budget

MODEL = "openai/dashscope/qwen3.7-plus"
ROLE = contextvars.ContextVar("role", default="native")
RUN_ID = contextvars.ContextVar("run_id", default="unattributed")


class Gateway:
    def __init__(self, root):
        self.root = Path(root)
        self.budget = Budget(self.root / "results/request_budget.sqlite3")
        self.base = os.environ["EVOTAU_API_BASE"].rstrip("/")
        self.key = (
            os.environ.get("EVOTAU_API_KEY")
            or subprocess.check_output(
                [
                    "security",
                    "find-generic-password",
                    "-a",
                    "litellm-api-key",
                    "-s",
                    "litellm",
                    "-w",
                ],
                text=True,
            ).strip()
        )
        self.opener = build_opener(ProxyHandler({}))
        (self.root / "results/requests").mkdir(exist_ok=True)

    def chat(self, messages, role, tools=None, seed=None, max_tokens=4096, **ignored):
        payload = {
            "model": MODEL.removeprefix("openai/"),
            "messages": messages,
            "temperature": 0,
            "enable_thinking": False,
            "max_tokens": max_tokens,
        }
        if tools:
            payload.update(tools=tools, tool_choice="auto")
        if seed is not None:
            payload["seed"] = seed
        request_id = self.budget.reserve(role, MODEL)
        started = time.monotonic()
        record = {"id": request_id, "role": role, "run_id": RUN_ID.get(), "request": payload}
        try:
            request = Request(
                self.base + "/chat/completions",
                data=json.dumps(payload).encode(),
                headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"},
            )
            with self.opener.open(request, timeout=120) as response:
                body = json.load(response)
            record["response"] = body
            self.budget.finish(
                request_id,
                True,
                time.monotonic() - started,
                {"usage": body.get("usage", {}), "role": role, "run_id": RUN_ID.get()},
            )
            return body
        except (OSError, ValueError) as exc:
            error = str(exc).replace(self.key, "[REDACTED]")
            if isinstance(exc, HTTPError):
                error += ": " + exc.read().decode().replace(self.key, "[REDACTED]")[:2000]
            record["error"] = error
            self.budget.finish(request_id, False, time.monotonic() - started, {"error": error})
            raise RuntimeError(error) from None
        finally:
            record["latency"] = time.monotonic() - started
            (self.root / f"results/requests/{request_id:06d}.json").write_text(json.dumps(record))

    def completion(self, model, messages, tools=None, **kwargs):
        from litellm import ModelResponse

        if model != MODEL:
            raise ValueError(f"Forbidden model change: {model}")
        return ModelResponse(**self.chat(messages, ROLE.get(), tools=tools, **kwargs))

    def reflection(self, prompt):
        messages = [{"role": "user", "content": prompt}] if isinstance(prompt, str) else prompt
        return self.chat(messages, "reflection")["choices"][0]["message"]["content"]

    def install_native(self):
        from tau2.evaluator import evaluator_nl_assertions
        from tau2.utils import llm_utils

        llm_utils.completion = self.completion
        evaluator_nl_assertions.DEFAULT_LLM_NL_ASSERTIONS = MODEL
        evaluator_nl_assertions.DEFAULT_LLM_NL_ASSERTIONS_ARGS = {
            "temperature": 0,
            "num_retries": 0,
        }
        # The gateway does not publish verified prices. Never use guessed LiteLLM prices.
        llm_utils.get_response_cost = lambda response: None
