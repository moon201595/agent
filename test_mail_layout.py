"""새 메일 배치와 저장 본문의 재배치는 기존 분석을 바꾸면 안 된다."""
from html.parser import HTMLParser

import pytest

import digest
import saved_digest


class VisibleText(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.data = []
        self.feed(html)

    def handle_data(self, data):
        self.data.append(data)


def test_short_threads_pair_and_long_threads_keep_full_width():
    """두 흐름의 표 칸·Outlook 표·모바일 폭을 없애거나 긴 절을 반 폭에 넣으면 실패한다."""
    short = "■ 오늘의 한 줄\n결론\n■ 1. 첫 흐름\n선택\n■ 2. 두 번째 흐름\n탐색"
    scan = {"narrative": (short, []), "papers": [], "title_only_papers": []}
    html = digest.generate_digest_html(scan, "프로필")
    # 2026-10-02 후속 요청으로 고정340/680/720px 계약을 비율/최대1040px으로 바꿨다.
    assert html.count('class="ph-col"') == 2 and '<td width="50%" valign="top">' in html
    assert '<!--[if mso]><table role="presentation" width="100%"' in html
    assert '.ph-col{width:50%!important}' in html
    assert "@media screen and (max-width:600px)" in html and 'max-width:1040px' in html
    assert 'role="presentation"' in html and 'display:inline-table' in html
    scan["narrative"] = (short.replace("선택", "긴 분석 " * 250), [])
    wide = digest.generate_digest_html(scan, "프로필")
    assert 'class="ph-col"' not in wide and "긴 분석" in wide and "두 번째 흐름" in wide


def test_story_links_stay_inline_and_do_not_repeat_named_papers():
    """제목의 원문 링크를 없애거나 별도 목록을 다시 반복하거나 HTML을 신뢰하면 실패한다."""
    title = "Outside <script>alert(1)</script>"
    scan = {"papers": [], "title_only_papers": [{"title": title, "arxiv_id": "2609.00001"}],
            "narrative": ("■ 주변 신호\n- " + title + " — 연구 관찰 [P3:A]", [])}
    html = digest.generate_digest_html(scan, "프로필")
    assert '<a href="https://arxiv.org/abs/2609.00001"' in html
    assert "위에서 이름으로 부른 논문" not in html
    assert '<span style="color:#526675;font-size:11.5px;white-space:nowrap;">[P3:A]</span>' in html
    assert "Outside &lt;script&gt;" in html and "<script>" not in html


SAVED = """연구 동향 브리핑 · 2026-10-02
■ 오늘의 연구 흐름
   ■ 오늘의 한 줄
   오늘 요지 [P1:A]
   ■ 1. 첫 흐름
   역할을 비교한다 [P1:A][P2:A]
   - One & Paper [P1:A]
   ■ 2. 두 번째 흐름
   다른 방법을 본다 [P2:A][P3:A]
   - Other paper [P3:A]
   ▸ 위에서 이름으로 부른 논문
      · Other paper (다른 논문) — https://arxiv.org/abs/2609.00003
■ 오늘의 핵심 논문 1편 (전체 후보 9건 중)
1. One & Paper (첫 논문)
   핵심 키워드: agent
   요지 숫자 76.3%
   읽을 포인트 — 조건
   무엇을·어떻게 :
     - 저장 분석 <unsafe> & 42
   핵심 결과 :
     - 정확도 76.3%, 비교 3.7% [S0012]
   [원문 분석 완료] [재현 실패]
   https://arxiv.org/abs/2609.00001
■ 이번 창의 키워드별 적중 편수 (후보 9건 기준)
   agent 3건
■ 이번 실행에서 걸러진 것: 이미 보낸 논문 2건
"""


def test_saved_render_keeps_all_analysis_and_old_feedback_outside_toggle():
    """저장 요지·결과·주의·원문을 누락하거나 토글·기존 버튼을 바꾸거나 새 과거 해석을 넣으면 실패한다."""
    papers = [{"title": "One & Paper", "link": "https://arxiv.org/abs/2609.00001", "_feedback_links": {
        "more": "https://example.com/original-token"}}]
    html = saved_digest.render_html(SAVED, "사용자 <팀>", papers, title_only_count=8)
    text = ''.join(VisibleText(html).data)
    for expected in ["요지 숫자 76.3%", "읽을 포인트 — 조건", "저장 분석 <unsafe> & 42", "정확도 76.3%, 비교 3.7% [S0012]",
                     "[원문 분석 완료] [재현 실패]", "agent 3건", "이미 보낸 논문 2건"]:
        assert expected in text
    assert html.count("<details ") == 1 and html.index("원문 분석 완료") < html.index("<details ")
    assert html.index("original-token") > html.index("</details>")
    assert "<unsafe>" not in html and "사용자 &lt;팀&gt;" in html
    assert "위에서 이름으로 부른 논문" not in html
    assert 'href="https://arxiv.org/abs/2609.00003"' in html
    assert "지난 관측" not in html
    assert '제목만 실은 논문' in text and '8편' in text and '9건' in text


@pytest.mark.parametrize("changed", [SAVED.replace("1편", "2편"), SAVED.replace("1. One", "2. One")])
def test_saved_render_rejects_count_or_position_mismatch(changed):
    """본문의 카드 수·순번을 대조하지 않으면 다른 회차로 재발송될 수 있어 실패해야 한다."""
    with pytest.raises(ValueError):
        saved_digest.parse(changed)


def test_saved_render_rejects_wrong_issue_papers():
    """다른 발송 회차의 논문 메타데이터를 붙이면 실패해야 한다."""
    with pytest.raises(ValueError):
        saved_digest.render_html(SAVED, "팀", [{"title": "Different paper", "link": "https://arxiv.org/abs/2609.00002"}])


def test_inline_links_cover_multiple_overlapping_titles_without_touching_markup():
    """한 줄의 두 번째 제목 링크를 잃거나 짧은 이름으로 긴 제목 안에 링크를 중첩하거나 CSS 속성을 바꾸면 실패한다."""
    html = digest._narrative_boxes_html("■ 주변 신호\n- color와 color extended를 비교한다", [
        {"title": "color", "link": "https://arxiv.org/abs/2609.00001"},
        {"title": "color extended", "link": "https://arxiv.org/abs/2609.00002"}])
    assert html.count('href="https://arxiv.org/abs/2609.00001"') == 1
    assert html.count('href="https://arxiv.org/abs/2609.00002"') == 1
    assert '>color extended</a>' in html and 'color:#' in html and '<a href="<a' not in html


def test_saved_titles_must_match_the_whole_original_title():
    """제목의 앞부분만 같은 다른 논문을 원래 회차로 오인하면 실패한다."""
    with pytest.raises(ValueError):
        saved_digest.render_html(SAVED, "팀", [{"title": "One", "link": "https://arxiv.org/abs/2609.00002"}])
