"""⑨ 운영 화면 독립 검토 회귀 — 실제 저장·렌더링 경로를 지킨다(2026-10-02)."""
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest
import ops_dashboard as od
import research_profile as rp
import storage
import mail_ledger


@pytest.mark.parametrize('new', [False, True])
def test_profile_form_saves_after_sidebar_exists(tmp_path, monkeypatch, new):
    """저장 뒤 위젯 상태를 직접 바꾸거나 다음 실행의 선택 적용을 빼면 실패한다."""
    import server
    from streamlit.testing.v1 import AppTest
    db = tmp_path / 'ui.db'
    rp.create_profile(db, 'p', '기존', ['robot'])
    storage.init_storage(db)
    monkeypatch.setattr(server, 'DB_PATH', db)
    at = AppTest.from_file('review_app.py', default_timeout=120).run()
    at.button(key='nav_profile').click().run()
    prefix = 'new_profile' if new else 'edit_p'
    if new:
        at.text_input(key=prefix + '_id').set_value('new_p')
        at.text_area(key=prefix + '_core').set_value('vision')
    at.text_input(key=prefix + '_name').set_value('저장한 이름')
    at.button(key=prefix + '_submit').click().run()
    assert not at.exception
    pid = 'new_p' if new else 'p'
    assert rp.get_profile(db, pid)['name'] == '저장한 이름'
    assert at.selectbox(key='_research_selected_profile').value == pid
    assert at.query_params['profile'] == [pid]


def test_query_navigation_and_profile_backtracking(tmp_path, monkeypatch):
    """URL 프로필을 영구 무시하거나 메뉴가 URL을 지워 뒤로가기를 잃으면 실패한다."""
    import server
    from streamlit.testing.v1 import AppTest
    db = tmp_path / 'nav.db'
    for pid in ('a', 'b'):
        rp.create_profile(db, pid, pid, ['robot'])
    storage.init_storage(db)
    monkeypatch.setattr(server, 'DB_PATH', db)
    at = AppTest.from_file('review_app.py', default_timeout=120)
    at.query_params.update(page='profile', profile='a')
    at.run()
    at.selectbox(key='_research_selected_profile').select('b').run()
    assert at.query_params['profile'] == ['b']
    at.button(key='nav_activity').click().run()
    assert at.query_params['page'] == ['activity']
    assert at.query_params['profile'] == ['b']
    at.query_params.update(page='profile', profile='a')
    at.run()
    assert not at.exception
    assert at.session_state.nav_page == 'profile'
    assert at.selectbox(key='_research_selected_profile').value == 'a'
    at.query_params.clear()
    at.run()
    assert at.session_state.nav_page == 'overview'
    at.query_params['paper'] = 'missing'
    at.run()
    at.button(key='nav_papers').click().run()
    assert at.session_state.nav_page == 'papers' and 'paper' not in at.query_params


def test_overview_all_connections_are_readonly(tmp_path, monkeypatch):
    """하위 window_movement·system_status 중 하나라도 쓰기 연결을 열면 실패한다."""
    db = tmp_path / 'read.db'
    rp.create_profile(db, 'p', 'P', ['robot'])
    storage.init_storage(db)
    original = sqlite3.connect
    calls = []
    def connect(database, *args, **kwargs):
        calls.append(str(database))
        assert kwargs.get('uri') and 'mode=ro' in str(database)
        return original(database, *args, **kwargs)
    monkeypatch.setattr(sqlite3, 'connect', connect)
    assert od.research_overview(db, 'p') is not None
    assert len(calls) >= 4


def _card_case(tmp_path, monkeypatch):
    db = tmp_path / 'card.db'
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    rp.create_profile(db, 'p', 'P', ['robot'])
    storage.init_storage(db)
    monkeypatch.setattr(od, '_parse_daily_log', lambda root: {})
    paper = {'title': 'A robot paper with enough letters', 'arxiv_id': '2610.00001',
             'deep_status': 'abstract_only', 'abstract_brief': '공개 초록으로만 정리했다.',
             '_score': {'core_hits': ['robot'], 'domain_hits': [], 'venue_hit': None, 'priority': 1.0}}
    mail_ledger.record_issue(db, 'sent', 'p', 'S', [paper], 1, 1, when=now)
    return db, now, paper


