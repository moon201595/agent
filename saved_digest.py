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
                cards.append({"position": int(match[1]), "title": match[2], "lines": [], "raw_lines": []})
            elif cards:
                cards[-1]["lines"].append(line.strip())
                cards[-1]["raw_lines"].append(line)
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
    """원래 문장만 카드 틀에 넣는다. 현재 DB 상태로 과거 숫자·상태를 덮으면 안 된다."""
    title = escape(card["title"])
    url = paper.get("link") or ""
    if url:
        title = f'<a href="{escape(url, quote=True)}" style="background-color:#FFFFFF;color:#0B5F68;text-decoration:none;">{title}</a>'
    observations: list[tuple[str, list[str]]] = []
    before, analysis, states, codes = [], [], [], []
    in_analysis = False
    observation = None
    for raw in card.get("raw_lines", card["lines"]):
        line = raw.strip()
        if observation is not None and not raw.startswith("     "):
            observation = None
        if line == url or line.startswith("반응 :"):
            continue
        if line.startswith(("성능 관측 ·", "논문 간 관측", "외부 관측 신호", "논문 자체 주장 ·")):
            observations.append((line, []))
            observation = observations[-1][1]
            continue
        if line.startswith(("무엇을·어떻게", "연구 개요", "방법 상세", "방법론", "실험 설정", "실험 구성",
                            "핵심 결과", "주요 결과", "성능 비교", "연구 한계", "분석 메모", "본문 비공개")):
            in_analysis = True
            observation = None
        if line.startswith("[") and not re.match(r"\[S\d", line):
            states.append(line)
            observation = None
        elif line.startswith("코드:") or line.startswith("코드 :"):
            codes.append(line)
            observation = None
        elif line.startswith("핵심 키워드:"):
            before.append(line)
        elif observation is not None:
            # 관측 뒤 요지는 들여쓰기 깊이가 같지 않지만 parse는 과거 계약상 strip한다.
            # 읽을 포인트와 근거 없는 자유 요지는 관측값으로 새로 해석하지 않는다.
            if line.startswith("읽을 포인트"):
                before.append(line)
                observation = None
            else:
                observation.append(line)
        elif in_analysis:
            analysis.append(line)
        else:
            before.append(line)
    detail = ''.join(_line(line) for line in codes)
    restored = []
    for heading, lines in observations:
        entries = []
        for line in lines:
            match = re.fullmatch(r"(.*) <(https?://\S+)>", line)
            entries.append((match[1], match[2]) if match else (line, ""))
        restored.append((heading, entries))
    detail += digest._render_observation_sections(restored)
    # 구형 호출자가 명시적으로 준 저장 관측만 받는다. snapshot 경로는 현재 관측을 조회하지 않는다.
    legacy_observations = digest._observation_blocks_html(paper) if not restored and any(
        paper.get(key) for key in ("_signals", "_frontier", "_sota_claims")) else ""
    detail += legacy_observations
    chips = ''.join(digest._status_chip(line, flagged="실패" in line or "⚠" in line) for line in states)
    keywords, gist = [], []
    for line in before:
        if legacy_observations and paper.get("_signals") and line.startswith("외부 신호"):
            continue
        if line.startswith("핵심 키워드:"):
            keywords += [digest._chip(word.strip(), "kw") for word in line.split(":", 1)[1].split(",")]
        else:
            gist.append(_line(line))
    foot = ('<div style="background-color:#FFFFFF;border-top:1px solid #EDF1F3;padding-top:10px;margin-top:12px;">'
            + digest._feedback_buttons_html(paper)
            + (f'<a href="{escape(url, quote=True)}" style="color:#086C75;font-size:12.5px;">원문 {escape(url.replace("https://", ""))} ↗</a>' if url else "") + '</div>')
    return digest._render_card_parts(card["position"], title, "", ''.join(gist), chips, ''.join(keywords),
                                     detail, mail_document.from_lines(analysis, _line,
                                     brief=any(line.startswith("본문 비공개") for line in analysis)), foot)


