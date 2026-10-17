import json
import re
import shutil
from pathlib import Path
from datetime import datetime

root = Path("reports")
jp = root / "LAB_02.json"
mp = root / "LAB_02.md"

if not jp.exists() or not mp.exists():
    raise SystemExit("Please open the assignment root folder first.")

data = json.loads(jp.read_text(encoding="utf-8-sig"))
doc = mp.read_text(encoding="utf-8-sig")
configs = ["agent", "planned_off", "planned_self", "planned_external"]
stats = data["results"]

for name in configs:
    if stats[name]["runs"] != 18:
        raise SystemExit(f"Incomplete runs: {name}")

def replace_table(source, heading, rows):
    pattern = (
        r"(?m)^\| " + re.escape(heading)
        + r" \|[^\n]*\n\|[-| ]+\|\n(?:\|[^\n]*\n)*"
    )
    result, count = re.subn(
        pattern, lambda _: "\n".join(rows) + "\n",
        source, count=1
    )
    if count != 1:
        raise SystemExit(f"Table not found: {heading}")
    return result

def fmt(v):
    if v is None:
        return ""
    return f"{v:.2f}" if isinstance(v, float) else str(v)

def pct(v):
    return "" if v is None else f"{v*100:.1f}%"

# Part A
strict = data["schema"]["strict_compiles"]
if strict is True:
    conclusion = (
        "The provider accepted a strict plan-tool request "
        "using this schema, as confirmed by the checker."
    )
elif strict is False:
    conclusion = "The provider rejected the strict schema request."
else:
    conclusion = "The provider did not support strict mode."

doc = doc.replace(
    "[Replace after testing: The model provider accepted "
    "a strict request using this schema / The model "
    "provider rejected the strict schema request.]",
    conclusion
)

# Part B
fixtures = [
    "| Plan | Valid | Failures (step, check) |",
    "|---|---|---|"
]
for item in data["fixture_validation"]:
    failures = ", ".join(
        f"({e['step']}, {e['check']})"
        for e in item["failures"]
    ) or "none"
    fixtures.append(
        f"| `{item['plan']}` | {item['valid']} | {failures} |"
    )

doc = replace_table(doc, "Plan", fixtures)

# Part F table
fields = [
    ("Passes, of 18",
     lambda x: f"{x['passes']}/18"),
    ("Per issue, 4201 to 4206, of 3 each",
     lambda x: ", ".join(
         str(x["per_issue"][str(i)])
         for i in range(4201, 4207))),
    ("Spread: the lowest and highest sweep, of 6",
     lambda x: f"{fmt(x['sweep_min'])}-{fmt(x['sweep_max'])}"),
    ("Tokens per run",
     lambda x: f"{x['mean_tokens']:,.0f}"),
    ("Latency per run: median, and slowest",
     lambda x: f"{fmt(x['p50_latency_s'])}s / {fmt(x['max_latency_s'])}s"),
    ("Plan overhead: the mean share of prompt tokens, and how it changes with run length",
     lambda x: pct(x["plan_overhead_share"])),
    ("Second-pass overhead: the mean share of prompt tokens",
     lambda x: pct(x["critique_overhead_share"])),
    ("Runs with at least one exact repeat",
     lambda x: fmt(x["runs_with_repeat"])),
    ("`off_plan` lines",
     lambda x: fmt(x["off_plan"])),
    ("Revisions per run: mean, and maximum",
     lambda x: "" if x["revisions_mean"] is None else
     f"{fmt(x['revisions_mean'])} / {fmt(x['revisions_max'])}"),
    ("Second-pass rounds per run: mean, and maximum",
     lambda x: "" if x["rounds_mean"] is None else
     f"{fmt(x['rounds_mean'])} / {fmt(x['rounds_max'])}"),
    ("Drafts changed; of those fixed, and broken",
     lambda x: "" if x["drafts_changed"] is None else
     f"{x['drafts_changed']}; {x['drafts_fixed']} fixed, "
     f"{x['drafts_broken']} broken"),
    ("Drafts held at the bound",
     lambda x: fmt(x["drafts_held"]))
]

table = [
    "| Measure | Agent | Planned, off | Planned, self | Planned, external |",
    "|---|---|---|---|---|"
]
for label, fn in fields:
    table.append(
        "| " + " | ".join(
            [label] + [fn(stats[c]) for c in configs]
        ) + " |"
    )

