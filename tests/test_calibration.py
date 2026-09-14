"""Tests for the deterministic confidence-calibration harness (calibration/)."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from calibration.calibrator import HistogramBinningCalibrator  # noqa: E402
from calibration.dataset import default_dataset_path, load_labeled  # noqa: E402
from calibration.metrics import (  # noqa: E402
    brier_score,
    expected_calibration_error,
    max_calibration_error,
    reliability_bins,
    selective_metrics,
)

# --------------------------------------------------------------------------- #
# Metrics: exact values on tiny, hand-verifiable inputs
# --------------------------------------------------------------------------- #

def test_brier_score_perfect_and_worst():
    assert brier_score([(1.0, 1), (0.0, 0)]) == 0.0
    assert brier_score([(1.0, 0), (0.0, 1)]) == 1.0
    assert brier_score([]) == 0.0


def test_brier_score_mid():
    # (0.5-1)^2 + (0.5-0)^2 = 0.25 + 0.25, mean 0.25
    assert brier_score([(0.5, 1), (0.5, 0)]) == 0.25


def test_reliability_bins_group_and_accuracy():
    pairs = [(0.95, 1), (0.95, 0), (0.15, 0)]
    bins = reliability_bins(pairs, n_bins=10)
    top = [b for b in bins if b['lo'] == 0.9][0]
    assert top['count'] == 2
    assert top['accuracy'] == 0.5
    assert top['meanConfidence'] == 0.95
    assert top['gap'] == 0.45


def test_ece_and_mce_perfectly_calibrated_is_zero():
    # Two bins, each with accuracy == confidence.
    pairs = [(0.9, 1)] * 9 + [(0.9, 0)] * 1 + [(0.1, 1)] * 1 + [(0.1, 0)] * 9
    assert expected_calibration_error(pairs, 10) == 0.0
    assert max_calibration_error(pairs, 10) == 0.0


def test_ece_detects_overconfidence():
    # All predicted at 1.0 but only half correct -> gap 0.5.
    pairs = [(1.0, 1), (1.0, 0)]
    assert expected_calibration_error(pairs, 10) == 0.5
    assert max_calibration_error(pairs, 10) == 0.5


def test_reliability_bins_rejects_bad_bin_count():
    with pytest.raises(ValueError):
        reliability_bins([(0.5, 1)], n_bins=0)


# --------------------------------------------------------------------------- #
# Calibrator
# --------------------------------------------------------------------------- #

def test_histogram_binning_learns_empirical_accuracy():
    # Bin [0.9,1.0) has 4 records, 1 correct -> calibrated to 0.25.
    pairs = [(0.95, 1), (0.95, 0), (0.95, 0), (0.95, 0)]
    cal = HistogramBinningCalibrator(n_bins=10).fit(pairs)
    assert cal.predict(0.97) == 0.25


def test_calibration_reduces_ece_on_overconfident_data():
    pairs = [(1.0, 1)] * 3 + [(1.0, 0)] * 1  # accuracy 0.75 at confidence 1.0
    before = expected_calibration_error(pairs, 10)
    cal = HistogramBinningCalibrator(n_bins=10).fit(pairs)
    after = expected_calibration_error([(cal.predict(c), y) for c, y in pairs], 10)
    assert before > 0
    assert after < before


def test_calibrator_unseen_bin_falls_back_to_midpoint():
    cal = HistogramBinningCalibrator(n_bins=10).fit([(0.95, 1)])
    # Nothing learned for the [0.4,0.5) bin -> midpoint 0.45.
    assert cal.predict(0.42) == 0.45


def test_calibrator_roundtrip_serialization():
    cal = HistogramBinningCalibrator(n_bins=5).fit([(0.9, 1), (0.9, 0), (0.1, 0)])
    restored = HistogramBinningCalibrator.from_dict(cal.to_dict())
    assert restored.n_bins == 5
    assert restored.predict(0.95) == cal.predict(0.95)


# --------------------------------------------------------------------------- #
# Selective prediction (abstention)
# --------------------------------------------------------------------------- #

def test_selective_metrics_basic():
    records = [
        {'reviewRequired': False, 'correct': 1},
        {'reviewRequired': False, 'correct': 1},
        {'reviewRequired': True, 'correct': 0},
        {'reviewRequired': True, 'correct': 1},
    ]
    m = selective_metrics(records)
    assert m['total'] == 4
    assert m['coverage'] == 0.5           # 2 of 4 auto-accepted
    assert m['autoAcceptAccuracy'] == 1.0  # both auto-accepted are correct
    assert m['errorCaptureRate'] == 1.0    # the only error was flagged


def test_selective_metrics_empty():
    m = selective_metrics([])
    assert m['total'] == 0
    assert m['coverage'] == 0.0


# --------------------------------------------------------------------------- #
# Dataset loader
# --------------------------------------------------------------------------- #

def test_load_seed_dataset():
    records = load_labeled(default_dataset_path())
    assert len(records) == 28
    assert all(0.0 <= r.predictedConfidence <= 1.0 for r in records)
    assert all(r.correct in (0, 1) for r in records)


def test_loader_rejects_out_of_range_confidence(tmp_path):
    p = tmp_path / 'bad.jsonl'
    p.write_text(json.dumps({'predictedConfidence': 1.5, 'correct': 1}) + '\n')
    with pytest.raises(ValueError):
        load_labeled(str(p))


def test_loader_rejects_bad_correct_label(tmp_path):
    p = tmp_path / 'bad.jsonl'
    p.write_text(json.dumps({'predictedConfidence': 0.5, 'correct': 2}) + '\n')
    with pytest.raises(ValueError):
        load_labeled(str(p))


def test_loader_skips_blank_and_comment_lines(tmp_path):
    p = tmp_path / 'ok.jsonl'
    p.write_text('# header\n\n' + json.dumps({'predictedConfidence': 0.5, 'correct': 1}) + '\n')
    assert len(load_labeled(str(p))) == 1


# --------------------------------------------------------------------------- #
# Regression gate on the seed dataset (mirrors the intended CI gate)
# --------------------------------------------------------------------------- #

def test_seed_dataset_quality_gate():
    records = load_labeled(default_dataset_path())
    pairs = [r.as_pair() for r in records]
    ece_before = expected_calibration_error(pairs, 10)
    cal = HistogramBinningCalibrator(n_bins=10).fit(pairs)
    ece_after = expected_calibration_error([(cal.predict(c), y) for c, y in pairs], 10)
    sel = selective_metrics([r.__dict__ for r in records])

    assert ece_before < 0.15                    # raw confidence is reasonably calibrated
    assert ece_after <= ece_before              # calibration does not hurt
    assert sel['autoAcceptAccuracy'] >= 0.90    # auto-accepted risks are trustworthy
    assert sel['errorCaptureRate'] >= 0.85      # most errors are flagged for review
