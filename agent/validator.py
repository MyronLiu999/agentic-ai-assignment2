"""Assignment 2, Part B: the validator. This file is yours.

Four checks you can run before anything executes (Lecture 2, slide 17). The checker
calls validate directly, on slide 18's specimen, on the good plans in
fixtures/plans/, and on plans of its own with one defect each, so keep this exact
signature:

    validate(plan) -> {"valid": bool, "failures": [{"step": id, "check": n}]}

    plan      a plan (checks/FORMATS.md, section 10.1)
    valid     True when nothing failed
    failures  one entry for each step and check that fails, in any order; "step"
              is the step's id, or None for check 4, which is about the whole plan

The checks, defined exactly in section 10.2:

    1  every referenced tool exists: a step's tool is in the plan's own list,
       plan["meta"]["tools"]
    2  no step depends on a later step: every id in depends_on is an earlier step's
       (a dependency on itself, or on a step that does not exist, fails too)
    3  every step has an observable outcome: a tool, and an expected_result
    4  the step count is within bounds: at least one, and at most max_steps in
       agent/bounds.yaml (agent.loop.load_bounds())

Building it is half the job. The other half is in ways/planned.py: a plan that
fails is rejected, re-planned up to your bound, then escalated. Never executed.
"""

from __future__ import annotations
from agent.loop import load_bounds


def validate(plan: dict) -> dict:
    failures = []

    steps = plan.get("steps", [])
    allowed_tools = plan.get("meta", {}).get("tools", [])
    max_steps = load_bounds().max_steps

    # Check the plan must have 1 to max_steps steps
    if not 1 <= len(steps) <= max_steps:
        failures.append({"step": None, "check": 4})

    earlier_ids = set()

    for step in steps:
        step_id = step["id"]
        tool = step.get("tool")

        # Check the tool must be in the plan's tool list
        if tool and tool not in allowed_tools:
            failures.append({"step": step_id, "check": 1})

        # Check dependencies must refer to earlier steps
        if any(
            dep not in earlier_ids
            for dep in step.get("depends_on", [])
        ):
            failures.append({"step": step_id, "check": 2})

        # Check each step needs a tool and expected result
        if not tool or not step.get("expected_result"):
            failures.append({"step": step_id, "check": 3})

        earlier_ids.add(step_id)

    return {
        "valid": len(failures) == 0,
        "failures": failures,
    }
