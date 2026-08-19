"""
Answer-key (golden dataset) regression tests.

Each case in golden/cases/*.json pairs an input pipeline `state` with an
`expect` block. Running the case through generate_dashboard_html and comparing
the extracted data island against the answer key gives a deterministic
regression gate (no LLM), enforcing the "deterministic floors" from the design:
schema completeness, citation validity, and correct bands/scores/weighted index.
"""

import glob
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generate_dashboard import generate_dashboard_html  # noqa: E402

CASES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'golden', 'cases'
)
CASE_FILES = sorted(glob.glob(os.path.join(CASES_DIR, '*.json')))

REQUIRED_RISK_FIELDS = (
    'riskType', 'clauseReference', 'riskLevel', 'likelihood', 'score',
    'band', 'rationale', 'citations', 'playbookRef', 'mandatoryEscalation',
)


def _contract_data(html):
    m = re.search(
        r'<script type="application/json" id="contract-data">\n(.*?)\n</script>',
        html, re.DOTALL,
    )
    assert m, "contract-data island not found"
    return json.loads(m.group(1))


def _load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def test_golden_cases_exist():
    assert CASE_FILES, "no golden cases found under golden/cases/"


@pytest.mark.parametrize('path', CASE_FILES, ids=[os.path.basename(p) for p in CASE_FILES])
def test_golden_case(path):
    case = _load(path)
    data = _contract_data(generate_dashboard_html(case['state']))
    exp = case['expect']

    # Deterministic floor 1: schema completeness on every risk.
    for r in data['riskAssessment']:
        for field in REQUIRED_RISK_FIELDS:
            assert field in r, f"{case['name']}: risk missing field '{field}'"

    # Deterministic floor 2: no unverified citation is ever shipped.
    for r in data['riskAssessment']:
        for c in r['citations']:
            assert c['verified'] is True, f"{case['name']}: shipped an unverified citation"

    if 'allCitationsVerified' in exp:
        all_verified = all(
            c['verified'] for r in data['riskAssessment'] for c in r['citations']
        )
        assert all_verified == exp['allCitationsVerified']

    if 'clauseCount' in exp:
        assert len(data['keyClauses']) == exp['clauseCount']

    if 'weightedRiskScore' in exp:
        assert data['weightedRiskScore'] == exp['weightedRiskScore']

    # Per-risk answer key, matched by clause reference.
    for er in exp.get('risks', []):
        matches = [r for r in data['riskAssessment'] if r['clauseReference'] == er['clauseReference']]
        assert matches, f"{case['name']}: expected risk '{er['clauseReference']}' not found"
        r = matches[0]
        for key in ('riskLevel', 'score', 'band'):
            if key in er:
                assert r[key] == er[key], (
                    f"{case['name']} [{er['clauseReference']}] {key}: {r[key]} != {er[key]}"
                )
