"""A dependency-free histogram-binning calibrator.

Histogram binning is the simplest reliable post-hoc calibration method: split
the confidence range into equal-width bins, and on a held-out calibration set
learn the empirical accuracy in each bin. At prediction time, a raw confidence
is mapped to the learned accuracy of its bin. This corrects systematic over- or
under-confidence without any model dependency.

More expressive alternatives (isotonic regression, Platt scaling, conformal
prediction) are described in ``docs/confidence_calibration.md``; this class is
the v1 baseline the harness ships with.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import cast

Pair = tuple[float, int]


class HistogramBinningCalibrator:
    """Map raw confidence -> calibrated probability via per-bin empirical accuracy."""

    def __init__(self, n_bins: int = 10) -> None:
        if n_bins < 1:
            raise ValueError('n_bins must be >= 1')
        self.n_bins = n_bins
        # Learned accuracy per bin; None until fit(). Empty bins fall back to the
        # bin midpoint (an identity-like default) so prediction never fails.
        self.bin_accuracy: list[float | None] = [None] * n_bins
        self.fitted = False

    def _bin_index(self, confidence: float) -> int:
        c = min(1.0, max(0.0, float(confidence)))
        return min(self.n_bins - 1, int(c * self.n_bins))

    def fit(self, pairs: Sequence[Pair]) -> HistogramBinningCalibrator:
        counts = [0] * self.n_bins
        correct = [0] * self.n_bins
        for conf, y in pairs:
            i = self._bin_index(conf)
            counts[i] += 1
            correct[i] += 1 if int(y) else 0
        self.bin_accuracy = [
            (correct[i] / counts[i]) if counts[i] else None
            for i in range(self.n_bins)
        ]
        self.fitted = True
        return self

    def predict(self, confidence: float) -> float:
        i = self._bin_index(confidence)
        learned = self.bin_accuracy[i] if self.fitted else None
        if learned is None:
            # Unseen bin: fall back to the bin midpoint.
            return round((i + 0.5) / self.n_bins, 4)
        return round(learned, 4)

    def to_dict(self) -> dict[str, object]:
        return {'n_bins': self.n_bins, 'bin_accuracy': self.bin_accuracy, 'fitted': self.fitted}

    def save(self, path: str) -> None:
        """Write the calibrator as JSON in the format ``generate_dashboard.load_calibrator`` reads."""
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, indent=2)
            f.write('\n')

    @classmethod
    def load(cls, path: str) -> HistogramBinningCalibrator:
        with open(path, encoding='utf-8') as f:
            return cls.from_dict(json.load(f))

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> HistogramBinningCalibrator:
        obj = cls(int(cast(int, data.get('n_bins', 10))))
        obj.bin_accuracy = list(cast(list, data.get('bin_accuracy', [])))
        obj.fitted = bool(data.get('fitted', False))
        if len(obj.bin_accuracy) != obj.n_bins:
            obj.bin_accuracy = [None] * obj.n_bins
            obj.fitted = False
        return obj
