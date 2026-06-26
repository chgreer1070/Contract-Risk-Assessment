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
    parse_key_clauses,
    parse_risk_assessment,
    parse_recommended_actions,
    compute_risk_score,
    generate_dashboard_html,
    _canon_level,
    _canon_likelihood,
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
    """Pull the injected CONTRACT_DATA JSON object back out of the HTML."""
    m = re.search(r'const CONTRACT_DATA = (\{.*?\n\});', html, re.DOTALL)
    assert m, "CONTRACT_DATA block not found in generated HTML"
    # Undo the HTML-script escaping so json can parse it.
    raw = m.group(1).replace('<\\/', '</')
    return json.loads(raw)


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
    assert html.count('const CONTRACT_DATA = {') == 1


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
