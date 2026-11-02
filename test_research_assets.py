"""⑨ 참고 자료 — 운영 함수를 통해 수신 경계와 저장 본문의 보존을 검증한다."""
from copy import deepcopy
from datetime import date, datetime, timezone
import json
import sqlite3

import pytest

import digest
import email_delivery
import mail_ledger
import research_assets as ra
import research_profile as rp
import run_profile_scan as rps
import saved_digest
import scan_deliver
import storage

DAY = date(2026, 11, 3)
OPERATOR = 'operator@example.com'
TEAM = 'team@example.com'


def item(**changes):
    row = dict(item_id='gh:example/tool', profile_id='team_agent', task_id='TASK_PRIVATE_01',
               topic='결정표', name='공개 도구 <A> & B', what='규칙을 JSON으로 저장한다.',
               helps='판단 계층의 구조를 이해한다.', facts=['코드 MIT', 'Python 예제가 있다.'],
               refs=[dict(label='코드 · 문서', url='https://github.com/example/tool?x=1&y=2', note='환경 · 안내'),
                     dict(label='논문', url='https://arxiv.org/abs/2610.00001', note='')],
               status='ready', added_at='2026-11-01T00:00:00+00:00', checked_at='2026-11-01',
               source='manual', review={'source_excerpt': 'PRIVATE_REVIEW\n' * 10000})
    row.update(changes)
    return row


def config(base, **changes):
    cfg = dict(enabled=True, audience='operator', weekdays=[1, 3], max_items=2,
               profiles=['team_agent', 'team_robot'])
    cfg.update(changes)
    (base / 'config.json').write_text(json.dumps(cfg), encoding='utf-8')
    return cfg


@pytest.fixture
def assets(tmp_path, monkeypatch):
    base = tmp_path / 'research_assets'
    base.mkdir()
    monkeypatch.setattr(ra, 'default_base', lambda: base)
    monkeypatch.setattr(digest, 'reader_date', lambda: DAY.isoformat())
    config(base)
    assert ra.upsert_items([item()], base) == []
    return base


@pytest.fixture
def mail_db(tmp_path):
    db = tmp_path / 'mail.db'
    for pid in ('team_agent', 'team_robot', 'empty_daily', 'manual'):
        rp.create_profile(db, pid, pid, core_topics=['agent'])
    with sqlite3.connect(db) as con:
        con.execute("UPDATE profiles SET schedule_frequency='manual' WHERE profile_id='manual'")
        con.execute("UPDATE profiles SET schedule_frequency=NULL WHERE profile_id='empty_daily'")
    for pid in ('team_agent', 'team_robot', 'empty_daily'):
        rp.add_recipient(db, pid, OPERATOR)
    rp.add_recipient(db, 'team_agent', TEAM)
    rp.add_recipient(db, 'team_robot', TEAM, active=False)
    return db


@pytest.fixture
def fake_mail(monkeypatch):
    sent = []
    refused = set()
    def send(text, subject, recipients, html=None):
        assert len(recipients) == 1
        sent.append(dict(text=text, subject=subject, recipient=recipients[0], html=html))
        if recipients[0] in refused:
            raise RuntimeError('가짜 SMTP 거절')
    monkeypatch.setattr(email_delivery, 'send_digest_email', send)
    return sent, refused


def prepared():
    result = dict(papers=[], candidates_found=0)
    rps._attach_research_assets(result, 'team_agent')
    return result


def deliver(db, result):
    return scan_deliver._deliver(db, 'team_agent', result, digest.generate_digest(result, '팀'))


@pytest.mark.parametrize('bad', ['missing', 'json', 'disabled', 'audience', 'days', 'bool_day', 'limit', 'profiles', 'null'])
def test_invalid_config_omits_section(assets, bad):
    """설정 없음·손상·필드 오류·비활성에서 자료를 싣거나 메일 생성을 막으면 실패한다."""
    if bad == 'missing':
        (assets / 'config.json').unlink()
    elif bad == 'json':
        (assets / 'config.json').write_text('{')
    elif bad == 'null':
        (assets / 'config.json').write_text('null')
    else:
        changes = {'disabled': {'enabled': False}, 'audience': {'audience': 'other'},
                   'days': {'weekdays': [7]}, 'bool_day': {'weekdays': [True]},
                   'limit': {'max_items': 3}, 'profiles': {'profiles': 'team_agent'}}[bad]
        config(assets, **changes)
    assert ra.load_config(assets) is None
    result = prepared()
    assert result['research_assets'] == []
    for body in (digest.generate_digest(result, '팀'), digest.generate_digest_html(result, '팀')):
        assert '참고 자료' not in body and '새로 걸린 논문이 없습니다' in body


