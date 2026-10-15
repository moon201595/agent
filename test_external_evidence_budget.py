"""③⑤ 외부 검색 범위·시간 예산·테스트 응답 로그 격리를 검증한다."""
import json
from pathlib import Path

import external_evidence as ee

TARGET = {'paper_id': 'budget-test', 'title': 'Sample', 'method': 'Sample',
          'benchmark': 'Bench', 'metric': 'Score', 'value': '10',
          'existing_methods': ['Baseline']}


def test_search_prompt_limits_work_to_new_competitors():
    """무엇을 망가뜨리면 실패하는가: 인기 탐색을 다시 넣거나 기존 방법 제외·출처 셀·조건 근거 요구를 빼면 실패한다."""
    prompts = []
    def agent(prompt, timeout):
        prompts.append(prompt)
        return json.dumps({'papers': [{'paper_id': TARGET['paper_id'], 'competitors': []}]})
    assert ee.check([TARGET], run_agent=agent)[TARGET['paper_id']]['status'] == 'done'
    prompt, = prompts
    for phrase in ('most widely adopted', 'most cited', 'most used', 'standard reference'):
        assert phrase not in prompt
    for phrase in ('newer or stronger', 'NOT in the already compared methods list',
                   'already compared methods=["Baseline"]', 'up to 3', 'HTML/Markdown TABLE',
                   'row label exactly', 'column header path exactly', 'match_evidence',
                   'dataset_version, split, protocol, training_setting', 'Do not compute who is better'):
        assert phrase in prompt


def test_default_budget_preserves_verification_reserve(monkeypatch):
    """무엇을 망가뜨리면 실패하는가: 기본 215초·에이전트 180초·검증 예약 35초나 절대 마감 전달을 바꾸면 실패한다."""
    now = [0.0]
    bounds = []
    def timed(call, timeout):
        bounds.append(timeout)
        return call()
    monkeypatch.setattr(ee, '_timed', timed)
    def agent(prompt, timeout):
        assert timeout == 180.0
        now[0] = 180.0
        comp = {'source_url': 'https://arxiv.org/html/2601.00001', 'model': 'Candidate',
                'value': '33', 'same_conditions': False, 'differences': [],
                'same_task': True, 'task_note': '같은 평가 과제'}
        return json.dumps({'papers': [{'paper_id': TARGET['paper_id'], 'competitors': [comp]}]})
    def verify(comp, target, fetch, timeout):
        assert timeout == 15.0
        now[0] = 215.0
        return {'status': 'verified'}
    monkeypatch.setattr(ee, 'verify_competitor', verify)
    assert ee.BUDGET_S == 215.0 and ee.VERIFY_RESERVE_S == 35.0
    got = ee.check([TARGET], run_agent=agent, clock=lambda: now[0])[TARGET['paper_id']]
    assert got['status'] == 'done'
    assert bounds == [180.0, 35.0]


def test_timeout_has_no_response_to_save_or_verify(monkeypatch):
    """무엇을 망가뜨리면 실패하는가: 시간 초과를 재호출하거나 없는 응답을 저장·검증·성공 처리하면 실패한다."""
    def fail(*args):
        raise AssertionError('완성 응답 없이 저장·검증했다')
    monkeypatch.setattr(ee, '_save_agent_response', fail)
    monkeypatch.setattr(ee, 'verify_competitor', fail)
    calls = []
    def agent(*args):
        calls.append(args)
        raise TimeoutError('가짜 시간 초과')
    got = ee.check([TARGET], run_agent=agent)[TARGET['paper_id']]
    assert len(calls) == 1 and got['agent_calls'] == 1 and got['retried'] is False
    assert got['status'] == 'incomplete' and got['reason'] == 'TimeoutError'
    assert got['competitors'] == []


def test_default_fixture_never_writes_production_agent_log(monkeypatch):
    """무엇을 망가뜨리면 실패하는가: 공용 픽스처의 경로 격리를 빼거나 저장 함수가 운영 경로를 직접 쓰면 실패한다."""
    production = Path(ee.__file__).resolve().parent / 'logs' / 'frontier_agent'
    before = sorted(p.name for p in production.glob('*.txt'))
    attempted = []
    write_bytes = Path.write_bytes
    def guarded(path, data):
        attempted.append(path)
        if path.parent == production:
            raise AssertionError('운영 로그 쓰기 시도')
        return write_bytes(path, data)
    monkeypatch.setattr(Path, 'write_bytes', guarded)
    raw = json.dumps({'papers': [{'paper_id': TARGET['paper_id'], 'competitors': []}]})
    ee.check([TARGET], run_agent=lambda *args: raw)
    assert ee.AGENT_LOG_DIR != production
    assert len(attempted) == 1 and attempted[0].parent == ee.AGENT_LOG_DIR
    assert attempted[0].read_text() == raw
    assert sorted(p.name for p in production.glob('*.txt')) == before