doc = replace_table(doc, "Measure", table)

agent, off, self_mode, external = [
    stats[c] for c in configs
]

issue_counts = "; ".join(
    f"{i}: " + "/".join(
        str(m["per_issue"][str(i)])
        for m in (agent, off, self_mode, external)
    )
    for i in range(4201, 4207)
)

saving = 100 * (
    1 - off["mean_tokens"] / agent["mean_tokens"]
)

# Part F written answers
part_f = f"""
**1. What did the plan buy RepoMind, concretely?**

The planned system generated a structured plan before execution, validated its schema and dependencies, recorded plan versions, and linked tool calls to plan steps. It also enforced an execution boundary and limited revision attempts. Across 54 planned runs, revision was bounded to one attempt per run.

Planned/off averaged {off['mean_tokens']:,.0f} tokens per run, compared with {agent['mean_tokens']:,.0f} for the baseline, a reduction of {saving:.1f}%. However, its pass count decreased from {agent['passes']}/18 to {off['passes']}/18. This means the plan reduced resource consumption but did not improve task success in this experiment. Some savings came from runs stopping before completing the planned steps, so lower token use alone is not evidence of greater efficiency.

The mean planning prompt-token share was {pct(off['plan_overhead_share'])} for off, {pct(self_mode['plan_overhead_share'])} for self, and {pct(external['plan_overhead_share'])} for external. The relationship between overhead share and run length needs per-run trace analysis rather than just these averages.

**2. Where did it not pay?**

Planning was less useful in four situations I observed: short issues that could be handled with a few tool calls, tasks whose useful search terms were not known before reading the issue, searches that returned irrelevant documentation or no source files, and runs that reached the revision bound before the remaining steps could execute.

For these cases, planning and revision calls still added overhead even when execution made little progress. Per-issue passes (agent/off/self/external, each out of three) were {issue_counts}.

Issue 4202 performed better with planned/off than with the baseline, but issues 4201 and 4205 performed worse. These examples illustrate the trade-off, although the four categories should be cross-checked against the exact wording on Lecture 2 slide 52 before submission.

**3. What did each second pass buy, and what did it cost?**

With critique off, no second-pass check was performed. Self-critique could reconsider the draft but did not have independent evidence. External verification checked the comment format, whether the named file existed, whether the file had appeared in this run's tool results, and whether its label agreed with the file choice.

Under slide 41's three gates, off passes none; self partially satisfies error detection, lacks independent knowledge, and has a bound and log; external can detect its four defined errors, uses independent evidence, and is bounded and logged. Both critique modes used MAX_ROUNDS = 2.

The pass counts for off, self, and external were {off['passes']}/18, {self_mode['passes']}/18, and {external['passes']}/18. Mean tokens per run were {off['mean_tokens']:,.0f}, {self_mode['mean_tokens']:,.0f}, and {external['mean_tokens']:,.0f}. Median latency was {off['p50_latency_s']:.2f}s, {self_mode['p50_latency_s']:.2f}s, and {external['p50_latency_s']:.2f}s.

Self and external critique consumed {pct(self_mode['critique_overhead_share'])} and {pct(external['critique_overhead_share'])} of prompt tokens on average. Their changed/fixed/broken/held draft counts were {self_mode['drafts_changed']}/{self_mode['drafts_fixed']}/{self_mode['drafts_broken']}/{self_mode['drafts_held']} and {external['drafts_changed']}/{external['drafts_fixed']}/{external['drafts_broken']}/{external['drafts_held']}, respectively. No recorded rewrite converted a passing draft into a failing one, but no draft was fixed either.

For issue 4206, off/self/external passed {off['per_issue']['4206']}/{self_mode['per_issue']['4206']}/{external['per_issue']['4206']} out of three. Neither second-pass mode rescued this issue. A verifier checking a final comment cannot fix an earlier search failure that prevents the comment from being drafted.

**4. Which failure did no detector catch?**

The Part E s49 example showed that a run can report success before its actual task is complete. The last test output still reported `1 failing: test_export_csv`, and the final browser confirmation never ran. The detectors identified repeated work and a skipped plan step, but they could not independently establish that the bug had been fixed.

The same distinction matters in Part F: a completed run and a well-formed comment do not guarantee the correct label or responsible file. Grounded correctness must be evaluated separately from plan compliance.

_Statistics generated from reports/LAB_02.json. Each configuration includes 18 runs._
"""

