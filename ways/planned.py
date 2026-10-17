"""Way 5, planned: RepoMind with a plan it cannot quietly abandon. This file is yours.

Assignment 2, Parts B, C and D. Like every way, run(ctx) -> None (see ways/__init__.py):

    python run.py --way planned --set dev
    python run.py --way planned --critique self --set dev      # or external; off is the default

The run, in order (checks/FORMATS.md, section 10):

  1. Plan: agent.plan.make_plan(ctx.client, ctx.issue_number, ctx.tools.schemas,
     trace=ctx.trace). The planner never sees the issue.
  2. Check, then validate. First the plan against agent/plan_schema.json, with
     harness.schema.errors (a vendor may accept "strict" and not enforce it), and
     that its last step is post_comment (a plan without one passes the four checks
     and posts nothing); a plan that breaks either writes
     ctx.trace.event("schema", errors=...). Then agent.validator.validate(plan). A
     plan that fails any of these is rejected and re-planned, at most MAX_REPLANS
     times; then the run escalates: ctx.trace.end("escalated", detail), no tool
     call, nothing posted.
  3. Log: ctx.trace.plan(1, plan), before the first tool call.
  4. Execute the steps in order, each tool call through ctx.call_tool, naming the
     plan step it runs for: ctx.call_tool(n, tool, args, plan_step=step["id"]). Where a step
     has arguments marked "$fill", one model call fills them in: offer it that
     step's tool alone, and trace it with ctx.trace.model_call(n,
     response, purpose="execute"). Act on the tool calls a response holds,
     whatever its stop_reason says. The executor may run, retry or halt a step; it
     may not add, drop or reorder one. A call that is not the current step's tool
     never runs: refuse it and write ctx.trace.event("off_plan", step=..., tool=...).
  5. Part C: revise only at one checkpoint, after a search step: the first search
     step, and any search step a revision added or changed, until MAX_REVISIONS is
     spent; and only when the check there fails (a check in code, not in a prompt,
     such as "the search returned at least one source file, not only documentation
     or translations"): revise_plan, which never changes a step that is done but may
     change a failed one to retry it, then check the new version against the schema
     and validate it, write
     ctx.trace.event("revision", checkpoint=..., version=..., valid=...), and log a
     valid one with ctx.trace.plan(version, plan). A revision that fails the
     validator never runs, and counts against the bound.
  6. Part D: before post_comment, the second pass, in the mode ctx.critique names
     (off, self or external), at most MAX_ROUNDS rounds. A round checks the
     current draft (agent.critique.verify, or agent.critique.self_critique) and
     writes ctx.trace.event("critique", round=..., mode=..., result="pass" or
     "fail", reason=..., changed=..., draft=...). A draft that fails, with a round
     left, is rewritten by one model call given the reason, traced with
     purpose="critique", and the next round checks the new draft. A draft still
     failing in the last round is held: not posted, and the run ends escalated.
     Nothing is posted until the last round, and a run posts one comment at most.

The comment is Assignment 1's: two lines, label: and file: (section 1). A run that
finishes its plan can leave its ending to the runner, which writes completed.

Part G's ways/notes.py extends this way without changing it, so build this one with
hooks it can override: for example an executor class whose methods run after every
step, build what each model call sees, and run before the comment.

The checker reads MAX_REPLANS, MAX_REVISIONS and MAX_ROUNDS from this module, and
runs this way against scripted models (harness/scripted.py). You can run them too,
free:

    python run.py --way planned --backend harness.scripted:AlwaysInvalidPlanner --set dev
    python run.py --way planned --backend harness.scripted:OffPlanExecutor --set dev
    python run.py --way planned --critique external --backend harness.scripted:DraftNamesUnseenFile --set dev
"""



from __future__ import annotations
import json
from pathlib import Path
from agent.plan import make_plan, revise_plan
from agent.validator import validate
from harness.schema import errors as schema_errors
from harness.formats import result_paths
from agent.critique import verify, self_critique

MAX_REPLANS = 1
MAX_REVISIONS = 1  # Part C
MAX_ROUNDS = 2    # Part D

SCHEMA_PATH = Path(__file__).resolve().parents[1] / 'agent' / 'plan_schema.json'


def step_args(step: dict) -> dict:
    value = step.get('args_json', step.get('args'))
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError('step arguments must be a JSON object')
    return value


