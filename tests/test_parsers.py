"""
Unit tests for generate_dashboard.py — the pipeline→HTML bridge.

These functions are pure (no PDF/LLM/network), so the suite is cheap and runs on
CPU. They cover the fixes for real-world Mixtral/Mistral output: numbered lists,
missing bold markers, prose-qualified risk values, multi-line clauses, the
</script> breakout (security), and backslash-containing contract text.

Run: pytest tests/  (from the repo root)
"""

import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generate_dashboard import (  # noqa: E402
    _build_metadata,
    _build_timeline,
    _canon_level,
    _canon_likelihood,
    _parse_date,
    compute_risk_score,
    generate_dashboard_html,
    parse_key_clauses,
    parse_recommended_actions,
    parse_risk_assessment,
)

# --------------------------------------------------------------------------- #
# parse_key_clauses
# --------------------------------------------------------------------------- #

def test_clauses_wellformed_two_blocks():
    md = (
        "- **Clause Type**: Liability\n"
        "- **Extracted Clause**: Provider liability is capped at fees paid.\n"
        "- **Summary**: Caps liability.\n"
        "\n"
        "- **Clause Type**: Termination\n"
        "- **Extracted Clause**: 30 days notice required.\n"
        "- **Summary**: Notice period.\n"
    )
    out = parse_key_clauses(md)
    assert len(out) == 2
    assert out[0]['clauseType'] == 'Liability'
    assert out[1]['clauseType'] == 'Termination'
    assert out[0]['id'] == 'KC-001' and out[1]['id'] == 'KC-002'


def test_clauses_numbered_list_not_collapsed():
    """Mixtral often emits numbered lists; both items must survive."""
    md = (
        "1. **Clause Type**: Liability\n"
        "   **Extracted Clause**: Liability capped.\n"
        "   **Summary**: Caps it.\n"
        "2. **Clause Type**: Confidentiality\n"
        "   **Extracted Clause**: Mutual NDA.\n"
        "   **Summary**: Keep secrets.\n"
    )
    out = parse_key_clauses(md)
    assert len(out) == 2
    assert {c['clauseType'] for c in out} == {'Liability', 'Confidentiality'}
    # The '2.' prefix must not bleed into the first clause's summary.
    assert '2.' not in out[0]['summary']


def test_clauses_no_bold_markers():
    """Model drops the ** markers — fields must still parse."""
    md = (
        "Clause Type: Indemnification\n"
        "Extracted Clause: Provider indemnifies against IP claims.\n"
        "Summary: IP indemnity only.\n"
    )
    out = parse_key_clauses(md)
    assert len(out) == 1
    assert out[0]['clauseType'] == 'Indemnification'
    assert 'IP claims' in out[0]['extractedClause']


def test_clauses_multiline_extracted_preserved():
    md = (
        "- **Clause Type**: Data Protection\n"
        "- **Extracted Clause**: Provider complies with GDPR.\n"
        "  This obligation spans all regions and survives termination.\n"
        "- **Summary**: GDPR compliance.\n"
    )
    out = parse_key_clauses(md)
    assert len(out) == 1
    assert 'survives termination' in out[0]['extractedClause']


def test_clauses_section_extracted():
    md = (
        "- **Clause Type**: Liability\n"
        "- **Extracted Clause**: Per Section 8.1 liability is capped.\n"
        "- **Summary**: Cap.\n"
    )
    out = parse_key_clauses(md)
    assert out[0]['section'] == 'Section 8.1'


def test_clauses_fallback_on_garbage():
    out = parse_key_clauses("totally unstructured blob with no fields")
    assert len(out) == 1
    assert out[0]['clauseType'] == 'General'


def test_clauses_empty_input():
    assert parse_key_clauses('') == []


# --------------------------------------------------------------------------- #
# parse_risk_assessment
# --------------------------------------------------------------------------- #

def test_risks_wellformed_and_scored():
    md = (
        "- **Risk Type**: Legal\n"
        "- **Clause Reference**: Section 8.1\n"
        "- **Risk Level**: High\n"
        "- **Likelihood**: Likely\n"
        "- **Potential Consequence**: Unrecoverable losses.\n"
    )
    out = parse_risk_assessment(md)
    assert len(out) == 1
    r = out[0]
    assert r['riskType'] == 'Legal'
    assert r['riskLevel'] == 'High'
    assert r['likelihood'] == 'Likely'
    assert r['score'] == min(10, round(8 * 1.1))  # 9


