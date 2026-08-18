"""
Tests for the ISO 31000-aligned risk scoring in generate_dashboard.py:

  * the explicit likelihood x consequence matrix (RISK_MATRIX / _score_for),
  * the severity-weighted overall index (compute_weighted_risk_score),
  * top-risk prioritization (top_risks),
  * and that both new fields are surfaced in the generated dashboard.

The existing flat-mean compute_risk_score behavior is intentionally unchanged.
"""

import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generate_dashboard import (  # noqa: E402
    RISK_MATRIX,
    _score_for,
    compute_risk_score,
    compute_weighted_risk_score,
    top_risks,
    generate_dashboard_html,
)


def _contract_data(html):
    m = re.search(
        r'<script type="application/json" id="contract-data">\n(.*?)\n</script>',
        html, re.DOTALL,
    )
    assert m
    return json.loads(m.group(1))


# --------------------------------------------------------------------------- #
# Explicit matrix
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize('level,likelihood,expected', [
    ('High', 'Likely', 9), ('High', 'Possible', 8), ('High', 'Unlikely', 6),
    ('Medium', 'Likely', 6), ('Medium', 'Possible', 5), ('Medium', 'Unlikely', 4),
    ('Low', 'Likely', 3), ('Low', 'Possible', 3), ('Low', 'Unlikely', 2),
])
def test_score_matrix_values(level, likelihood, expected):
    assert _score_for(level, likelihood) == expected


def test_score_matrix_monotonic_and_bounded():
    for level in ('High', 'Medium', 'Low'):
        row = RISK_MATRIX[level]
        assert row['Unlikely'] <= row['Possible'] <= row['Likely']
        for v in row.values():
            assert 0 <= v <= 10
    # Higher consequence always dominates for a fixed likelihood.
    for lk in ('Unlikely', 'Possible', 'Likely'):
        assert RISK_MATRIX['High'][lk] >= RISK_MATRIX['Medium'][lk] >= RISK_MATRIX['Low'][lk]


def test_score_defaults_for_unknown_inputs():
    # Unknown level -> Medium row; unknown likelihood -> Possible column.
    assert _score_for('Bogus', 'Possible') == RISK_MATRIX['Medium']['Possible']
    assert _score_for('High', 'Bogus') == RISK_MATRIX['High']['Possible']


# --------------------------------------------------------------------------- #
# Severity-weighted index
# --------------------------------------------------------------------------- #

def test_weighted_index_emphasizes_severe_risks_over_mean():
    risks = [
        {'riskLevel': 'High', 'score': 9},
        {'riskLevel': 'Low', 'score': 2},
        {'riskLevel': 'Low', 'score': 2},
    ]
    mean = compute_risk_score(risks)             # (9+2+2)/3 = 4.3
    weighted = compute_weighted_risk_score(risks)  # (9*3+2+2)/5 = 6.2
    assert weighted > mean
    assert weighted == 6.2


def test_weighted_index_empty_is_zero():
    assert compute_weighted_risk_score([]) == 0


def test_weighted_equals_mean_when_all_same_level():
    risks = [{'riskLevel': 'Medium', 'score': 5}, {'riskLevel': 'Medium', 'score': 7}]
    assert compute_weighted_risk_score(risks) == compute_risk_score(risks) == 6.0


# --------------------------------------------------------------------------- #
# Top risks
# --------------------------------------------------------------------------- #

def test_top_risks_orders_by_score_then_severity():
    risks = [
        {'riskType': 'A', 'riskLevel': 'Low', 'score': 3},
        {'riskType': 'B', 'riskLevel': 'High', 'score': 9},
        {'riskType': 'C', 'riskLevel': 'Medium', 'score': 6},
        {'riskType': 'D', 'riskLevel': 'High', 'score': 8},
    ]
    top = top_risks(risks, 3)
    assert [r['riskType'] for r in top] == ['B', 'D', 'C']


def test_top_risks_respects_n_and_short_lists():
    risks = [{'riskType': 'A', 'riskLevel': 'High', 'score': 9}]
    assert len(top_risks(risks, 3)) == 1
    assert top_risks([], 3) == []


# --------------------------------------------------------------------------- #
# Surfaced in generated dashboard
# --------------------------------------------------------------------------- #

def test_dashboard_exposes_weighted_score_and_top_risks():
    state = {
        'key_clauses': '', 'recommended_actions': '',
        'risk_assessment_data': [
            {'riskType': 'Compliance', 'riskLevel': 'High', 'likelihood': 'Likely',
             'potentialConsequence': 'Fines', 'clauseReference': 'S6.1'},
            {'riskType': 'Ops', 'riskLevel': 'Low', 'likelihood': 'Unlikely',
             'potentialConsequence': 'Minor', 'clauseReference': 'S9'},
        ],
    }
    data = _contract_data(generate_dashboard_html(state))
    assert isinstance(data['weightedRiskScore'], (int, float))
    assert 0 <= data['weightedRiskScore'] <= 10
    assert len(data['topRisks']) == 2
    # Highest-scoring risk comes first.
    assert data['topRisks'][0]['riskType'] == 'Compliance'
    assert data['topRisks'][0]['score'] >= data['topRisks'][1]['score']
