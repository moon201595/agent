"""⑨ 상세 문서의 경고·분석·반응 경계와 문서형 절을 지킨다."""
from html.parser import HTMLParser

import digest
import mail_document
import mail_layout
import saved_digest
from test_mail_layout import SAVED


class Regions(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.depth = 0
        self.inside = []
        self.outside = []
        self.toggles = 0
        self.open_toggles = 0
        self.stack = []
        self.section_titles = []
        self.sections = []
        self.section_bodies = []
        self.list_styles = []
        self.info_blocks = []
        self.cards = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get('class') == 'ph-card':
            attrs['_owner'] = len(self.cards)
            self.cards.append({'toggles': 0, 'body': []})
        if attrs.get('class') == 'ph-section':
            self.sections.append(attrs)
        if attrs.get('class') == 'ph-section-body':
            self.section_bodies.append(attrs)
        if tag == 'li':
            self.list_styles.append(attrs.get('style', ''))
        if attrs.get('class') == 'ph-info':
            self.info_blocks.append(attrs)
        if tag not in ('br', 'hr', 'img', 'meta', 'link', 'input'):
            self.stack.append((tag, attrs))
        if tag == 'details':
            self.depth += 1
            self.toggles += 1
            self.open_toggles += int('open' in dict(attrs))
            owner = self.owner()
            if owner is not None:
                self.cards[owner]['toggles'] += 1

    def handle_endtag(self, tag):
        if tag == 'details':
            self.depth -= 1
        if self.stack and self.stack[-1][0] == tag:
            self.stack.pop()

    def handle_data(self, value):
        (self.inside if self.depth else self.outside).append(value)
        owner = self.owner()
        if self.depth and owner is not None:
            self.cards[owner]['body'].append(value)
        if self.stack and self.stack[-1][1].get('class') == 'ph-section-title':
            self.section_titles.append(value)

    def owner(self):
        """직전 카드 번호 대신 실제 부모를 찾으므로 카드 밖으로 새는 분석도 잡는다."""
        return next((attrs['_owner'] for _, attrs in reversed(self.stack) if attrs.get('class') == 'ph-card'), None)


def test_warning_before_analysis_keeps_last_paper_closed_and_body_inside_toggle():
    """앞 경고가 상세 토글을 없애거나 본문을 밖으로 밀거나 경고/반응을 안으로 감추면 실패한다."""
    text = SAVED.replace('   핵심 키워드:', '   [⚠ 본문에 모델 대상 지시로 보이는 패턴 — 확인 필요]\n   핵심 키워드:')
    html = saved_digest.render_html(text, '팀', [{'title': 'One & Paper', 'link': 'https://arxiv.org/abs/2609.00001',
                                               '_feedback_links': {'more': 'https://example.com/original-token'}}])
    parsed = Regions(html)
    assert parsed.toggles == 1 and parsed.open_toggles == 0
    assert '저장 분석 <unsafe> & 42' in ''.join(parsed.inside)
    assert '정확도 76.3%, 비교 3.7% [S0012]' in ''.join(parsed.inside)
    assert '저장 분석 <unsafe> & 42' not in ''.join(parsed.outside)
    assert '[⚠ 본문에 모델 대상 지시로 보이는 패턴 — 확인 필요]' in ''.join(parsed.outside)
    assert '[원문 분석 완료] [재현 실패]' in ''.join(parsed.outside)
    assert html.index('original-token') > html.index('</details>')


def test_all_five_saved_cards_keep_their_own_closed_analysis_even_with_last_warning():
    """5편 중 경고가 있는 마지막 편만 토글이 없거나 분석이 다른 카드로 넘어가면 실패한다."""
    text = '연구 동향 브리핑 · 2026-10-02\n■ 오늘의 핵심 논문 5편 (전체 후보 8건 중)\n'
    text += '\n'.join(f'{i}. Paper {i}\n' + ('   [⚠ 입력 경고]\n' if i == 5 else '')
                      + f'   요지 {i}\n   무엇을·어떻게 :\n   - 분석 대상 {i} [S000{i}]\n   [원문 분석 완료]'
                      for i in range(1, 6))
    html = saved_digest.render_html(text, '팀', [{'title': f'Paper {i}'} for i in range(1, 6)])
    parsed = Regions(html)
    assert parsed.toggles == 5 and parsed.open_toggles == 0 and len(parsed.cards) == 5
    assert [card['toggles'] for card in parsed.cards] == [1, 1, 1, 1, 1]
    assert [''.join(card['body']).strip() for card in parsed.cards] == [
        f'＋ 상세 분석 펼쳐보기연구 개요- 분석 대상 {i} [S000{i}]근거 · S000{i}' for i in range(1, 6)]
    assert '분석 대상 5' not in ''.join(parsed.outside)


DETAIL_LINES = ['무엇을·어떻게 :', '- 목적 <unsafe> [S0011]', '방법 상세 :', '- 모델 : Qwen 2B',
                '- 학습 : SFT → RL', '실험 설정 :', '- 비교 조건 : L4, L5', '핵심 결과 :',
                '- 성공 76.3% / 기준 3.7% [S0018]', '저자가 명시한 한계 : 단일 시행 [S0020]',
                '요약자의 해석 : 일반화 미확인', '알 수 없는 메모 : 그대로 보존']
HEADINGS = ['연구 개요', '방법론', '실험 구성', '주요 결과', '연구 한계', '분석 메모']


def assert_document(html):
    parsed = Regions(html)
    assert parsed.section_titles == HEADINGS
    assert len(parsed.sections) == 6
    assert len(parsed.section_bodies) == 6
    for section in parsed.sections:
        assert 'border-top:1px solid #DDE5E9' in section['style']
        assert 'border-radius' not in section['style'] and 'border:1px' not in section['style']
    for section in parsed.section_bodies:
        assert 'font-size:14px' in section['style'] and 'line-height:1.8' in section['style']
    text = ''.join(parsed.outside + parsed.inside)
    for phrase in ['목적 <unsafe> [S0011]', '모델 : Qwen 2B', '학습 : SFT → RL', '비교 조건 : L4, L5',
                   '성공 76.3% / 기준 3.7% [S0018]', '단일 시행 [S0020]', '일반화 미확인']:
        assert phrase in text
    assert '<unsafe>' not in html and '&lt;unsafe&gt;' in html
    assert 'font-size:14px' in html and 'line-height:1.8' in html


def test_saved_analysis_groups_only_known_headings_as_document_sections():
    """저장 절을 카드로 쪼개거나 하위 모델/학습 콜론을 상위 절로 오인하거나 한계·미지 메모를 누락하면 실패한다."""
    html = mail_document.from_lines(DETAIL_LINES, saved_digest._line)
    assert_document(html)
    assert '알 수 없는 메모 : 그대로 보존' in ''.join(Regions(html).outside)


def test_generated_analysis_uses_same_document_sections_and_keeps_all_source_values(monkeypatch):
    """새 메일 경로만 옛 촘촘한 분석/구분 없는 한계로 남거나 수치·문장 번호를 바꾸면 실패한다."""
    sections = {'overview': ['목적 <unsafe> [S0011]'], 'method': ['모델 : Qwen 2B', '학습 : SFT → RL'],
                'setup': ['비교 조건 : L4, L5'], 'results': ['성공 76.3% / 기준 3.7% [S0018]'],
                'author_limits': '단일 시행 [S0020]', 'limits': '일반화 미확인'}
    monkeypatch.setattr(digest, 'summary_sections', lambda aid: sections)
    html = digest._summary_block_html('2609.00001', {'title': 'Paper'}, 'ok')
    assert_document(html)
    styles = Regions(html).list_styles
    assert len(styles) == 5
    assert all('font-size:14px' in style and 'line-height:1.8' in style for style in styles)


def test_legacy_and_expanded_external_signals_keep_colored_info_outside_toggle():
    """옛/확장 신호의 옅은 색·관측값·이스케이프를 잃거나 분석 접힘 안으로 숨기면 실패한다."""
    old = saved_digest._line('외부 신호 인용 0회 · 공식 GitHub ★0 · <repo>')
    new = digest._observation_blocks_html({'_signals': {'scholarly': {'citations': 0, 'influential': 0},
                                                    'github': {'repo': '<repo>', 'stars': 0}, 'github_tier': 'official'}})
    for html in (old, new):
        parsed = Regions(html)
        assert len(parsed.info_blocks) == 1 and parsed.toggles == 0
        assert parsed.info_blocks[0]['bgcolor'] == '#EDF5FA'
        assert 'background-color:#EDF5FA' in html and 'border-left:3px solid #7394A6' in html
        assert '인용 0회' in ''.join(parsed.outside) and '★0' in ''.join(parsed.outside)
        assert '<repo>' not in html and '&lt;repo&gt;' in html


def test_warning_tint_and_gray_canvas_are_distinct_from_signal_tint():
    """UI 시안의 회색 바탕/파란 관측/주황 경고를 같게 칠하거나 큰 여백으로 되돌리면 실패한다."""
    html = mail_layout.frame(saved_digest._line('[⚠ <warning>]'))
    assert 'background-color:#EEF2F5' in html and 'max-width:1040px;background-color:#EEF2F5' in html
    assert 'class="ph-canvas" align="center" style="padding:8px;"' in html
    assert '.ph-canvas{padding:4px!important}' in html
    assert 'bgcolor="#FFF4E5"' in html and '#7394A6' not in html
    assert '&lt;warning&gt;' in html and '<warning>' not in html