# ── 이번 주 브리프 절의 역파싱(2026-10-06 사용자 지적) ─────────────────────────────────────
# 형식 수정본의 브리프가 아침 메일과 달랐다 — 상승·하락 두 칸·칩·주황 딱지·막대·영향 표 대신 저장 줄을 한 칸에 나열했다.
# 저장 평문은 digest 의 평문 함수(`_window_section`·`_reserve_lines`·`_external_scout_lines`·`_profile_changes_section`)가
# 결정적으로 쓴 것이라 그 역을 풀면 **digest 의 같은 HTML 함수**로 그릴 수 있다. 숫자·용어·URL 은 저장 글 그대로 옮긴다.
# 한 줄이라도 형식이 맞지 않으면 `ValueError` 로 그 절만 옛 줄 렌더러로 떨어진다 — 모르는 줄을 버리거나 추측해 채우지 않는다.

_SCOUT_HEAD_RE = re.compile(r"▶ 외부 정찰 — 이번 주 검증된 외부 연구 (\d+)편")
_SCOUT_CAPTURE_RE = re.compile(r"판정 가능 (\d+)편 중 검색 소스가 가져온 것 (\d+) · 핵심어에 걸린 것 (\d+) · 메일까지 간 것 (\d+)")
_WINDOW_MOVE_RE = re.compile(r"([▲▼]) (.+?)\s+(\d+)   ([+−])(\d+)")
_COUNT_ROW_RE = re.compile(r"(.+?)\s+(\d+)")
_RESERVE_HEAD_RE = re.compile(r"▸ 자리에 못 든 후보 (\d+)편에서 자주 나온 말")
_RESERVE_ROW_RE = re.compile(r"(.+) : (\d+)편")
_CHANGE_MOVE_RE = re.compile(r"([▲▼]) (.+?)(?:   (\[.+\]))?")
_CHANGE_BAR_RE = re.compile(r"(\d+\.\d{2}) (━+) (\d+\.\d{2})   ([+−])(\d+\.\d{2})")
_CHANGE_ITEM_RE = re.compile(r"(NEW|▪)  (.+?)   (.+)")
_REASON_HEAD_RE = re.compile(r"(\S.*?)(?:   \[(.+)\])?")
_FAILED_RE = re.compile(r"⚠ 주간 관리 실패 : (.+) — 키워드는 그대로입니다\.")
_IMPACT_NAMES = ("적격 논문", "잃은 논문", "상위 겹침", "새로 걸린 논문", "빠진 논문", "Top-K 교체")


def _split_scout(lines: list[str]) -> tuple[list[str], list[str]]:
    """`▶ 외부 정찰` 은 `■` 절 머리가 아니라 앞 절(대개 최근 7일 흐름) 줄에 섞여 저장된다 — 그 자리에서 끊는다."""
    for i, line in enumerate(lines):
        if _SCOUT_HEAD_RE.fullmatch(line.strip()):
            return lines[:i], lines[i:]
    return lines, []