@pytest.mark.parametrize('pid,day', [('team_agent', date(2026, 11, 4)), ('other', DAY)])
def test_wrong_day_or_profile_omits_section(assets, pid, day):
    """설정 요일이나 프로필 필터를 제거해 대상 밖 자료를 싣거나 렌더링을 깨면 실패한다."""
    rows = ra.pick_for_mail(pid, day, assets)
    assert rows == []
    for body in (digest.generate_digest({'research_assets': rows}, '팀'), digest.generate_digest_html({'research_assets': rows}, '팀')):
        assert '참고 자료' not in body


@pytest.mark.parametrize('change', [
    {'refs': [dict(label='코드', url='http://github.com/a/b', note='')]},
    {'refs': [dict(label='코드', url='https://github.com.evil.example/a', note='')]},
    {'refs': [dict(label='코드', url='https://user:pass@github.com/a', note='')]},
    {'refs': []}, {'refs': [dict(label='x', url='https://github.com/a', note='')] * 5},
    {'name': None}, {'name': 'a' * 121}, {'topic': 'a' * 41}, {'what': 'a' * 701},
    {'helps': 'a' * 451}, {'facts': ['a' * 221]}, {'facts': ['a'] * 7},
    {'what': 'a\n■ 위조 절'}, {'helps': 'a\x00b'}, {'name': '숨김\u202eb'},
    {'status': 'unapproved'}, {'source': 'other'}, {'checked_at': '2026-13-01'},
    {'added_at': 'yesterday'}, {'review': 'text'}, {'refs': [None]}, {'facts': 'x'},
])
def test_invalid_items_are_rejected_and_skipped(tmp_path, caplog, change):
    """URL·필수 문자열·상한·제어문자 검사를 빼면 거부·저장 제외·읽기 제외 검사가 실패한다."""
    row = item(**change)
    assert ra.validate_item(row)
    assert ra.upsert_items([row], tmp_path)
    assert not (tmp_path / 'items.json').exists()
    (tmp_path / 'items.json').write_text(json.dumps([row]), encoding='utf-8')
    assert ra.load_items(tmp_path) == []
    assert len(caplog.records) == 1
    assert '@' not in caplog.text and 'https' not in caplog.text and 'PRIVATE_REVIEW' not in caplog.text


def test_missing_field_and_nonobject_rejected(tmp_path):
    """필수 필드 존재나 항목 타입 검사를 빼면 실패한다."""
    for field in item():
        if field == 'review':
            continue
        row = item()
        del row[field]
        assert ra.validate_item(row), field
    for row in (None, [], 'secret'):
        assert ra.validate_item(row)
        assert ra.upsert_items([row], tmp_path)


def test_valid_boundaries_and_atomic_upsert(tmp_path, monkeypatch):
    """상한의 정상 자료·큰 검토 원문을 거부하거나 교체 실패 때 기존 파일을 잃으면 실패한다."""
    row = item(name='n' * 120, topic='t' * 40, what='w' * 700, helps='h' * 450,
               facts=['f' * 220] * 6, refs=[dict(label='하위', url='https://docs.github.com/a', note='')] * 4)
    assert ra.validate_item(row) == []
    assert ra.upsert_items([row], tmp_path) == []
    changed = item(item_id=row['item_id'], name='갱신', facts=[])
    assert ra.upsert_items([changed, item(item_id='second'), item(name='x' * 121)], tmp_path)
    assert [r['name'] for r in ra.load_items(tmp_path)] == ['갱신', item()['name']]
    original = (tmp_path / 'items.json').read_bytes()
    def fail(*args):
        raise OSError('실패')
    monkeypatch.setattr(ra.os, 'replace', fail)
    assert ra.upsert_items([item(name='덮기')], tmp_path)
    assert (tmp_path / 'items.json').read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ['.items.lock', 'items.json']


def test_default_base_follows_isolated_storage():
    """기본 경로가 storage.DATA_DIR 대신 운영 경로를 고정하면 실패한다."""
    assert ra.default_base() == storage.DATA_DIR / 'research_assets'


