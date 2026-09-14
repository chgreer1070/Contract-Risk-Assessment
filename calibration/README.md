# `calibration/` — confidence calibration harness

Deterministic, dependency-free tooling to check whether the contract risk tool's
confidence scores are **statistically trustworthy** and to correct systematic
over/under-confidence. Full design: [`../docs/confidence_calibration.md`](../docs/confidence_calibration.md).

## Layout

| file | purpose |
|---|---|
| `metrics.py` | Brier, ECE, MCE, reliability bins, selective-prediction metrics |
| `calibrator.py` | `HistogramBinningCalibrator` (fit / predict / (de)serialize) |
| `dataset.py` | JSONL loader + `LabeledRecord` schema/validation |
| `evaluate.py` | CLI report (`python -m calibration.evaluate`) |
| `data/seed_labeled.jsonl` | **seed** labeled data (replace with real labels) |

## Quick start

```bash
# Report metrics on the seed dataset, and show before/after calibration:
python -m calibration.evaluate --fit

# Use your own labels:
python -m calibration.evaluate --data calibration/data/mine.jsonl --bins 10
```

## Adding real labels

Append rows to a JSONL file (see schema in the design doc). The only
human/model-supplied field is `correct` (1 = the assessment was right/acceptable,
0 = not). Then re-run the CLI and the regression gate:

```bash
python -m pytest tests/test_calibration.py -q
```

Everything here runs on CPU with no network or model dependency.
