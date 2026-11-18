"""Append-only score audit records (CRA-01 In-3 / AC4 and In-4 / AC5).

Every scored risk in the sample build has exactly one score record, and that
record carries every In-3 field. A later human override appends its own record
and leaves the first one byte-for-byte unchanged. The sample contract file is
only read. Override cases below the sample fixture use synthetic data only.
"""

import hashlib
import json
import multiprocessing
import os
import re
import stat
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import generate_dashboard as gd  # noqa: E402
from generate_dashboard import (  # noqa: E402
    AUDIT_FIELDS,
    GOLDEN_MANIFEST_UNAVAILABLE,
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
# AC7: the dashboard page security hash stays on this value.
CSP_HASH = 'sha384-0DBm+e7srqe4yFAAG0iPj5krWxrZvvJwJGK8uHUsb5QLnWKA2AjBirmrZuWTwVT6'


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
    empty = tmp_path / 'empty'
    empty.mkdir()
    empty_hash = golden_manifest_hash(str(empty))
    assert empty_hash != GOLDEN_MANIFEST_UNAVAILABLE
    assert re.fullmatch(r'[0-9a-f]{64}', empty_hash)
    assert golden_manifest_hash(str(tmp_path / 'missing')) == GOLDEN_MANIFEST_UNAVAILABLE


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
        'reviewer': 'Synthetic Reviewer',
        'oldBand': 'Walk',
        'newBand': 'Negotiate',
        'rationale': 'Reviewer recorded a different score.',
        'originalRecordId': score['recordId'],
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
    source = {
        'recordId': 'AR-001',
        'riskId': 'RK-001',
        'clauseReference': 'Section 1',
        'score': 9,
        'playbookVersion': '2026-08-01',
        'scorerVersion': 'scorer-3x3-1',
        'goldenManifestHash': 'a' * 64,
        'inputFileSha256': 'b' * 64,
    }
    with pytest.raises(ValueError, match='override requires'):
        log.append(_override_from(source))
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


def _synthetic_state(clause='Section 2'):
    return {
        'contract_type': 'default',
        'risk_assessment_data': [{
            'riskType': 'Legal',
            'clauseReference': clause,
            'riskLevel': 'Medium',
            'likelihood': 'Possible',
            'potentialConsequence': 'Synthetic loss on a made-up contract.',
        }],
    }


def _override_from(source, **overrides):
    old_band = gd._band_for(source['score'])
    new_band = 'Accept' if old_band != 'Accept' else 'Negotiate'
    body = {
        'event': 'override',
        'riskId': source['riskId'],
        'clauseReference': source['clauseReference'],
        'likelihood': 'Possible',
        'severity': 'Medium',
        'score': 5,
        'playbookVersion': source['playbookVersion'],
        'scorerVersion': source['scorerVersion'],
        'goldenManifestHash': source['goldenManifestHash'],
        'inputFileSha256': source['inputFileSha256'],
        'timestamp': '2026-11-16T00:00:01Z',
        'reviewer': 'Synthetic Reviewer',
        'oldBand': old_band,
        'newBand': new_band,
        'rationale': 'Synthetic reviewer note.',
        'originalRecordId': source['recordId'],
        'rule': {'id': 'human_override', 'evidence': 'Synthetic reviewer note.'},
    }
    body.update(overrides)
    return body


def test_missing_golden_directory_marks_manifest_unavailable(tmp_path):
    """Notebook download has no golden/cases folder. Scoring still records."""
    contract = tmp_path / 'synthetic.txt'
    contract.write_text('Synthetic contract. No golden cases shipped.\n', encoding='utf-8')
    missing = tmp_path / 'golden' / 'cases'
    records = build_score_audit(
        _synthetic_state('Section 1'),
        str(contract),
        TIMESTAMP,
        cases_dir=str(missing),
    ).records()
    assert len(records) == 1
    assert records[0]['goldenManifestHash'] == GOLDEN_MANIFEST_UNAVAILABLE
    assert records[0]['inputFileSha256'] == hashlib.sha256(contract.read_bytes()).hexdigest()
    assert records[0]['clauseReference'] == 'Section 1'
    log = AuditLog()
    body = {key: value for key, value in records[0].items() if key != 'recordId'}
    log.append(body)
    log.append(_override_from(records[0]))
    assert [item['goldenManifestHash'] for item in log.records()] == [
        GOLDEN_MANIFEST_UNAVAILABLE,
        GOLDEN_MANIFEST_UNAVAILABLE,
    ]


def test_generate_dashboard_html_writes_audit_without_changing_html(tmp_path):
    contract = tmp_path / 'synthetic.txt'
    original = b'Section 2 synthetic liability text.\n'
    contract.write_bytes(original)
    state = _synthetic_state()
    missing = str(tmp_path / 'missing-golden')
    # The header stamp includes the manifest, so both pages must use the same
    # cases directory. Writing the audit file still does not change the HTML.
    plain = generate_dashboard_html(state, cases_dir=missing)
    audit_path = tmp_path / 'dash.score-audit.jsonl'
    html = generate_dashboard_html(
        state,
        input_path=str(contract),
        timestamp=TIMESTAMP,
        audit_path=str(audit_path),
        cases_dir=missing,
    )
    assert html == plain
    assert CSP_HASH in html
    assert 'goldenManifestHash' not in html
    assert 'AR-001' not in html
    assert contract.read_bytes() == original

    assert stat.S_IMODE(audit_path.stat().st_mode) == 0o600
    risks = build_contract_data(state)['riskAssessment']
    records = AuditLog.load(str(audit_path)).records()
    assert len(records) == len(risks)
    assert records[0]['event'] == 'score'
    assert records[0]['goldenManifestHash'] == GOLDEN_MANIFEST_UNAVAILABLE
    assert records[0]['inputFileSha256'] == hashlib.sha256(original).hexdigest()
    assert records[0]['clauseReference'] == risks[0]['clauseReference']
    assert records[0]['score'] == risks[0]['score']

    before = audit_path.read_bytes()
    again = generate_dashboard_html(
        state,
        input_path=str(contract),
        timestamp='2026-11-16T00:00:02Z',
        audit_path=str(audit_path),
        cases_dir=missing,
    )
    assert again == html
    assert audit_path.read_bytes() == before

    other = tmp_path / 'other.txt'
    other.write_text('A different synthetic contract.\n', encoding='utf-8')
    with pytest.raises(ValueError, match='different provenance'):
        generate_dashboard_html(
            state,
            input_path=str(other),
            timestamp=TIMESTAMP,
            audit_path=str(audit_path),
            cases_dir=missing,
        )
    assert audit_path.read_bytes() == before
    assert other.read_bytes() == b'A different synthetic contract.\n'

    log = AuditLog.load(str(audit_path))
    first_line = before.splitlines()[0]
    added = log.append(_override_from(
        log.records()[0],
        likelihood='Unlikely',
        severity='Medium',
        score=4,
    ))
    log.write_new(str(audit_path), 1)
    reloaded = AuditLog.load(str(audit_path)).records()
    assert audit_path.read_bytes().splitlines()[0] == first_line
    assert reloaded[0] == records[0]
    assert reloaded[1]['recordId'] == added['recordId']
    assert reloaded[1]['event'] == 'override'
    assert len(reloaded) == len(risks) + 1


def test_notebook_pdf_path_writes_a_sidecar_audit(tmp_path):
    contract = tmp_path / 'deal.txt'
    contract.write_text('Synthetic notebook upload.\n', encoding='utf-8')
    state = _synthetic_state('Section 9')
    state['pdf_path'] = str(contract)
    missing = str(tmp_path / 'no-golden')
    html = generate_dashboard_html(state, timestamp=TIMESTAMP, cases_dir=missing)
    sidecar = tmp_path / 'deal.score-audit.jsonl'
    records = AuditLog.load(str(sidecar)).records()
    assert len(records) == len(build_contract_data(state)['riskAssessment'])
    assert records[0]['goldenManifestHash'] == GOLDEN_MANIFEST_UNAVAILABLE
    assert CSP_HASH in html
    assert 'goldenManifestHash' not in html

    clocked = tmp_path / 'clock.score-audit.jsonl'
    generate_dashboard_html(
        _synthetic_state(),
        input_path=str(contract),
        audit_path=str(clocked),
        cases_dir=missing,
    )
    stamped = AuditLog.load(str(clocked)).records()[0]['timestamp']
    assert re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', stamped)

    absent = dict(state)
    absent['pdf_path'] = str(tmp_path / 'missing-upload.txt')
    before = sorted(path.name for path in tmp_path.glob('*.score-audit.jsonl'))
    assert generate_dashboard_html(absent) == generate_dashboard_html(_synthetic_state('Section 9'))
    assert sorted(path.name for path in tmp_path.glob('*.score-audit.jsonl')) == before


def test_audit_file_cannot_replace_the_contract_or_follow_a_symlink(tmp_path):
    contract = tmp_path / 'synthetic.txt'
    original = b'synthetic contract bytes\n'
    contract.write_bytes(original)
    state = _synthetic_state()
    missing = str(tmp_path / 'missing-golden')
    with pytest.raises(ValueError, match='different from the contract'):
        generate_dashboard_html(
            state,
            input_path=str(contract),
            timestamp=TIMESTAMP,
            audit_path=str(contract),
            cases_dir=missing,
        )
    assert contract.read_bytes() == original

    target = tmp_path / 'keep.txt'
    target.write_text('keep\n', encoding='utf-8')
    link = tmp_path / 'audit-link.jsonl'
    link.symlink_to(target)
    with pytest.raises(OSError):
        generate_dashboard_html(
            state,
            input_path=str(contract),
            timestamp=TIMESTAMP,
            audit_path=str(link),
            cases_dir=missing,
        )
    assert target.read_text(encoding='utf-8') == 'keep\n'
    assert contract.read_bytes() == original

    source = tmp_path / 'real-contract.txt'
    source.write_text('synthetic linked contract\n', encoding='utf-8')
    linked = tmp_path / 'linked.txt'
    linked.symlink_to(source)
    audit_path = tmp_path / 'linked.score-audit.jsonl'
    generate_dashboard_html(
        state,
        input_path=str(linked),
        timestamp=TIMESTAMP,
        audit_path=str(audit_path),
        cases_dir=missing,
    )
    stored = AuditLog.load(str(audit_path)).records()[0]
    assert stored['inputFileSha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert source.read_text(encoding='utf-8') == 'synthetic linked contract\n'
    with pytest.raises(OSError):
        file_sha256(str(linked))


def test_reopened_log_rejects_a_damaged_file(tmp_path):
    empty = tmp_path / 'empty.jsonl'
    empty.write_bytes(b'')
    assert AuditLog.load(str(empty)).records() == []

    bare = tmp_path / 'bare.jsonl'
    bare.write_text('{"recordId": "AR-001"}', encoding='utf-8')
    with pytest.raises(ValueError, match='newline-terminated'):
        AuditLog.load(str(bare))

    not_object = tmp_path / 'list.jsonl'
    not_object.write_text('[]\n', encoding='utf-8')
    with pytest.raises(ValueError, match='JSON object'):
        AuditLog.load(str(not_object))

    missing_id = tmp_path / 'no-id.jsonl'
    missing_id.write_text('{}\n', encoding='utf-8')
    with pytest.raises(ValueError, match='recordId'):
        AuditLog.load(str(missing_id))

    skipped = tmp_path / 'skipped.jsonl'
    body = _body()
    body['recordId'] = 'AR-002'
    skipped.write_text(json.dumps(body) + '\n', encoding='utf-8')
    with pytest.raises(ValueError, match='not sequential'):
        AuditLog.load(str(skipped))

    blank = tmp_path / 'blank.jsonl'
    blank.write_text('\n', encoding='utf-8')
    with pytest.raises(ValueError, match='blank line'):
        AuditLog.load(str(blank))

    log = AuditLog()
    with pytest.raises(ValueError, match='outside the log'):
        log.write_new(str(tmp_path / 'unused.jsonl'), 1)
    assert not (tmp_path / 'unused.jsonl').exists()


def test_clause_line_separators_round_trip_and_can_be_reopened(tmp_path):
    contract = tmp_path / 'synthetic.txt'
    contract.write_text('synthetic contract\n', encoding='utf-8')
    clause = 'Section 2\u2028\u2029\u0085 tail'
    state = _synthetic_state(clause)
    audit = tmp_path / 'dash.score-audit.jsonl'
    missing = str(tmp_path / 'missing-golden')
    generate_dashboard_html(
        state,
        input_path=str(contract),
        timestamp=TIMESTAMP,
        audit_path=str(audit),
        cases_dir=missing,
    )
    raw = audit.read_bytes()
    assert raw.count(b'\n') == 1
    assert b'\xe2\x80\xa8' not in raw
    assert b'\xe2\x80\xa9' not in raw
    assert b'\xc2\x85' not in raw
    loaded = AuditLog.load(str(audit)).records()
    assert loaded[0]['clauseReference'] == clause
    generate_dashboard_html(
        state,
        input_path=str(contract),
        timestamp='2026-11-16T00:00:09Z',
        audit_path=str(audit),
        cases_dir=missing,
    )
    assert AuditLog.load(str(audit)).records() == loaded


def test_a_changed_score_is_refused_and_the_audit_stays(tmp_path):
    contract = tmp_path / 'synthetic.txt'
    contract.write_text('synthetic contract\n', encoding='utf-8')
    audit = tmp_path / 'dash.score-audit.jsonl'
    missing = str(tmp_path / 'missing-golden')
    generate_dashboard_html(
        _synthetic_state(),
        input_path=str(contract),
        timestamp=TIMESTAMP,
        audit_path=str(audit),
        cases_dir=missing,
    )
    before = audit.read_bytes()
    changed = _synthetic_state()
    changed['risk_assessment_data'][0]['riskLevel'] = 'High'
    changed['risk_assessment_data'][0]['likelihood'] = 'Likely'
    with pytest.raises(ValueError, match='different score'):
        generate_dashboard_html(
            changed,
            input_path=str(contract),
            timestamp=TIMESTAMP,
            audit_path=str(audit),
            cases_dir=missing,
        )
    assert audit.read_bytes() == before


def test_a_failed_append_does_not_leave_a_partial_line(tmp_path, monkeypatch):
    contract = tmp_path / 'synthetic.txt'
    contract.write_text('synthetic contract\n', encoding='utf-8')
    audit = tmp_path / 'dash.score-audit.jsonl'
    generate_dashboard_html(
        _synthetic_state(),
        input_path=str(contract),
        timestamp=TIMESTAMP,
        audit_path=str(audit),
        cases_dir=str(tmp_path / 'missing-golden'),
    )
    before = audit.read_bytes()
    real_write = os.write

    def fail_after_one(fd, data):
        real_write(fd, data[:1])
        raise OSError('disk full')

    monkeypatch.setattr(os, 'write', fail_after_one)
    log = AuditLog.load(str(audit))
    log.append(_override_from(log.records()[0]))
    with pytest.raises(OSError, match='disk full'):
        log.write_new(str(audit), 1)
    assert audit.read_bytes() == before


def test_dashboard_audit_requires_a_real_contract_path(tmp_path):
    state = _synthetic_state()
    with pytest.raises(ValueError, match='input_path'):
        generate_dashboard_html(state, input_path='')
    broken = tmp_path / 'broken.txt'
    broken.symlink_to(tmp_path / 'missing-target.txt')
    with pytest.raises(OSError, match='regular file'):
        generate_dashboard_html(
            state,
            input_path=str(broken),
            timestamp=TIMESTAMP,
            audit_path=str(tmp_path / 'audit.jsonl'),
        )
    assert not (tmp_path / 'audit.jsonl').exists()


def _concurrent_dashboard_build(payload):
    """One dashboard build. A missing sidecar pauses so two builds overlap."""
    contract, audit, cases, barrier = payload
    real_exists = os.path.exists

    def slow_exists(path):
        found = real_exists(path)
        if path == audit and not found:
            time.sleep(0.3)
        return found

    os.path.exists = slow_exists
    try:
        barrier.wait(30)
        generate_dashboard_html(
            _synthetic_state(),
            input_path=contract,
            timestamp=TIMESTAMP,
            audit_path=audit,
            cases_dir=cases,
        )
    finally:
        os.path.exists = real_exists


def test_concurrent_builds_keep_one_score_record_per_risk(tmp_path):
    """Two builds of one sidecar must not reuse record ids."""
    contract = tmp_path / 'synthetic.txt'
    contract.write_bytes(b'Section 2 synthetic liability text.\n')
    audit = tmp_path / 'dash.score-audit.jsonl'
    missing = tmp_path / 'missing-golden'
    ctx = multiprocessing.get_context('spawn')
    barrier = ctx.Barrier(2)
    payload = (str(contract), str(audit), str(missing), barrier)
    procs = [ctx.Process(target=_concurrent_dashboard_build, args=(payload,)) for _ in range(2)]
    for proc in procs:
        proc.start()
    codes = []
    for proc in procs:
        proc.join(30)
        if proc.is_alive():
            proc.terminate()
            proc.join(5)
            codes.append(None)
        else:
            codes.append(proc.exitcode)
    assert codes == [0, 0]
    records = AuditLog.load(str(audit)).records()
    risks = build_contract_data(_synthetic_state())['riskAssessment']
    scores = [record for record in records if record['event'] == 'score']
    assert len(scores) == len(risks)
    assert len({record['riskId'] for record in scores}) == len(scores)
    assert len({record['recordId'] for record in records}) == len(records)
    lock_path = tmp_path / 'dash.score-audit.jsonl.lock'
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600


def test_analysis_snapshot_on_the_state_is_what_the_audit_stores(tmp_path):
    contract = tmp_path / 'synthetic.txt'
    original = b'original synthetic bytes\n'
    contract.write_bytes(original)
    digest = hashlib.sha256(original).hexdigest()
    state = _synthetic_state()
    state['local_llm'] = {'input_file_sha256': digest}
    contract.write_bytes(b'changed synthetic bytes\n')
    audit = tmp_path / 'dash.score-audit.jsonl'
    generate_dashboard_html(
        state,
        input_path=str(contract),
        timestamp=TIMESTAMP,
        audit_path=str(audit),
        cases_dir=str(tmp_path / 'missing-golden'),
    )
    stored = AuditLog.load(str(audit)).records()[0]['inputFileSha256']
    assert stored == digest
    assert stored != hashlib.sha256(contract.read_bytes()).hexdigest()
    bad = _synthetic_state()
    bad['local_llm'] = {'input_file_sha256': 5}
    with pytest.raises(ValueError, match='input_sha256'):
        generate_dashboard_html(
            bad,
            input_path=str(contract),
            timestamp=TIMESTAMP,
            audit_path=str(tmp_path / 'bad.score-audit.jsonl'),
            cases_dir=str(tmp_path / 'missing-golden'),
        )
    assert not (tmp_path / 'bad.score-audit.jsonl').exists()


def test_saved_digest_is_kept_when_the_file_later_changes(tmp_path):
    contract = tmp_path / 'synthetic.txt'
    contract.write_bytes(b'original synthetic bytes\n')
    state = _synthetic_state()
    digest = 'ab' * 32
    records = build_score_audit(
        state, str(contract), TIMESTAMP, input_sha256=digest,
    ).records()
    assert records[0]['inputFileSha256'] == digest
    contract.write_bytes(b'changed synthetic bytes\n')
    again = build_score_audit(
        state, str(contract), TIMESTAMP, input_sha256=digest,
    ).records()
    assert again[0]['inputFileSha256'] == digest
    with pytest.raises(ValueError, match='input_sha256'):
        build_score_audit(state, str(contract), TIMESTAMP, input_sha256='ABC')


class _FakeMsvcrt:
    LK_LOCK = 1
    LK_UNLCK = 0

    def __init__(self):
        self.calls = []

    def locking(self, fd, mode, nbytes):
        self.calls.append((mode, nbytes, os.lseek(fd, 0, os.SEEK_CUR)))


def test_windows_lock_uses_one_byte_and_does_not_grow_twice(tmp_path, monkeypatch):
    lock_path = tmp_path / 'audit.jsonl.lock'
    fake = _FakeMsvcrt()
    monkeypatch.setattr(gd, '_msvcrt', fake)
    monkeypatch.setattr(gd.os, 'name', 'nt')
    with gd._InterprocessLock(str(lock_path)):
        assert lock_path.read_bytes() == b'\0'
    assert fake.calls == [(1, 1, 0), (0, 1, 0)]
    with gd._InterprocessLock(str(lock_path)):
        pass
    assert lock_path.read_bytes() == b'\0'
    assert fake.calls[2] == (1, 1, 0)
    assert fake.calls[3] == (0, 1, 0)


def test_lock_reports_when_the_platform_lock_is_missing(tmp_path, monkeypatch):
    path = tmp_path / 'missing.lock'
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        monkeypatch.setattr(gd.os, 'name', 'posix')
        monkeypatch.setattr(gd, '_fcntl', None)
        with pytest.raises(OSError, match='POSIX'):
            gd._acquire_exclusive_lock(fd)
        with pytest.raises(OSError, match='POSIX'):
            gd._release_exclusive_lock(fd)
        with pytest.raises(OSError, match='POSIX'):
            with gd._InterprocessLock(str(tmp_path / 'via-context.lock')):
                pass
        monkeypatch.setattr(gd.os, 'name', 'nt')
        monkeypatch.setattr(gd, '_msvcrt', None)
        with pytest.raises(OSError, match='Windows'):
            gd._acquire_exclusive_lock(fd)
        with pytest.raises(OSError, match='Windows'):
            gd._release_exclusive_lock(fd)
    finally:
        os.close(fd)


@pytest.mark.parametrize('field,value', [
    ('inputFileSha256', 'c' * 64),
    ('clauseReference', 'Section 99'),
    ('goldenManifestHash', 'd' * 64),
    ('playbookVersion', '1999-01-01'),
    ('scorerVersion', 'scorer-other'),
])
def test_override_rejects_mismatched_provenance(field, value):
    log = AuditLog()
    score = log.append(_body())
    before = log.records()
    with pytest.raises(ValueError, match='provenance'):
        log.append(_override_from(score, **{field: value}))
    assert log.records() == before


def _one_risk_audit(tmp_path):
    """One synthetic contract and its single score record. No real contract text."""
    contract = tmp_path / 'synthetic.txt'
    contract.write_bytes(b'Section 2. Synthetic liability clause for an invented vendor.\n')
    audit = tmp_path / 'synthetic.score-audit.jsonl'
    generate_dashboard_html(
        _synthetic_state('Section 2'),
        input_path=str(contract),
        timestamp=TIMESTAMP,
        audit_path=str(audit),
        cases_dir=str(tmp_path / 'missing-golden'),
    )
    return audit


def _ac5_override(**kwargs):
    payload = {
        'original_record_id': 'AR-001',
        'reviewer': 'Synthetic Reviewer',
        'new_band': 'Escalate',
        'rationale': 'Synthetic review: the action band should be Escalate.',
        'timestamp': '2026-11-16T01:00:00Z',
    }
    payload.update(kwargs)
    return payload


def test_ac5_human_override_keeps_original_score_record_bytes(tmp_path):
    """CRA-01 In-4 / AC5.

    Before the override there is 1 record for the risk. After it there are 2.
    The first record is byte-for-byte unchanged.
    """
    audit = _one_risk_audit(tmp_path)
    before_bytes = audit.read_bytes()
    before = AuditLog.load(str(audit)).records()
    assert len(before) == 1
    assert before[0]['event'] == 'score'
    assert before[0]['riskId'] == 'RK-001'
    assert before[0]['score'] == 5
    assert 'oldBand' not in before[0]

    stored = gd.write_human_override(str(audit), **_ac5_override())
    after_bytes = audit.read_bytes()
    after = AuditLog.load(str(audit)).records()
    same_risk = [record for record in after if record['riskId'] == 'RK-001']

    assert len(same_risk) == 2
    assert after_bytes[:len(before_bytes)] == before_bytes
    assert after_bytes.splitlines(keepends=True)[0] == before_bytes
    assert after[0] == before[0]
    assert json.dumps(after[0], ensure_ascii=False).encode('utf-8') + b'\n' == before_bytes
    assert stored['recordId'] == 'AR-002'
    assert stored['event'] == 'override'
    assert stored['originalRecordId'] == before[0]['recordId']
    assert stored['reviewer'] == 'Synthetic Reviewer'
    assert stored['oldBand'] == 'Negotiate'
    assert stored['newBand'] == 'Escalate'
    assert stored['rationale'] == 'Synthetic review: the action band should be Escalate.'
    assert stored['timestamp'] == '2026-11-16T01:00:00Z'
    assert stored['clauseReference'] == before[0]['clauseReference']
    assert stored['inputFileSha256'] == before[0]['inputFileSha256']
    assert after[1] == stored


@pytest.mark.parametrize('kwargs,match', [
    ({'rationale': ''}, 'rationale'),
    ({'rationale': '   '}, 'rationale'),
    ({'rationale': None}, 'rationale'),
    ({'original_record_id': None}, 'unknown original record id'),
    ({'original_record_id': 'AR-999'}, 'unknown original record id'),
    ({'original_record_id': ''}, 'unknown original record id'),
    ({'original_record_id': 'AR-002'}, 'unknown original record id'),
    ({'new_band': 'Negotiate'}, 'unchanged'),
    ({'new_band': 'High'}, 'newBand'),
    ({'reviewer': '   '}, 'reviewer'),
    ({'reviewer': None}, 'reviewer'),
    ({'clause_reference': 'Section 99'}, 'provenance'),
    ({'input_file_sha256': 'c' * 64}, 'provenance'),
])
def test_ac5_rejected_override_leaves_the_history_file_unchanged(tmp_path, kwargs, match):
    audit = _one_risk_audit(tmp_path)
    before = audit.read_bytes()
    with pytest.raises(ValueError, match=match):
        gd.write_human_override(str(audit), **_ac5_override(**kwargs))
    assert audit.read_bytes() == before
    reloaded = AuditLog.load(str(audit)).records()
    assert len(reloaded) == 1
    assert reloaded[0]['event'] == 'score'


def test_ac5_blank_original_record_id_on_the_record_is_rejected():
    log = AuditLog()
    score = log.append(_body())
    before = log.records()
    for blank in ('', '   ', None):
        with pytest.raises(ValueError, match='unknown original record id'):
            log.append(_override_from(score, originalRecordId=blank))
    assert log.records() == before


def test_ac5_empty_history_file_rejects_an_unknown_original(tmp_path):
    audit = tmp_path / 'empty.score-audit.jsonl'
    audit.write_bytes(b'')
    before = audit.read_bytes()
    with pytest.raises(ValueError, match='unknown original record id'):
        gd.write_human_override(str(audit), **_ac5_override())
    assert audit.read_bytes() == before


def test_ac5_override_does_not_follow_a_symlink(tmp_path):
    target = tmp_path / 'keep.jsonl'
    target.write_text('keep\n', encoding='utf-8')
    link = tmp_path / 'audit.jsonl'
    link.symlink_to(target)
    with pytest.raises(OSError):
        gd.write_human_override(str(link), **_ac5_override())
    assert target.read_text(encoding='utf-8') == 'keep\n'


def test_ac5_missing_rationale_field_does_not_append(tmp_path):
    audit = _one_risk_audit(tmp_path)
    before = audit.read_bytes()
    log = AuditLog.load(str(audit))
    body = _override_from(log.records()[0], newBand='Escalate')
    del body['rationale']
    with pytest.raises(ValueError, match='rationale'):
        log.append(body)
    assert len(log.records()) == 1
    assert audit.read_bytes() == before


def test_ac5_unknown_original_does_not_create_a_history_file(tmp_path):
    audit = tmp_path / 'missing.score-audit.jsonl'
    with pytest.raises(ValueError, match='unknown original record id'):
        gd.write_human_override(str(audit), **_ac5_override())
    assert not audit.exists()
    assert not (tmp_path / 'missing.score-audit.jsonl.lock').exists()


def test_ac5_override_id_is_not_an_original_record(tmp_path):
    """A later line cannot be used as the original score record."""
    audit = _one_risk_audit(tmp_path)
    gd.write_human_override(str(audit), **_ac5_override())
    before = audit.read_bytes()
    with pytest.raises(ValueError, match='unknown original record id'):
        gd.write_human_override(
            str(audit),
            **_ac5_override(original_record_id='AR-002', new_band='Walk'),
        )
    assert audit.read_bytes() == before
    assert len(AuditLog.load(str(audit)).records()) == 2


def test_ac5_invalid_old_band_is_rejected():
    log = AuditLog()
    score = log.append(_body())
    before = log.records()
    with pytest.raises(ValueError, match='oldBand'):
        log.append(_override_from(score, oldBand='High', newBand='Accept'))
    assert log.records() == before


def test_ac5_old_band_must_match_the_original_score():
    log = AuditLog()
    score = log.append(_body())
    before = log.records()
    with pytest.raises(ValueError, match='oldBand'):
        log.append(_override_from(score, oldBand='Accept', newBand='Negotiate'))
    assert log.records() == before


def test_ac5_original_id_from_another_risk_is_unknown():
    log = AuditLog()
    first = log.append(_body())
    second = log.append(_body(riskId='RK-002', clauseReference='Section 2'))
    before = log.records()
    with pytest.raises(ValueError, match='unknown original record id'):
        log.append(_override_from(first, originalRecordId=second['recordId']))
    assert log.records() == before


def _legacy_override_line(source, **changes):
    """One override line in the shape saved before the reviewer fields existed."""
    body = {key: source[key] for key in AUDIT_FIELDS}
    body['event'] = 'override'
    body['recordId'] = 'AR-002'
    body['timestamp'] = '2026-11-16T00:00:01Z'
    body['rule'] = {
        'id': 'human_override',
        'evidence': 'Older override note with no reviewer fields.',
    }
    body.update(changes)
    return gd._audit_line(body)


def test_ac5_legacy_override_history_stays_readable(tmp_path):
    """An older override line can be reopened and is not rewritten or filled in."""
    audit = _one_risk_audit(tmp_path)
    score_bytes = audit.read_bytes()
    score = AuditLog.load(str(audit)).records()[0]
    legacy = _legacy_override_line(score, likelihood='Unlikely', severity='Low', score=2)
    audit.write_bytes(score_bytes + legacy)
    saved = audit.read_bytes()

    loaded = AuditLog.load(str(audit)).records()
    assert len(loaded) == 2
    assert loaded[0] == score
    assert loaded[1]['event'] == 'override'
    assert loaded[1]['recordId'] == 'AR-002'
    for field in ('reviewer', 'oldBand', 'newBand', 'rationale', 'originalRecordId'):
        assert field not in loaded[1]
    assert audit.read_bytes() == saved

    bare = {key: value for key, value in loaded[1].items() if key != 'recordId'}
    with pytest.raises(ValueError, match='reviewer'):
        AuditLog.load(str(audit)).append(bare)
    assert audit.read_bytes() == saved

    state = _synthetic_state('Section 2')
    generate_dashboard_html(
        state,
        input_path=str(tmp_path / 'synthetic.txt'),
        timestamp=TIMESTAMP,
        audit_path=str(audit),
        cases_dir=str(tmp_path / 'missing-golden'),
    )
    assert audit.read_bytes() == saved

    added = gd.write_human_override(str(audit), **_ac5_override())
    after = audit.read_bytes()
    reloaded = AuditLog.load(str(audit)).records()
    assert after.startswith(saved)
    assert after.splitlines(keepends=True)[0] == score_bytes
    assert after.splitlines(keepends=True)[1] == legacy
    assert len(reloaded) == 3
    assert reloaded[0] == score
    assert 'reviewer' not in reloaded[1]
    assert added['recordId'] == 'AR-003'
    assert added['originalRecordId'] == 'AR-001'
    assert added['reviewer'] == 'Synthetic Reviewer'
    assert added['oldBand'] == 'Negotiate'
    assert added['newBand'] == 'Escalate'
    assert reloaded[2] == added


def test_ac5_legacy_override_with_a_different_clause_is_rejected(tmp_path):
    audit = _one_risk_audit(tmp_path)
    score = AuditLog.load(str(audit)).records()[0]
    legacy = _legacy_override_line(score, clauseReference='Section 99')
    audit.write_bytes(audit.read_bytes() + legacy)
    saved = audit.read_bytes()
    with pytest.raises(ValueError, match='provenance'):
        AuditLog.load(str(audit))
    assert audit.read_bytes() == saved


def test_ac5_partial_override_line_is_not_treated_as_legacy(tmp_path):
    audit = _one_risk_audit(tmp_path)
    score = AuditLog.load(str(audit)).records()[0]
    body = json.loads(_legacy_override_line(score))
    body['reviewer'] = 'Synthetic Reviewer'
    audit.write_bytes(audit.read_bytes() + gd._audit_line(body))
    saved = audit.read_bytes()
    with pytest.raises(ValueError, match='oldBand'):
        AuditLog.load(str(audit))
    assert audit.read_bytes() == saved
