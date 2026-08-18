"""
Tests for the messy-LLM-output robustness improvements in generate_dashboard.py:

  * deduplication of repeated clauses / risks / actions (with clause re-id),
  * expanded risk-level and likelihood synonym recognition,
  * en/em dash field separators,
  * broadened section-reference detection (§, Sec., Art., Schedule, ...).

These are pure functions (no PDF/LLM/network), so the suite runs on CPU.
"""

import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generate_dashboard import (  # noqa: E402
    parse_key_clauses,
    _canon_level,
    _canon_likelihood,
    generate_dashboard_html,
)


def _extract_contract_data(html):
    """Pull the injected CONTRACT_DATA JSON object back out of the HTML."""
    m = re.search(r'const CONTRACT_DATA = (\{.*?\n\});', html, re.DOTALL)
    assert m, "CONTRACT_DATA block not found in generated HTML"
    raw = m.group(1).replace('<\\/', '</')
    return json.loads(raw)


# --------------------------------------------------------------------------- #
# Expanded level synonyms
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize('raw,expected', [
    ('significant', 'High'), ('substantial', 'High'), ('major', 'High'),
    ('serious concern', 'High'), ('grave', 'High'), ('elevated risk', 'High'),
    ('immaterial', 'Low'), ('trivial', 'Low'), ('nominal', 'Low'),
    ('slight', 'Low'), ('very low', 'Low'),
    ('average', 'Medium'), ('intermediate', 'Medium'),
])
def test_canon_level_new_synonyms(raw, expected):
    assert _canon_level(raw) == expected


def test_canon_level_insignificant_not_read_as_high():
    """Substring trap: 'insignificant' contains 'significant' — must be Low."""
    assert _canon_level('insignificant exposure') == 'Low'


# --------------------------------------------------------------------------- #
# Expanded likelihood synonyms
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize('raw,expected', [
    ('remote', 'Unlikely'), ('doubtful', 'Unlikely'),
    ('anticipated', 'Likely'), ('foreseeable', 'Likely'), ('often', 'Likely'),
    ('conceivable', 'Possible'), ('sometimes', 'Possible'),
])
def test_canon_likelihood_new_synonyms(raw, expected):
    assert _canon_likelihood(raw) == expected


def test_canon_likelihood_improbable_not_read_as_likely():
    """Substring trap: 'improbable' contains 'probable' — must be Unlikely."""
    assert _canon_likelihood('improbable') == 'Unlikely'


# --------------------------------------------------------------------------- #
# En / em dash field separators
# --------------------------------------------------------------------------- #

def test_en_dash_separator_parses_fields():
    md = (
        "- **Clause Type** \u2013 Liability\n"
        "- **Extracted Clause** \u2013 Provider liability is capped.\n"
        "- **Summary** \u2013 Caps liability.\n"
    )
    out = parse_key_clauses(md)
    assert len(out) == 1
    assert out[0]['clauseType'] == 'Liability'
    assert 'capped' in out[0]['extractedClause']


def test_em_dash_separator_parses_fields():
    md = (
        "Clause Type \u2014 Termination\n"
        "Extracted Clause \u2014 30 days notice required.\n"
        "Summary \u2014 Notice period.\n"
    )
    out = parse_key_clauses(md)
    assert len(out) == 1
    assert out[0]['clauseType'] == 'Termination'


# --------------------------------------------------------------------------- #
# Broadened section detection
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize('text,expected', [
    ('Per \u00a7 8.1 liability is capped.', '\u00a7 8.1'),
    ('See Sec. 8.1 for the cap.', 'Sec. 8.1'),
    ('Under Art. 5 the parties agree.', 'Art. 5'),
    ('Refer to Schedule 2 for pricing.', 'Schedule 2'),
    ('Detailed in Exhibit 3 attached.', 'Exhibit 3'),
])
def test_section_detection_variants(text, expected):
    md = (
        "- **Clause Type**: Liability\n"
        "- **Extracted Clause**: " + text + "\n"
        "- **Summary**: cap.\n"
    )
    out = parse_key_clauses(md)
    assert out[0]['section'] == expected


