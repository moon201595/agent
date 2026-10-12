"""⑨ 월요일 운행 전 경계 회귀 — 합성 응답으로 실제 검증 경로를 실행한다."""
import json
import sqlite3

import pytest
import agent_maintenance as am
import external_scout as es
import trend_report as tr
import trend_history
from test_trend_history import CORPUS, PAST
from test_external_scout import NOW


def test_followup_cap_cannot_hide_later_common_thread():
    """무엇을 망가뜨리면 실패하는가: 보류 중인 후속 3개로 순회를 끝내 뒤 공통 흐름을 누락하면 실패한다."""
    follow = {"name": "후속", "papers": ["P2"], "body": "경로 탐색 [P2:A].",
              "history": {"ref": "H1", "relation": "continuing", "note": "선택 문제를 탐색으로 잇는다 [P2:A]."}}
    common = {"name": "공통", "papers": ["P1", "P2"], "body": "선택과 탐색 [P1:A][P2:A].", "history": None}
    plan = {"headline": "", "threads": [follow, follow, follow, common], "relation": "", "implications": [], "side_signals": []}
    story = tr.repair_story(tr.parse_story(json.dumps(plan)), CORPUS, history=trend_history.catalog(PAST))
    assert [(t['name'], t['papers']) for t in story['threads']] == [('공통', [1, 2])]


def test_official_nonresearch_title_is_not_axis_evidence():
    """무엇을 망가뜨리면 실패하는가: 신원 확인 뒤 공식 제목의 튜토리얼을 연구축 편수에 넣으면 실패한다."""
    titles = ['Semantic Communication for Robotic Control', 'Semantic Communication for Robots: A Tutorial on Representation']
    items = [{"paper_key": f"2610.0000{i}", "title": title, "abstract": "semantic communication", "published": "2026-10-01",
              "axes": ["semantic communication"], "gap_stage": "not_retrieved", "id": f"E{i}"} for i, title in enumerate(titles)]
    assert es._axis_groups(items, NOW) == []
    assert len(es._axis_groups(items, NOW, minimum=1)[0]['items']) == 1


def test_catchup_honors_no_scout(tmp_path, monkeypatch):
    """무엇을 망가뜨리면 실패하는가: 따라잡기에서 --no-scout을 무시하고 외부 정찰을 실행하면 실패한다."""
    async def sync(*args):
        return {}
    calls = []
    monkeypatch.setattr(am.feedback_links, 'sync', sync)
    monkeypatch.setattr(am, 'catch_up_targets', lambda db: ['p'])
    monkeypatch.setattr(es, 'run_weekly', lambda *a, **k: calls.append('scout') or {})
    monkeypatch.setattr(am, 'run_week', lambda *a, **k: calls.append('week') or [])
    assert am.main(['--db', str(tmp_path/'x.db'), '--catch-up', '--no-scout']) == 0
    assert calls == ['week']


def test_week_continues_after_profile_reservation_failure(tmp_path, monkeypatch):
    """무엇을 망가뜨리면 실패하는가: 한 프로필 예약의 DB 예외가 다음 프로필의 주간 관리를 막으면 실패한다."""
    from test_agent_maintenance import _profile
    db = tmp_path / 'w.db'
    _profile(db)
    reserve = am._reserve
    def broken(db, pid, *args):
        if pid == 'broken':
            raise sqlite3.OperationalError('reservation failed')
        return reserve(db, pid, *args)
    monkeypatch.setattr(am, '_reserve', broken)
    monkeypatch.setattr(am, 'build_brief', lambda *a: None)
    results = am.run_week(db, runner=object(), profile_ids=['broken', 'p'], now=NOW)
    assert [r['profile_id'] for r in results] == ['broken', 'p']
    assert results[0]['status'] == 'failed'
    assert results[1]['status'] == 'failed' and results[1]['error'] == 'no_profile'


def test_repeated_term_comparisons_do_not_repeat_normalization(monkeypatch):
    """무엇을 망가뜨리면 실패하는가: 같은 용어를 비교할 때마다 다시 정규화해 브리프를 지연시키거나 토큰 경계를 바꾸면 실패한다."""
    import term_hygiene as h
    calls = []
    original = h.norm_tokens
    def counted(words):
        calls.append(tuple(words))
        return original(words)
    monkeypatch.setattr(h, 'norm_tokens', counted)
    for _ in range(100):
        assert h.overlaps_known('orbital widget models', {'orbital widget model'})
        assert not h.overlaps_known('superorbital widget model', {'orbital widget model'})
    assert len(calls) <= 3
    # 호출자가 목록을 바꾸더라도 다음 비교의 정규화 결과를 오염시키지 않는다.
    words = ['orbital', 'widget', 'models']
    assert h.token_sequence_contains(words, 'widget model')
    words[1] = 'sensor'
    assert not h.token_sequence_contains(words, 'widget model')


