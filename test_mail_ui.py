"""⑨ UI와 같은 회색 바탕·흰 카드·항목과 설명의 배치를 지킨다."""
from html.parser import HTMLParser

import pytest

import digest
import mail_document
import saved_digest
from test_mail_detail import DETAIL_LINES, HEADINGS, Regions
from test_mail_layout import SAVED


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.nodes = []
        self.stack = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = {'tag': tag, 'attrs': dict(attrs), 'parent': self.stack[-1] if self.stack else None}
        self.nodes.append(node)
        if tag not in ('br', 'hr', 'img', 'meta', 'link', 'input'):
            self.stack.append(node)

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1]['tag'] == tag:
            self.stack.pop()

    def by_class(self, name):
        return [node for node in self.nodes if node['attrs'].get('class') == name]


@pytest.mark.parametrize('saved', [False, True])
def test_both_mail_paths_use_bounded_gray_background_and_white_stat_cards(saved, monkeypatch):
    """새 메일/저장 메일의 바탕·지표 숫자를 잃거나 저장 카드만 별도 표 틀로 돌아가면 실패한다."""
    if saved:
        html = saved_digest.render_html(SAVED, '팀', [{'title': 'One & Paper'}], title_only_count=8)
        values = ['1편', '9건', '8편']
    else:
        # 저장 상태 조회만 가짜로 대고 실제 카드 생성·접힘·분석 함수는 호출한다.
        monkeypatch.setattr(digest, 'injection_label', lambda aid: '')
        paper = {'arxiv_id': '2609.00001', 'title': 'Paper', 'deep_status': 'abstract_only',
                 'abstract_brief': '무엇을·어떻게 : 원래 초록 분석'}
        html = digest.generate_digest_html({'papers': [paper], 'candidates_found': 19}, '팀')
        values = ['1편', '19건']
    parsed = Elements(html)
    shell = parsed.by_class('ph-shell')[0]
    main = parsed.by_class('ph-main')[0]
    assert shell['attrs']['style'] == 'width:100%;max-width:1056px;background-color:#EEF2F5;'
    assert main['parent']['parent']['attrs']['style'] == 'width:100%;max-width:1040px;background-color:#EEF2F5;'
    stats = parsed.by_class('ph-stat')
    assert len(stats) == len(values)
    for stat in stats:
        assert stat['attrs']['bgcolor'] == '#FFFFFF'
        assert 'background-color:#FFFFFF' in stat['attrs']['style']
    text = ''.join(Regions(html).outside)
    for value in values:
        assert value in text
    cards = parsed.by_class('ph-card')
    assert len(cards) == 1
    # 저장 경로의 별도 box 표를 요구하던 계약을 공용 일일 카드의 정확한 스타일로 바꿨다(2026-10-06).
    assert cards[0]['attrs']['style'] == (
        'background-color:#FFFFFF;color:#162536;border:1px solid #E2E8EC;'
        'border-radius:10px;padding:18px 20px 14px;margin:18px 0 0;')
    card_tables = [node for node in parsed.nodes if node['tag'] == 'table' and node['parent'] is cards[0]]
    assert card_tables == []
    assert Regions(html).toggles == 1 and Regions(html).open_toggles == 0


@pytest.mark.parametrize('saved', [False, True])
def test_analysis_uses_tinted_labels_and_white_body_without_changing_evidence(saved, monkeypatch):
    """항목명/본문의 같은 절 관계·색·원 수치/S번호·하위 모델/학습을 깨면 실패한다."""
    if saved:
        html = mail_document.from_lines(DETAIL_LINES, saved_digest._line)
    else:
        monkeypatch.setattr(digest, 'summary_sections', lambda aid: {
            'overview': ['목적 <unsafe> [S0011]'], 'method': ['모델 : Qwen 2B', '학습 : SFT → RL'],
            'setup': ['비교 조건 : L4, L5'], 'results': ['성공 76.3% / 기준 3.7% [S0018]'],
            'author_limits': '단일 시행 [S0020]', 'limits': '일반화 미확인'})
        html = digest._summary_block_html('2609.00001', {'title': 'Paper'}, 'ok')
    parsed = Elements(html)
    labels, bodies = parsed.by_class('ph-label'), parsed.by_class('ph-detail-body')
    assert len(labels) == len(bodies) == 6
    for label, body in zip(labels, bodies):
        assert label['tag'] == body['tag'] == 'td'
        assert label['parent'] is body['parent']
        assert label['parent']['tag'] == 'tr'
        assert label['attrs']['bgcolor'] == '#EDF5FA'
        assert body['attrs']['bgcolor'] == '#FFFFFF'
        assert label['attrs']['align'] == 'center' and label['attrs']['valign'] == 'middle'
        assert body['attrs']['align'] == 'left' and body['attrs']['valign'] == 'top'
    regions = Regions(html)
    assert regions.section_titles == HEADINGS
    text = ''.join(regions.outside)
    for phrase in ['모델 : Qwen 2B', '학습 : SFT → RL', '성공 76.3% / 기준 3.7% [S0018]', '단일 시행 [S0020]']:
        assert phrase in text
    assert '<unsafe>' not in html and '&lt;unsafe&gt;' in html
    if saved:
        assert '알 수 없는 메모 : 그대로 보존' in text


def test_detail_keeps_real_cells_without_styles_and_escapes_the_label():
    """항목명을 본문 위로 쌓거나 좁은 화면에서 칸의 폭을 줄이지 않거나 HTML을 실행하면 실패한다."""
    import mail_layout
    html = mail_layout.frame(mail_document.document(mail_document.section('<unsafe>', '원래 내용')))
    assert '.ph-label{width:88px!important;padding:8px 6px!important}' in html
    assert 'width="120" align="center" valign="middle"' in html
    assert 'class="ph-detail-body" align="left" valign="top"' in html
    assert 'display:inline-table' not in html
    assert '&lt;unsafe&gt;' in html and '<unsafe>' not in html
    assert '원래 내용' in html


def test_section_label_is_centered_horizontally_and_vertically():
    """항목명이 긴 본문 옆에서 왼쪽이나 위로 붙거나 Outlook 칸만 위 정렬로 남으면 실패한다."""
    html = mail_document.document(mail_document.section('방법 상세', '긴 설명 ' * 60))
    parsed = Elements(html)
    title = parsed.by_class('ph-section-title')[0]
    label = parsed.by_class('ph-label')[0]
    body = parsed.by_class('ph-detail-body')[0]
    assert 'text-align:center' in title['attrs']['style']
    assert 'vertical-align:middle' in label['attrs']['style']
    assert label['tag'] == body['tag'] == 'td'
    assert body['attrs']['align'] == 'left' and body['attrs']['valign'] == 'top'
    assert label['attrs']['align'] == 'center' and label['attrs']['valign'] == 'middle'