def test_risks_numbered_list_not_collapsed():
    md = (
        "1. **Risk Type**: Legal\n"
        "   **Risk Level**: High\n"
        "   **Potential Consequence**: A.\n"
        "2. **Risk Type**: Financial\n"
        "   **Risk Level**: Medium\n"
        "   **Potential Consequence**: B.\n"
    )
    out = parse_risk_assessment(md)
    assert len(out) == 2
    assert {r['riskType'] for r in out} == {'Legal', 'Financial'}


def test_risks_prose_qualified_level_normalized():
    """'CRITICAL' / 'very high' must not silently default to Medium."""
    md = (
        "- **Risk Type**: Compliance\n"
        "- **Risk Level**: Critical (regulatory exposure)\n"
        "- **Likelihood**: almost certain\n"
        "- **Potential Consequence**: Fines.\n"
    )
    out = parse_risk_assessment(md)
    assert out[0]['riskLevel'] == 'High'      # critical -> High band
    assert out[0]['likelihood'] == 'Likely'   # almost certain -> Likely
    assert out[0]['score'] >= 8


def test_risks_unlikely_not_misread_as_likely():
    md = (
        "- **Risk Type**: Reputational\n"
        "- **Risk Level**: Low\n"
        "- **Likelihood**: Unlikely\n"
        "- **Potential Consequence**: Minor.\n"
    )
    out = parse_risk_assessment(md)
    assert out[0]['likelihood'] == 'Unlikely'


# --------------------------------------------------------------------------- #
# parse_recommended_actions
# --------------------------------------------------------------------------- #

def test_actions_bulleted_two_blocks():
    md = (
        "- **Clause**: Section 8.1\n"
        "- **Risk Level**: High\n"
        "- **Recommended Action**: Negotiate a higher cap.\n"
        "\n"
        "- **Clause**: Section 9.2\n"
        "- **Risk Level**: Medium\n"
        "- **Recommended Action**: Add deletion terms.\n"
    )
    out = parse_recommended_actions(md)
    assert len(out) == 2
    assert out[0]['priority'] == 'Urgent'        # High -> Urgent
    assert out[0]['category'] == 'Negotiation'   # "negotiate" keyword
    assert out[0]['clause'] == 'Section 8.1'
    assert out[1]['clause'] == 'Section 9.2'


def test_actions_numbered_not_collapsed():
    md = (
        "1. **Clause**: A\n"
        "   **Recommended Action**: Negotiate cap.\n"
        "2. **Clause**: B\n"
        "   **Recommended Action**: Audit vendor.\n"
    )
    out = parse_recommended_actions(md)
    assert len(out) == 2
    assert out[0]['category'] == 'Negotiation'
    assert out[1]['category'] == 'Compliance'


# --------------------------------------------------------------------------- #
# canonicalizers & scoring
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize('raw,expected', [
    ('High', 'High'), ('HIGH RISK', 'High'), ('critical', 'High'),
    ('very high', 'High'), ('Low', 'Low'), ('negligible', 'Low'),
    ('moderate', 'Medium'), ('', 'Medium'), (None, 'Medium'),
    ('nonsense', 'Medium'),
])
def test_canon_level(raw, expected):
    assert _canon_level(raw) == expected


@pytest.mark.parametrize('raw,expected', [
    ('Likely', 'Likely'), ('probable', 'Likely'), ('almost certain', 'Likely'),
    ('Unlikely', 'Unlikely'), ('rare', 'Unlikely'),
    ('Possible', 'Possible'), ('', 'Possible'), (None, 'Possible'),
])
def test_canon_likelihood(raw, expected):
    assert _canon_likelihood(raw) == expected


def test_compute_risk_score_empty_returns_float_zero():
    assert compute_risk_score([]) == 0


def test_compute_risk_score_average():
    risks = [{'score': 8}, {'score': 4}]
    assert compute_risk_score(risks) == 6.0


