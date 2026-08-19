"""Tests for the configurable playbook layer in generate_dashboard.py."""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import generate_dashboard as gd  # noqa: E402
from generate_dashboard import (  # noqa: E402
    _apply_playbook,
    _band_for,
    generate_dashboard_html,
    load_playbook,
    match_playbook_entry,
)


def test_load_playbook_default_has_entries():
    pb = load_playbook('default')
    assert pb['version'] and isinstance(pb['entries'], list) and pb['entries']


def test_load_playbook_unknown_falls_back_to_default():
    assert load_playbook('does-not-exist')['version'] == load_playbook('default')['version']


def test_load_playbook_builtin_when_files_missing(monkeypatch):
    monkeypatch.setattr(gd.os.path, 'isfile', lambda p: False)
    pb = load_playbook('default')
    assert pb['version'] == 'builtin'
    assert pb['entries'] == []


@pytest.mark.parametrize('score,expected', [
    (0, 'Accept'), (3, 'Accept'), (4, 'Negotiate'), (6, 'Negotiate'),
    (7, 'Escalate'), (8, 'Escalate'), (9, 'Walk'), (10, 'Walk'),
])
def test_band_for(score, expected):
    assert _band_for(score) == expected


def test_band_for_mandatory_escalation_floor():
    assert _band_for(2, True) == 'Escalate'   # Accept -> Escalate
    assert _band_for(5, True) == 'Escalate'   # Negotiate -> Escalate
    assert _band_for(9, True) == 'Walk'       # already above the floor


def test_match_playbook_entry():
    pb = load_playbook('default')
    hit = match_playbook_entry(
        {'riskType': 'Legal', 'clauseReference': 'Section 8.1 - Liability Cap'}, pb
    )
    assert hit['id'] == 'limitation_of_liability'
    assert match_playbook_entry({'riskType': 'Misc', 'clauseReference': 'Section 99'}, pb) is None


def test_apply_playbook_sets_ref_band_and_mandatory_escalation():
    pb = load_playbook('default')
    risks = [{
        'riskType': 'Compliance', 'clauseReference': 'Section 6.1 - Data Protection',
        'riskLevel': 'Medium', 'likelihood': 'Possible', 'score': 5,
    }]
    _apply_playbook(risks, pb)
    r = risks[0]
    assert r['playbookRef']['id'] == 'data_protection'
    assert r['mandatoryEscalation'] is True
    assert r['band'] == 'Escalate'  # forced up from Negotiate


def test_apply_playbook_no_match_leaves_null_ref():
    pb = load_playbook('default')
    risks = [{'riskType': 'Misc', 'clauseReference': 'Section 99', 'score': 8}]
    _apply_playbook(risks, pb)
    assert risks[0]['playbookRef'] is None
    assert risks[0]['band'] == 'Escalate'  # from score alone


def test_generated_data_exposes_playbook_version():
    html = generate_dashboard_html(
        {'key_clauses': '', 'risk_assessment_report': '', 'recommended_actions': ''}
    )
    assert re.search(r'"playbookVersion":\s*"[^"]+"', html)