def test_pick_orders_limits_and_filters_ready(assets):
    """정렬·두 건 상한·프로필·ready 필터나 review 제거를 빼면 실패한다."""
    rows = [item(item_id='z', added_at='2026-10-01T00:00:00Z'),
            item(item_id='a', added_at='2026-10-01T00:00:00Z'),
            item(item_id='pending', status='pending_review', added_at='2026-09-01T00:00:00Z'),
            item(item_id='rejected', status='rejected'), item(item_id='robot', profile_id='team_robot')]
    assert ra.upsert_items(rows, assets) == []
    picked = ra.pick_for_mail('team_agent', DAY, assets)
    assert [r['item_id'] for r in picked] == ['a', 'z']
    assert all('review' not in row for row in picked)
    config(assets, max_items=1)
    assert [r['item_id'] for r in ra.pick_for_mail('team_agent', DAY, assets)] == ['a']


def test_audience_ledger_and_next_day(assets):
    """같은 audience 재소개를 허용하거나 operator 기록으로 all 자료를 소진시키면 실패한다."""
    rows = ra.pick_for_mail('team_agent', DAY, assets)
    ra.record_sent('team_agent', rows, 'operator', DAY, assets)
    ledger = [json.loads(s) for s in (assets / 'sent.jsonl').read_text().splitlines()]
    assert len(ledger) == 1 and ledger[0]['day'] == DAY.isoformat()
    assert datetime.fromisoformat(ledger[0]['sent_at']).tzinfo is not None
    assert set(ledger[0]) == {'profile_id', 'item_id', 'audience', 'day', 'sent_at'}
    assert not ra.pick_for_mail('team_agent', date(2026, 11, 5), assets)
    config(assets, audience='all')
    assert ra.pick_for_mail('team_agent', date(2026, 11, 5), assets) == rows
    ra.record_sent('team_agent', rows, 'all', DAY, assets)
    assert not ra.pick_for_mail('team_agent', date(2026, 11, 10), assets)


@pytest.mark.parametrize('audience', ['operator', 'all'])
def test_recipient_scope_and_acceptance_record(assets, mail_db, fake_mail, monkeypatch, audience):
    """수신자별 본문 범위를 넓히거나 SMTP 수락 전에 기록하거나 여러 번 기록하면 실패한다."""
    config(assets, audience=audience)
    sent, _ = fake_mail
    original = ra.record_sent
    calls = []
    def record(*args):
        assert len(sent) == 2  # 수신자 순회가 끝난 뒤 한 번만 기록한다.
        calls.append(args)
        original(*args)
    monkeypatch.setattr(ra, 'record_sent', record)
    result = prepared()
    assert '참고 자료' in digest.generate_digest(result, '팀')
    assert deliver(mail_db, result).startswith('발송 완료')
    assert len(calls) == 1 and calls[0][2:4] == (audience, DAY)
    assert {m['recipient'] for m in sent} == {OPERATOR, TEAM}
    for message in sent:
        expected = audience == 'all' or message['recipient'] == OPERATOR
        assert ('참고 자료' in message['text']) == expected
        assert ('참고 자료' in message['html']) == expected
        assert 'TASK_PRIVATE' not in message['text'] + message['html']
        assert 'PRIVATE_REVIEW' not in message['text'] + message['html']
    assert not ra.pick_for_mail('team_agent', date(2026, 11, 5), assets)


@pytest.mark.parametrize('failures,recorded', [({OPERATOR, TEAM}, False), ({OPERATOR}, False), ({TEAM}, True)])
def test_partial_failure_records_only_section_acceptance(assets, mail_db, fake_mail, failures, recorded):
    """자료 없는 팀원 수락만으로 기록하거나 자료 수신자 수락을 놓치면 실패한다."""
    sent, refused = fake_mail
    refused.update(failures)
    result = prepared()
    assert deliver(mail_db, result).startswith('발송 실패')
    assert len(sent) == 2
    assert (assets / 'sent.jsonl').exists() == recorded
    assert bool(ra.pick_for_mail('team_agent', DAY, assets)) != recorded


def test_common_recipients_matches_alert_definition(mail_db):
    """NULL daily·활성 교집합을 무시하거나 manual을 포함하거나 빈 교집합에 대체자를 쓰면 실패한다."""
    assert ra.common_recipients(mail_db) == {OPERATOR}
    rp.add_recipient(mail_db, 'empty_daily', OPERATOR, active=False)
    assert ra.common_recipients(mail_db) == set()
    with sqlite3.connect(mail_db) as con:
        con.execute("UPDATE profiles SET schedule_frequency='manual'")
    assert ra.common_recipients(mail_db) == set()