def _window_from_lines(heading: str, lines: list[str]) -> dict:
    """`최근 N일 흐름` 평문 → digest `_window_html` 이 읽는 `trend_window`·`reserve_terms`."""
    days = int(re.fullmatch(r"최근 (\d+)일 흐름", heading)[1])
    body = [line.strip() for line in lines if line.strip()]
    if len(body) < 2:
        raise ValueError("흐름 머리 두 줄이 없음")
    comparable = re.fullmatch(r"관련 논문 (\d+)편 \(직전 (\d+)편\)", body[0])
    if comparable:
        covered = re.fullmatch(r"관측일 (\d+)일 / 직전 (\d+)일", body[1])
        papers = (int(comparable[1]), int(comparable[2]))
    else:
        first = re.fullmatch(r"관련 논문 (\d+)편 · 관측일 (\d+)일", body[0])
        if not first or body[1] != f"직전 {days}일에는 관측이 없어 증감을 내지 않았다.":
            raise ValueError("흐름 머리 형식이 다름")
        covered, papers = re.fullmatch(r"(\d+)()", first[2]), (int(first[1]), 0)
    if not covered:
        raise ValueError("관측일 줄 형식이 다름")
    keywords, terms, reserve, reserve_count, part = [], [], [], 0, None
    seen: set[str] = set()
    for line in body[2:]:
        reserve_head = _RESERVE_HEAD_RE.fullmatch(line)
        if line in ("상승", "하락", "키워드 (이번 창 편수)", "핵심 키워드 밖 반복 관측") or reserve_head:
            part = "reserve" if reserve_head else line
            if part in seen:
                raise ValueError("같은 소제목이 두 번 나옴")      # 뒤 머리가 앞 편수를 덮으면 저장 숫자가 사라진다(§226 P2)
            seen.add(part)
            reserve_count = int(reserve_head[1]) if reserve_head else reserve_count
            continue
        if part in ("상승", "하락"):
            m = _WINDOW_MOVE_RE.fullmatch(line)
            if not m or (m[1] == "▲") != (part == "상승") or (m[1] == "▲") != (m[4] == "+"):
                raise ValueError("증감 줄 형식이 다름")      # 화살표·부호가 어긋난 줄을 받으면 상승이 하락으로 바뀐다(§226 P3)
            now, delta = int(m[3]), int(m[5]) * (1 if m[4] == "+" else -1)
            keywords.append((m[2], now, now - delta))
        elif part in ("키워드 (이번 창 편수)", "핵심 키워드 밖 반복 관측"):
            m = _COUNT_ROW_RE.fullmatch(line)
            if not m:
                raise ValueError("편수 줄 형식이 다름")
            (keywords if part.startswith("키워드") else terms).append((m[1], int(m[2]), int(m[2])))
        elif part == "reserve":
            m = _RESERVE_ROW_RE.fullmatch(line)
            if m:
                reserve.append((m[1], int(m[2])))
            elif line != "(여러 편에 겹치는 말이 없었다)":
                raise ValueError("자리 밖 후보 줄 형식이 다름")
        else:
            raise ValueError("흐름 절의 모르는 줄")
    window = {"days": days, "papers": papers, "days_covered": (int(covered[1]), int(covered[2] or 0)),
              "comparable": bool(comparable), "keywords": keywords, "terms": terms}
    return {"trend_window": window, "reserve_terms": {"count": reserve_count, "terms": reserve}}


def _reserve_only_from_lines(lines: list[str]) -> dict:
    """창 집계 없이 자리 밖 후보만 실린 날(`■ 이번 실행에서 자리에 못 든 후보`)."""
    body = [line.strip() for line in lines if line.strip()]
    if not body or not _RESERVE_HEAD_RE.fullmatch(body[0]):
        raise ValueError("자리 밖 후보 머리가 없음")
    terms = []
    for line in body[1:]:
        m = _RESERVE_ROW_RE.fullmatch(line)
        if m:
            terms.append((m[1], int(m[2])))
        elif line != "(여러 편에 겹치는 말이 없었다)":
            raise ValueError("자리 밖 후보 줄 형식이 다름")
    return {"reserve_terms": {"count": int(_RESERVE_HEAD_RE.fullmatch(body[0])[1]), "terms": terms}}


def _scout_from_lines(lines: list[str]) -> dict:
    """`▶ 외부 정찰` 평문 → digest `_external_scout_html` 이 읽는 `external_scout`.

    학회명은 평문에서 제목 끝 괄호로만 남는다. 제목 자체가 괄호로 끝나면 그 괄호를 학회명으로 읽어 흐린 글자로 그린다 —
    글자는 그대로이고 색만 다르다(되살릴 정보가 저장 글에 없다)."""
    body = [line.strip() for line in lines if line.strip()]
    head = _SCOUT_HEAD_RE.fullmatch(body[0]) if body else None
    capture = _SCOUT_CAPTURE_RE.fullmatch(body[1]) if len(body) > 1 else None
    if not head or not capture:
        raise ValueError("외부 정찰 머리 형식이 다름")
    missed: list[dict] = []
    for line in body[2:]:
        if re.fullmatch(r"https?://\S+", line) and missed and not missed[-1]["link"]:
            missed[-1]["link"] = line
            continue
        if not line.startswith("- ") or " — " not in line:
            raise ValueError("외부 정찰 줄 형식이 다름")
        title, stage = line[2:].rsplit(" — ", 1)
        venue = re.fullmatch(r"(.+) \(([^()]+)\)", title)
        missed.append({"title": venue[1] if venue else title, "venue": venue[2] if venue else "",
                       "stage": stage, "link": ""})
    return {"verified": int(head[1]), "missed": missed,
            "capture": dict(zip(("evaluable", "retrieved", "core_hit", "delivered"), map(int, capture.groups())))}