# --------------------------------------------------------------------------- #
# Deduplication (via generate_dashboard_html, covering both data paths)
# --------------------------------------------------------------------------- #

def test_duplicate_clauses_collapsed_and_reindexed():
    state = {
        'key_clauses': '', 'risk_assessment_report': '', 'recommended_actions': '',
        'key_clauses_data': [
            {'clauseType': 'Liability', 'extractedClause': 'A', 'summary': 'a'},
            {'clauseType': 'Termination', 'extractedClause': 'B', 'summary': 'b'},
            {'clauseType': 'Liability', 'extractedClause': 'A', 'summary': 'a'},  # dup of 1st
            {'clauseType': 'Confidentiality', 'extractedClause': 'C', 'summary': 'c'},
        ],
    }
    data = _extract_contract_data(generate_dashboard_html(state))
    types = [c['clauseType'] for c in data['keyClauses']]
    assert types == ['Liability', 'Termination', 'Confidentiality']
    # ids stay sequential after the middle duplicate is dropped
    assert [c['id'] for c in data['keyClauses']] == ['KC-001', 'KC-002', 'KC-003']


def test_duplicate_clauses_in_markdown_collapsed():
    md = (
        "- **Clause Type**: Liability\n- **Extracted Clause**: Capped.\n- **Summary**: Cap.\n"
        "- **Clause Type**: Liability\n- **Extracted Clause**: Capped.\n- **Summary**: Cap.\n"
    )
    state = {'key_clauses': md, 'risk_assessment_report': '', 'recommended_actions': ''}
    data = _extract_contract_data(generate_dashboard_html(state))
    assert len(data['keyClauses']) == 1


def test_duplicate_clauses_whitespace_insensitive():
    state = {
        'key_clauses': '', 'risk_assessment_report': '', 'recommended_actions': '',
        'key_clauses_data': [
            {'clauseType': 'Liability', 'extractedClause': 'Capped at fees.', 'summary': 's'},
            {'clauseType': 'Liability', 'extractedClause': 'Capped   at   fees.', 'summary': 's'},
        ],
    }
    data = _extract_contract_data(generate_dashboard_html(state))
    assert len(data['keyClauses']) == 1


def test_duplicate_risks_collapsed_before_scoring():
    state = {
        'key_clauses': '', 'recommended_actions': '',
        'risk_assessment_data': [
            {'riskType': 'Legal', 'clauseReference': 'S1', 'riskLevel': 'High',
             'likelihood': 'Possible', 'potentialConsequence': 'X'},
            {'riskType': 'Legal', 'clauseReference': 'S1', 'riskLevel': 'High',
             'likelihood': 'Possible', 'potentialConsequence': 'X'},  # dup
        ],
    }
    data = _extract_contract_data(generate_dashboard_html(state))
    assert len(data['riskAssessment']) == 1


def test_duplicate_actions_collapsed():
    state = {
        'key_clauses': '', 'risk_assessment_report': '',
        'recommended_actions_data': [
            {'clause': 'Section 8.1', 'action': 'Negotiate a higher cap.'},
            {'clause': 'Section 8.1', 'action': 'Negotiate a higher cap.'},  # dup
            {'clause': 'Section 9.2', 'action': 'Add deletion terms.'},
        ],
    }
    data = _extract_contract_data(generate_dashboard_html(state))
    clauses = [a['clause'] for a in data['recommendedActions']]
    assert clauses == ['Section 8.1', 'Section 9.2']


def test_distinct_entries_are_not_collapsed():
    state = {
        'key_clauses': '', 'risk_assessment_report': '', 'recommended_actions': '',
        'key_clauses_data': [
            {'clauseType': 'Liability', 'extractedClause': 'A', 'summary': 'a'},
            {'clauseType': 'Liability', 'extractedClause': 'A different clause', 'summary': 'b'},
        ],
    }
    data = _extract_contract_data(generate_dashboard_html(state))
    assert len(data['keyClauses']) == 2
