"""⑨ 저장된 메일의 재배치 — 요약·모델·수집을 다시 돌리지 않고 본문만 그린다."""
from __future__ import annotations

import re
from html import escape

import digest
import mail_layout
import mail_document


def parse(text: str) -> dict:
    """발송 회차와 대조할 카드 제목을 얻는다. 알 수 없는 줄은 삭제하지 않는다."""
    lines = text.splitlines()
    if not lines or not re.fullmatch(r"연구 동향 브리핑 · \d{4}-\d{2}-\d{2}", lines[0]):
        raise ValueError("저장 메일의 날짜 머리말이 없음")
    sections: list[dict] = []
    for line in lines[1:]:
        if re.fullmatch(r"[─━]+", line.strip()) or not line.strip():
            continue
        if line.startswith("■ "):
            sections.append({"heading": line[2:], "lines": []})
        else:
            if not sections:
                sections.append({"heading": "", "lines": []})
            sections[-1]["lines"].append(line)
    cards: list[dict] = []
    for section in sections:
        if not section["heading"].startswith("오늘의 핵심 논문 "):
            continue
        for line in section["lines"]:
            match = re.match(r"^(\d+)\. (.+)$", line)
            if match:
                cards.append({"position": int(match[1]), "title": match[2], "lines": []})
            elif cards:
                cards[-1]["lines"].append(line.strip())
            else:
                raise ValueError("저장 카드의 제목보다 본문이 먼저 나옴")
        expected = re.match(r"오늘의 핵심 논문 (\d+)편", section["heading"])
        if not expected or len(cards) != int(expected[1]) or [c["position"] for c in cards] != list(range(1, len(cards) + 1)):
            raise ValueError("저장 메일의 카드 수·번호가 다름")
    return {"date": lines[0].rsplit(" · ", 1)[1], "sections": sections, "cards": cards}


# 옛 저장 본문에는 꼬리 문구가 글자로 박혀 있다 — 2026-10-06 사용자 요청으로 뺀 `그 밖에 작게 움직인 가중치 N건`.
# 저장 평문·운영 DB 는 **다시 쓰지 않는다**(발송된 그대로가 기록이다). 다시 그릴 때 표시에서만 지운다.
_HIDDEN_MOVES_RE = re.compile(r"그 밖에 작게 움직인 가중치 \d+건")
_TAIL_SEP_RE = re.compile(r"\s*·\s*")


def _without_hidden_moves(text: str) -> str:
    """꼬리의 그 문구만 지우고 같은 줄의 `반영된 사용자 반응 N건` 은 **건수 그대로** 남긴다.

    꼬리 줄은 ` · ` 로 이은 조각들이다. 문구 **뒤**의 구분자만 먹으면 문구가 줄 끝에 온 순서(`반응 15건 · 그 밖에 …`)에서
    `반응 15건 ·` 이 남는다(2026-10-06 독립 검토 P3-2) — 문구가 든 줄만 조각으로 갈라 그 조각을 빼고 다시 잇는다.
    다른 줄은 손대지 않는다(카드 본문의 `원문 분석 · 초록 기반` 같은 구분자는 그대로)."""
    if not _HIDDEN_MOVES_RE.search(text):
        return text.strip()
    parts = [x for x in _TAIL_SEP_RE.split(text.strip()) if x and not _HIDDEN_MOVES_RE.fullmatch(x)]
    return _HIDDEN_MOVES_RE.sub("", " · ".join(parts)).strip()   # 문구 뒤에 다른 말이 붙은 조각은 문구만 지운다


def plain_text(body: str) -> str:
    """저장 평문을 다시 보낼 때의 평문 쪽. HTML 만 고치면 평문만 읽는 클라이언트에는 그 문구가 그대로 간다(P3-1).
    기록(운영 DB 의 저장 평문)은 바꾸지 않는다 — 나가는 사본에서만 뺀다. 그 문구뿐이던 줄은 빈 줄도 남기지 않는다."""
    out = []
    for line in body.split("\n"):
        if _HIDDEN_MOVES_RE.search(line):
            kept = _without_hidden_moves(line)
            if not kept:
                continue
            line = line[:len(line) - len(line.lstrip())] + kept       # 들여쓰기는 원래대로
        out.append(line)
    return "\n".join(out)


def _line(text: str) -> str:
    """모든 저장 문장을 그대로 이스케이프하고 절 이름만 강조한다."""
    text = _without_hidden_moves(text)
    if not text:
        return ""                                           # 그 문구뿐이던 줄은 빈 칸을 남기지 않는다
    if re.fullmatch(r"https?://\S+", text):
        return f'<div><a href="{escape(text, quote=True)}" style="color:#0B5F68;">{escape(text)}</a></div>'
    if text.startswith("외부 신호 "):
        return mail_document.info("외부 관측 신호", digest._summary_label_html(digest._plain(text[len("외부 신호 "):].strip())))
    if text.startswith("[⚠"):
        return mail_document.info("", digest._summary_label_html(digest._plain(text)), warning=True)
    return ('<div style="font-size:14px;line-height:1.8;margin:8px 0;word-break:break-word;">'
            + digest._summary_label_html(digest._plain(text)) + '</div>')