@pytest.mark.parametrize('boundary', ['empty', 'query_error', 'config_change'])
def test_scope_failure_omits_only_assets(assets, mail_db, fake_mail, monkeypatch, boundary):
    """공통 수신자가 없거나 범위 조회 실패·대상 변경 때 팀원에게 자료를 보내거나 메일을 막으면 실패한다."""
    result = prepared()
    if boundary == 'empty':
        rp.add_recipient(mail_db, 'empty_daily', OPERATOR, active=False)
    elif boundary == 'query_error':
        def fail(*a):
            raise RuntimeError('실패')
        monkeypatch.setattr(ra, 'common_recipients', fail)
    else:
        config(assets, audience='all')
    assert deliver(mail_db, result).startswith('발송 완료')
    for message in fake_mail[0]:
        assert '참고 자료' not in message['text'] + message['html']
    assert not (assets / 'sent.jsonl').exists()


def test_record_failure_does_not_change_delivery(assets, mail_db, fake_mail, monkeypatch):
    """장부 쓰기 예외를 전송 실패로 바꾸면 실패한다."""
    def fail(*args):
        raise OSError('쓰기 실패')
    monkeypatch.setattr(ra, 'record_sent', fail)
    assert deliver(mail_db, prepared()).startswith('발송 완료')
    assert len(fake_mail[0]) == 2


@pytest.mark.parametrize('failure', ['config', 'items', 'ledger', 'pick'])
def test_selection_exception_keeps_digest_and_delivery(assets, mail_db, fake_mail, monkeypatch, failure):
    """선정의 설정·파일·장부 예외가 digest 또는 발송을 중단시키거나 자료를 노출하면 실패한다."""
    def fail(*args):
        raise OSError('읽기 실패')
    if failure == 'ledger':
        (assets / 'sent.jsonl').write_text('{')
    else:
        monkeypatch.setattr(ra, {'config': 'load_config', 'items': 'load_items', 'pick': 'pick_for_mail'}[failure], fail)
    if failure != 'pick':
        assert ra.pick_for_mail('team_agent', DAY, assets) == []
    result = prepared()
    assert not result.get('research_assets')
    assert '참고 자료' not in digest.generate_digest_html(result, '팀')
    assert deliver(mail_db, result).startswith('발송 완료')
    assert len(fake_mail[0]) == 2


def test_plain_html_placement_roundtrip_and_private_fields(assets, monkeypatch):
    """절 배치·표시 필드·공용 HTML 왕복·이스케이프를 깨거나 ID·원문·반응 버튼을 섞으면 실패한다."""
    from test_saved_digest_parity import realistic_result
    result = realistic_result(monkeypatch)
    result['research_assets'] = [item(), item(item_id='two', name='두 번째', facts=[])]
    result['core_hit_counts'] = {'agent': 9}
    plain = digest.generate_digest(result, '팀')
    html = digest.generate_digest_html(result, '팀')
    saved = saved_digest.render_html(plain, '팀', result['papers'])
    expected = digest.research_assets_html(result['research_assets'])
    assert expected in html and expected in saved
    section = next(s for s in saved_digest.parse(plain)['sections'] if s['heading'] == '참고 자료')
    parsed = saved_digest._assets_from_lines(section['lines'])
    assert parsed == [{k: row[k] for k in ('name', 'topic', 'what', 'helps', 'facts', 'refs')} for row in result['research_assets']]
    for body in (plain, html, saved):
        assert 'TASK_PRIVATE' not in body and 'PRIVATE_REVIEW' not in body
        assert body.index('Paper 5') < body.index('참고 자료')
        stat = '이번 창의 키워드별 적중 편수' if body == plain else '이번 검색에서 관심 키워드에 해당한 논문 수'
        assert body.index('참고 자료') < body.index(stat)
    assert '&lt;A&gt; &amp; B' in expected and '<A>' not in expected
    assert 'x=1&amp;y=2' in expected
    assert '관심 밖' not in expected and 'TASK_PRIVATE' not in expected
    readable = digest.readable_mail_text(plain)
    assert expected in saved_digest.render_html(readable, '팀', result['papers'])


def test_empty_papers_still_preserves_asset_roundtrip(assets):
    """논문 0편 조기 반환으로 자료 절이나 저장 왕복을 없애면 실패한다."""
    result = prepared()
    plain = digest.generate_digest(result, '팀')
    section = digest.research_assets_html(result['research_assets'])
    assert section in digest.generate_digest_html(result, '팀')
    assert section in saved_digest.render_html(plain, '팀', [])
    assert 'PRIVATE_REVIEW' not in plain


def test_collector_imports_with_storage_api():
    """수집기가 의존하는 모듈 이름이나 저장 API 를 깨면 import 검사가 실패한다."""
    import research_assets_collect
    assert research_assets_collect.research_assets is ra