def test_batch_subsumption_preserves_variants_without_pairwise_rescans(monkeypatch):
    """무엇을 망가뜨리면 실패하는가: 후보마다 전체 집합을 다시 비교하거나 하이픈·복수형·토큰 경계 정책을 바꾸면 실패한다."""
    import term_hygiene as h
    terms = {'language models', 'large language models', 'vision model', 'supervision model',
             'tactile-skin sensing', 'tactile skin sensing', 'skin sensing'}
    terms.update(f'unique{i} sensor' for i in range(300))
    calls = []
    original = h.token_sequence_contains
    def counted(*args):
        calls.append(1)
        return original(*args)
    monkeypatch.setattr(h, 'token_sequence_contains', counted)
    got = tr._subsumed_terms(terms)
    assert got == {'language models', 'tactile-skin sensing', 'tactile skin sensing', 'skin sensing'}
    assert len(calls) < 1000


def test_brief_and_trend_consumers_use_batch_subsumption(monkeypatch):
    """무엇을 망가뜨리면 실패하는가: 브리프·동향 소비자가 후보마다 전체를 재비교하는 옛 경로로 돌아가면 실패한다."""
    import term_discovery as td
    def quadratic(*args):
        raise AssertionError('후보별 전체 재비교')
    monkeypatch.setattr(tr, '_subsumed', quadratic)
    rows = [{'title': 'Tactile skin sensing', 'abstract': 'Orbital widget sensing.', '_paper_key': str(i),
             'day': 20261001, 'published': '2026-10-01', 'domain_hits': [], 's2_seeds': []} for i in range(3)]
    assert {x['term'] for x in td.discover(rows, {}, {'top_terms': 10})} == {'tactile skin sensing', 'orbital widget sensing'}
    assert {x[0] for x in tr.emerging_terms(rows, {})} == {'tactile skin sensing', 'orbital widget sensing'}


def test_brief_reuses_known_term_decision_for_repeated_papers(monkeypatch):
    """무엇을 망가뜨리면 실패하는가: 반복 등장한 같은 후보 용어의 기존 키워드 대조를 논문마다 다시 하면 실패한다."""
    import term_discovery as td
    import term_hygiene as h
    calls = []
    original = h.overlaps_known
    def counted(term, known):
        calls.append(term)
        return original(term, known)
    monkeypatch.setattr(h, 'overlaps_known', counted)
    rows = [{'title': 'Tactile skin sensing', 'abstract': '', '_paper_key': str(i), 'day': 20261001,
             'published': '2026-10-01', 'domain_hits': [], 's2_seeds': []} for i in range(20)]
    result = td.discover(rows, {'core_topics': ['robot manipulation']})
    assert [(x['term'], x['support']) for x in result] == [('tactile skin sensing', 20)]
    assert len(calls) == len(set(calls))


def test_llm_retry_never_obeys_a_day_long_retry_after(monkeypatch):
    """Codex 전체 검토(2026-10-11)가 정책으로 남긴 P2: 본 요약·서술 호출은 max_wait=None 이라 Retry-After: 86400 이면 하루를 잤다 — 월요일 메일이 안 나간다.
    무엇을 망가뜨리면 실패하는가: 기본 상한을 없애 긴 대기를 그대로 따르는 것, 짧은 대기(서버가 곧 풀린다고 한 것)까지 포기하는 것."""
    import asyncio
    import httpx
    import summarize_engine as engine
    slept = []

    async def fake_sleep(s):
        slept.append(s)
    monkeypatch.setattr(engine.asyncio, "sleep", fake_sleep)
    req = httpx.Request("POST", "http://api.example/v1")

    def limited(after):
        async def call():
            raise httpx.HTTPStatusError("429", request=req, response=httpx.Response(429, headers={"retry-after": after}, request=req))
        return call
    import pytest
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(engine._call_with_rate_limit_retry(limited("86400"), "Gemini"))
    assert slept == []                                    # 하루를 기다리지 않고 바로 다음 엔진으로
    calls = []

    async def once_then_ok():
        calls.append(1)
        if len(calls) == 1:
            raise httpx.HTTPStatusError("429", request=req, response=httpx.Response(429, headers={"retry-after": "5"}, request=req))
        return "ok"
    assert asyncio.run(engine._call_with_rate_limit_retry(once_then_ok, "Gemini")) == "ok" and len(slept) == 1
