"""Assignment 2, Part D: the second pass before posting. This file is yours.

Two ways to read a drafted comment before it is posted (Lecture 2, slides 31 to 34).
The checker runs your planned way in each mode against scripted models, so keep
these exact signatures (checks/FORMATS.md, section 10.2). You may add keyword
arguments with defaults; the checker passes only these.

    verify(draft, ctx) -> {"passed": bool, "reasons": [str, ...]}

        The external mode: checks in code, against the repository and this run's
        own record. Four checks:
          1. the draft parses: a `label:` line with a known label, and a `file:`
             line with a path or none (section 1; harness.formats.parse_triage_comment);
          2. a path it names exists in the repository (ctx.root / "fixtures" / "repo");
          3. this run saw that path: a search_repo result or a read_file call in this
             run's trace returned it. Each tool_call line's `paths` field lists the
             files it returned; read the trace so far with
             harness.trace.read_trace(ctx.trace.path);
          4. the label and the file agree: a question names none, and every other
             label names a file.
        "reasons" says why each failed check failed; it is empty when all pass.
        verify reads only what the run itself can see: never the answer key, and
        never Assignment 1's grounded check in eval/. The checker flags a second
        pass that does.

    self_critique(client, draft, evidence, trace=None) -> {"decision": "accept" | "revise", "reason": str | None}

        The self mode: one model call that rereads the draft, given the issue and
        what the run's steps returned (`evidence`, a dict of your design), and
        accepts it or asks for a revision with a reason. Given a trace, trace the
        call: trace.model_call(n, response, purpose="critique").

    draft   the comment your executor drafted, the text post_comment would post
    ctx     the run's harness.runner.WayContext
    client  harness.client.ModelClient

The loop that uses them, its rewrite call and MAX_ROUNDS live in ways/planned.py.
Keep your prompts in prompts/.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness.formats import LABELS, normalize_path, parse_triage_comment
from harness.trace import read_trace


CRITIQUE_TOOL = {
    "name": "submit_critique",
    "description": "Accept the triage draft or request a revision with a reason.",
    "input_schema": {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": ["accept", "revise"]},
            "reason": {"type": ["string", "null"]},
        },
        "required": ["decision", "reason"],
        "additionalProperties": False,
    },
    "strict": True,
}


def verify(draft: str, ctx) -> dict:
    """Run four independent external checks, without consulting the answer key."""
    reasons = []
    label, path = parse_triage_comment(draft)
    parsed = label in LABELS and path is not None
    if not parsed:
        reasons.append("parse: missing or unknown label/file")

    # A `file: none` answer has no physical path to check or require in the trace.
    if path is not None and path != "none":
        repo = (Path(ctx.root) / "fixtures" / "repo").resolve()
        target = (repo / path).resolve()
        if not target.is_relative_to(repo) or not target.is_file():
            reasons.append("exists: the named file is not in the repository")

        trace_lines = read_trace(ctx.trace.path)
        seen = {
            normalize_path(str(p))
            for line in trace_lines
            if line.get("type") == "tool_call"
            for p in (line.get("paths") or [])
        }
        if path not in seen:
            reasons.append("seen: this run did not search for or read the named file")

    if parsed and ((label == "question") != (path == "none")):
        reasons.append("agreement: question requires none; other labels require a file")

    return {"passed": not reasons, "reasons": reasons}


def self_critique(client, draft: str, evidence: dict, trace=None, *, step: int = 0) -> dict:
    """Ask the model for an accept/revise decision based on this run's evidence."""
    prompt_path = Path(__file__).resolve().parents[1] / "prompts" / "self_critique.md"
    system = prompt_path.read_text(encoding="utf-8")
    message = (
        "Review this triage draft against the available evidence.\n"
        f"Draft:\n{draft}\n\n"
        f"Evidence:\n{json.dumps(evidence, ensure_ascii=False, default=str)}"
    )
    response = client.create(
        system=system,
        messages=[{"role": "user", "content": message}],
        tools=[CRITIQUE_TOOL],
    )
    if trace is not None:
        trace.model_call(step, response, purpose="critique")

    for call in response.tool_calls:
        if call.name == "submit_critique":
            decision = call.input.get("decision")
            reason = call.input.get("reason")
            if decision in ("accept", "revise"):
                return {"decision": decision, "reason": None if decision == "accept" else (reason or "Revise draft")}

    # If a provider replies in prose, accept a small set of unambiguous answers.
    text = response.text.strip()
    if text.lower() in ("accept", "yes"):
        return {"decision": "accept", "reason": None}
    if text.lower().startswith("revise"):
        return {"decision": "revise", "reason": text[len("revise"):].lstrip(": ") or "Revise draft"}
    return {"decision": "revise", "reason": "No valid critique decision returned"}
