"""Append-only score audit records (CRA-01 In-3 / AC4).

Every scored risk in the sample build has exactly one score record, and that
record carries every In-3 field. A later override appends a second record and
leaves the first one unchanged. The sample contract file is only read.
"""

import hashlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import generate_dashboard as gd  # noqa: E402
from generate_dashboard import (  # noqa: E402
    AUDIT_FIELDS,
    AuditLog,
    build_contract_data,
    build_score_audit,
    file_sha256,
    generate_dashboard_html,
    golden_manifest_hash,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT = os.path.join(ROOT, 'golden', 'sample', 'acme_widget_supply.txt')
STATE_PATH = os.path.join(ROOT, 'golden', 'sample', 'acme_widget_supply_state.json')
FIXTURE = os.path.join(ROOT, 'golden', 'fixtures', 'acme_widget_supply_audit.json')
TIMESTAMP = '2026-11-16T00:00:00Z'


def _load(path):
    with open(path, encoding='utf-8') as handle:
        return json.load(handle)


def _sample_state():
    return _load(STATE_PATH)


def _sample_log():
    return build_score_audit(_sample_state(), CONTRACT, TIMESTAMP)


def _body(**overrides):
    body = {
        'event': 'score',
        'riskId': 'RK-001',
        'clauseReference': 'Section 1',
        'likelihood': 'Likely',
        'severity': 'High',
        'score': 9,
        'playbookVersion': '2026-08-01',
        'scorerVersion': 'scorer-3x3-1',
        'goldenManifestHash': 'a' * 64,
        'inputFileSha256': 'b' * 64,
        'timestamp': TIMESTAMP,
        'rule': {'id': 'RISK_MATRIX', 'evidence': 'RISK_MATRIX[High][Likely] = 9'},
    }
    body.update(overrides)
    return body


def test_sample_audit_fixture_matches_scored_risks():
    state = _sample_state()
    with open(CONTRACT, 'rb') as handle:
        before = handle.read()
    digest = hashlib.sha256(before).hexdigest()
    risks = build_contract_data(state)['riskAssessment']
    log = build_score_audit(state, CONTRACT, TIMESTAMP)
    with open(CONTRACT, 'rb') as handle:
        after = handle.read()
    fixture = _load(FIXTURE)
    records = log.records()

    assert after == before
    assert file_sha256(CONTRACT) == digest
    assert fixture['sampleContract'] == 'golden/sample/acme_widget_supply.txt'
    assert fixture['inputFileSha256'] == digest
    assert fixture['scoredRiskCount'] == len(risks)
    assert fixture['recordCount'] == len(risks)
    assert len(records) == len(risks)
    assert records == fixture['records']
    assert len(records) > 0

    risk_ids = [record['riskId'] for record in records]
    assert risk_ids == [f'RK-{index:03d}' for index in range(1, len(records) + 1)]
    assert len(set(risk_ids)) == len(risk_ids)
    for record, risk in zip(records, risks, strict=True):
        assert record['event'] == 'score'
        assert list(record) == list(AUDIT_FIELDS)
        assert record['clauseReference'] == risk['clauseReference']
        assert record['likelihood'] == risk['likelihood']
        assert record['severity'] == risk['riskLevel']
        assert record['score'] == risk['score']
        assert record['playbookVersion'] == '2026-08-01'
        assert record['scorerVersion'] == gd.SCORER_VERSION
        assert record['goldenManifestHash'] == golden_manifest_hash()
        assert record['inputFileSha256'] == digest
        assert record['timestamp'] == TIMESTAMP
        assert record['rule']['id'] == 'RISK_MATRIX'
        assert f"RISK_MATRIX[{record['severity']}][{record['likelihood']}] = {record['score']}" in (
            record['rule']['evidence']
        )
        section = record['clauseReference'].split(' - ')[0]
        assert section in before.decode('utf-8')


def test_sample_risks_are_not_written_into_the_dashboard():
    html = generate_dashboard_html(_sample_state())
    assert 'goldenManifestHash' not in html
    assert 'AR-001' not in html
    assert 'inputFileSha256' not in html


def test_empty_assessment_has_no_score_records(tmp_path):
    contract = tmp_path / 'empty-input.txt'
    contract.write_bytes(b'synthetic\n')
    state = {'key_clauses': '', 'risk_assessment_report': '', 'recommended_actions': ''}
    log = build_score_audit(state, str(contract), TIMESTAMP)
    assert build_contract_data(state)['riskAssessment'] == []
    assert log.records() == []


def test_manifest_hash_ignores_non_json_and_uses_file_bytes(tmp_path):
    cases = tmp_path / 'cases'
    cases.mkdir()
    (cases / 'b.json').write_bytes(b'{"id":"b"}\n')
    (cases / 'a.json').write_bytes(b'{"id":"a"}\n')
    (cases / 'notes.txt').write_text('ignore me', encoding='utf-8')
    first = golden_manifest_hash(str(cases))
    assert first == golden_manifest_hash(str(cases))
    (cases / 'a.json').write_bytes(b'{"id":"changed"}\n')
    assert golden_manifest_hash(str(cases)) != first
    with pytest.raises(FileNotFoundError):
        golden_manifest_hash(str(tmp_path / 'missing'))


def test_bad_timestamp_does_not_change_the_contract():
    with open(CONTRACT, 'rb') as handle:
        before = handle.read()
    with pytest.raises(ValueError, match='timestamp'):
        build_score_audit(_sample_state(), CONTRACT, 'now')
    with pytest.raises(ValueError, match='real UTC time'):
        build_score_audit(_sample_state(), CONTRACT, '2026-02-31T00:00:00Z')
    with open(CONTRACT, 'rb') as handle:
        assert handle.read() == before


def test_score_must_match_the_matrix():
    risk = {
        'riskLevel': 'High',
        'likelihood': 'Likely',
        'score': 1,
        'clauseReference': 'Section 8.1',
        'playbookRef': None,
    }
    with pytest.raises(ValueError, match='RISK_MATRIX'):
        gd._score_audit_body(1, risk, '2026-08-01', 'a' * 64, 'b' * 64, TIMESTAMP)


def test_returned_records_are_copies():
    log = _sample_log()
    returned = log.records()
    returned[0]['score'] = 0
    returned[0]['rule']['evidence'] = 'changed'
    assert log.records()[0]['score'] == 9
    assert log.records()[0]['rule']['evidence'] != 'changed'


def test_append_copies_the_caller_dict():
    log = AuditLog()
    payload = _body()
    stored = log.append(payload)
    payload['score'] = 0
    payload['rule']['evidence'] = 'changed'
    stored['score'] = 0
    assert log.records()[0]['score'] == 9
    assert log.records()[0]['rule']['evidence'] != 'changed'


def test_override_appends_a_second_record_without_changing_the_first():
    log = _sample_log()
    before = log.records()
    first = json.dumps(before[0], sort_keys=True)
    score = before[0]
    log.append({
        'event': 'override',
        'riskId': score['riskId'],
        'clauseReference': score['clauseReference'],
        'likelihood': 'Possible',
        'severity': 'Medium',
        'score': 5,
        'playbookVersion': score['playbookVersion'],
        'scorerVersion': score['scorerVersion'],
        'goldenManifestHash': score['goldenManifestHash'],
        'inputFileSha256': score['inputFileSha256'],
        'timestamp': '2026-11-16T00:00:01Z',
        'rule': {'id': 'human_override', 'evidence': 'Reviewer recorded a different score.'},
    })
    after = log.records()
    added = after[-1]
    assert json.dumps(after[0], sort_keys=True) == first
    assert after[0] == before[0]
    assert after[:-1] == before
    assert len(after) == len(before) + 1
    assert added['event'] == 'override'
    assert added['recordId'] == f'AR-{len(before) + 1:03d}'
    assert added['recordId'] != after[0]['recordId']
    assert added['riskId'] == after[0]['riskId']
    assert added['score'] == 5
    assert after[0]['score'] == before[0]['score']
    score_events = [record for record in after if record['event'] == 'score']
    assert len(score_events) == len(before)


def test_direct_score_must_match_the_matrix_cell():
    log = AuditLog()
    with pytest.raises(ValueError, match='does not match'):
        log.append(_body(score=1))
    with pytest.raises(ValueError, match='matrix cell'):
        log.append(_body(rule={'id': 'RISK_MATRIX', 'evidence': 'not the cell'}))
    assert log.records() == []


def test_hash_fields_must_be_strings_and_score_must_be_in_range():
    log = AuditLog()
    with pytest.raises(ValueError, match='inputFileSha256'):
        log.append(_body(inputFileSha256=10**63))
    with pytest.raises(ValueError, match='0 to 10'):
        log.append(_body(score=-1, rule={'id': 'RISK_MATRIX', 'evidence': 'x'}))
    assert log.records() == []


def test_symlink_input_is_not_hashed(tmp_path):
    target = tmp_path / 'real.txt'
    target.write_text('synthetic\n', encoding='utf-8')
    link = tmp_path / 'link.txt'
    link.symlink_to(target)
    with pytest.raises(OSError):
        file_sha256(str(link))
    assert target.read_bytes() == b'synthetic\n'


def test_second_score_for_the_same_risk_is_rejected():
    log = AuditLog()
    log.append(_body())
    with pytest.raises(ValueError, match='already exists'):
        log.append(_body())
    assert len(log.records()) == 1


def test_override_requires_an_existing_score_record():
    log = AuditLog()
    with pytest.raises(ValueError, match='override requires'):
        log.append(_body(event='override'))
    assert log.records() == []


def test_failed_append_does_not_add_a_record():
    log = AuditLog()
    log.append(_body())
    before = log.records()
    with pytest.raises(ValueError):
        log.append(_body(event='nope'))
    assert log.records() == before


@pytest.mark.parametrize('bad', [
    {'recordId': 'AR-999'},
    {'event': 'delete'},
    {'timestamp': '2026-11-16'},
    {'goldenManifestHash': 'abc'},
    {'inputFileSha256': 'B' * 64},
    {'score': True},
    {'score': 9.0},
    {'likelihood': '  '},
    {'clauseReference': None},
    {'rule': 'RISK_MATRIX'},
    {'rule': {'id': '', 'evidence': 'x'}},
    {'rule': {'id': 'RISK_MATRIX', 'evidence': '  '}},
])
def test_invalid_record_is_rejected(bad):
    log = AuditLog()
    payload = _body()
    if 'recordId' in bad:
        payload['recordId'] = bad['recordId']
    else:
        payload.update(bad)
    with pytest.raises((ValueError, TypeError)):
        log.append(payload)
    assert log.records() == []


def test_missing_field_names_the_gap():
    log = AuditLog()
    payload = _body()
    del payload['playbookVersion']
    with pytest.raises(ValueError, match='playbookVersion'):
        log.append(payload)


def test_append_requires_a_dict():
    with pytest.raises(TypeError):
        AuditLog().append(['not', 'a', 'dict'])  # type: ignore[arg-type]


def test_empty_clause_reference_is_still_one_record(tmp_path):
    contract = tmp_path / 'clause.txt'
    contract.write_text('synthetic clause file\n', encoding='utf-8')
    state = {
        'contract_type': 'default',
        'risk_assessment_data': [{
            'riskType': 'Legal',
            'clauseReference': '',
            'riskLevel': 'Low',
            'likelihood': 'Unlikely',
            'potentialConsequence': 'A scored risk with no clause reference.',
        }],
        'recommended_actions': '',
    }
    records = build_score_audit(state, str(contract), TIMESTAMP).records()
    assert len(records) == 1
    assert records[0]['clauseReference'] == ''
    assert records[0]['score'] == 2