def _changes_view_from_lines(heading: str, lines: list[str]) -> dict:
    """`지난 N일 검색 기준 변화` 평문 → digest `_profile_changes_view` 와 같은 모양의 값."""
    view = {"days": int(re.fullmatch(r"지난 (\d+)일 검색 기준 변화", heading)[1]), "moves": [], "added": [],
            "removed": [], "groups": [], "rows": [], "failed": "", "tail": ""}
    part = None
    for raw in lines:
        line = _without_hidden_moves(raw)
        if not line:
            continue
        indent = len(raw) - len(raw.lstrip())
        if indent == 3 and line in ("가중치 변화", "신규", "삭제", "이유", "검색 영향"):
            part = line
            continue
        if indent == 3:
            if view["tail"]:
                raise ValueError("꼬리 줄이 둘")
            view["tail"] = line
            continue
        failed = _FAILED_RE.fullmatch(line)
        if failed:
            view["failed"] = failed[1]
        elif part == "가중치 변화" and indent == 6 and _CHANGE_MOVE_RE.fullmatch(line):
            m = _CHANGE_MOVE_RE.fullmatch(line)
            view["moves"].append({"rising": m[1] == "▲", "keyword": m[2], "tags": m[3] or ""})
        elif part == "가중치 변화" and indent == 8 and view["moves"] and "blocks" not in view["moves"][-1]:
            m = _CHANGE_BAR_RE.fullmatch(line)
            if not m or (m[4] == "+") != view["moves"][-1]["rising"]:
                raise ValueError("가중치 막대 줄 형식이 다름")
            view["moves"][-1].update(blocks=len(m[2]), before=m[1], after=m[3], delta=m[5])
        elif part in ("신규", "삭제") and indent == 6:
            m = _CHANGE_ITEM_RE.fullmatch(line)
            if not m or (m[1] == "NEW") != (part == "신규"):
                raise ValueError("신규·삭제 줄 형식이 다름")
            view["added" if part == "신규" else "removed"].append((m[2], m[3]))
        elif part == "이유" and indent == 6:
            m = _REASON_HEAD_RE.fullmatch(line)
            view["groups"].append([m[1], m[2] or "", None])
        elif part == "이유" and indent == 8 and view["groups"] and view["groups"][-1][2] is None:
            view["groups"][-1][2] = line
        elif part == "검색 영향" and indent == 6:
            name = next((n for n in _IMPACT_NAMES if line.startswith(n + " ")), None)
            if not name:
                raise ValueError("검색 영향 줄 형식이 다름")
            view["rows"].append((name, line[len(name):].strip()))
        else:
            raise ValueError("검색 기준 변화의 모르는 줄")
    if any("blocks" not in w for w in view["moves"]) or any(g[2] is None for g in view["groups"]):
        raise ValueError("가중치 막대나 이유 본문이 빠짐")
    view["groups"] = [tuple(g) for g in view["groups"]]
    return view


