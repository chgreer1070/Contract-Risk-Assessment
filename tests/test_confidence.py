"""
Tests for the deterministic confidence + human-review (abstention) layer in
generate_dashboard.py (design doc Section 10).

Confidence is a pure function of observable signals -- citation verification,
source-clause linkage, playbook coverage, field completeness, and mandatory
escalation -- so these run on CPU with no model/network.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generate_dashboard import (  # noqa: E402
    assess_risk_confidence,
    attach_confidence,
    generate_dashboard_html,
    load_playbook,
)


def _extract_contract_data(html):
    """Pull the injected data back out of the JSON data island."""
    m = re.search(
        r'<script type="application/json" id="contract-data">\n(.*?)\n</script>',
        html, re.DOTALL,
    )
    assert m, "contract-data island not found in generated HTML"
    return json.loads(m.group(1))


def _clause(section='Section 8.1', ctype='Liability', text='Liability is capped at fees paid.'):
    return {'id': 'KC-001', 'clauseType': ctype, 'section': section, 'extractedClause': text}


def _verified_risk():
    """A fully-supported risk: verified citation, source clause, all fields."""
    return {
        'riskType': 'Liability',
        'clauseReference': 'Section 8.1',
        'riskLevel': 'High',
        'likelihood': 'Likely',
        'potentialConsequence': 'Unrecoverable losses.',
        'mandatoryEscalation': False,
        'citations': [{'clauseId': 'KC-001', 'quote': 'Liability is capped', 'verified': True}],
    }


# --------------------------------------------------------------------------- #
# assess_risk_confidence: per-signal scoring
# --------------------------------------------------------------------------- #

def test_fully_supported_risk_is_high_confidence_no_review():
    risk = _verified_risk()
    conf = assess_risk_confidence(risk, source_clause=_clause(), playbook_entry={'id': 'liability'})
    assert conf['score'] == 1.0
    assert conf['level'] == 'High'
    assert conf['reviewRequired'] is False
    assert conf['reasons'] == []


def test_missing_citation_penalizes_confidence():
    risk = _verified_risk()
    risk['citations'] = []
    conf = assess_risk_confidence(risk, source_clause=_clause(), playbook_entry={'id': 'x'})
    assert conf['score'] == 0.70  # 1.0 - 0.30
    assert 'no source citation' in conf['reasons']


def test_unverified_citation_penalizes_less_than_missing():
    risk = _verified_risk()
    risk['citations'] = [{'quote': 'nope', 'verified': False}]
    conf = assess_risk_confidence(risk, source_clause=_clause(), playbook_entry={'id': 'x'})
    assert conf['score'] == 0.75  # 1.0 - 0.25
    assert 'citation not verified against clause text' in conf['reasons']


def test_no_source_clause_forces_review():
    risk = _verified_risk()
    conf = assess_risk_confidence(risk, source_clause=None, playbook_entry={'id': 'x'})
    assert conf['reviewRequired'] is True
    assert 'no matching source clause' in conf['reasons']


def test_out_of_playbook_forces_review_but_keeps_confidence():
    risk = _verified_risk()
    conf = assess_risk_confidence(risk, source_clause=_clause(), playbook_entry=None)
    # Only the playbook signal is missing: confidence stays high, but a human
    # must decide because there is no playbook guidance for this clause type.
    assert conf['score'] == 0.80
    assert conf['level'] == 'High'
    assert conf['reviewRequired'] is True
    assert 'clause type not covered by playbook' in conf['reasons']


def test_mandatory_escalation_forces_review():
    risk = _verified_risk()
    risk['mandatoryEscalation'] = True
    conf = assess_risk_confidence(risk, source_clause=_clause(), playbook_entry={'id': 'x'})
    assert conf['reviewRequired'] is True
    assert any('mandatory escalation' in r for r in conf['reasons'])


def test_missing_fields_penalize_and_floor_at_zero():
    risk = {
        'riskType': 'Mystery', 'clauseReference': '', 'riskLevel': 'High',
        'likelihood': 'Possible', 'potentialConsequence': '', 'citations': [],
    }
    conf = assess_risk_confidence(risk, source_clause=None, playbook_entry=None)
    # Every signal fails: score floors at 0.0, low confidence, review required.
    assert conf['score'] == 0.0
    assert conf['level'] == 'Low'
    assert conf['reviewRequired'] is True
    for reason in ('no source citation', 'no matching source clause',
                   'clause type not covered by playbook',
                   'missing potential consequence', 'missing clause reference'):
        assert reason in conf['reasons']


# --------------------------------------------------------------------------- #
# attach_confidence: aggregate summary + per-risk attachment
# --------------------------------------------------------------------------- #

def test_attach_confidence_aggregate_all_clean():
    pb = load_playbook('default')
    clauses = [_clause(ctype='Liability')]
    risks = [_verified_risk()]
    # Give the risk a real playbook match by keying off "liability".
    summary = attach_confidence(risks, clauses, pb)
    assert risks[0]['confidence']['reviewRequired'] is False
    assert summary['riskCount'] == 1
    assert summary['reviewRequired'] == 0
    assert summary['autoAcceptable'] is True
    assert summary['meanConfidence'] == 1.0
    assert summary['confidenceLevel'] == 'High'


def test_attach_confidence_counts_reviews():
    pb = load_playbook('default')
    clauses = [_clause(ctype='Liability')]
    good = _verified_risk()
    bad = {
        'riskType': 'Mystery', 'clauseReference': '', 'riskLevel': 'High',
        'likelihood': 'Possible', 'potentialConsequence': '', 'citations': [],
    }
    summary = attach_confidence([good, bad], clauses, pb)
    assert summary['riskCount'] == 2
    assert summary['reviewRequired'] == 1
    assert summary['autoAcceptable'] is False
    assert 0.0 <= summary['meanConfidence'] <= 1.0


def test_attach_confidence_empty_is_not_auto_acceptable():
    summary = attach_confidence([], [], load_playbook('default'))
    assert summary['riskCount'] == 0
    assert summary['reviewRequired'] == 0
    assert summary['autoAcceptable'] is False
    assert summary['meanConfidence'] == 1.0


# --------------------------------------------------------------------------- #
# Integration: confidence flows into the generated dashboard data island
# --------------------------------------------------------------------------- #

def test_confidence_present_in_generated_html():
    state = {
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
    data = _extract_contract_data(generate_dashboard_html(state))
    assert 'reliability' in data
    rel = data['reliability']
    assert set(rel) >= {'riskCount', 'reviewRequired', 'meanConfidence',
                        'confidenceLevel', 'autoAcceptable'}
    assert rel['riskCount'] == len(data['riskAssessment'])
    for r in data['riskAssessment']:
        conf = r['confidence']
        assert set(conf) == {'score', 'level', 'reviewRequired', 'reasons'}
        assert 0.0 <= conf['score'] <= 1.0
        assert conf['level'] in ('High', 'Medium', 'Low')
