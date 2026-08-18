"""
Security tests for the HTML-script embedding in generate_dashboard.py.

Contract text is attacker-influenceable (it comes from a parsed PDF), so when it
is embedded inside the dashboard's inline <script> block it must not be able to
break out of the script context. These tests assert the OWASP/Flask
``htmlsafe_json_dumps`` escaping (<, >, &, ', U+2028, U+2029) is applied and that
every value still round-trips back to its original text via JSON.parse/json.loads.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generate_dashboard import generate_dashboard_html  # noqa: E402


def _data_block(html):
    m = re.search(r'const CONTRACT_DATA = (\{.*?\n\});', html, re.DOTALL)
    assert m, "CONTRACT_DATA block not found in generated HTML"
    return m.group(1)


def _contract_data(html):
    # The block is valid JSON (dangerous chars are \uXXXX escapes), so json can
    # parse it directly, exactly as the browser's JSON.parse would.
    return json.loads(_data_block(html))


DANGEROUS = "</script><script>alert(1)</script> <b>tag</b> & ' \" \u2028 \u2029 <!-- c -->"


def test_script_breakout_sequences_absent_raw():
    state = {
        'key_clauses': (
            "- **Clause Type**: Liability\n"
            "- **Extracted Clause**: " + DANGEROUS + "\n"
            "- **Summary**: s.\n"
        ),
        'risk_assessment_report': '',
        'recommended_actions': '',
    }
    html = generate_dashboard_html(state)
    assert '</script><script>' not in html
    assert '<script>alert(1)' not in html
    # ...but the value round-trips intact.
    data = _contract_data(html)
    assert data['keyClauses'][0]['extractedClause'] == DANGEROUS


def test_all_dangerous_chars_escaped_in_data_block():
    state = {
        'key_clauses': '', 'risk_assessment_report': '', 'recommended_actions': '',
        'key_clauses_data': [
            {'clauseType': 'X', 'extractedClause': "</script> <a> & ' end", 'summary': 's'},
        ],
    }
    html = generate_dashboard_html(state)
    block = _data_block(html)
    # No raw angle brackets / ampersand / apostrophe from contract text remain.
    assert '</script>' not in block
    assert '<a>' not in block
    # Escaped forms are present instead.
    assert '\\u003c/script\\u003e' in block
    assert '\\u0026' in block
    assert '\\u0027' in block
    data = json.loads(block)
    assert data['keyClauses'][0]['extractedClause'] == "</script> <a> & ' end"


def test_line_and_paragraph_separators_escaped():
    state = {
        'key_clauses': '', 'risk_assessment_report': '', 'recommended_actions': '',
        'key_clauses_data': [
            {'clauseType': 'X', 'extractedClause': 'a\u2028b\u2029c', 'summary': 's'},
        ],
    }
    html = generate_dashboard_html(state)
    assert '\u2028' not in html and '\u2029' not in html
    assert generate_dashboard_html(state) and _contract_data(html)['keyClauses'][0]['extractedClause'] == 'a\u2028b\u2029c'


def test_backslashes_and_regex_metachars_roundtrip():
    state = {
        'key_clauses': '', 'risk_assessment_report': '', 'recommended_actions': '',
        'key_clauses_data': [
            {'clauseType': 'X', 'extractedClause': r'Path C:\Users\x and \1 and \g<0>', 'summary': 's'},
        ],
    }
    data = _contract_data(generate_dashboard_html(state))
    assert data['keyClauses'][0]['extractedClause'] == r'Path C:\Users\x and \1 and \g<0>'


def test_single_quote_roundtrips():
    state = {
        'key_clauses': '', 'risk_assessment_report': '', 'recommended_actions': '',
        'key_clauses_data': [
            {'clauseType': 'X', 'extractedClause': "O'Brien's liability clause", 'summary': 's'},
        ],
    }
    data = _contract_data(generate_dashboard_html(state))
    assert data['keyClauses'][0]['extractedClause'] == "O'Brien's liability clause"


def test_exactly_one_contract_data_block_after_escaping():
    state = {'key_clauses': '', 'risk_assessment_report': '', 'recommended_actions': ''}
    html = generate_dashboard_html(state)
    assert html.count('const CONTRACT_DATA = {') == 1
