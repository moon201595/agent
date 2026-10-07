"""⑨ 저장 문장의 숫자와 원래 메일의 배치를 함께 지킨다."""
from copy import deepcopy

import digest
import saved_digest
from test_mail_canvas import WEEKLY_SCAN
from test_mail_layout import SAVED, VisibleText


def realistic_result(monkeypatch) -> dict:
    """실측용 입력은 다섯 카드와 네 원문 분석을 고정해 누락을 숨기지 않는다."""
    monkeypatch.setattr(digest, 'reader_date', lambda: '2026-10-06')
    monkeypatch.setattr(digest, 'summary_sections', lambda aid: {
        'one_liner': '성공률 76.3%를 보고했다.', 'overview': ['저장 분석 42 [S0012]'],
        'method': ['학습 3단계'], 'setup': ['표본 120개'], 'results': ['76.3%, 비교 3.7% [S0012]'],
        'limits': '표본의 한계', 'author_limits': ''})
    monkeypatch.setattr(digest, 'coverage_label', lambda aid: '')
    monkeypatch.setattr(digest, 'injection_label', lambda aid: '')
    monkeypatch.setattr(digest, '_paper_retraction_label', lambda p: '')
    monkeypatch.setattr(digest, '_paper_repro_label', lambda p: '[재현 실패]')
    monkeypatch.setattr(digest, 'code_ladder_line', lambda aid: '코드: 공식 구현')
    monkeypatch.setattr(digest, '_observation_sections', lambda p: [
        ('성능 관측 · 논문 내 결과', [('정확도 76.3% [S0012]', '')]),
        ('논문 간 관측', [('동일 조건 미확인', '')]),
        ('외부 관측 신호', [('인용 12회', '')])])
    scan = deepcopy(WEEKLY_SCAN)
    scan['papers'] = [dict(arxiv_id=f'2610.0000{i}', title=f'Paper {i}', deep_status='ok' if i < 5 else 'abstract_only',
                           abstract_brief='무엇을 하려 했는가: 표본 120개',
                           _score={'core_hits': ['agent']},
                           _feedback_links={'more': f'https://example.com/old-token-{i}'}) for i in range(1, 6)]
    scan['narrative'] = ('■ 오늘의 요점\n두 문제를 다룬다.\n■ 1. 예측\nPaper 1과 Paper 2를 비교한다.\n■ 2. 이전\nPaper 3과 Paper 4를 비교한다.\n■ 주변 신호\n외부 표본을 관측했다.', [])
    return scan


def test_saved_cards_share_the_daily_frame_and_preserve_evidence(monkeypatch):
    """카드 공용 틀·관측 상자·상세 표·두 흐름 칸을 빼거나 숫자·S번호·기존 토큰을 바꾸면 실패한다."""
    scan = realistic_result(monkeypatch)
    plain = digest.generate_digest(scan, '팀')
    live = digest.generate_digest_html(scan, '팀')
    saved = saved_digest.render_html(plain, '팀', scan['papers'])
    for html in (live, saved):
        assert html.count('class="ph-card"') == 5
        assert html.count('class="ph-col"') == 2
        assert html.count('class="ph-info"') == 5
        assert html.count('class="ph-document"') == 5
        assert 'border-radius:10px;padding:18px 20px 14px' in html
        assert '76.3%' in html and '3.7%' in html and '[S0012]' in html
        for i in range(1, 6):
            assert f'href="https://example.com/old-token-{i}"' in html
        assert '그 밖에 작게 움직인 가중치' not in html
    assert saved.index('성능 관측 · 논문 내 결과') < saved.index('<details')
    assert 'padding:12px 14px' in saved and 'padding:10px 14px 0' in saved
    assert '반영된 사용자 반응 15건' in saved


def test_saved_render_uses_shared_card_renderer(monkeypatch):
    """저장 렌더러가 독립 카드 틀로 돌아가면 공용 함수 호출과 반환값 검사가 실패한다."""
    calls = []
    def render(*args):
        calls.append(args)
        return 'SHARED_CARD'
    monkeypatch.setattr(digest, '_render_card_parts', render)
    html = saved_digest.render_html(SAVED, '팀', [{'title': 'One & Paper'}])
    assert len(calls) == 1 and 'SHARED_CARD' in html
    assert '76.3%' in calls[0][7] and '[S0012]' in calls[0][7]


def test_saved_observation_does_not_swallow_gist_or_original_reaction_line():
    """관측 뒤 요지를 관측 상자 안에 넣거나 평문 반응 줄을 중복 출력하면 실패한다."""
    text = SAVED.replace('   요지 숫자', '   성능 관측 · 논문 내 결과\n     정확도 42%\n   요지 숫자')
    text = text.replace('   https://arxiv.org/abs/2609.00001', '   반응 : 기존 링크\n   https://arxiv.org/abs/2609.00001')
    html = saved_digest.render_html(text, '팀', [{'title': 'One & Paper', 'link': 'https://arxiv.org/abs/2609.00001'}])
    assert html.index('요지 숫자') < html.index('성능 관측 · 논문 내 결과')
    assert '반응 : 기존 링크' not in ''.join(VisibleText(html).data)



