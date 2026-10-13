"""⑤ 외부 제안의 경쟁자별 격리와 제한된 진단 원문 저장을 오프라인으로 검증한다."""
import hashlib
import json
from datetime import datetime, timezone

import pytest
import external_evidence as ee

TARGET = {'paper_id': 'test-A', 'title': 'Sample', 'method': 'Sample',
          'benchmark': 'Bench', 'metric': 'Score', 'value': '10'}
COMP = {'source_url': 'https://arxiv.org/html/2601.00001', 'model': 'Candidate',
        'value': '33', 'same_conditions': False, 'differences': [],
        'same_task': True, 'task_note': '같은 평가 과제'}
PAGE = ('<figure class="ltx_table"><table><tr><th>Method</th><th>Score</th></tr>'
        '<tr><td>Candidate</td><td>33</td></tr></table></figure>')


@pytest.mark.parametrize('change,path', [({'task_note': '가' * 41}, 'task_note'),
                                         ({'unknown': 1}, None), ({'value': {}}, 'value')])
def test_invalid_candidate_does_not_discard_verified_peers(change, path):
    """무엇을 망가뜨리면 실패하는가: 한 후보 오류로 다른 논문·후보를 버리거나 출처 숫자 검증을 건너뛰면 실패한다."""
    data = {'papers': [{'paper_id': 'test-A', 'competitors': [{**COMP, **change}, COMP, {**COMP, 'value': '999'}]},
                       {'paper_id': 'test-B', 'competitors': [COMP]}]}
    calls = []
    def fetch(*args):
        calls.append(args)
        return PAGE
    result = ee.check([TARGET, {**TARGET, 'paper_id': 'test-B'}],
                      run_agent=lambda *a: json.dumps(data), fetch=fetch)
    a, b = result['test-A'], result['test-B']
    assert len(calls) == 3
    assert a['status'] == 'incomplete' and a['agent_calls'] == 1
    assert a['invalid_competitors'] == 1
    expected_path = ['papers', 0, 'competitors', 0] + ([path] if path else [])
    prefix = json.dumps(expected_path) + ': '
    assert a['invalid_reason'].startswith(prefix)
    assert len(a['invalid_reason'][len(prefix):]) <= 120
    assert [c['status'] for c in a['competitors']] == ['verified', 'not_found']
    assert a['competitors'][0]['value'] == 33 and 'value' not in a['competitors'][1]
    assert b['status'] == 'done' and b['invalid_competitors'] == 0
    assert b['competitors'][0]['value'] == 33


@pytest.mark.parametrize('data', [{'papers': {}}, {'papers': [{'paper_id': 'test-A'}]},
                                  {'papers': [{'paper_id': 'test-A', 'competitors': 'bad'}]},
                                  {'papers': [], 'extra': True},
                                  {'papers': [{'paper_id': 'test-A', 'competitors': [COMP] * 4}]}])
def test_bad_envelope_remains_incomplete(data):
    """무엇을 망가뜨리면 실패하는가: 최상위 구조·후보 수 상한 위반을 성공으로 처리하거나 재호출하면 실패한다."""
    got = ee.check([TARGET], run_agent=lambda *a: json.dumps(data),
                   fetch=lambda *a: pytest.fail('잘못된 구조로 검증을 시작했다'))['test-A']
    assert got['status'] == 'incomplete' and got['reason'] == 'ValidationError'
    assert got['agent_calls'] == 1 and got['competitors'] == []


def test_all_invalid_counts_and_keeps_first_path():
    """무엇을 망가뜨리면 실패하는가: 모두 버린 응답을 done으로 남기거나 누적 개수·첫 오류 경로를 잃으면 실패한다."""
    raw = json.dumps({'papers': [{'paper_id': 'test-A', 'competitors': [None, {**COMP, 'value': []}]}]})
    got = ee.check([TARGET], run_agent=lambda *a: raw)['test-A']
    assert got['status'] == 'incomplete' and got['competitors'] == []
    assert got['invalid_competitors'] == 2
    assert got['invalid_reason'].startswith('["papers", 0, "competitors", 0]: ')


def test_response_log_exact_text_hash_utc_and_byte_cap(tmp_path, monkeypatch):
    """무엇을 망가뜨리면 실패하는가: 원문 대신 파싱값을 저장하거나 UTC·전체 응답 해시·바이트 상한을 어기면 실패한다."""
    monkeypatch.setattr(ee, 'AGENT_LOG_DIR', tmp_path / 'raw')
    raw = 'invalid reply 한글\n'
    before = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    got = ee.check([TARGET], run_agent=lambda *a: raw)['test-A']
    after = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    files = list(ee.AGENT_LOG_DIR.glob('*.txt'))
    assert len(files) == 1 and files[0].read_text() == raw
    digest = hashlib.sha256(raw.encode()).hexdigest()[:12]
    assert files[0].name in {f'{day}_{digest}.txt' for day in (before, after)}
    assert str(files[0]) not in str(got)
    long_raw = '한' * (ee.MAX_AGENT_LOG_BYTES // 3 + 5)
    ee._save_agent_response(long_raw)
    long_file = ee.AGENT_LOG_DIR / f'{after}_{hashlib.sha256(long_raw.encode()).hexdigest()[:12]}.txt'
    assert long_file.stat().st_size <= ee.MAX_AGENT_LOG_BYTES
    assert long_file.read_text() == long_raw[:ee.MAX_AGENT_LOG_BYTES // 3]


def test_log_failure_does_not_skip_verification(tmp_path, monkeypatch):
    """무엇을 망가뜨리면 실패하는가: 진단 디렉터리·파일 쓰기 실패가 검증을 막거나 비교를 incomplete로 바꾸면 실패한다."""
    raw = json.dumps({'papers': [{'paper_id': 'test-A', 'competitors': [COMP]}]})
    blocked = tmp_path / 'blocked'
    blocked.write_text('file instead of directory')
    monkeypatch.setattr(ee, 'AGENT_LOG_DIR', blocked)
    def run():
        return ee.check([TARGET], run_agent=lambda *a: raw, fetch=lambda *a: PAGE)['test-A']
    assert run()['competitors'][0]['status'] == 'verified'
    monkeypatch.setattr(ee, 'AGENT_LOG_DIR', tmp_path / 'ok')
    def fail(*args, **kwargs):
        raise OSError('disk full')
    monkeypatch.setattr(ee.Path, 'write_bytes', fail)
    assert run()['status'] == 'done'
