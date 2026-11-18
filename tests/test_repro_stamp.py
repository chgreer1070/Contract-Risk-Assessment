"""Dashboard header reproducibility stamp (CRA-01 In-5 / AC6).

Every dashboard build shows the playbook version, scorer version, and
golden-set manifest hash in the header. Those are the same three values the
score audit record stores, including ``unavailable`` when the cases directory
is missing. The line is escaped static HTML. The hashed inline script and the
style block stay byte-for-byte the same (AC7).
"""

import base64
import hashlib
import html
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import generate_dashboard as gd  # noqa: E402
from generate_dashboard import (  # noqa: E402
    AuditLog,
    build_contract_data,
    format_reproducibility_stamp,
    generate_dashboard_html,
    golden_manifest_hash,
    inject_reproducibility_stamp,
    reproducibility_fields,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML_PATH = os.path.join(ROOT, 'contract_visualization.html')
CONTRACT = os.path.join(ROOT, 'golden', 'sample', 'acme_widget_supply.txt')
STATE_PATH = os.path.join(ROOT, 'golden', 'sample', 'acme_widget_supply_state.json')
TIMESTAMP = '2026-11-16T00:00:00Z'
# AC7: this is the script hash before and after the header stamp.
CSP_HASH = 'sha384-0DBm+e7srqe4yFAAG0iPj5krWxrZvvJwJGK8uHUsb5QLnWKA2AjBirmrZuWTwVT6'


def _load(path):
    with open(path, encoding='utf-8') as handle:
        return json.load(handle)


def _header(page):
    start = page.find('<header')
    end = page.find('</header>', start)
    assert start != -1 and end != -1
    return page[start:end]


def _stamp(page):
    match = re.search(
        r'<p id="reproStamp" class="subtitle">(.*?)</p>',
        _header(page),
        re.DOTALL,
    )
    assert match, 'reproducibility stamp is missing from the header'
    return match.group(1)


def _bounded(page, open_tag, close_tag):
    start = page.find(open_tag)
    end = page.find(close_tag, start)
    assert start != -1 and end != -1
    return page[start:end + len(close_tag)]


def _code_script(page):
    """Exact text of the bare code <script> element (what the CSP hash covers)."""
    open_tag = '<script>'
    start = page.find(open_tag)
    assert start != -1, 'bare code <script> not found'
    start += len(open_tag)
    end = page.find('</script>', start)
    assert end != -1, 'closing </script> not found'
    return page[start:end]


def _synthetic_state():
    return {
        'contract_type': 'default',
        'risk_assessment_data': [{
            'riskType': 'Legal',
            'clauseReference': 'Section 2',
            'riskLevel': 'Medium',
            'likelihood': 'Possible',
            'potentialConsequence': 'Synthetic loss on a made-up contract.',
        }],
    }


def test_stamp_text_escapes_markup():
    text = format_reproducibility_stamp({
        'playbookVersion': '</p><script>alert(1)</script>',
        'scorerVersion': "a&b'c",
        'goldenManifestHash': '"hash"',
    })
    assert '<script>' not in text
    assert '</p>' not in text
    assert '&lt;/p&gt;&lt;script&gt;alert(1)&lt;/script&gt;' in text
    assert 'a&amp;b&#x27;c' in text
    assert '&quot;hash&quot;' in text
    assert text.startswith('Playbook version: ')
    assert 'Scorer version: ' in text
    assert text.endswith('Golden-set manifest hash: &quot;hash&quot;.')


def test_inject_requires_the_header_element():
    fields = reproducibility_fields({})
    with pytest.raises(ValueError, match='reproducibility stamp'):
        inject_reproducibility_stamp('<header><p>no stamp</p></header>', fields)
    # A copy of the stamp outside the header does not count.
    outside = '<p id="reproStamp" class="subtitle">stale</p><header></header>'
    with pytest.raises(ValueError, match='reproducibility stamp'):
        inject_reproducibility_stamp(outside, fields)
    with pytest.raises(ValueError, match='reproducibility stamp'):
        inject_reproducibility_stamp('<p id="reproStamp" class="subtitle">x</p>', fields)


def test_shipped_header_matches_the_audit_values():
    page = open(HTML_PATH, encoding='utf-8').read()
    embedded = re.search(r'"playbookVersion":\s*"([^"]+)"', page)
    assert embedded
    playbook_version = gd.load_playbook('default').get('version')
    assert embedded.group(1) == playbook_version
    fields = reproducibility_fields({'playbookVersion': playbook_version})
    assert _stamp(page) == format_reproducibility_stamp(fields)
    assert fields['scorerVersion'] == gd.SCORER_VERSION
    assert fields['goldenManifestHash'] == golden_manifest_hash()
    assert CSP_HASH in page


def test_sample_header_shows_the_audit_record_values(tmp_path):
    audit_path = tmp_path / 'acme.score-audit.jsonl'
    page = generate_dashboard_html(
        _load(STATE_PATH),
        input_path=CONTRACT,
        timestamp=TIMESTAMP,
        audit_path=str(audit_path),
    )
    records = AuditLog.load(str(audit_path)).records()
    assert records
    first = records[0]
    stamp = _stamp(page)
    assert stamp == format_reproducibility_stamp({
        'playbookVersion': first['playbookVersion'],
        'scorerVersion': first['scorerVersion'],
        'goldenManifestHash': first['goldenManifestHash'],
    })
    assert html.escape(first['playbookVersion'], quote=True) in stamp
    assert html.escape(first['scorerVersion'], quote=True) in stamp
    assert html.escape(first['goldenManifestHash'], quote=True) in stamp
    for record in records:
        assert record['playbookVersion'] == first['playbookVersion']
        assert record['scorerVersion'] == first['scorerVersion']
        assert record['goldenManifestHash'] == first['goldenManifestHash']
    assert 'goldenManifestHash' not in page
    assert 'inputFileSha256' not in page
    assert 'AR-001' not in page


def test_missing_manifest_shows_unavailable_in_the_header(tmp_path):
    contract = tmp_path / 'synthetic.txt'
    contract.write_text('Synthetic contract. No golden cases shipped.\n', encoding='utf-8')
    missing = tmp_path / 'missing-cases'
    audit_path = tmp_path / 'dash.score-audit.jsonl'
    page = generate_dashboard_html(
        _synthetic_state(),
        input_path=str(contract),
        timestamp=TIMESTAMP,
        audit_path=str(audit_path),
        cases_dir=str(missing),
    )
    record = AuditLog.load(str(audit_path)).records()[0]
    assert record['goldenManifestHash'] == 'unavailable'
    stamp = _stamp(page)
    assert stamp == format_reproducibility_stamp({
        'playbookVersion': record['playbookVersion'],
        'scorerVersion': record['scorerVersion'],
        'goldenManifestHash': record['goldenManifestHash'],
    })
    assert 'Golden-set manifest hash: unavailable.' in stamp
    assert html.escape(record['playbookVersion'], quote=True) in stamp
    assert html.escape(record['scorerVersion'], quote=True) in stamp


def test_header_escapes_the_playbook_version_the_audit_stores(tmp_path, monkeypatch):
    monkeypatch.setattr(gd, 'load_playbook', lambda contract_type='default': {
        'version': 'v1 <safe> & "quoted"',
        'entries': [],
    })
    contract = tmp_path / 'synthetic.txt'
    contract.write_text('Synthetic only.\n', encoding='utf-8')
    audit_path = tmp_path / 'dash.score-audit.jsonl'
    page = generate_dashboard_html(
        _synthetic_state(),
        input_path=str(contract),
        timestamp=TIMESTAMP,
        audit_path=str(audit_path),
        cases_dir=str(tmp_path / 'missing-cases'),
    )
    record = AuditLog.load(str(audit_path)).records()[0]
    assert record['playbookVersion'] == 'v1 <safe> & "quoted"'
    assert record['goldenManifestHash'] == 'unavailable'
    stamp = _stamp(page)
    assert record['playbookVersion'] not in stamp
    assert html.escape(record['playbookVersion'], quote=True) in stamp
    assert html.escape(record['scorerVersion'], quote=True) in stamp
    assert 'Golden-set manifest hash: unavailable.' in stamp
    assert '<script>alert' not in page
    assert '&lt;safe&gt;' in stamp


def test_empty_assessment_still_stamps_the_header():
    state = {'key_clauses': '', 'risk_assessment_report': '', 'recommended_actions': ''}
    page = generate_dashboard_html(state)
    fields = reproducibility_fields(build_contract_data(state))
    assert _stamp(page) == format_reproducibility_stamp(fields)
    assert fields['playbookVersion']
    assert fields['scorerVersion'] == gd.SCORER_VERSION
    assert fields['goldenManifestHash'] == golden_manifest_hash()


def test_stamp_does_not_change_the_csp_script_or_style():
    template = open(HTML_PATH, encoding='utf-8').read()
    present = generate_dashboard_html(_synthetic_state())
    missing = generate_dashboard_html(
        _synthetic_state(),
        cases_dir=os.path.join(ROOT, 'no-such-golden-cases'),
    )
    assert _bounded(template, '<style>', '</style>') == _bounded(present, '<style>', '</style>')
    assert _code_script(template) == _code_script(present) == _code_script(missing)
    digest = hashlib.sha384(_code_script(present).encode('utf-8')).digest()
    assert 'sha384-' + base64.b64encode(digest).decode('ascii') == CSP_HASH
    assert CSP_HASH in present
    assert _stamp(present) != _stamp(missing)
    assert 'Golden-set manifest hash: unavailable.' in _stamp(missing)
