"""Tests for the deterministic confidence-calibration harness (calibration/)."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from calibration.calibrator import HistogramBinningCalibrator  # noqa: E402
from calibration.dataset import default_dataset_path, load_labeled  # noqa: E402
from calibration.evaluate import main as evaluate_main  # noqa: E402
from calibration.export_labels import (  # noqa: E402
    extract_contract_data,
    label_rows,
    write_rows,
)
from calibration.export_labels import (
    main as export_main,
)
from calibration.metrics import (  # noqa: E402
    brier_score,
    expected_calibration_error,
    max_calibration_error,
    reliability_bins,
    selective_metrics,
)
from generate_dashboard import (  # noqa: E402
    attach_confidence,
    calibrate_score,
    generate_dashboard_html,
    load_calibrator,
    load_playbook,
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


# --------------------------------------------------------------------------- #
# Integration with generate_dashboard.py (dormant by default, opt-in via file)
# --------------------------------------------------------------------------- #

_STATE = {
    'key_clauses': (
        '- **Clause Type**: Liability\n'
        '- **Extracted Clause**: Liability is capped at fees paid.\n'
        '- **Summary**: Cap.\n'
        '- **Section**: Section 8.1\n'
    ),
    'risk_assessment_report': (
        '- **Risk Type**: Limitation of Liability\n'
        '- **Clause Reference**: Section 8.1\n'
        '- **Risk Level**: High\n'
        '- **Likelihood**: Likely\n'
        '- **Potential Consequence**: Unrecoverable losses.\n'
    ),
    'recommended_actions': (
        '- **Clause**: Section 8.1\n'
        '- **Recommended Action**: Negotiate a cap.\n'
    ),
}


def _island(html):
    import re
    m = re.search(r'<script type="application/json" id="contract-data">\n(.*?)\n</script>',
                  html, re.DOTALL)
    return json.loads(m.group(1))


def test_calibrate_score_matches_calibrator_predict():
    # The standalone lookup in generate_dashboard.py must stay in sync with the
    # package's calibrator (including the unseen-bin midpoint fallback).
    cal = HistogramBinningCalibrator(n_bins=10).fit(
        [(1.0, 1), (1.0, 0), (0.8, 1), (0.55, 0), (0.1, 0)])
    cdict = cal.to_dict()
    for s in (0.0, 0.05, 0.1, 0.3, 0.42, 0.55, 0.75, 0.8, 0.95, 1.0):
        assert calibrate_score(s, cdict) == cal.predict(s)


def test_load_calibrator_missing_and_malformed(tmp_path, monkeypatch):
    monkeypatch.delenv('CONTRACT_RISK_CALIBRATOR', raising=False)
    assert load_calibrator(str(tmp_path / 'nope.json')) is None
    bad = tmp_path / 'bad.json'
    bad.write_text('{not json')
    assert load_calibrator(str(bad)) is None
    wrong = tmp_path / 'wrong.json'
    wrong.write_text(json.dumps({'n_bins': 3, 'bin_accuracy': [0.1]}))  # length mismatch
    assert load_calibrator(str(wrong)) is None
    unfitted = tmp_path / 'unfit.json'
    unfitted.write_text(json.dumps({'n_bins': 2, 'bin_accuracy': [None, None], 'fitted': False}))
    assert load_calibrator(str(unfitted)) is None


def test_load_calibrator_from_env(tmp_path, monkeypatch):
    p = tmp_path / 'cal.json'
    HistogramBinningCalibrator(n_bins=4).fit([(0.9, 1), (0.9, 0)]).save(str(p))
    monkeypatch.setenv('CONTRACT_RISK_CALIBRATOR', str(p))
    cal = load_calibrator()
    assert cal == {'n_bins': 4, 'bin_accuracy': [None, None, None, 0.5]}


def test_malformed_calibrator_bin_values_do_not_crash_dashboard(tmp_path, monkeypatch):
    p = tmp_path / 'bad-bin.json'
    p.write_text(json.dumps({'n_bins': 2, 'bin_accuracy': [0.5, 'bad'], 'fitted': True}))
    monkeypatch.setenv('CONTRACT_RISK_CALIBRATOR', str(p))

    assert load_calibrator() is None
    data = _island(generate_dashboard_html(_STATE))
    assert data['reliability']['calibrated'] is False
    assert all('calibratedScore' not in r['confidence'] for r in data['riskAssessment'])


def test_attach_confidence_without_calibrator_is_unchanged():
    pb = load_playbook('default')
    risks = [{
        'riskType': 'Liability', 'clauseReference': 'Section 8.1', 'riskLevel': 'High',
        'likelihood': 'Likely', 'potentialConsequence': 'Losses.',
        'citations': [{'clauseId': 'KC-001', 'quote': 'capped', 'verified': True}],
    }]
    clauses = [{'id': 'KC-001', 'clauseType': 'Liability', 'section': 'Section 8.1',
                'extractedClause': 'Liability is capped at fees paid.'}]
    summary = attach_confidence(risks, clauses, pb)
    assert summary['calibrated'] is False
    assert 'meanCalibratedConfidence' not in summary
    assert 'calibratedScore' not in risks[0]['confidence']


def test_attach_confidence_with_calibrator_adds_calibrated_score():
    pb = load_playbook('default')
    risks = [{
        'riskType': 'Liability', 'clauseReference': 'Section 8.1', 'riskLevel': 'High',
        'likelihood': 'Likely', 'potentialConsequence': 'Losses.',
        'citations': [{'clauseId': 'KC-001', 'quote': 'capped', 'verified': True}],
    }]
    clauses = [{'id': 'KC-001', 'clauseType': 'Liability', 'section': 'Section 8.1',
                'extractedClause': 'Liability is capped at fees paid.'}]
    cal = HistogramBinningCalibrator(n_bins=10).fit([(1.0, 1)] * 3 + [(1.0, 0)]).to_dict()
    summary = attach_confidence(risks, clauses, pb, cal)
    conf = risks[0]['confidence']
    assert conf['score'] == 1.0                 # raw score untouched
    assert conf['calibratedScore'] == 0.75       # 3 of 4 correct in that bin
    assert conf['reviewRequired'] is False       # policy unchanged
    assert summary['calibrated'] is True
    assert summary['meanCalibratedConfidence'] == 0.75


def test_dashboard_html_carries_calibrated_score_when_file_present(tmp_path, monkeypatch):
    p = tmp_path / 'cal.json'
    HistogramBinningCalibrator(n_bins=10).fit([(1.0, 1)] * 3 + [(1.0, 0)]).save(str(p))
    monkeypatch.setenv('CONTRACT_RISK_CALIBRATOR', str(p))
    data = _island(generate_dashboard_html(_STATE))
    assert data['reliability']['calibrated'] is True
    assert all('calibratedScore' in r['confidence'] for r in data['riskAssessment'])


def test_dashboard_html_default_has_no_calibration(monkeypatch):
    monkeypatch.delenv('CONTRACT_RISK_CALIBRATOR', raising=False)
    data = _island(generate_dashboard_html(_STATE))
    assert data['reliability']['calibrated'] is False
    assert all('calibratedScore' not in r['confidence'] for r in data['riskAssessment'])


# --------------------------------------------------------------------------- #
# Label export -> partial labeling -> evaluate/save loop
# --------------------------------------------------------------------------- #

def test_export_label_rows_from_dashboard_html(monkeypatch):
    monkeypatch.delenv('CONTRACT_RISK_CALIBRATOR', raising=False)
    html = generate_dashboard_html(_STATE)
    data = extract_contract_data(html)
    rows = label_rows(data, 'demo')
    assert len(rows) == len(data['riskAssessment']) > 0
    row = rows[0]
    assert row['id'] == 'demo#1'
    assert row['correct'] is None
    assert 0.0 <= row['predictedConfidence'] <= 1.0
    assert row['context']['clauseReference'] == 'Section 8.1'


def test_extract_contract_data_accepts_raw_json_and_rejects_other():
    assert extract_contract_data(json.dumps({'riskAssessment': []})) == {'riskAssessment': []}
    with pytest.raises(ValueError):
        extract_contract_data(json.dumps({'foo': 1}))


def test_unlabeled_rows_are_skipped_only_when_asked(tmp_path):
    p = tmp_path / 'labels.jsonl'
    write_rows([
        {'id': 'a', 'predictedConfidence': 0.9, 'correct': None, 'reviewRequired': False},
        {'id': 'b', 'predictedConfidence': 0.9, 'correct': 1, 'reviewRequired': False},
    ], str(p))
    assert [r.id for r in load_labeled(str(p), skip_unlabeled=True)] == ['b']
    with pytest.raises(ValueError):
        load_labeled(str(p))  # strict mode: null label is an error


def test_export_cli_then_evaluate_cli_saves_calibrator(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv('CONTRACT_RISK_CALIBRATOR', raising=False)
    html_path = tmp_path / 'dash.html'
    html_path.write_text(generate_dashboard_html(_STATE), encoding='utf-8')
    labels = tmp_path / 'labels.jsonl'
    assert export_main([str(html_path), '-o', str(labels), '--source', 'c1']) == 0

    # Nothing labeled yet -> evaluate reports and exits non-zero.
    assert evaluate_main(['--data', str(labels)]) == 1
    assert 'No labeled rows' in capsys.readouterr().out

    # Reviewer labels every row as correct, then fit + save.
    rows = [json.loads(line) for line in labels.read_text().splitlines()]
    for r in rows:
        r['correct'] = 1
    labels.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    out_cal = tmp_path / 'calibrator.json'
    assert evaluate_main(['--data', str(labels), '--fit', '--save-calibrator', str(out_cal)]) == 0
    assert out_cal.exists()
    loaded = load_calibrator(str(out_cal))
    assert loaded is not None and loaded['n_bins'] == 10
