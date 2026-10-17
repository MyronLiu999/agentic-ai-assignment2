"""Assignment 2, Parts A and C: the planner and the reviser. This file is yours.

The contract (checks/FORMATS.md, section 10). The checker calls both functions
directly, with a scripted model that keeps a copy of every request, so keep these
exact signatures. You may add keyword arguments with defaults; the checker passes
only these.

    make_plan(client, issue_number, tool_schemas, trace=None) -> dict
    revise_plan(client, plan, step_results, trace=None) -> dict

    client        harness.client.ModelClient: client.create(system=, messages=, tools=)
    issue_number  the issue to triage, an int
    tool_schemas  the tool definitions, as ctx.tools.schemas gives them
    plan          the active plan, its steps' statuses brought up to date
    step_results  one entry per step that has run (section 10.3)
    trace         a harness.trace.TraceWriter, or None. When you get one, trace your
                  model call: trace.model_call(0, response, purpose="plan") in
                  make_plan, purpose="revise" in revise_plan
    returns       a plan (section 10.1): {"meta": {...}, "steps": [...]}

make_plan writes the plan and stops (Lecture 2, slide 15). Its request carries the
task, the issue number and the tool list, and nothing from the issue: not its body,
not its title. The checker looks for each issue's sentinel string in every request.
The usual way to get the plan back as data: offer the model one tool whose
input_schema is agent/plan_schema.json, and read the plan from its call. Mark an
argument that waits on an earlier step's result "$fill"; the executor fills it in.

revise_plan repairs before it replaces: it never changes a step whose status is
done; it may change a step that failed, to retry it, and any step not yet run. A
step whose step result says "done" comes back unchanged, every field but status,
whatever the model wrote. Its request carries structured step results only (status, counts, paths,
error codes): never the issue's text, and never a step result's `args`, which the
executor may have filled in from the issue.

Keep your prompts in prompts/.
"""

from __future__ import annotations
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "agent" / "plan_schema.json"
PROMPT_PATH = ROOT / "prompts" / "planner.md"


def make_plan(
    client,
    issue_number: int,
    tool_schemas: list[dict],
    trace=None
) -> dict:

    # Load the schema and planner prompt
    schema = json.loads(
        SCHEMA_PATH.read_text(encoding="utf-8")
    )

    system_prompt = PROMPT_PATH.read_text(
        encoding="utf-8"
    )

    # Extract available tool names
    tool_names = [tool["name"] for tool in tool_schemas]

    # Provide the task, issue number and tool list
    user_message = (
        "Create a triage plan for this GitHub issue.\n"
        f"Issue number: {issue_number}\n"
        f"Available tools: {json.dumps(tool_schemas)}\n"
        "Do not access or assume the issue's content."
    )

    # Define the tool that receives the plan
    plan_tool = {
        "name": "submit_plan",
        "description": "Submit a structured execution plan.",
        "input_schema": schema,
        "strict": True
    }

    # Ask the model to generate a plan
    response = client.create(
        system=system_prompt,
        messages=[
            {"role": "user", "content": user_message}
        ],
        tools=[plan_tool]
    )

    # Record the model call
    if trace is not None:
        trace.model_call(
            0, response, purpose="plan"
        )

    # Extract the plan from the tool call
    plan_data = {"steps": []}

    for call in response.tool_calls:
        if call.name == "submit_plan":
            plan_data = call.input
            break

    # Add metadata
    plan_data["meta"] = {
        "model": response.model,
        "prompt": "prompts/planner.md",
        "tools": tool_names
    }

    return plan_data



def revise_plan(client, plan: dict, step_results: list[dict], trace=None) -> dict:
    """Revise using structured outcomes, never untrusted issue text or filled args."""
    import copy
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / "agent" / "plan_schema.json").read_text(encoding="utf-8"))
    system = (root / "prompts" / "reviser.md").read_text(encoding="utf-8")

    # A step result's args can contain the issue text, so explicitly exclude them.
    safe_results = [
        {key: copy.deepcopy(result.get(key)) for key in
         ("id", "tool", "status", "count", "paths", "error_code")}
        for result in step_results
    ]

    # The original plan has only pre-issue placeholders, not the filled arguments.
    current_steps = copy.deepcopy(plan.get("steps", []))
    messages = [{"role": "user", "content": json.dumps({
        "plan": {"steps": current_steps},
        "step_results": safe_results,
        "instruction": "Revise failed or pending steps. Keep completed steps unchanged."
    }, ensure_ascii=False)}]

    response = client.create(
        system=system,
        messages=messages,
        tools=[{
            "name": "submit_plan",
            "description": "Submit a revised, structured plan.",
            "input_schema": schema,
            "strict": True,
        }],
    )
    if trace is not None:
        trace.model_call(0, response, purpose="revise")

    candidate = {"steps": []}
    for call in response.tool_calls:
        if call.name == "submit_plan" and isinstance(call.input, dict):
            candidate = copy.deepcopy(call.input)
            break

    # If the model changes or omits a done step, restore it in its original order.
    completed_ids = {
        str(r["id"]) for r in safe_results if r.get("status") == "done"
    }
    completed_ids.update(str(s["id"]) for s in current_steps if s.get("status") == "done")
    frozen = [s for s in current_steps if str(s["id"]) in completed_ids]
    others = [s for s in candidate.get("steps", [])
              if isinstance(s, dict) and str(s.get("id")) not in completed_ids]
    for step in frozen:
        step["status"] = "done"
    candidate["steps"] = frozen + others
    candidate["meta"] = {
        "model": response.model,
        "prompt": "prompts/reviser.md",
        "tools": list(plan.get("meta", {}).get("tools", [])),
    }
    return candidate
