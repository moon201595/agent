"""⑨ 회색 배경의 폭 — 본문 양옆만 감싸고 화면 바깥은 흰색으로 남긴다."""
import re
from html.parser import HTMLParser

import digest
import mail_layout


class Tables(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.tables = []
        self.cells = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'table':
            node = {'attrs': attrs, 'parent': self.stack[-1] if self.stack else None}
            self.tables.append(node)
            self.stack.append(node)
        elif tag == 'td':
            self.cells.append({'attrs': attrs, 'parent': self.stack[-1]})

    def handle_endtag(self, tag):
        if tag == 'table':
            self.stack.pop()


def test_gray_canvas_only_surrounds_body_with_eight_pixel_edges():
    """회색을 화면 전체에 칠하거나 UI 시안의 제한된 회색 바탕·1056/1040px·8px 계약을 깨면 실패한다."""
    html = mail_layout.frame('원래 본문')
    parsed = Tables()
    parsed.feed(html)
    assert len(parsed.tables) == 3
    page, shell, body = parsed.tables
    assert page['attrs']['class'] == 'ph-page'
    assert page['attrs']['bgcolor'] == '#FFFFFF'
    assert page['attrs']['style'] == 'width:100%;background-color:#FFFFFF;'
    assert shell['parent'] is page and body['parent'] is shell
    assert shell['attrs']['class'] == 'ph-shell'
    assert shell['attrs']['bgcolor'] == '#EEF2F5'
    assert shell['attrs']['style'] == 'width:100%;max-width:1056px;background-color:#EEF2F5;'
    # 2026-10-02 UI 시안은 내부도 회색이고 카드만 흰색이다. 바깥 폭 계약은 유지한다.
    assert body['attrs']['style'] == 'width:100%;max-width:1040px;background-color:#EEF2F5;'
    canvas = next(cell for cell in parsed.cells if cell['attrs'].get('class') == 'ph-canvas')
    assert canvas['parent'] is shell
    assert canvas['attrs']['style'] == 'padding:8px;'
    assert '.ph-canvas{padding:4px!important}' in html
    assert '<!--[if mso]><table role="presentation" width="1056"' in html
    assert '원래 본문' in html


class Tree(HTMLParser):
    """부모·형제를 볼 수 있는 최소 트리. 어느 조각이 어느 흰 블록 **안**에 있는지 보려면 필요하다."""

    VOID = {"br", "img", "hr", "meta", "input"}

    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.root = {"tag": "", "style": "", "children": [], "text": ""}
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, "style": dict(attrs).get("style", ""), "children": [], "text": ""}
        self.stack[-1]["children"].append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i]["tag"] == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1]["text"] += data


def _all(node: dict) -> list:
    out = [node]
    for child in node["children"]:
        out += _all(child)
    return out


def _text(node: dict) -> str:
    return "".join(n["text"] for n in _all(node))


def _side_padding(style: str) -> tuple[int, int]:
    """인라인 style 에서 좌·우 padding 픽셀을 읽는다. 구현을 다시 계산하지 않고 **나온 결과**만 읽는다."""
    left = right = 0
    shorthand = re.search(r"(?:^|;)\s*padding:([^;]+)", style)
    if shorthand:
        parts = [p for p in shorthand[1].split()]
        px = lambda v: int(re.sub(r"[^0-9-]", "", v) or 0)       # noqa: E731
        if len(parts) == 1:
            left = right = px(parts[0])
        elif len(parts) == 2:
            left = right = px(parts[1])
        elif len(parts) == 3:
            left = right = px(parts[1])
        else:
            right, left = px(parts[1]), px(parts[3])
    for name, value in re.findall(r"padding-(left|right):\s*(-?\d+)px", style):
        if name == "left":
            left = int(value)
        else:
            right = int(value)
    return left, right


