"""Way 6, notes: the planned way with a progress file and a compaction. This file is yours.

Assignment 2, Part G. Your planned way with the second pass off, plus two things,
so that Part F's configurations stay as they were (checks/FORMATS.md, section 10):

    python run.py --way notes --notes off --issues 4203,4204,4206 --runs 2
    python run.py --way notes --notes on --issues 4203,4204,4206 --runs 2

  1. A progress file, ctx.progress_path (runs/<run_id>.progress.json): JSON outside
     the conversation, written after every step, holding at least
       "steps_done"  the ids of the steps that have run, in order
       "paths"       every path a search returned or a read read, in order
     and never a search term: terms come from the issue's text. Say in the file what
     it records (an "about" line, or labelled keys): a bare list of paths can be read
     as a to-do list. With ctx.notes == "on", every executor call and the questions'
     call after its first write show the model the file's text, exactly as written
     (not the planner, not the reviser). With "off", it is still written, and never
     shown.
  2. A history and a compaction: every model call sees the run's history so far,
     its step results oldest first. Once that history passes COMPACT_AT tokens, the
     oldest entries become a model-written summary and the recent ones stay as
     they were. Write ctx.trace.event("compaction", threshold=COMPACT_AT, tokens=...)
     each time. It runs whatever ctx.notes says. A low threshold is a test setting
     here, not advice.

Then, at the end of the run, however it ended (comment posted, escalated, halted),
one model call asks six closed questions, each "Has this run seen `<path>`: did a
search return it, or did you read it?", and returns six answers, yes or no: about
the three earliest paths the run saw (its tool_call lines returned them; all of
them, if it saw fewer), and paths it did not see for the rest. If the run has not
compacted yet, compact its history once first. Write each answer as
ctx.trace.event("question", path=..., answer=...), the six together.

Part G's 12 runs are the ones on issues 4203, 4204 and 4206; a notes run on a
development issue is development, and reports/LAB_02.json's context leaves it out.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from agent.plan import make_plan
from harness.trace import read_trace
from ways.planned import MAX_REPLANS, PlannedExecutor, SCHEMA_PATH, check_plan

COMPACT_AT = 220  # Approximate history tokens. Low on purpose for this experiment.


def approx_tokens(value) -> int:
    """An inexpensive, provider-independent context-size estimate."""
    return max(1, (len(json.dumps(value, ensure_ascii=False, default=str)) + 3) // 4)


class NotesExecutor(PlannedExecutor):
    def __init__(self, ctx, schema):
        super().__init__(ctx, schema)
        self.steps_done: list = []
        self.paths_seen: list[str] = []
        self.recent_history: list[dict] = []
        self.summary: str | None = None
        self.did_compact = False
        self._write_progress()

    def _write_progress(self) -> None:
        """Persist only step IDs and returned paths, never terms or issue text."""
        state = {
            "about": "Steps executed and repository paths returned so far (not a to-do list)",
            "steps_done": self.steps_done,
            "paths": self.paths_seen,
        }
        path = Path(self.ctx.progress_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def after_step(self, step: dict, result) -> None:
        # The parent invokes this after its call_tool trace and step_results update.
        self.steps_done.append(step["id"])
        if self.step_results:
            for path in self.step_results[-1].get("paths") or []:
                self.paths_seen.append(path)
        self.recent_history.append(dict(self.history[-1]))
        self._write_progress()

    def _context(self) -> dict:
        return {"summary": self.summary, "recent_steps": self.recent_history}

    def compact(self, *, force: bool = False) -> None:
        """Replace oldest context entries with a model-written summary."""
        size = approx_tokens(self._context())
        if not force and size <= COMPACT_AT:
            return
        n = max(1, len(self.recent_history) // 2)
        older = self.recent_history[:n]
        later = self.recent_history[n:]
        source = {"previous_summary": self.summary, "older_steps": older}
        self.model_step += 1
        response = self.ctx.client.create(
            system=("Summarize past triage step results for a continuing executor. "
                    "Preserve important evidence and uncertainty. Do not add new facts. "
                    "Return only a concise summary."),
            messages=[{"role": "user", "content": json.dumps(source, ensure_ascii=False, default=str)}],
            tools=[],
        )
        self.ctx.trace.model_call(self.model_step, response)
        self.summary = response.text.strip()
        self.recent_history = later
        self.did_compact = True
        self.ctx.trace.event("compaction", threshold=COMPACT_AT, tokens=size)

    def _notes_text(self) -> str:
        # The file contents must appear byte-for-byte in each relevant request.
        if self.ctx.notes == "on":
            return "\nProgress file:\n" + Path(self.ctx.progress_path).read_text(encoding="utf-8")
        return ""

    def messages_for(self, step: dict) -> list[dict]:
        self.compact()  # Never compact between a search tool call and its checkpoint.
        request = (
            f"Triage issue #{self.ctx.issue_number}.\n"
            f"Current step: {json.dumps(step, ensure_ascii=False)}\n"
            f"Earlier results, oldest first (older results summarized): "
            f"{json.dumps(self._context(), ensure_ascii=False, default=str)}\n"
            "Fill only this step and call its tool."
            + self._notes_text()
        )
        return [{"role": "user", "content": request}]

    def _questions(self) -> list[str]:
        """Three earliest DISTINCT seen paths, plus paths this run did not see."""
        seen = list(dict.fromkeys(self.paths_seen))
        chosen = seen[:3]
        seen_set = set(seen)
        repo = Path(self.ctx.root) / "fixtures" / "repo"
        if repo.is_dir():
            for f in sorted(repo.rglob("*")):
                if len(chosen) == 6:
                    break
                if f.is_file():
                    candidate = f.relative_to(repo).as_posix()
                    if candidate not in seen_set and candidate not in chosen:
                        chosen.append(candidate)
        index = 1
        while len(chosen) < 6:
            candidate = f"unseen/never_observed_{index}.ts"
            index += 1
            if candidate not in seen_set and candidate not in chosen:
                chosen.append(candidate)
        return chosen

    @staticmethod
    def _answers(text: str) -> list[str]:
        """Read six yes/no answers; accept a bare yes/no from free scripted models."""
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped).strip()
        try:
            value = json.loads(stripped)
            if isinstance(value, dict):
                value = value.get("answers", value.get("results"))
            if isinstance(value, list) and len(value) == 6:
                parsed = [str(v).strip().lower() for v in value]
                if all(v in ("yes", "no") for v in parsed):
                    return parsed
        except (ValueError, TypeError):
            pass
        matches = re.findall(r"\b(?:yes|no)\b", stripped.lower())
        if len(matches) == 6:
            return matches
        if stripped.lower() in ("yes", "no"):
            # ScriptedTriage gives one shared answer to a multi-question request.
            return [stripped.lower()] * 6
        # A malformed answer cannot be trusted as evidence of having seen a file.
        return ["no"] * 6

    def ask_questions(self) -> None:
        """Always ask six closed questions in ONE model call after compaction."""
        if not self.did_compact:
            self.compact(force=True)
        targets = self._questions()
        questions = [
            f"{i}. Has this run seen `{path}`: did a search return it, or did you read it?"
            for i, path in enumerate(targets, 1)
        ]
        # A posting step has no file-path evidence.  Do not inject its drafted
        # text into this evidence-recall question (it is not a search result).
        recent = [
            item for item in self.recent_history
            if item.get("tool") in ("read_issue", "search_repo", "read_file")
        ]
        request = (
            "Answer these six questions using your available, possibly compacted "
            "run context. Do not guess that an unmentioned path was seen. "
            "Return exactly one JSON array of six yes/no strings in order.\n"
            "Run context, oldest to newest:\n"
            + json.dumps({"summary": self.summary, "recent_steps": recent},
                         ensure_ascii=False, default=str)
            + "\n" + "\n".join(questions)
            + self._notes_text()
        )
        self.model_step += 1
        response = self.ctx.client.create(
            system="Answer whether this run saw each named repository path. Return six yes/no answers.",
            messages=[{"role": "user", "content": request}],
            tools=[],
        )
        self.ctx.trace.model_call(self.model_step, response)
        for path, answer in zip(targets, self._answers(response.text)):
            self.ctx.trace.event("question", path=path, answer=answer)


def run(ctx) -> None:
    """Same plan-and-validate entry point as planned, with notes and a final quiz."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    executor = NotesExecutor(ctx, schema)
    try:
        plan = None
        for _ in range(MAX_REPLANS + 1):
            candidate = make_plan(ctx.client, ctx.issue_number, ctx.tools.schemas, trace=ctx.trace)
            if check_plan(candidate, schema, ctx.trace):
                plan = candidate
                break
        if plan is None:
            ctx.trace.end("escalated", detail="no valid plan within re-plan limit")
            return
        ctx.trace.plan(1, plan)
        executor.execute(plan)
    finally:
        executor.ask_questions()
