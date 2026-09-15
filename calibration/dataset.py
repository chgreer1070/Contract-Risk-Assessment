"""Loader + schema for the labeled calibration dataset (JSONL).

Each line is one labeled risk assessment:

    {"id": "...", "predictedConfidence": 0.8, "correct": 1,
     "reviewRequired": true, "riskType": "Liability"}

- ``predictedConfidence``: the tool's confidence for this risk, in [0, 1]
  (from ``generate_dashboard.assess_risk_confidence``).
- ``correct``: ground-truth label, 1 if the assessment was right/acceptable else 0.
  This is the only human/model-dependent field (see docs/confidence_calibration.md).
- ``reviewRequired``: whether the tool flagged the risk for human review.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from typing import cast


@dataclass(frozen=True)
class LabeledRecord:
    id: str
    predictedConfidence: float
    correct: int
    reviewRequired: bool
    riskType: str = ''

    def as_pair(self) -> tuple[float, int]:
        return (self.predictedConfidence, self.correct)


def _iter_json_lines(path: str) -> Iterator[dict[str, object]]:
    with open(path, encoding='utf-8') as f:
        for line_no, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            try:
                obj = json.loads(line)
            except ValueError as exc:
                raise ValueError(f'{path}:{line_no}: invalid JSON ({exc})') from exc
            if not isinstance(obj, dict):
                raise ValueError(f'{path}:{line_no}: expected a JSON object')
            yield obj


def is_unlabeled(obj: dict[str, object]) -> bool:
    """True for rows exported for labeling whose ``correct`` is still null."""
    return obj.get('correct') is None


def load_labeled(path: str, skip_unlabeled: bool = False) -> list[LabeledRecord]:
    """Parse and validate a JSONL labeled dataset; raise on malformed rows.

    With ``skip_unlabeled=True``, rows whose ``correct`` is null (exported by
    ``calibration.export_labels`` but not yet reviewed) are ignored instead of
    raising, so a partially-labeled file can still be evaluated.
    """
    records: list[LabeledRecord] = []
    for obj in _iter_json_lines(path):
        if skip_unlabeled and is_unlabeled(obj):
            continue
        try:
            conf = float(cast(float, obj['predictedConfidence']))
            correct = int(cast(int, obj['correct']))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f'{path}: row missing/invalid required field ({exc})') from exc
        if not 0.0 <= conf <= 1.0:
            raise ValueError(f'{path}: predictedConfidence out of [0,1]: {conf}')
        if correct not in (0, 1):
            raise ValueError(f'{path}: correct must be 0 or 1, got {correct}')
        records.append(LabeledRecord(
            id=str(obj.get('id', f'row-{len(records) + 1}')),
            predictedConfidence=conf,
            correct=correct,
            reviewRequired=bool(obj.get('reviewRequired', False)),
            riskType=str(obj.get('riskType', '')),
        ))
    return records


def count_unlabeled(path: str) -> int:
    return sum(1 for obj in _iter_json_lines(path) if is_unlabeled(obj))


def default_dataset_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'seed_labeled.jsonl')