# --------------------------------------------------------------------------- #
# generate_dashboard_html — security & robustness
# --------------------------------------------------------------------------- #

def _extract_contract_data(html):
    """Pull the injected data back out of the JSON data island."""
    m = re.search(
        r'<script type="application/json" id="contract-data">\n(.*?)\n</script>',
        html, re.DOTALL,
    )
    assert m, "contract-data island not found in generated HTML"
    # The island holds valid JSON (dangerous chars are \\uXXXX escapes), so json
    # parses it directly, exactly as the browser's JSON.parse would.
    return json.loads(m.group(1))


def test_script_breakout_is_neutralized():
    """A </script> in contract text must NOT appear verbatim in the output."""
    state = {
        'key_clauses': (
            "- **Clause Type**: Liability\n"
            "- **Extracted Clause**: </script><script>alert(1)</script>\n"
            "- **Summary**: malicious.\n"
        ),
        'risk_assessment_report': '',
        'recommended_actions': '',
    }
    html = generate_dashboard_html(state)
    # The raw breakout sequence must not be present...
    assert '</script><script>alert(1)' not in html
    # ...but the escaped form must be, and must round-trip back to the original.
    data = _extract_contract_data(html)
    assert data['keyClauses'][0]['extractedClause'] == '</script><script>alert(1)</script>'


def test_backslashes_in_contract_text_roundtrip():
    """Backslashes / regex metachars must survive the re.sub injection."""
    state = {
        'key_clauses': (
            "- **Clause Type**: Liability\n"
            "- **Extracted Clause**: Path C:\\Users\\x and group \\1 and \\g<0>.\n"
            "- **Summary**: backslashes.\n"
        ),
        'risk_assessment_report': '',
        'recommended_actions': '',
    }
    html = generate_dashboard_html(state)
    data = _extract_contract_data(html)
    assert data['keyClauses'][0]['extractedClause'] == 'Path C:\\Users\\x and group \\1 and \\g<0>.'


def test_generated_html_has_single_contract_data_block():
    state = {'key_clauses': '', 'risk_assessment_report': '', 'recommended_actions': ''}
    html = generate_dashboard_html(state)
    assert html.count('id="contract-data"') == 1


# --------------------------------------------------------------------------- #
# metadata & timeline
# --------------------------------------------------------------------------- #

def test_build_metadata_from_pipeline_dict():
    state = {'metadata': {
        'title': 'SaaS Agreement',
        'parties': ['Acme Corp', 'CloudServe Inc.'],
        'effectiveDate': '2025-01-15',
        'expirationDate': '2027-01-14',
        'contractValue': '$480,000',
        'jurisdiction': 'Delaware',
    }}
    m = _build_metadata(state)
    assert m['title'] == 'SaaS Agreement'
    assert m['parties'] == ['Acme Corp', 'CloudServe Inc.']
    assert m['contractValue'] == '$480,000'
    assert m['jurisdiction'] == 'Delaware'


def test_build_metadata_tolerates_key_casing_and_synonyms():
    state = {'metadata': {
        'Title': 'X', 'Effective_Date': '2025-01-01', 'end date': '2026-01-01',
        'governing_law': 'New York', 'value': '$1M',
    }}
    m = _build_metadata(state)
    assert m['effectiveDate'] == '2025-01-01'
    assert m['expirationDate'] == '2026-01-01'
    assert m['jurisdiction'] == 'New York'
    assert m['contractValue'] == '$1M'


def test_build_metadata_parties_string_split():
    m = _build_metadata({'metadata': {'parties': 'Acme Corp and CloudServe Inc.'}})
    assert m['parties'] == ['Acme Corp', 'CloudServe Inc.']


def test_build_metadata_defaults_when_absent():
    m = _build_metadata({})
    assert m['title'] == 'Contract Analysis Results'
    assert m['parties'] == []
    assert m['contractValue'] == 'N/A'
    assert m['expirationDate'] == ''


@pytest.mark.parametrize('s,ok', [
    ('2025-01-15', True), ('01/15/2025', True), ('January 15, 2025', True),
    ('', False), ('not a date', False), (None, False),
])
def test_parse_date(s, ok):
    assert (_parse_date(s) is not None) == ok