def test_overview_depth_reads_real_mail_card(tmp_path, monkeypatch):
    """메일의 실제 초록 라벨을 못 읽거나 현재 요약을 카드보다 우선하면 실패한다."""
    import digest
    db, now, paper = _card_case(tmp_path, monkeypatch)
    # 외부 조회 없이 실제 평문 카드 생성 함수를 탄다.
    monkeypatch.setattr(digest, 'frontier_lines', lambda p: [])
    monkeypatch.setattr(digest, '_paper_retraction_label', lambda p: '')
    monkeypatch.setattr(digest, 'injection_label', lambda aid: '')
    text = '■ 오늘의 핵심 논문 1편\n\n' + digest._paper_entry(1, paper)
    with sqlite3.connect(db) as con:
        con.execute('UPDATE profiles SET last_digest=?, last_digest_at=?', (text, (now-timedelta(seconds=5)).isoformat()))
        # 발송 후 본문을 얻은 경우에도 보낸 메일의 깊이는 초록이다.
        con.execute("INSERT INTO summaries(arxiv_id,coverage_ratio) VALUES (?,1)", (paper['arxiv_id'],))
    got = od.research_overview(db, 'p', now=now)
    assert got['papers'][0]['depth'] == '초록 기반'
    assert dict(got['kpis'])['원문 분석 · 초록 기반'] == '0 · 1'


def test_overview_does_not_attach_later_unsent_digest(tmp_path, monkeypatch):
    """같은 날 수동 스캔의 미발송 본문을 아침 발송 회차에 붙이면 실패한다."""
    db, now, paper = _card_case(tmp_path, monkeypatch)
    text = '■ 오늘의 핵심 논문 1편\n\n1. ' + paper['title'] + '\n   아직 보내지 않은 본문'
    with sqlite3.connect(db) as con:
        con.execute('UPDATE profiles SET last_digest=?, last_digest_at=?', (text, (now+timedelta(hours=1)).isoformat()))
    assert od.research_overview(db, 'p', now=now+timedelta(hours=2))['papers'][0]['card'] == []


def test_title_link_does_not_match_short_candidate():
    """짧은 후보 제목이 긴 제목의 접두사라는 이유만으로 연결하면 실패한다."""
    assert od.paper_id_for_title('A long unrelated research paper', {'a': 'wrong'}) == ''
    assert od.paper_id_for_title('A', {'a': 'exact'}) == 'exact'


def test_overview_keeps_ambiguous_titles_unlinked(tmp_path, monkeypatch):
    """동명 제목을 후보에서 버린 뒤 더 긴 다른 제목에 연결하면 실패한다."""
    import narrative_store
    db = tmp_path / 'titles.db'
    rp.create_profile(db, 'p', 'P', ['robot'])
    storage.init_storage(db)
    title = 'Robot learning with demonstrations'
    with sqlite3.connect(db) as con:
        for aid, t in [('one', title), ('two', title), ('other', title + ' and feedback')]:
            con.execute('INSERT INTO papers(arxiv_id,title) VALUES (?,?)', (aid, t))
    narrative_store.save(db, 'p', 'daily', '■ 1. 흐름\n내용\n- ' + title + ' [P1:A]')
    monkeypatch.setattr(od, '_parse_daily_log', lambda root: {})
    item = od.research_overview(db, 'p')['story']['threads'][0]['items'][0]
    assert item['paper_id'] == ''