def check_plan(plan: dict, schema: dict, trace) -> bool:
    if not isinstance(plan, dict):
        trace.event('schema', errors=['plan is not an object'])
        return False
    raw = {k: v for k, v in plan.items() if k != 'meta'}
    problems = schema_errors(raw, schema)
    steps = plan.get('steps', [])
    if not isinstance(steps, list) or not steps:
        problems.append('plan must contain steps')
    else:
        if not isinstance(steps[-1], dict) or steps[-1].get('tool') != 'post_comment':
            problems.append('last step must be post_comment')
        if any(isinstance(s, dict) and s.get('tool') == 'post_comment' for s in steps[:-1]):
            problems.append('post_comment may only be the final step')
        for i, step in enumerate(steps):
            try:
                step_args(step)
            except (TypeError, ValueError, json.JSONDecodeError, AttributeError) as exc:
                problems.append(f'steps[{i}]: invalid args_json ({exc})')
    if problems:
        trace.event('schema', errors=problems)
        return False
    return bool(validate(plan)['valid'])


class PlannedExecutor:
    """Executes one active plan version, with bounded revision at search checkpoints."""

    def __init__(self, ctx, schema: dict):
        self.ctx = ctx
        self.schema = schema
        self.history = []
        self.step_results = []
        self.model_step = 0
        self.tool_defs = {t["name"]: t for t in ctx.tools.schemas}
        self.version = 1
        self.revisions = 0
        self.checkpoints = set()


    @staticmethod
    def normalize_search_query(query: str) -> str:
        """
        Convert a natural-language search phrase into
        one code-oriented keyword for literal search.
        """
        import re

        words = re.findall(
            r"[A-Za-z_][A-Za-z0-9_]*",
            query
        )

        if not words:
            return query.strip()

        # Common descriptions mapped to code concepts.
        code_aliases = {
            "greyed": "disabled",
            "grayed": "disabled",
            "grey": "disabled",
            "gray": "disabled",
            "inactive": "disabled",
            "unclickable": "disabled",
            "duplicates": "dedupe",
            "duplicated": "dedupe",
            "duplication": "dedupe",
            "timeouts": "timeout",
            "timedout": "timeout",
            "translations": "i18n",
            "translated": "i18n",
        }

        # Prefer programming terminology when applicable.
        for word in words:
            replacement = code_aliases.get(word.lower())
            if replacement:
                return replacement

        # Ignore common natural-language filler words.
        low_signal = {
            "a", "an", "the", "is", "are", "was",
            "were", "of", "to", "in", "on", "for",
            "and", "or", "with", "after", "before",
            "when", "from", "by", "should",
            "does", "not", "no", "issue", "bug",
            "problem", "fails", "broken", "stays",
            "appears", "button", "screen",
            "page", "still", "out",
        }

        choices = [
            word for word in words
            if word.lower() not in low_signal
        ]

        if not choices:
            choices = words

        # Prefer identifier-like tokens, then longer words.
        best = max(
            choices,
            key=lambda word: (
                "_" in word or any(
                    c.isupper() for c in word[1:]
                ),
                len(word),
            )
        )

        return best.lower()




    
    def messages_for(self, step: dict) -> list[dict]:
        previous_queries = [
            r["args"]["query"]
            for r in self.step_results
            if r.get("tool") == "search_repo"
            and isinstance(r.get("args"), dict)
            and isinstance(r["args"].get("query"), str)
        ]

        message = (
            f"Triage issue #{self.ctx.issue_number}.\n"
            f"Current step: {json.dumps(step, ensure_ascii=False)}\n"
            f"Earlier results: {json.dumps(self.history, ensure_ascii=False)}\n"
            f"Previous search queries: {json.dumps(previous_queries)}\n"
        )

        if step.get("tool") == "search_repo":
            message += (
                "Choose a short code-related search keyword, "
                "not a full sentence or issue title.\n"
                "Search matches literal substrings of source lines.\n"
                "Do not repeat or slightly rephrase previous queries.\n"
                "If an earlier search failed, use a substantially "
                "different keyword.\n"
            )

        message += "Fill only this step and call its tool."

        return [{"role": "user", "content": message}]


    def after_step(self, step: dict, result) -> None:
        """Part G can override this to write a progress file."""
        pass

    def rewrite_draft(self, draft: str, reason: str) -> str:
        """One bounded model call to repair a draft; it cannot post a comment."""
        self.model_step += 1
        request = (
            f"Previous draft:\n{draft}\n\n"
            f"Reason for revision:\n{reason}\n\n"
            f"Evidence from this run:\n{json.dumps(self.history, ensure_ascii=False, default=str)}\n"
            "Rewrite the draft comment using only the evidence above."
        )
        response = self.ctx.client.create(
            system=self.ctx.prompt("rewrite.md"),
            messages=[{"role": "user", "content": request}],
            tools=[],
        )
        self.ctx.trace.model_call(self.model_step, response, purpose="critique")
        return response.text.strip() or draft

    def before_comment(self, text: str) -> bool:
        """Approve a draft, or hold it after the bounded second pass.

        On approval, self.approved_comment contains the exact text to post.
        This hook stays boolean so Parts B/C and Part G can reuse it.
        """
        mode = self.ctx.critique
        self.approved_comment = text
        if mode == "off":
            return True
        if mode not in ("self", "external"):
            return False

        draft = text
        for round_number in range(1, MAX_ROUNDS + 1):
            if mode == "external":
                assessment = verify(draft, self.ctx)
                passed = assessment["passed"]
                reason = "; ".join(assessment["reasons"]) or None
            else:
                self.model_step += 1
                assessment = self_critique(
                    self.ctx.client, draft,
                    {"issue_number": self.ctx.issue_number, "steps": self.history},
                    trace=self.ctx.trace, step=self.model_step,
                )
                passed = assessment["decision"] == "accept"
                reason = None if passed else (assessment.get("reason") or "Review requested a revision")

            next_draft = draft
            if not passed and round_number < MAX_ROUNDS:
                next_draft = self.rewrite_draft(draft, reason or "Revise the draft")

            self.ctx.trace.event(
                "critique", round=round_number, mode=mode,
                result="pass" if passed else "fail",
                reason=None if passed else reason,
                changed=next_draft != draft,
                draft=draft,
            )
            if passed:
                self.approved_comment = draft
                return True
            draft = next_draft

        return False

    
    def fill(self, step: dict, template: dict) -> dict | None:
        import re

        name = step["tool"]
        self.model_step += 1

        response = self.ctx.client.create(
            system=self.ctx.prompt("executor.md"),
            messages=self.messages_for(step),
            tools=[self.tool_defs[name]],
        )

        self.ctx.trace.model_call(
            self.model_step,
            response,
            purpose="execute"
        )

        calls = response.tool_calls

        for call in calls:
            if call.name != name:
                self.ctx.trace.event(
                    "off_plan",
                    step=step["id"],
                    tool=call.name
                )

        if len(calls) != 1 or calls[0].name != name:
            return None

        args = calls[0].input

        if not isinstance(args, dict):
            return None

        args = dict(args)

        for key, planned in template.items():

            # Fill placeholders, including those inside strings.
            if isinstance(planned, str) and "$fill" in planned:

                actual = args.get(key)

                if not isinstance(actual, str):
                    return None

                if not actual.strip() or "$fill" in actual:
                    return None

                # Preserve the fixed text around placeholders.
                if planned != "$fill":
                    pattern = re.escape(planned).replace(
                        re.escape("$fill"),
                        r".+?"
                    )

                    if not re.fullmatch(
                        pattern, actual, flags=re.DOTALL
                    ):
                        return None

            else:
                # Reject changes to arguments fixed by the plan.
                if key in args and args[key] != planned:
                    self.ctx.trace.event(
                        "off_plan",
                        step=step["id"],
                        tool=name
                    )
                    return None

                # Restore fixed arguments omitted by the model.
                args[key] = planned

        
        # Normalize only runtime-filled search queries.
        # Never modify a query fixed explicitly by the plan.
        if (
            name == "search_repo"
            and template.get("query") == "$fill"
        ):
            original_query = args.get("query")

            if not isinstance(original_query, str):
                return None

            normalized_query = self.normalize_search_query(
                original_query
            )

            if not normalized_query:
                return None

            args["query"] = normalized_query


        return args




    @staticmethod
    def source_found(paths: list[str]) -> bool:
        """A search succeeds only when it finds a TS/TSX source file."""
        return any(
            path.startswith("src/") and path.endswith((".ts", ".tsx"))
            and not path.startswith("src/i18n/")
            for path in paths
        )

    def revision_checkpoint(self, plan: dict, step: dict) -> tuple[dict | None, bool]:
        """Return (new_plan, rejected). Only the current search may trigger this."""
        sid = str(step["id"])
        if sid not in self.checkpoints:
            return None, False
        self.checkpoints.remove(sid)
        if self.revisions >= MAX_REVISIONS:
            return None, True
        self.revisions += 1
        candidate = revise_plan(
            self.ctx.client, plan, self.step_results, trace=self.ctx.trace
        )
        valid = check_plan(candidate, self.schema, self.ctx.trace)
        # A failed step needs an actual change to be retried.
        original = {str(s["id"]): s for s in plan["steps"]}
        revised = {str(s["id"]): s for s in candidate.get("steps", [])}
        old = original.get(sid, {})
        new = revised.get(sid, {})
        if new and all(new.get(k) == old.get(k) for k in
                       ("action", "tool", "args_json", "args", "depends_on", "expected_result")):
            valid = False
        self.ctx.trace.event(
            "revision", checkpoint="source_file_found",
            version=self.version + 1, valid=bool(valid)
        )
        if not valid:
            return None, True
        self.version += 1
        self.ctx.trace.plan(self.version, candidate)
        self.checkpoints.update(
            str(s["id"]) for s in candidate["steps"]
            if s["tool"] == "search_repo" and s.get("status") != "done"
            and (str(s["id"]) not in original or any(
                s.get(k) != original[str(s["id"])].get(k)
                for k in ("action", "args_json", "args", "depends_on", "expected_result")
            ))
        )
        return candidate, False

    def execute(self, plan: dict) -> None:
        self.checkpoints = {
            str(next(s["id"] for s in plan["steps"] if s["tool"] == "search_repo"))
        } if any(s["tool"] == "search_repo" for s in plan["steps"]) else set()
        index = 0
        while index < len(plan["steps"]):
            step = plan["steps"][index]
            if step.get("status") == "done":
                index += 1
                continue
            name = step["tool"]
            try:
                template = step_args(step)
            except (TypeError, ValueError):
                self.ctx.trace.end("escalated", detail=f"invalid args in step {step['id']}")
                return
            has_fill = any(
                  isinstance(value, str) and "$fill" in value
                  for value in template.values()
               )
            args = self.fill(step, template) if has_fill else template
            tool_def = self.tool_defs.get(name)
            if args is None or tool_def is None or schema_errors(args, tool_def["input_schema"]):
                step["status"] = "failed"
                self.ctx.trace.end("escalated", detail=f"cannot execute step {step['id']}")
                return
            props = tool_def["input_schema"].get("properties", {})
            if set(args) - set(props):
                self.ctx.trace.end("escalated", detail=f"unknown arguments in step {step['id']}")
                return
            if name == "post_comment":
               
               if "$fill" in args.get("body", ""):
                  self.ctx.trace.end("escalated", detail="unresolved comment placeholders")
                  return
                
               if not self.before_comment(args.get("body", "")):
                  self.ctx.trace.end("escalated", detail="comment held at second-pass bound")
                  return
               args = {**args, "body": self.approved_comment}

            result = self.ctx.call_tool(self.model_step, name, args, plan_step=step["id"])
            paths = result_paths(name, args, result.content, result.is_error)
            search_failed = name == "search_repo" and not self.source_found(paths)
            failed = result.is_error or search_failed
            step["status"] = "failed" if failed else "done"
            self.history.append({
                "id": step["id"], "tool": name,
                "status": step["status"], "result": result.content,
            })
            self.step_results.append({
                "id": step["id"], "tool": name, "status": step["status"],
                "count": len(paths) if name in ("search_repo", "read_file") else None,
                "paths": paths,
                "error_code": ("tool_error" if result.is_error else
                               "no_matches" if search_failed else None),
                "args": args,
            })
            self.after_step(step, result)

            if search_failed and str(step["id"]) in self.checkpoints:
                new_plan, rejected = self.revision_checkpoint(plan, step)
                if rejected:
                    self.ctx.trace.end("escalated", detail="revision rejected or bound reached")
                    return
                if new_plan is not None:
                    plan = new_plan
                    index = 0  # Resume at the first unfinished step.
                    continue
            if failed:
                self.ctx.trace.end("escalated", detail=f"step {step['id']} failed")
                return
            index += 1


def run(ctx) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
    plan = None
    for _ in range(MAX_REPLANS + 1):
        candidate = make_plan(ctx.client, ctx.issue_number, ctx.tools.schemas, trace=ctx.trace)
        if check_plan(candidate, schema, ctx.trace):
            plan = candidate
            break
    if plan is None:
        ctx.trace.end('escalated', detail='no valid plan within re-plan limit')
        return
    ctx.trace.plan(1, plan)
    PlannedExecutor(ctx, schema).execute(plan)

