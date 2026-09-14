# Confidence calibration: design & evaluation setup

**Status:** Evaluation harness implemented (deterministic); large-scale labeling and
production calibrator wiring are the remaining, model/human-dependent work.

**Related:** `docs/contract_risk_scoring_architecture.md` §10 (reliability), the
deterministic confidence layer in `generate_dashboard.py`
(`assess_risk_confidence` / `attach_confidence`), and the `calibration/` package.

---

## 1. Goal

Today each risk carries a **confidence score** in [0, 1] derived from observable
signals (citation verified, source clause linked, playbook coverage, field
completeness, mandatory escalation). Those numbers are *sensible* but not
*calibrated*: nothing guarantees that risks shown at "80%" are actually correct
80% of the time.

**Calibration** turns the percentages into statistically trustworthy
probabilities, so that:

- a stated confidence of *p* means "right about *p* of the time," and
- the **auto-accept / needs-review** boundary can be set from real error rates
  (e.g. "auto-accept only where empirical accuracy ≥ 95%"), satisfying the
  human-oversight expectations of the EU AI Act / NIST AI RMF.

## 2. Why it's model-dependent (and what isn't)

The one input we cannot synthesize is the **ground-truth label**: for a sample of
real assessments, *was each risk actually correct?* That requires human review
(or a high-quality LLM judge, itself validated against humans). Everything else —
metrics, reliability diagrams, the calibrator, the CI gate — is deterministic and
already implemented in `calibration/`, so it runs in the CPU test loop with no
model or network.

## 3. Labeled dataset

### 3.1 Schema (`calibration/data/*.jsonl`)

One JSON object per line:

| field | type | meaning |
|---|---|---|
| `id` | string | stable id for the labeled risk |
| `predictedConfidence` | float [0,1] | the tool's confidence (`assess_risk_confidence`) |
| `correct` | 0 or 1 | **ground truth**: was the assessment right/acceptable? |
| `reviewRequired` | bool | did the tool flag it for human review? |
| `riskType` | string | for slice analysis (optional) |

`calibration/data/seed_labeled.jsonl` is a small **seed** dataset used to exercise
the harness in tests. It is intentionally slightly over-confident and must be
**replaced by real labels** before any production claim.

### 3.2 Label definition

`correct = 1` when a qualified reviewer agrees the risk is real, correctly
leveled, correctly cited, and the recommended action is appropriate; else `0`.
Record the rubric alongside labels so the definition stays stable over time.

### 3.3 Producing labels

1. **Sample** production assessments stratified across confidence bins and risk
   types (avoid only labeling the easy, high-confidence cases).
2. **Human labeling** by a legal reviewer against the rubric is the gold source.
3. **LLM-as-judge** can pre-label to scale up, but only after measuring its
   agreement with humans (e.g. Cohen's κ) on a held-out slice; treat it as a
   noisy labeler, not ground truth.
4. **Size & splits:** target ≥ ~50 labels per confidence bin for stable bin
   estimates. Split into **fit** (learn the calibrator) and **test** (report
   metrics) sets — never report calibration metrics on the fit split.

## 4. Metrics (implemented in `calibration/metrics.py`)

- **Brier score** — mean squared error between confidence and outcome. Overall
  sharpness + calibration in one number (lower is better).
- **ECE (Expected Calibration Error)** — bin predictions, take the count-weighted
  average `|accuracy − confidence|` per bin. The headline calibration number.
- **MCE (Maximum Calibration Error)** — the worst single-bin gap.
- **Reliability diagram** — per-bin `(mean_confidence, accuracy, count)`; the
  data behind the classic calibration plot.
- **Selective-prediction metrics** (`selective_metrics`) — evaluate the
  abstention policy: **coverage** (fraction auto-accepted), **auto-accept
  accuracy** (must be high), and **error-capture rate** (fraction of all errors
  that were flagged for review).

## 5. Calibration methods

| method | status | notes |
|---|---|---|
| **Histogram binning** | **implemented** (`HistogramBinningCalibrator`) | simplest, dependency-free; maps a raw score to its bin's empirical accuracy |
| Isotonic regression | roadmap | monotonic, non-parametric; better with enough data (PAV algorithm, pure-Python feasible) |
| Platt scaling | roadmap | logistic fit; good when miscalibration is roughly sigmoidal |
| Conformal prediction | roadmap | distribution-free risk guarantees; strongest for the "abstain above a risk budget" policy |

The v1 shipped calibrator is histogram binning because it is transparent,
deterministic, and needs no numerical dependencies (keeping the CPU test loop
lean). The others are additive once enough labels exist.

## 6. Acceptance thresholds & CI gate

The harness is wired as a **pytest regression gate** in
`tests/test_calibration.py::test_seed_dataset_quality_gate`, so calibration
quality is checked in the existing test job. Starter thresholds (tune as the real
dataset grows):

- **ECE ≤ 0.10** on the test split
- **Calibration must not increase ECE** (`ece_after ≤ ece_before`)
- **Auto-accept accuracy ≥ 0.95** (production target; seed uses ≥ 0.90)
- **Error-capture rate ≥ 0.85**

`mypy` now also type-checks the `calibration/` package in CI.

Run the report locally:

```bash
python -m calibration.evaluate --fit          # seed dataset, with before/after
python -m calibration.evaluate --data calibration/data/mine.jsonl --bins 10
```

## 7. Wiring calibration into the product (roadmap)

Once a calibrator is fit on real labels:

1. Persist it (`HistogramBinningCalibrator.to_dict()` → JSON, versioned).
2. In `generate_dashboard.py`, after `attach_confidence`, map each
   `confidence.score` through the loaded calibrator and store both the raw and
   calibrated values.
3. Drive the **auto-accept / needs-review** threshold from the calibrated
   probability and the accuracy target above.
4. Re-fit on a schedule; monitor ECE drift in production sampling (§10 of the
   architecture doc).

## 8. What ships now vs. later

- **Now (this change):** dataset schema + loader, seed dataset, all metrics,
  histogram-binning calibrator, evaluation CLI, tests, and a CI quality gate.
- **Later (model/human-dependent):** real labeled data at scale, LLM-judge
  agreement study, isotonic/conformal calibrators, and wiring the calibrated
  score back into the dashboard threshold.