@pytest.mark.parametrize('mode', ['single', 'all', 'pick_failure'])
def test_scan_entrypoints_save_before_recipient_filter(assets, tmp_path, monkeypatch, fake_mail, mode):
    """실제 단일·배치 경로에서 저장 전 부착을 빼거나 조정기 전역 patch를 우회하거나 선정 실패로 중단하면 실패한다."""
    import asyncio
    import evidence_state
    import feedback_links
    import feedback_weights
    import plain_ko
    import quote_ko
    import sys
    import title_ko
    db = tmp_path / 'entry.db'
    for pid in ('team_agent', 'team_robot'):
        rp.create_profile(db, pid, pid, core_topics=['agent'])
        rp.add_recipient(db, pid, OPERATOR)
    rp.add_recipient(db, 'team_agent', TEAM)
    assert ra.upsert_items([item(item_id='robot', profile_id='team_robot')], assets) == []
    called = []
    async def scan(db_path, pid, *args, **kwargs):
        called.append(pid)
        return dict(papers=[], candidates_found=0, scored_count=0, run_status='ok')
    async def empty(*args, **kwargs):
        return {}
    async def sweep(*args, **kwargs):
        return dict(checked=0, resolved=0, retracted=0, remaining=0)
    monkeypatch.setattr(rps, 'scan_profile', scan)
    monkeypatch.setattr(rps, 'is_weekly_review_day', lambda *a: False)
    monkeypatch.setattr(rps, 'record_weekly_diagnostics', empty)
    monkeypatch.setattr(rps.server, 'sweep_retraction_status', sweep)
    monkeypatch.setattr(feedback_links, 'sync', empty)
    monkeypatch.setattr(feedback_weights, 'update_profile', lambda *a: {})
    monkeypatch.setattr(evidence_state, 'prepare', empty)
    monkeypatch.setattr(title_ko, 'translate', empty)
    monkeypatch.setattr(quote_ko, 'translate', empty)
    monkeypatch.setattr(plain_ko, 'explain', empty)
    if mode == 'pick_failure':
        def fail(*args):
            raise OSError('선정 실패')
        monkeypatch.setattr(ra, 'pick_for_mail', fail)
    if mode == 'all':
        summary = asyncio.run(rps.scan_all_profiles(db, None, send=True))
        assert set(summary) == {'team_agent', 'team_robot'}
        assert all(row['delivery'].startswith('발송 완료') for row in summary.values())
        assert called == ['team_agent', 'team_robot']
    else:
        class Client:
            async def __aenter__(self):
                return None
            async def __aexit__(self, *args):
                return None
        monkeypatch.setattr(rps.httpx, 'AsyncClient', Client)
        monkeypatch.setattr(sys, 'argv', ['run_profile_scan.py', 'team_agent', '--db', str(db), '--send'])
        assert rps.main() == 0
        assert called == ['team_agent']
    with sqlite3.connect(db) as con:
        text = con.execute("SELECT last_digest FROM profiles WHERE profile_id='team_agent'").fetchone()[0]
    assert ('참고 자료' in text) == (mode != 'pick_failure')
    assert ('참고 자료' in saved_digest.render_html(text, '팀', [])) == (mode != 'pick_failure')
    assert all('참고 자료' not in m['html'] for m in fake_mail[0] if m['recipient'] == TEAM)


def test_kst_day_is_pinned_to_mail_header(assets, mail_db, fake_mail, monkeypatch):
    """UTC 날짜로 요일을 세거나 선정 뒤 날짜 재조회로 제목·본문·장부 날짜가 갈라지면 실패한다."""
    from time_policy import KST
    moment = datetime(2026, 11, 2, 20, 30, tzinfo=timezone.utc)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz)
    monkeypatch.setattr(digest, 'datetime', Clock)
    monkeypatch.setattr(digest, 'reader_date', lambda: digest.datetime.now(KST).strftime('%Y-%m-%d'))
    result = prepared()
    assert result['_research_assets_day'] == '2026-11-03'
    assert result['research_assets']
    monkeypatch.setattr(digest, 'reader_date', lambda: '2026-11-04')
    assert digest.generate_digest(result, '팀').splitlines()[0].endswith('2026-11-03')
    assert deliver(mail_db, result).startswith('발송 완료')
    for m in fake_mail[0]:
        assert m['subject'].endswith('2026-11-03') and '2026-11-03' in m['html']
    assert json.loads((assets / 'sent.jsonl').read_text())['day'] == '2026-11-03'