WEEKLY_SCAN = {
    "candidates_found": 431, "already_seen_count": 12,
    "papers": [{"arxiv_id": "a", "title": "Paper A", "_score": {"core_hits": ["defect detection"]}}],
    "trend_window": {"days": 7, "papers": (41, 33), "days_covered": (5, 5), "comparable": True,
                     "keywords": [("defect detection", 12, 7), ("world model", 4, 9)],
                     "terms": [("ground truth", 6, 3)]},
    "reserve_terms": {"count": 418, "terms": [("point cloud", 19)]},
    "external_scout": {"verified": 2, "capture": {"evaluable": 2, "retrieved": 1, "core_hit": 1, "delivered": 0},
                       "missed": [{"title": "An Outside Paper", "venue": "CVPR 2026",
                                   "link": "https://doi.org/10.1000/xyz", "stage": "검색 소스 미수집"}]},
    "profile_changes": {"window": ("2026-09-29T00:00:00+00:00", "2026-10-06T00:00:00+00:00"),
                        "days": 7, "by_actor": {}, "reactions_used": 15,
                        "weights": [{"keyword": "defect detection", "kind": "core", "before": 1.0,
                                     "after": 1.8, "delta": 0.8, "origins": ("agent",)}],
                        "added": [], "removed": [],
                        "agent": {"applied": [], "impact": {"gained": 3, "lost": 1, "topk_changed": 2},
                                  "shadow": None, "failed": None}},
}


def test_weekly_brief_white_blocks_keep_their_text_off_the_left_edge():
    """2026-10-06 사용자 지적: `최근 7일 흐름`부터 메일 끝까지 흰 바탕 안의 글이 왼쪽 경계에 붙어 있었다.

    이 테스트가 잡는 것: ① 절 본문을 다시 맨 div 로 되돌려(`_block` 을 빼) 여백이 사라지는 것
    ② 여백을 0 으로 만드는 것(`padding:12px 0`) ③ 맨 아래 꼬리 줄만 빠뜨리는 것
    ④ 여백 대신 **바깥 폭을 늘려** 해결하는 것(회색 1056 / 본문 1040 계약은 그대로여야 한다).

    생산 함수 `digest.generate_digest_html` 이 실제로 낸 HTML 을 읽는다 — 여기서 다시 조립하지 않는다.
    """
    html = digest.generate_digest_html(dict(WEEKLY_SCAN), "t")
    tree = Tree(html)
    nodes = _all(tree.root)

    # 바깥 폭 계약 — 여백을 폭으로 때우면 여기서 걸린다
    assert "max-width:1056px" in html and "max-width:1040px" in html

    for heading, inside in (("최근 7일 흐름", "상승"),
                            ("외부 정찰", "에이전트가 놓친 연구"),
                            ("지난 7일 검색 기준 변화", "가중치 변화")):
        parent = next(n for n in nodes if any(
            c["tag"] == "p" and _text(c).startswith(heading) for c in n["children"]))
        kids = parent["children"]
        head = next(i for i, c in enumerate(kids) if _text(c).startswith(heading))
        block = kids[head + 1]
        assert "background-color:#FFFFFF" in block["style"], f"{heading} 본문이 흰 블록이 아니다"
        left, right = _side_padding(block["style"])
        assert left >= 12 and right >= 12, f"{heading} 흰 블록의 좌우 여백이 {left}/{right}px"
        assert inside in _text(block), f"{heading} 본문이 그 블록 안에 없다"
        assert "반영된 사용자 반응 15건" not in _text(kids[head])      # 제목 줄에 꼬리가 새지 않는다

    tail = next(n for n in nodes if n["tag"] == "p" and _text(n).startswith("이번 실행에서 걸러진 것"))
    assert "background-color:#FFFFFF" in tail["style"]
    assert all(pad >= 12 for pad in _side_padding(tail["style"])), tail["style"]


def test_weekly_brief_block_is_not_drawn_when_the_section_is_empty():
    """빈 내용에 흰 상자만 남기면 "뭔가 있나" 하고 눈을 끌고 아무것도 주지 않는다 — `_h1` 만 뜬 빈 절과 같다."""
    assert digest._block("") == ""
    assert "내용" in digest._block("내용")