start = doc.index("## Part F")
end = doc.index("## Part G", start)
section = doc[start:end]
section = re.sub(
    r"(?s)Leave a cell empty where a measure does not apply\..*$",
    lambda _: part_f,
    section, count=1
)
doc = doc[:start] + section + "\n" + doc[end:]

# Part G table
context = data["context"]
if len(context) != 12:
    raise SystemExit("Expected 12 Part G runs.")

gtable = [
    "| Run | Issue | Progress file shown | Compacted before the questions | Answers right, of 6 |",
    "|---|---|---|---|---|"
]

for q in sorted(
    context,
    key=lambda q: (q["issue"], q["notes_shown"], q["run_id"])
):
    gtable.append(
        f"| `{q['run_id']}` | {q['issue']} | "
        f"{q['notes_shown']} | {q['compacted']} | {q['right']}/6 |"
    )

doc = replace_table(doc, "Run", gtable)

notes_off = [q for q in context if not q["notes_shown"]]
notes_on = [q for q in context if q["notes_shown"]]

correct_off = sum(q["right"] for q in notes_off)
correct_on = sum(q["right"] for q in notes_on)
positive = sum(
    v.get("truth") == "yes"
    for q in context for v in q["questions"]
)

part_g = f"""
I set COMPACT_AT to 220 approximate tokens as a deliberately low experimental threshold. The progress file records completed step IDs and file paths returned by searches and reads, without storing issue text or search terms.

**1. What did the progress file keep that compaction lost, and what did it cost?**

The progress file preserved completed steps and file paths from earlier tool results, even after older conversation history had been compacted.

All 12 runs were compacted before the questions. Notes off answered {correct_off}/36 questions correctly, while notes on answered {correct_on}/36 correctly. Therefore, the experiment did not demonstrate an accuracy improvement from notes.

Only {positive} of the 72 ground-truth answers were "yes", which also limits how much the results demonstrate about remembering positive evidence. Including the progress file adds model-input text, but the exact incremental token cost cannot be isolated from the summary JSON alone. A matched-call comparison is needed to separate note overhead from other run differences.

**2. Which context operations did RepoMind need?**

Structured note-taking was useful for preserving completed steps and previously discovered paths, although including the notes added prompt tokens. Sub-agent isolation was unnecessary for this short, sequential triage workflow and would introduce additional coordination overhead.

Just-in-time retrieval through search_repo and read_file was important because RepoMind needed repository evidence without loading the entire repository into context.

Compaction helped test how the system behaved when earlier context was removed, but it also risked losing useful details. The 220-token threshold was deliberately low for experimentation rather than a production recommendation. The longest Part F run still needs to be identified from its trace for a precise run-specific comparison.
"""

start = doc.index("## Part G")
end = doc.index("## Assistance", start)
section = doc[start:end]
section = re.sub(
    r"(?s)I set COMPACT_AT to 220 approximate tokens.*$",
    lambda _: part_g,
    section, count=1
)
doc = doc[:start] + section + "\n" + doc[end:]

# Assistance statement
assistance = """
I used ChatGPT to help debug Python code and prompts, write local reporting scripts, and draft explanations in this report. I checked the implementation with the course checker and compared the reported statistics with the experiment JSON and traces. I reviewed the final report and take responsibility for the submission.
"""
doc = re.sub(
    r"(?s)(## Assistance\s*).*$",
    lambda m: m.group(1) + assistance,
    doc, count=1
)

# Save with backup
if "[Fill in" in doc or "[Replace after testing" in doc:
    raise SystemExit("Some placeholders remain; original report unchanged.")

backup = mp.with_name(
    "LAB_02_backup_" +
    datetime.now().strftime("%Y%m%d_%H%M%S") +
    ".md"
)

shutil.copy2(mp, backup)
mp.write_text(doc, encoding="utf-8")

print("REPORT UPDATED:", mp.resolve())
print("BACKUP:", backup)
print("PART F PASSES:", *(stats[c]["passes"] for c in configs))
print("PART G RUNS:", len(context))
print("No API calls. JSON unchanged.")
