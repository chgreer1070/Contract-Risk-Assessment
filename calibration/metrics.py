"""Calibration and selective-prediction metrics (pure, deterministic).

A "pair" is ``(confidence, correct)`` where ``confidence`` is a probability in
[0, 1] and ``correct`` is 1 if the prediction was right, else 0. These are the
standard reliability metrics used to judge whether confidence is trustworthy.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

Pair = tuple[float, int]


def _clean_pairs(pairs: Sequence[Pair]) -> list[Pair]:
    out: list[Pair] = []
    for conf, correct in pairs:
        c = min(1.0, max(0.0, float(conf)))
        out.append((c, 1 if int(correct) else 0))
    return out


def brier_score(pairs: Sequence[Pair]) -> float:
    """Mean squared error between confidence and outcome (0 best, 1 worst)."""
    p = _clean_pairs(pairs)
    if not p:
        return 0.0
    return round(sum((c - y) ** 2 for c, y in p) / len(p), 4)


def reliability_bins(pairs: Sequence[Pair], n_bins: int = 10) -> list[dict[str, float]]:
    """Group predictions into equal-width confidence bins.

    Returns one dict per non-empty bin with the bin range, count, mean predicted
    confidence, and empirical accuracy -- the data behind a reliability diagram.
    """
    if n_bins < 1:
        raise ValueError('n_bins must be >= 1')
    p = _clean_pairs(pairs)
    buckets: list[list[Pair]] = [[] for _ in range(n_bins)]
    for conf, y in p:
        # Map [0,1] to a bin index; 1.0 lands in the last bin.
        idx = min(n_bins - 1, int(conf * n_bins))
        buckets[idx].append((conf, y))
    bins: list[dict[str, float]] = []
    for i, bucket in enumerate(buckets):
        if not bucket:
            continue
        count = len(bucket)
        mean_conf = sum(c for c, _ in bucket) / count
        accuracy = sum(y for _, y in bucket) / count
        bins.append({
            'lo': round(i / n_bins, 4),
            'hi': round((i + 1) / n_bins, 4),
            'count': count,
            'meanConfidence': round(mean_conf, 4),
            'accuracy': round(accuracy, 4),
            'gap': round(abs(accuracy - mean_conf), 4),
        })
    return bins


def expected_calibration_error(pairs: Sequence[Pair], n_bins: int = 10) -> float:
    """ECE: count-weighted average gap between confidence and accuracy per bin."""
    p = _clean_pairs(pairs)
    if not p:
        return 0.0
    total = len(p)
    ece = sum(b['gap'] * b['count'] for b in reliability_bins(p, n_bins)) / total
    return round(ece, 4)


def max_calibration_error(pairs: Sequence[Pair], n_bins: int = 10) -> float:
    """MCE: the worst per-bin gap between confidence and accuracy."""
    bins = reliability_bins(pairs, n_bins)
    if not bins:
        return 0.0
    return round(max(b['gap'] for b in bins), 4)


def _is_correct(record: Mapping[str, object]) -> bool:
    return record.get('correct', 0) in (1, True)


def selective_metrics(records: Sequence[Mapping[str, object]]) -> dict[str, float]:
    """Evaluate the abstention policy from records with reviewRequired + correct.

    Coverage is the fraction auto-accepted (not flagged for review). The key
    quality signal is that auto-accepted risks should be very accurate, and that
    most actual errors get captured by the review flag.
    """
    n = len(records)
    if n == 0:
        return {
            'total': 0, 'coverage': 0.0, 'autoAcceptCount': 0,
            'autoAcceptAccuracy': 0.0, 'reviewCount': 0,
            'errorCaptureRate': 0.0,
        }
    auto = [r for r in records if not bool(r.get('reviewRequired'))]
    review = [r for r in records if bool(r.get('reviewRequired'))]
    errors = [r for r in records if not _is_correct(r)]
    auto_correct = sum(1 for r in auto if _is_correct(r))
    errors_in_review = sum(1 for r in review if not _is_correct(r))
    return {
        'total': n,
        'coverage': round(len(auto) / n, 4),
        'autoAcceptCount': len(auto),
        'autoAcceptAccuracy': round(auto_correct / len(auto), 4) if auto else 0.0,
        'reviewCount': len(review),
        'errorCaptureRate': round(errors_in_review / len(errors), 4) if errors else 1.0,
    }
