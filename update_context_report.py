"""Fill Part G context rows in reports/LAB_02.json from finished real notes traces.

Run after completing all twelve notes measurements. Scripted development runs are
excluded; each row is grounded in the original trace, not a model prediction.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "reports" / "LAB_02.json"
RUNS = ROOT / "runs"
ISSUES = {4203, 4204, 4206}


def main() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    rows = []
    for path in sorted(RUNS.glob("*.jsonl")):
        if path.name.startswith("_"):
            continue
        try:
            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except (ValueError, UnicodeDecodeError):
            continue
        if not records:
            continue
        start = records[0]
        if (start.get("type") != "run_start" or start.get("lab") != "02"
                or start.get("way") != "notes" or start.get("issue") not in ISSUES):
            continue
        backend = start.get("backend", "")
        if backend == "never-stops" or backend.startswith("harness.scripted:"):
            continue
        if records[-1].get("type") != "run_end":
            continue
        if any(x.get("type") == "end" and x.get("terminal_reason") in ("quota", "rate_limited")
               for x in records):
            continue

        seen = set()
        compacted = False
        questions_started = False
        questions = []
        for item in records:
            typ = item.get("type")
            if typ == "tool_call":
                seen.update(item.get("paths") or [])
            elif typ == "compaction" and not questions_started:
                compacted = True
            elif typ == "question":
                questions_started = True
                question = {
                    "path": item.get("path"),
                    "truth": "yes" if item.get("path") in seen else "no",
                    "answer": item.get("answer"),
                }
                questions.append(question)
        rows.append({
            "run_id": start["run_id"],
            "issue": start["issue"],
            "notes_shown": start.get("notes", "off") == "on",
            "compacted": compacted,
            "questions": questions,
            "right": sum(q["truth"] == q["answer"] for q in questions),
        })
    report["context"] = rows
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Recorded {len(rows)} finished, real Part G notes runs in {REPORT}")
    if len(rows) < 12:
        print("Part G is not yet complete: expect 12 finished runs (2 x 3 issues x 2 notes modes).")


if __name__ == "__main__":
    main()
