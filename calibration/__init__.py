"""Deterministic confidence-calibration harness for the contract risk tool.

This package evaluates whether the risk tool's confidence scores are
*statistically trustworthy* -- i.e. whether risks predicted at confidence p are
actually correct about p of the time -- and provides a simple, dependency-free
calibrator to correct systematic over/under-confidence.

Everything here is pure Python and deterministic (no model, no network), so it
runs in the CPU test loop. The only model/human-dependent input is the labeled
dataset of (predicted confidence, was-it-correct) pairs; see ``calibration/data``
and ``docs/confidence_calibration.md``.
"""

from calibration.calibrator import HistogramBinningCalibrator
from calibration.dataset import LabeledRecord, load_labeled
from calibration.metrics import (
    brier_score,
    expected_calibration_error,
    max_calibration_error,
    reliability_bins,
    selective_metrics,
)

__all__ = [
    'HistogramBinningCalibrator',
    'LabeledRecord',
    'load_labeled',
    'brier_score',
    'expected_calibration_error',
    'max_calibration_error',
    'reliability_bins',
    'selective_metrics',
]
