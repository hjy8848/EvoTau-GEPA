"""Only a measured paired repair can become repair feedback."""

import difflib

from .customer import public_trace
from .retail import digest


def make_feedback(before_prompt, after_prompt, before, after):
    if before["task"] != after["task"] or before["seed"] != after["seed"]:
        raise ValueError("Repair evidence must share task and seed")
    if before["customer"] != after["customer"] or before["split"] == "H" or after["split"] == "H":
        raise ValueError("Repair evidence has changed customer or leaked H")
    if before["service"] != before_prompt or after["service"] != after_prompt:
        raise ValueError("Prompt/trajectory attribution mismatch")
    if not (
        before["native_reward"] == 0
        and after["native_reward"] == 1
        and before_prompt != after_prompt
    ):
        raise ValueError("No observed fail-to-pass repair")
    return {
        "s0": digest(before_prompt),
        "s1": digest(after_prompt),
        "task": before["task"],
        "seed": before["seed"],
        "text_diff": "\n".join(
            difflib.unified_diff(before_prompt.splitlines(), after_prompt.splitlines())
        ),
        "before": public_trace(before),
        "after": public_trace(after),
        "native_reward_before": 0,
        "native_reward_after": 1,
        "scope": "Observed on this task and seed; independent repetitions recorded separately.",
        "uncertain_boundary": "No general repair effectiveness implied outside measured conditions.",
    }


def reflection_context(condition, common_history, feedback):
    if condition == "free":
        return {"history": common_history}
    if condition == "rc":
        return {"history": common_history, "repair_feedback": feedback}
    raise ValueError(condition)