def _card(card: dict, paper: dict) -> str:
    """긴 분석만 기존 토글에 넣고 제목·주의·반응은 항상 표시한다."""
    title = escape(card["title"])
    url = paper.get("link") or ""
    if url:
        title = f'<a href="{escape(url, quote=True)}" style="color:#0B5F68;text-decoration:none;">{title}</a>'
    before: list[str] = []
    analysis: list[str] = []
    after: list[str] = []
    observations = digest._observation_blocks_html(paper)
    in_analysis, in_after = False, False
    for line in card["lines"]:
        if line == url:
            continue
        if observations and paper.get("_signals") and line.startswith("외부 신호"):
            continue                                        # 확장 신호를 표시하면 옛 축약 줄은 중복하지 않는다
        if line.startswith(("본문 비공개", "무엇을·어떻게", "연구 개요", "방법 상세", "방법론", "실험 설정", "실험 구성",
                            "핵심 결과", "주요 결과", "성능 비교", "연구 한계", "분석 메모")):
            in_analysis = True
        if in_analysis and line.startswith("[") and not re.match(r"\[S\d", line):
            in_after = True
        (after if in_after else analysis if in_analysis else before).append(line)
    content = (f'<div style="font-size:17px;font-weight:700;line-height:1.5;color:#1E2B37;">'
               f'<span style="color:#086C75;">{card["position"]}.</span> {title}</div>'
               + ''.join(_line(line) for line in before) + ''.join(_line(line) for line in after))
    content += observations
    if analysis:
        content += ('<details style="border-top:1px solid #E3E8EC;padding-top:10px;margin-top:12px;">'
                    '<summary style="display:block;list-style:none;font-size:13px;font-weight:700;color:#0B5F68;'
                    'cursor:pointer;">＋ 상세 분석 펼쳐보기</summary>'
                    + mail_document.from_lines(analysis, _line, brief=any(line.startswith("본문 비공개") for line in analysis)) + '</details>')
    content += '<div style="border-top:1px solid #E3E8EC;margin-top:12px;padding-top:10px;">'
    content += digest._feedback_buttons_html(paper)
    if url:
        content += f'<a href="{escape(url, quote=True)}" style="color:#086C75;font-size:13px;">원문 ↗</a>'
    return '<div class="ph-card" style="margin:18px 0;">' + mail_layout.box(content + '</div>') + '</div>'


def render_html(text: str, profile_name: str, papers: list[dict], *, title_only_count: int | None = None) -> str:
    """오늘 본문을 다시 해석하지 않는다. 원래 발송 명세와 다른 카드는 거부한다."""
    parsed = parse(text)
    if len(papers) != len(parsed["cards"]) or any(
            not (card["title"] == paper.get("title") or card["title"].startswith((paper.get("title") or "\0") + " ("))
            for card, paper in zip(parsed["cards"], papers)):
        raise ValueError("저장 본문과 발송 명세의 논문이 일치하지 않음")
    content = (f'<div style="color:#086C75;font-size:12px;font-weight:700;">RESEARCH BRIEF · {parsed["date"]}</div>'
               '<div style="font-size:25px;font-weight:700;line-height:1.4;margin:4px 0;">연구 동향 브리핑</div>'
               f'<div style="color:#526675;font-size:13px;">{escape(profile_name)}</div>')
    counts = [("핵심 논문", f'{len(papers)}편')]
    candidate = next((re.search(r"전체 후보 (\d+)건", s["heading"]) for s in parsed["sections"]
                      if s["heading"].startswith("오늘의 핵심 논문 ")), None)
    if candidate:
        counts.append(("전체 후보", candidate[1] + "건"))
    if title_only_count is not None:
        if title_only_count < 0:
            raise ValueError("제목만 실은 논문 수가 음수")
        counts.append(("제목만 실은 논문", f"{title_only_count}편"))
    content += mail_layout.stats(counts)
    for section in parsed["sections"]:
        heading = section["heading"]
        content += digest._h1(heading) if heading else ""
        if heading == "오늘의 연구 흐름":
            story, named = [], []
            for line in section["lines"]:
                if "위에서 이름으로 부른 논문" in line:
                    continue
                match = re.match(r"\s*· (.+) — (https?://\S+)$", line)
                if match:
                    # 한국어 괄호만 벗긴 이름은 본문의 원제와 대조한다.
                    name = match[1].rsplit(" (", 1)[0]
                    named.append({"title": name, "link": match[2], "label": match[1]})
                else:
                    story.append(("↳ " + line.strip()) if line.startswith("        ") else line.strip())
            narrative = '\n'.join(story)
            content += digest._narrative_boxes_html(narrative, papers + named)
            # 이름이 짧은 별칭으로만 나왔다면 원문 링크를 잃지 않게 별도 목록을 남긴다.
            for paper in named:
                if paper["title"] not in narrative:
                    content += _line(f'{paper["label"]} — {paper["link"]}')
        elif heading.startswith("오늘의 핵심 논문 "):
            content += ''.join(_card(card, paper) for card, paper in zip(parsed["cards"], papers))
        else:
            content += ''.join(_line(line.strip()) for line in section["lines"])
    return mail_layout.frame(content)