def test_build_timeline_full():
    m = {'effectiveDate': '2025-01-01', 'expirationDate': '2026-01-01'}
    tl = _build_timeline(m)
    names = [e['event'] for e in tl]
    assert 'Contract Effective' in names
    assert 'Contract Expiration' in names
    assert 'Mid-term Review' in names
    # sorted ascending by date
    dates = [e['date'] for e in tl]
    assert dates == sorted(dates)


def test_build_timeline_empty_without_dates():
    assert _build_timeline({'effectiveDate': '', 'expirationDate': ''}) == []


def test_generate_html_populates_metadata_and_timeline():
    state = {
        'key_clauses': '- **Clause Type**: Liability\n- **Extracted Clause**: x.\n- **Summary**: y.\n',
        'risk_assessment_report': '',
        'recommended_actions': '',
        'metadata': {
            'title': 'Vendor MSA', 'parties': ['A Inc', 'B LLC'],
            'effectiveDate': '2025-03-01', 'expirationDate': '2026-03-01',
            'contractValue': '$250,000', 'jurisdiction': 'California',
        },
    }
    html = generate_dashboard_html(state)
    data = _extract_contract_data(html)
    assert data['metadata']['title'] == 'Vendor MSA'
    assert data['metadata']['parties'] == ['A Inc', 'B LLC']
    assert data['metadata']['contractValue'] == '$250,000'
    assert len(data['timeline']) >= 2  # effective + expiration at minimum


# --------------------------------------------------------------------------- #
# structured (JSON) path — preferred over markdown parsing
# --------------------------------------------------------------------------- #

def test_structured_clauses_preferred_over_markdown():
    state = {
        'key_clauses': '- **Clause Type**: SHOULD_NOT_APPEAR\n- **Extracted Clause**: x.\n- **Summary**: y.\n',
        'key_clauses_data': [
            {'clauseType': 'Liability', 'section': 'Section 8.1',
             'extractedClause': 'Capped at fees.', 'summary': 'Cap.', 'riskImpact': 'High'},
            {'clauseType': 'Termination', 'extractedClause': '30 days.', 'summary': 'Notice.'},
        ],
        'risk_assessment_report': '', 'recommended_actions': '',
    }
    html = generate_dashboard_html(state)
    data = _extract_contract_data(html)
    assert [c['clauseType'] for c in data['keyClauses']] == ['Liability', 'Termination']
    assert 'SHOULD_NOT_APPEAR' not in html          # markdown path NOT used
    assert data['keyClauses'][0]['id'] == 'KC-001'


def test_structured_risks_normalized_and_scored():
    state = {
        'key_clauses': '', 'recommended_actions': '',
        'risk_assessment_data': [
            {'riskType': 'Privacy', 'riskLevel': 'very high', 'likelihood': 'almost certain',
             'potentialConsequence': 'Fines.', 'clauseReference': 'Section 6.1'},
        ],
    }
    data = _extract_contract_data(generate_dashboard_html(state))
    r = data['riskAssessment'][0]
    assert r['riskType'] == 'Privacy'
    assert r['riskLevel'] == 'High' and r['likelihood'] == 'Likely'   # normalized
    assert r['score'] >= 8


def test_structured_actions_key_casing_tolerant():
    state = {
        'key_clauses': '', 'risk_assessment_report': '',
        'recommended_actions_data': [
            {'Clause': 'Section 8.1', 'Risk_Level': 'High', 'Recommended Action': 'Negotiate the cap.'},
        ],
    }
    data = _extract_contract_data(generate_dashboard_html(state))
    a = data['recommendedActions'][0]
    assert a['clause'] == 'Section 8.1'
    assert a['priority'] == 'Urgent'        # derived from High
    assert a['category'] == 'Negotiation'   # derived from text


def test_legacy_risk_assessment_list_field_consumed():
    """The previously-unused State['risk_assessment'] is honored if it's a list."""
    state = {
        'key_clauses': '', 'recommended_actions': '',
        'risk_assessment': [
            {'riskType': 'Legal', 'riskLevel': 'High', 'likelihood': 'Possible',
             'potentialConsequence': 'X', 'clauseReference': 'S1'},
        ],
    }
    data = _extract_contract_data(generate_dashboard_html(state))
    assert len(data['riskAssessment']) == 1
    assert data['riskAssessment'][0]['riskType'] == 'Legal'


