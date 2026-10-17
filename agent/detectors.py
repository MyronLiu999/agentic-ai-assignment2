"""Assignment 2, Part E: three detectors, and Lecture 2's ladder. This file is yours.

Three of the four plan failures have a detector of a few lines over a trace
(Lecture 2, slides 45 to 49); the validator is the fourth. The checker calls these
on fixtures/traces/, so keep these exact signatures (checks/FORMATS.md, section
10.2):

    repeat_check(trace) -> list[list]
        Circular reasoning. Exact repeats: two tool_call lines with the same tool
        and the same arguments. Returns each group of repeats as the step numbers
        of its tool_call lines, e.g. [[1, 3], [2, 4]].

    orphan_check(plan) -> list
        Hallucinated steps. The ids of steps, other than the last, whose result no
        later step uses: no later step lists them in depends_on.

    expected_result_check(plan, trace) -> list
        Premature stopping, in part. The ids of plan steps that did not run and
        return their result. It catches skipped steps, not a plan too short.

    ladder(plan, trace) -> {"q1": {"fired": bool, "ids": [...]}, "q2": ..., "q3": ...}
        Lecture 2's questions 1 to 3 (slide 50), in order: did any two steps do
        the same thing; is any step's result never used; did every planned step
        execute. "ids" says what fired: groups of step numbers for q1, step ids
        (or, from a trace, step numbers) for q2 and q3. Question 4 needs
        judgment, so it goes in your report, not here.

    plan    a plan (section 10.1)
    trace   a list of trace lines, as harness.trace.read_trace returns them

A plan with no depends_on cannot answer question 2 from the plan. Answer it from
the trace, and say how in your report.
"""

from __future__ import annotations

import json

from agent.diff import _match_calls


def repeat_check(trace: list[dict]) -> list[list]:
    """Return groups of exact repeats, by traced model-call step number."""
    groups = {}
    for line in trace:
        if line.get("type") != "tool_call":
            continue
        key = json.dumps(
            [line.get("name"), line.get("input") or {}],
            sort_keys=True, ensure_ascii=False,
        )
        groups.setdefault(key, []).append(line.get("step"))
    return [steps for steps in groups.values() if len(steps) >= 2]


def orphan_check(plan: dict) -> list:
    """Find non-final plan steps never depended on by a later step."""
    steps = plan.get("steps", [])
    unused = []
    for i, step in enumerate(steps[:-1]):
        step_id = str(step.get("id"))
        if not any(
            any(str(dep) == step_id for dep in later.get("depends_on", []) or [])
            for later in steps[i + 1:]
        ):
            unused.append(step.get("id"))
    return unused


def expected_result_check(plan: dict, trace: list[dict]) -> list:
    """Report steps with no matching successful tool call."""
    steps = plan.get("steps", [])
    matched = {}
    for line, index in _match_calls(plan, trace):
        if index is not None:
            matched[index] = line
    return [
        step.get("id") for index, step in enumerate(steps)
        if index not in matched or matched[index].get("is_error", False)
    ]


def _trace_orphans(trace: list[dict]) -> list:
    """Conservative fallback when there is no dependency graph.

    A final tool result with no subsequent tool use and a turn-limit ending
    never fed a later action. Earlier uses cannot be proved from the available
    excerpts, so do not guess about them.
    """
    calls = [r for r in trace if r.get("type") == "tool_call"]
    if not calls:
        return []
    # A later model response could consume the result in a final answer.
    # With no later model call, the final tool output was visibly unused.
    last_index = max(i for i, item in enumerate(trace)
                     if item.get("type") == "tool_call")
    if any(item.get("type") == "model_call"
           for item in trace[last_index + 1:]):
        return []
    return [calls[-1].get("step")]


def ladder(plan: dict, trace: list[dict]) -> dict:
    """Lecture 2's three diagnostic questions."""
    repeats = repeat_check(trace)
    steps = plan.get("steps", [])
    if any("depends_on" in step for step in steps):
        orphans = orphan_check(plan)
    else:
        orphans = _trace_orphans(trace)
    unfinished = expected_result_check(plan, trace)
    return {
        "q1": {"fired": bool(repeats), "ids": repeats},
        "q2": {"fired": bool(orphans), "ids": orphans},
        "q3": {"fired": bool(unfinished), "ids": unfinished},
    }


if __name__ == "__main__":
    from pathlib import Path
    from harness.trace import read_trace

    root = Path(__file__).resolve().parents[1] / "fixtures" / "traces"
    for name in ("looping", "s49"):
        folder = root / name
        plan = json.loads((folder / "plan.json").read_text(encoding="utf-8"))
        trace = read_trace(folder / "trace.jsonl")
        print(name, json.dumps(ladder(plan, trace), indent=2))
