"""Fill only Part E fields in reports/LAB_02.json using the two recorded runs.
Run at the repository root: python -X utf8 update_report.py
"""
from __future__ import annotations

import json
from pathlib import Path

from agent.detectors import ladder
from agent.diff import diff
from harness.trace import read_trace


def load(name: str):
    folder = Path('fixtures') / 'traces' / name
    plan = json.loads((folder / 'plan.json').read_text(encoding='utf-8'))
    trace = read_trace(folder / 'trace.jsonl')
    return plan, trace


def main():
    report_path = Path('reports/LAB_02.json')
    report = json.loads(report_path.read_text(encoding='utf-8'))
    looping_plan, looping_trace = load('looping')
    s49_plan, s49_trace = load('s49')

    report.setdefault('ladder', {})['looping'] = ladder(looping_plan, looping_trace)
    report['ladder']['s49'] = ladder(s49_plan, s49_trace)
    result = diff(s49_plan, s49_trace)
    report['diff_s49'] = {
        'steps': {str(k): value for k, value in result['steps'].items()},
        'extra': result['extra'],
    }
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n',
                           encoding='utf-8')
    print('Part E fields updated in', report_path)
    print('Looping ladder:', report['ladder']['looping'])
    print('s49 ladder:', report['ladder']['s49'])
    print('s49 diff:', report['diff_s49'])


if __name__ == '__main__':
    main()
