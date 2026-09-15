"""CLI: turn a generated dashboard into a label-collection file.

    python -m calibration.export_labels contract_visualization.html -o labels.jsonl
    python -m calibration.export_labels analysis.json -o labels.jsonl

The input is either a generated dashboard HTML (the embedded ``contract-data``
JSON island is read) or a JSON file containing ``riskAssessment``. One JSONL row
is written per risk with the tool's ``predictedConfidence`` and ``reviewRequired``
already filled in, ``correct`` left as ``null`` for a reviewer to set to 1 or 0,
and enough context (clause, level, rationale, quote) to judge it without opening
the dashboard. Use ``--append`` to grow an existing file across many contracts.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Iterable
from typing import Any, cast

_ISLAND_RE = re.compile(
    r'<script type="application/json" id="contract-data">\s*(.*?)\s*</script>', re.DOTALL)


def extract_contract_data(text: str) -> dict[str, object]:
    """Return the dashboard's data object from HTML (data island) or raw JSON."""
    m = _ISLAND_RE.search(text)
    payload = m.group(1) if m else text
    data = json.loads(payload)
    if not isinstance(data, dict) or 'riskAssessment' not in data:
        raise ValueError('input has no riskAssessment (expected dashboard HTML or analysis JSON)')
    return data


def label_rows(data: dict[str, object], source: str = '') -> list[dict[str, object]]:
    """Build one unlabeled row per risk, ready for human review."""
    rows: list[dict[str, object]] = []
    risks = cast(list[dict[str, Any]], data.get('riskAssessment') or [])
    for i, r in enumerate(risks, 1):
        conf: dict[str, Any] = r.get('confidence') or {}
        cites: list[dict[str, Any]] = r.get('citations') or []
        quote = next((c.get('quote', '') for c in cites if c.get('quote')), '')
        rows.append({
            'id': f"{source or 'risk'}#{i}",
            'predictedConfidence': float(conf.get('score', 0.0)),
            'correct': None,
            'reviewRequired': bool(conf.get('reviewRequired', False)),
            'riskType': r.get('riskType', ''),
            'context': {
                'clauseReference': r.get('clauseReference', ''),
                'riskLevel': r.get('riskLevel', ''),
                'likelihood': r.get('likelihood', ''),
                'rationale': r.get('rationale', ''),
                'potentialConsequence': r.get('potentialConsequence', ''),
                'sourceQuote': quote,
                'confidenceReasons': list(conf.get('reasons') or []),
            },
        })
    return rows


def write_rows(rows: Iterable[dict[str, object]], path: str, append: bool = False) -> int:
    n = 0
    with open(path, 'a' if append else 'w', encoding='utf-8') as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
            n += 1
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Export risks for confidence labeling.')
    ap.add_argument('input', help='generated dashboard .html or analysis .json')
    ap.add_argument('-o', '--output', required=True, help='JSONL file to write')
    ap.add_argument('--append', action='store_true', help='append instead of overwrite')
    ap.add_argument('--source', default='', help='id prefix (e.g. contract name)')
    args = ap.parse_args(argv)

    with open(args.input, encoding='utf-8') as f:
        data = extract_contract_data(f.read())
    rows = label_rows(data, args.source or args.input.rsplit('/', 1)[-1])
    n = write_rows(rows, args.output, append=args.append)
    print(f'Wrote {n} unlabeled rows to {args.output} -- set "correct" to 1 or 0 for each.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
