"""Tests for the deterministic reasoning + source-citation layer."""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generate_dashboard import (  # noqa: E402
    _apply_playbook,
    _attach_reasoning,
    _find_source_clause,
    _verify_citation,
    generate_dashboard_html,
    load_playbook,
)


def test_verify_citation_true_false():
    assert _verify_citation('liability is capped', 'The provider liability is capped at fees') is True
    assert _verify_citation('whitespace   collapsed', 'whitespace collapsed here') is True  # ws-insensitive
    assert _verify_citation('CASE insensitive', 'here is case insensitive text') is True
    assert _verify_citation('not present', 'something else entirely') is False
    assert _verify_citation('', 'x') is False
    assert _verify_citation('x', '') is False


def test_find_source_clause_by_section_then_type():
    clauses = [{'id': 'KC-001', 'clauseType': 'Liability', 'section': 'Section 8.1',
                'extractedClause': 'cap'}]
    assert _find_source_clause({'clauseReference': 'Section 8.1 - Cap'}, clauses)['id'] == 'KC-001'
    assert _find_source_clause({'clauseReference': 'the liability clause'}, clauses)['id'] == 'KC-001'
    assert _find_source_clause({'clauseReference': 'Section 99'}, clauses) is None
    assert _find_source_clause({'clauseReference': ''}, clauses) is None


def test_attach_reasoning_adds_rationale_and_verified_citation():
    pb = load_playbook('default')
    clauses = [{'id': 'KC-001', 'clauseType': 'Liability', 'section': 'Section 8.1',
                'extractedClause': 'Provider liability shall not exceed fees.'}]
    risks = [{'riskType': 'Legal', 'clauseReference': 'Section 8.1 - Liability Cap',
              'riskLevel': 'High', 'likelihood': 'Likely', 'score': 9}]
    _apply_playbook(risks, pb)
    _attach_reasoning(risks, clauses)
    r = risks[0]
    assert 'score 9/10' in r['rationale'] and 'Walk' in r['rationale']
    assert len(r['citations']) == 1
    assert r['citations'][0]['verified'] is True
    assert r['citations'][0]['clauseId'] == 'KC-001'


def test_attach_reasoning_no_source_gives_empty_citations():
    risks = [{'riskType': 'Ops', 'clauseReference': 'Section 99',
              'riskLevel': 'Low', 'likelihood': 'Possible', 'score': 3}]
    _apply_playbook(risks, load_playbook('default'))
    _attach_reasoning(risks, [])
    assert risks[0]['citations'] == []
    assert 'Accept' in risks[0]['rationale']


def test_generated_html_risks_carry_reasoning_and_verified_citations():
    state = {
        'key_clauses': '', 'recommended_actions': '',
        'risk_assessment_data': [
            {'riskType': 'Legal', 'clauseReference': 'Section 8.1', 'riskLevel': 'High',
             'likelihood': 'Likely', 'potentialConsequence': 'x'}
        ],
        'key_clauses_data': [
            {'clauseType': 'Liability', 'section': 'Section 8.1',
             'extractedClause': 'Per Section 8.1 liability capped.', 'summary': 's'}
        ],
    }
    html = generate_dashboard_html(state)
    m = re.search(
        r'<script type="application/json" id="contract-data">\n(.*?)\n</script>',
        html, re.DOTALL,
    )
    r = json.loads(m.group(1))['riskAssessment'][0]
    assert r['rationale'] and r['band']
    assert r['citations'][0]['verified'] is True