def test_saved_observation_wins_over_current_metadata(monkeypatch):
    """원래 관측이 있는데 현재 관측을 조회해 수치를 덮거나 더하면 실패한다."""
    text = SAVED.replace('   요지 숫자', '   성능 관측 · 논문 내 결과\n     정확도 42%\n   요지 숫자')
    def forbidden(paper):
        raise AssertionError('과거 관측을 현재 값으로 덮었다')
    monkeypatch.setattr(digest, '_observation_blocks_html', forbidden)
    html = saved_digest.render_html(text, '팀', [{'title': 'One & Paper', '_frontier': {'new': '99%'}}])
    assert '정확도 42%' in html and '99%' not in html


def rich_weekly(monkeypatch, *, comparable=True, failed=None) -> dict:
    """주간 브리프의 모든 갈래(상승·하락·자리 밖·외부 정찰 링크 유무·신규·삭제·정밀도 근거·영향 표·실패)를 채운다."""
    scan = realistic_result(monkeypatch)
    scan['trend_window'] = {'days': 7, 'papers': (1128, 593), 'days_covered': (4, 3), 'comparable': comparable,
                            'keywords': [('reinforcement learning', 178, 88), ('world model', 4, 9), ('edge ai', 3, 3)],
                            'terms': [('learning with verifiable', 43, 0), ('latent space', 21, 4)]}
    scan['reserve_terms'] = {'count': 702, 'terms': [('action generation', 24), ('policy learning', 24)]}
    scan['external_scout'] = {'verified': 3, 'capture': {'evaluable': 3, 'retrieved': 2, 'core_hit': 2, 'delivered': 0},
                              'missed': [{'title': 'Agent as Policy', 'venue': 'CVPR 2026', 'link': 'https://arxiv.org/abs/2609.12541',
                                          'stage': '관련인데 자리에서 밀림'},
                                         {'title': 'No Link Paper', 'venue': '', 'link': '', 'stage': '검색 소스가 못 가져옴'}]}
    scan['profile_changes'] = {
        'window': ('2026-09-28T00:00:00+00:00', '2026-10-06T00:00:00+00:00'), 'days': 7, 'by_actor': {}, 'reactions_used': 5,
        'weights': [{'keyword': 'defect detection', 'kind': 'core', 'before': 1.0, 'after': 0.6, 'delta': -0.4, 'origins': ('agent',)},
                    {'keyword': 'vision-language navigation', 'kind': 'core', 'before': 0.4, 'after': 0.6, 'delta': 0.2,
                     'origins': ('feedback', 'agent')}],
        'added': [{'keyword': 'autonomous driving', 'kind': 'core', 'origins': ('agent',)}],
        'removed': [{'keyword': 'old seed', 'kind': 's2_seed', 'origins': ('agent',)}],
        'agent': {'applied': [{'term': 'defect detection', 'op': 'set_weight', 'basis': 'precision', 'evidence_count': 5,
                               'reason': '단독 적중 표본이 분야 밖이다.'},
                              {'term': 'autonomous driving', 'op': 'add_keyword', 'basis': 'feedback+trend',
                               'reason': '동향 자료에서 확인해 반영'}],
                  'shadow': {'eligible_before': 258, 'eligible_after': 260, 'eligible_lost': 0, 'topk_overlap': 1.0},
                  'impact': {'gained': 8, 'lost': 0, 'topk_changed': 0}, 'failed': failed}}
    return scan


def test_saved_weekly_brief_is_drawn_by_the_daily_weekly_functions(monkeypatch):
    """2026-10-06 사용자 지적: 형식 수정본의 이번 주 브리프가 아침 메일과 달리 줄 나열이었다(두 칸·칩·주황 딱지·막대·영향 표 없음).

    저장 평문 → `render_html` 이 낸 HTML 에 **실제 메일 경로의 HTML 조각이 그대로** 들어 있어야 한다. 기대값은 digest 생산 함수가
    직접 낸 것이고 여기서 다시 조립하지 않는다. 실패시키는 것: 역파싱 하나라도 빼고 줄 렌더러로 그리는 것, 증감 부호·편수를 잘못
    되살리는 것, 막대 칸 수·근거 딱지(정밀도 편수 포함)·영향 표 이름·실패 문구를 잃는 것, 학회명·링크 없는 정찰 항목을 버리는 것."""
    for comparable, failed in ((True, None), (False, None), (True, 'timeout')):
        scan = rich_weekly(monkeypatch, comparable=comparable, failed=failed)
        saved = saved_digest.render_html(digest.generate_digest(scan, '팀'), '팀', scan['papers'])
        for part in (digest._window_html(scan), digest._external_scout_html(scan), digest._profile_changes_html(scan)):
            assert part and part in saved, (comparable, failed, part[:80])
    assert '정밀도 근거(단독 적중 표본 5편)' in saved and '<table' in digest._window_html(rich_weekly(monkeypatch))