def test_structured_path_still_escapes_script_breakout():
    """Security: the </script> escape must apply on the JSON path too."""
    state = {
        'key_clauses': '', 'risk_assessment_report': '', 'recommended_actions': '',
        'key_clauses_data': [
            {'clauseType': 'Liability', 'extractedClause': '</script><script>alert(1)</script>',
             'summary': 's'},
        ],
    }
    html = generate_dashboard_html(state)
    assert '</script><script>alert(1)' not in html
    data = _extract_contract_data(html)
    assert data['keyClauses'][0]['extractedClause'] == '</script><script>alert(1)</script>'


def test_empty_structured_list_falls_back_to_markdown():
    state = {
        'key_clauses_data': [],   # empty -> ignored, markdown used
        'key_clauses': '- **Clause Type**: Liability\n- **Extracted Clause**: x.\n- **Summary**: y.\n',
        'risk_assessment_report': '', 'recommended_actions': '',
    }
    data = _extract_contract_data(generate_dashboard_html(state))
    assert data['keyClauses'][0]['clauseType'] == 'Liability'


def test_structured_lists_with_no_usable_items_fall_back_to_markdown():
    state = {
        'key_clauses_data': [{'clauseType': 'Liability', 'extractedClause': '', 'summary': ''}],
        'key_clauses': (
            '- **Clause Type**: Liability\n'
            '- **Extracted Clause**: Liability is capped.\n'
            '- **Summary**: Cap language.\n'
        ),
        'risk_assessment_data': [
            {
                'riskType': 'Legal',
                'riskLevel': 'High',
                'likelihood': 'Likely',
                'potentialConsequence': '',
                'clauseReference': '',
            },
        ],
        'risk_assessment_report': (
            '- **Risk Type**: Legal\n'
            '- **Clause Reference**: Section 8.1\n'
            '- **Risk Level**: High\n'
            '- **Likelihood**: Likely\n'
            '- **Potential Consequence**: Unrecoverable losses.\n'
        ),
        'recommended_actions_data': [{'Clause': '', 'Recommended Action': ''}],
        'recommended_actions': (
            '- **Clause**: Section 8.1\n'
            '- **Risk Level**: High\n'
            '- **Recommended Action**: Negotiate a higher cap.\n'
        ),
    }
    data = _extract_contract_data(generate_dashboard_html(state))
    assert len(data['keyClauses']) == 1
    assert data['keyClauses'][0]['extractedClause'] == 'Liability is capped.'
    assert len(data['riskAssessment']) == 1
    assert data['riskAssessment'][0]['clauseReference'] == 'Section 8.1'
    assert data['overallRiskScore'] > 0
    assert len(data['recommendedActions']) == 1
    assert data['recommendedActions'][0]['action'] == 'Negotiate a higher cap.'


def test_end_to_end_realistic_state():
    state = {
        'key_clauses': (
            "1. Clause Type: Liability\n"
            "   Extracted Clause: Capped at 12 months fees.\n"
            "   Summary: Caps liability.\n"
            "2. Clause Type: Termination\n"
            "   Extracted Clause: 30 days notice.\n"
            "   Summary: Notice.\n"
        ),
        'risk_assessment_report': (
            "1. Risk Type: Legal\n"
            "   Risk Level: very high\n"
            "   Likelihood: probable\n"
            "   Potential Consequence: Exposure.\n"
        ),
        'recommended_actions': (
            "1. Clause: Section 8.1\n"
            "   Risk Level: High\n"
            "   Recommended Action: Negotiate a higher cap.\n"
        ),
    }
    html = generate_dashboard_html(state)
    data = _extract_contract_data(html)
    assert len(data['keyClauses']) == 2          # numbered list survived
    assert data['riskAssessment'][0]['riskLevel'] == 'High'   # 'very high' normalized
    assert len(data['recommendedActions']) == 1
    assert data['overallRiskScore'] > 0