def _weekly_section_html(heading: str, lines: list[str]) -> str | None:
    """브리프 절 하나를 digest 의 HTML 함수로 그린다. 그릴 수 없으면 None — 호출부가 옛 줄 렌더러로 떨어진다."""
    try:
        if re.fullmatch(r"최근 \d+일 흐름", heading):
            return digest._window_html(_window_from_lines(heading, lines))
        if heading == "이번 실행에서 자리에 못 든 후보":
            return digest._block(digest._reserve_html(_reserve_only_from_lines(lines)))
        if re.fullmatch(r"지난 \d+일 검색 기준 변화", heading):
            return digest._profile_changes_view_html(_changes_view_from_lines(heading, lines))
        hits = re.fullmatch(r"이번 창의 키워드별 적중 편수 (\(후보 \d+건 기준\))", heading)
        if hits:
            trend = " ".join(line.strip() for line in lines if line.strip())
            return digest._keyword_hits_html(hits[1], trend) if trend else None
    except (ValueError, TypeError):
        return None
    return None


def _scout_html(lines: list[str]) -> str:
    try:
        return digest._external_scout_html({"external_scout": _scout_from_lines(lines)})
    except (ValueError, TypeError):
        return digest._block(''.join(digest._weekly_line_html(line) for line in lines if line.strip()))


def render_html(text: str, profile_name: str, papers: list[dict], *, title_only_count: int | None = None) -> str:
    """오늘 본문을 다시 해석하지 않는다. 원래 발송 명세와 다른 카드는 거부한다."""
    parsed = parse(text)
    if len(papers) != len(parsed["cards"]) or any(
            not (card["title"] == paper.get("title") or card["title"].startswith((paper.get("title") or "\0") + " ("))
            for card, paper in zip(parsed["cards"], papers)):
        raise ValueError("저장 본문과 발송 명세의 논문이 일치하지 않음")
    counts = [("핵심 논문", f'{len(papers)}편')]
    candidate = next((re.search(r"전체 후보 (\d+)건", s["heading"]) for s in parsed["sections"]
                      if s["heading"].startswith("오늘의 핵심 논문 ")), None)
    if candidate:
        counts.append(("전체 후보", candidate[1] + "건"))
    if title_only_count is not None:
        if title_only_count < 0:
            raise ValueError("제목만 실은 논문 수가 음수")
        counts.append(("제목만 실은 논문", f"{title_only_count}편"))
    window = next((line for section in parsed["sections"] if re.match(r"최근 \d+일 흐름", section["heading"])
                   for line in section["lines"] if "관련 논문" in line), "")
    number = re.search(r"관련 논문\s*(\d+)편", window)
    if number:
        days = next(re.search(r"최근 (\d+)일", section["heading"])[1] for section in parsed["sections"]
                    if re.match(r"최근 \d+일 흐름", section["heading"]))
        counts.insert(2, (f"최근 {days}일 관련", number[1] + "편"))
    content = digest._digest_head_html(parsed["date"], profile_name, counts)
    for section in parsed["sections"]:
        heading = section["heading"]
        section_lines, scout_lines = _split_scout(section["lines"])
        section = {**section, "lines": section_lines}
        if heading.startswith("이번 실행에서 걸러진 것"):
            tail = [heading] + [_without_hidden_moves(line) for line in section["lines"]]
            content += digest._saved_footer_html(" · ".join(line for line in tail if line))
            continue
        if heading.startswith(digest.WEEKLY_BRIEF_TITLE):
            content += digest._weekly_banner_html()
            # 띠 아래 일반 줄이 있으면 버리지 않고 그대로 그린다(§226 P2 — 생산 평문엔 없지만 저장 글은 기록이다).
            rest = ''.join(digest._weekly_line_html(_without_hidden_moves(line))
                           for line in section["lines"] if _without_hidden_moves(line))
            content += digest._block(rest) + (_scout_html(scout_lines) if scout_lines else "")
            continue
        weekly = _weekly_section_html(heading, section["lines"])
        if weekly is not None:
            content += weekly + (_scout_html(scout_lines) if scout_lines else "")
            continue
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
            lines_html = ''.join(digest._weekly_line_html(_without_hidden_moves(line))
                                 for line in section["lines"] if _without_hidden_moves(line))
            content += digest._block(lines_html) if re.match(r"최근 \d+일 흐름|외부 정찰|지난 \d+일 검색 기준 변화", heading) else lines_html
        content += _scout_html(scout_lines) if scout_lines else ""
    return mail_layout.frame(content)
