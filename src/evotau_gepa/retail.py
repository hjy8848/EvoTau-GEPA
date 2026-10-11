"""Overlays subclass upstream prompts; task, tools, policy and scorer stay native."""

import fcntl
import hashlib
import json
from pathlib import Path

from tau2.agent.llm_agent import LLMAgent
from tau2.domains.retail.environment import get_environment
from tau2.orchestrator.orchestrator import Orchestrator
from tau2.runner.simulation import run_simulation
from tau2.user.user_simulator import UserSimulator

from .transport import MODEL, ROLE, RUN_ID


class Service(LLMAgent):
    overlay = ""

    @property
    def system_prompt(self):
        return super().system_prompt + (
            "\n<strategy_overlay>\n" + self.overlay + "\n</strategy_overlay>"
            if self.overlay
            else ""
        )

    def generate_next_message(self, *args, **kwargs):
        token = ROLE.set("service")
        try:
            return super().generate_next_message(*args, **kwargs)
        finally:
            ROLE.reset(token)


class Customer(UserSimulator):
    overlay = ""

    @property
    def system_prompt(self):
        return super().system_prompt + (
            "\n<strategy_overlay>\n" + self.overlay + "\n</strategy_overlay>"
            if self.overlay
            else ""
        )

    def generate_next_message(self, *args, **kwargs):
        token = ROLE.set("customer")
        try:
            return super().generate_next_message(*args, **kwargs)
        finally:
            ROLE.reset(token)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class Retail:
    def __init__(self, root, manifest, gateway):
        self.root, self.manifest, self.gateway = Path(root), manifest, gateway
        gateway.install_native()
        self.directory = self.root / "results/episodes"
        self.directory.mkdir(exist_ok=True)

    def task(self, task_id, split, final=False):
        if task_id not in self.manifest[split] or (split == "H" and not final):
            raise ValueError("Task access violates frozen E/V/H protocol")
        from tau2.data_model.tasks import Task

        # Parse only the authorized task into a Task object; never send evaluation criteria to agents.
        path = self.root / self.manifest["data_dir"] / "tau2/domains/retail/tasks.json"
        raw = next(x for x in json.loads(path.read_text()) if x["id"] == task_id)
        return Task.model_validate(raw)

    def run(
        self, task_id, seed, service="", customer="", condition="native", split="E", final=False
    ):
        identity = {
            "task": task_id,
            "seed": seed,
            "service": service,
            "customer": customer,
            "condition": condition,
            "split": split,
            "manifest": digest(self.manifest),
            "model": MODEL,
        }
        locks = self.directory / "locks"
        locks.mkdir(exist_ok=True)
        with (locks / (digest(identity) + ".lock")).open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return self._run_unlocked(task_id, seed, service, customer, condition, split, final)

    def _run_unlocked(
        self, task_id, seed, service="", customer="", condition="native", split="E", final=False
    ):
        task = self.task(task_id, split, final)
        identity = {
            "task": task_id,
            "seed": seed,
            "service": service,
            "customer": customer,
            "condition": condition,
            "split": split,
            "manifest": digest(self.manifest),
            "model": MODEL,
        }
        key = digest(identity)
        path = self.directory / f"{key}.json"
        if path.exists():
            return json.loads(path.read_text())
        env = get_environment()
        args = {"temperature": 0, "num_retries": 0, "extra_body": {"enable_thinking": False}}
        agent = Service(
            tools=env.get_tools(), domain_policy=env.get_policy(), llm=MODEL, llm_args=args
        )
        agent.overlay = service
        user = Customer(llm=MODEL, instructions=str(task.user_scenario), tools=None, llm_args=args)
        user.overlay = customer
        orchestrator = Orchestrator(
            domain="retail",
            agent=agent,
            user=user,
            environment=env,
            task=task,
            seed=seed,
            max_steps=self.manifest["max_steps"],
            max_errors=10,
        )
        token = RUN_ID.set(condition)
        try:
            result = run_simulation(orchestrator)
        finally:
            RUN_ID.reset(token)
        record = {
            **identity,
            "key": key,
            "native_reward": result.reward_info.reward,
            "simulation": result.model_dump(mode="json"),
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record))
        temporary.replace(path)
        return record