def test_saved_weekly_brief_keeps_unknown_lines_instead_of_guessing(monkeypatch):
    """형식이 맞지 않는 줄이 있으면 그 절만 옛 줄 렌더러로 떨어지고 글자는 남는다. 실패시키는 것: 모르는 줄을 버리는 것,
    억지로 구조에 끼워 다른 절까지 망가뜨리는 것(외부 정찰은 여전히 공용 함수로 그려진다)."""
    scan = rich_weekly(monkeypatch)
    plain = digest.generate_digest(scan, '팀').replace('   관측일 4일 / 직전 3일', '   관측일 4일 / 직전 3일\n   손으로 고친 줄 77')
    saved = saved_digest.render_html(plain, '팀', scan['papers'])
    text = ''.join(VisibleText(saved).data)
    assert '손으로 고친 줄 77' in text and 'reinforcement learning' in text and '+90' in text
    assert digest._window_html(scan) not in saved
    assert digest._external_scout_html(scan) in saved and digest._profile_changes_html(scan) in saved


def test_saved_scout_without_window_is_not_dropped(monkeypatch):
    """창 집계·자리 밖 후보가 없는 주에는 `▶ 외부 정찰` 줄이 브리프 띠 절에 붙어 저장된다. 띠만 그리고 넘어가면 정찰이 통째로
    사라진다 — 그 경우를 잡는다."""
    scan = rich_weekly(monkeypatch)
    scan.pop('trend_window'); scan.pop('reserve_terms')
    saved = saved_digest.render_html(digest.generate_digest(scan, '팀'), '팀', scan['papers'])
    assert digest._external_scout_html(scan) in saved


def test_saved_keyword_hits_strip_uses_the_daily_strip(monkeypatch):
    """키워드별 적중 편수 띠도 실제 메일과 같은 함수로 그린다 — 줄 렌더러로 그리면 제목 주석·글자 크기가 달라진다."""
    scan = rich_weekly(monkeypatch)
    scan['core_hit_counts'] = {'reinforcement learning': 313, 'world model': 82}
    saved = saved_digest.render_html(digest.generate_digest(scan, '팀'), '팀', scan['papers'])
    strip = digest._keyword_hits_html(f"(후보 {scan['candidates_found']}건 기준)", 'reinforcement learning 313 · world model 82')
    assert strip in digest.generate_digest_html(scan, '팀') and strip in saved


def test_saved_and_daily_keyword_hits_keep_inner_padding(monkeypatch):
    """일일 또는 저장 경로에서 여백 있는 공용 띠를 빼면 실패한다. 실제 출력의 좌우 여백을 확인한다."""
    from test_mail_canvas import Tree, _all, _side_padding
    scan = rich_weekly(monkeypatch)
    scan['core_hit_counts'] = {'agent': 12, 'world model': 4}
    for html in (digest.generate_digest_html(scan, '팀'),
                 saved_digest.render_html(digest.generate_digest(scan, '팀'), '팀', scan['papers'])):
        strip = next(n for n in _all(Tree(html).root) if n['tag'] == 'p' and 'agent 12' in n['text'])
        assert all(p >= 12 for p in _side_padding(strip['style']))


def test_saved_weekly_edge_lines_fall_back_with_original_text(monkeypatch):
    """§226 독립 검토의 경계 입력 세 가지. 실패시키는 것: ① 브리프 띠 아래 일반 줄을 버리는 것 ② 자리 밖 후보 머리가 두 번 오면
    뒤 편수로 앞 편수를 덮는 것 ③ `▲` 인데 `−` 인 모순 줄을 받아 하락으로 바꿔 그리는 것. 셋 다 원래 글자가 보여야 하고
    ②③ 은 그 절이 공용 흐름 함수로 그려지면 안 된다(=줄 렌더러로 떨어진다)."""
    scan = rich_weekly(monkeypatch)
    plain = digest.generate_digest(scan, '팀')
    banner = next(line for line in plain.splitlines() if line.startswith('■ ' + digest.WEEKLY_BRIEF_TITLE))
    cases = {
        'banner': plain.replace(banner, banner + '\n   보존해야 할 추가 근거 77'),
        'reserve': plain.replace('   ▸ 자리에 못 든 후보 702편에서 자주 나온 말',
                                 '   ▸ 자리에 못 든 후보 702편에서 자주 나온 말\n      action generation : 24편\n'
                                 '   ▸ 자리에 못 든 후보 7편에서 자주 나온 말'),
        'sign': plain.replace('▲ reinforcement learning', '▲ reinforcement learning', 1).replace('   +90', '   −90', 1),
    }
    assert cases['sign'] != plain and '−90' in cases['sign']
    for name, text in cases.items():
        saved = saved_digest.render_html(text, '팀', scan['papers'])
        visible = ''.join(VisibleText(saved).data)
        if name == 'banner':
            assert '보존해야 할 추가 근거 77' in visible
        elif name == 'reserve':
            assert '702편' in visible and digest._window_html(scan) not in saved
        else:
            assert '−90' in visible and '▼ reinforcement learning' not in visible
