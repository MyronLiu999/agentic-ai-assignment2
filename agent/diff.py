"""Assignment 2, Part E: the diff of the plan against what ran. This file is yours.

"The one people skip" (Lecture 2, slide 54). The checker calls it on slide 51's run
in fixtures/traces/s49/, so keep this exact signature (checks/FORMATS.md, section
10.2):

    diff(plan, trace) -> {"steps": {step_id: label}, "extra": [step numbers]}

    steps   every plan step, labelled
              executed   it ran, in plan order
              skipped    it never ran
              reordered  it ran, out of plan order
    extra   the step numbers of tool_call lines that match no plan step

How a call matches a step is yours to decide: by its tool, and by any arguments
the plan fixes (an argument written "$fill" is fixed by nothing). Say how in your
report. Mind slide 51's run: it calls run_tests twice, and only one of those calls
can belong to each run_tests step.
"""

from __future__ import annotations

import json


def _arguments(step: dict) -> dict:
    """Return a plan step's argument template as a dictionary."""
    value = step.get("args", step.get("args_json", {}))
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    return value if isinstance(value, dict) else {}


def _compatible(template, actual) -> bool:
    """A $fill is a wildcard; planned fixed values must remain unchanged."""
    if template == "$fill":
        return True
    if isinstance(template, dict):
        return (isinstance(actual, dict) and
                all(k in actual and _compatible(v, actual[k])
                    for k, v in template.items()))
    if isinstance(template, list):
        return (isinstance(actual, list) and len(template) == len(actual)
                and all(_compatible(p, a) for p, a in zip(template, actual)))
    return template == actual


def _match_calls(plan: dict, trace: list[dict]):
    """Yield (tool-call line, matching step index or None), one-to-one."""
    steps = plan.get("steps", [])
    used: set[int] = set()

    for line in trace:
        if line.get("type") != "tool_call":
            continue
        selected = None
        explicit_id = line.get("plan_step")
        for i, step in enumerate(steps):
            if i in used or step.get("tool") != line.get("name"):
                continue
            if explicit_id is not None:
                if str(step.get("id")) == str(explicit_id):
                    selected = i
                    break
            elif _compatible(_arguments(step), line.get("input") or {}):
                selected = i
                break
        if selected is not None:
            used.add(selected)
        yield line, selected


def diff(plan: dict, trace: list[dict]) -> dict:
    """Compare planned steps with executed calls without reusing a plan step."""
    steps = plan.get("steps", [])
    labels = {step["id"]: "skipped" for step in steps}
    matched = []
    extra = []

    for call, index in _match_calls(plan, trace):
        if index is None:
            extra.append(call.get("step", 0))
        else:
            matched.append(index)
            labels[steps[index]["id"]] = "executed"

    # Two steps are out of order if their relative execution order is inverted.
    out_of_order = set()
    for i in range(len(matched)):
        for j in range(i + 1, len(matched)):
            if matched[i] > matched[j]:
                out_of_order.update((matched[i], matched[j]))
    for index in out_of_order:
        labels[steps[index]["id"]] = "reordered"

    return {"steps": labels, "extra": extra}