def test_existing_profile_actions_still_work(tmp_path, monkeypatch):
    """가중치 저장·되돌리기·수신자·수동 스캔의 실제 화면 버튼 연결을 검증한다."""
    import server
    import streamlit as st
    import run_profile_scan
    from streamlit.testing.v1 import AppTest
    db = tmp_path / 'actions.db'
    rp.create_profile(db, 'p', 'P', ['robot'], schedule_frequency='manual')
    storage.init_storage(db)
    monkeypatch.setattr(server, 'DB_PATH', db)
    edit = {'weight': None}
    real_editor = st.data_editor
    def editor(data, *args, **kwargs):
        result = real_editor(data, *args, **kwargs)
        if edit['weight'] is not None:
            result = result.copy()
            result.loc[result['키워드'] == 'robot', '가중치'] = edit['weight']
        return result
    monkeypatch.setattr(st, 'data_editor', editor)
    at = AppTest.from_file('review_app.py', default_timeout=120).run()
    at.button(key='nav_profile').click().run()
    edit['weight'] = 1.3
    at.run()
    at.button(key='kw_save_p').click().run()
    assert not at.exception
    assert rp.get_profile(db, 'p')['core_weights']['robot'] == 1.3
    edit['weight'] = None
    at.button(key='rollback_p_1').click().run()
    assert not at.exception
    assert rp.get_profile(db, 'p')['core_weights']['robot'] == 1.0
    assert rp.get_schedule(db, 'p')[0] == 'manual'
    at.text_input(key='add_recipient_p').set_value('test@example.com')
    at.button(key='add_recipient_btn_p').click().run()
    assert rp.get_recipients(db, 'p') == ['test@example.com']
    at.button(key='unsub_p_test@example.com').click().run()
    assert rp.get_recipients(db, 'p') == []
    scanned = []
    async def scan(db_path, pid, client, **kwargs):
        scanned.append((db_path, pid))
        return {}, '가짜 스캔'
    monkeypatch.setattr(run_profile_scan, 'scan_and_digest', scan)
    at.button(key='scan_now_p').click().run()
    assert not at.exception
    assert scanned == [(db, 'p')]


def test_overview_skips_unrequested_term_mining(monkeypatch, tmp_path):
    """top_terms=0 호출이 n-gram 계산을 다시 시작하면 실패한다."""
    import trend_report
    monkeypatch.setattr(trend_report, 'observed_rows', lambda *a: [{'first_seen': '2026-10-01'}])
    monkeypatch.setattr(trend_report, 'keyword_counts', lambda *a: {})
    def refuse(*a, **kw):
        pytest.fail('요청하지 않은 용어 계산')
    monkeypatch.setattr(trend_report, 'emerging_terms', refuse)
    assert trend_report.window_movement(tmp_path / 'unused', {}, top_terms=0)['terms'] == []


def test_detail_preserves_alias_and_nested_summary_sections(tmp_path, monkeypatch):
    """메일이 읽는 굵은 제목·별칭·하위 제목 아래 요약을 상세 화면이 버리면 실패한다."""
    import server
    from streamlit.testing.v1 import AppTest
    db = tmp_path / 'summary.db'
    rp.create_profile(db, 'p', 'P', ['robot'])
    storage.init_storage(db)
    md = tmp_path / 'summary.md'
    md.write_text('### **1. 개요**\n- 목표 : 경로 추종\n### 방법\n#### 학습 과정\n'
                  '  - 방법 : 관측으로 학습\n### 주요 결과\n- 결과 : 오차를 측정\n### 한계\n- 한계 : 실내만 평가\n')
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO papers(arxiv_id,title,abstract) VALUES ('2610.00001','Robot paper','Abstract')")
        con.execute("INSERT INTO summaries(arxiv_id,path) VALUES ('2610.00001',?)", (str(md),))
    monkeypatch.setattr(server, 'DB_PATH', db)
    at = AppTest.from_file('review_app.py', default_timeout=120)
    at.query_params['paper'] = '2610.00001'
    at.run()
    assert not at.exception
    html = '\n'.join(m.value for m in at.markdown)
    for value in ('경로 추종','관측으로 학습','오차를 측정','실내만 평가'):
        assert value in html
