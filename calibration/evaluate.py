"""CLI: report calibration + selective-prediction metrics for a labeled dataset.

    python -m calibration.evaluate                 # uses the seed dataset
    python -m calibration.evaluate --data path.jsonl --bins 10 --fit

With --fit, a HistogramBinningCalibrator is fit on the data and the metrics are
re-reported after calibration so you can see the improvement. (In production,
fit on a held-out split; see docs/confidence_calibration.md.)
"""

from __future__ import annotations

import argparse

from calibration.calibrator import HistogramBinningCalibrator
from calibration.dataset import default_dataset_path, load_labeled
from calibration.metrics import (
    brier_score,
    expected_calibration_error,
    max_calibration_error,
    reliability_bins,
    selective_metrics,
)


def _report(pairs: list[tuple[float, int]], n_bins: int, title: str) -> dict[str, float]:
    ece = expected_calibration_error(pairs, n_bins)
    mce = max_calibration_error(pairs, n_bins)
    brier = brier_score(pairs)
    print(f'\n== {title} ==')
    print(f'  Brier score : {brier:.4f}  (lower is better)')
    print(f'  ECE         : {ece:.4f}  (lower is better)')
    print(f'  MCE         : {mce:.4f}  (lower is better)')
    print('  Reliability bins (confidence -> accuracy):')
    print('    range          n   mean_conf   accuracy    gap')
    for b in reliability_bins(pairs, n_bins):
        print(f"    [{b['lo']:.2f},{b['hi']:.2f})  {int(b['count']):>3}   "
              f"{b['meanConfidence']:.3f}      {b['accuracy']:.3f}      {b['gap']:.3f}")
    return {'brier': brier, 'ece': ece, 'mce': mce}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Confidence calibration report.')
    ap.add_argument('--data', default=default_dataset_path(), help='JSONL labeled dataset')
    ap.add_argument('--bins', type=int, default=10, help='number of confidence bins')
    ap.add_argument('--fit', action='store_true', help='fit a calibrator and show after-metrics')
    args = ap.parse_args(argv)

    records = load_labeled(args.data)
    print(f'Loaded {len(records)} labeled records from {args.data}')
    pairs = [r.as_pair() for r in records]

    _report(pairs, args.bins, 'Raw confidence')

    sel = selective_metrics([r.__dict__ for r in records])
    print('\n== Selective prediction (abstention policy) ==')
    print(f"  Coverage (auto-accepted)   : {sel['coverage']:.4f}")
    print(f"  Auto-accept accuracy       : {sel['autoAcceptAccuracy']:.4f}")
    print(f"  Error capture rate (review): {sel['errorCaptureRate']:.4f}")

    if args.fit:
        cal = HistogramBinningCalibrator(n_bins=args.bins).fit(pairs)
        cal_pairs = [(cal.predict(c), y) for c, y in pairs]
        _report(cal_pairs, args.bins, 'Calibrated confidence (histogram binning)')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
