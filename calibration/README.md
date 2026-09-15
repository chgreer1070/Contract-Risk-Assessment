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
| `evaluate.py` | CLI report + fit/save (`python -m calibration.evaluate`) |
| `export_labels.py` | CLI: dashboard → rows for a reviewer to label |
| `data/seed_labeled.jsonl` | **seed** labeled data (replace with real labels) |

## Quick start

```bash
# Report metrics on the seed dataset, and show before/after calibration:
python -m calibration.evaluate --fit
```

## The real loop

```bash
# 1. Collect: one row per risk, correct=null, with context for the reviewer
python -m calibration.export_labels contract_visualization.html -o labels.jsonl --source acme-msa
python -m calibration.export_labels other.html -o labels.jsonl --source other --append

# 2. Label: a reviewer sets "correct" to 1 (assessment right) or 0 (wrong)

# 3. Evaluate + fit + save (null rows are skipped and counted)
python -m calibration.evaluate --data labels.jsonl --fit --save-calibrator calibration/calibrator.json

# 4. Apply: generate_dashboard.py picks up calibration/calibrator.json automatically
#    (or point $CONTRACT_RISK_CALIBRATOR at it) and adds confidence.calibratedScore
```

Then re-run the regression gate: `python -m pytest tests/test_calibration.py -q`.

Never commit a calibrator fitted on the seed data. Everything here runs on CPU
with no network or model dependency.
