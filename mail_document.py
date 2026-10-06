"""⑨ 긴 분석의 항목·본문 배치 — 항목명과 부가 관측만 옅은 색으로 가른다."""
from __future__ import annotations

import re
from html import escape
from typing import Callable

INFO_BG = "#EDF5FA"
CANVAS_BG = "#EEF2F5"

_LABEL_NAMES = {
    "무엇을 하려 했는가": "연구 목적",
    "무엇을 하려고 했는가": "연구 목적",
    "무엇을 하려 했다는가": "연구 목적",
    "어떻게 했는가": "방법론",
    "무엇을 보였는가": "주요 결과",
    "무엇을·어떻게": "연구 개요",
    "방법 상세": "방법론",
    "방법론 세부": "방법론",
    "실험 설정": "실험 구성",
    "핵심 결과": "주요 결과",
    "저자가 명시한 한계": "연구 한계",
    "저자가 밝힌 한계": "연구 한계",
    "요약자의 해석": "분석 메모",
    "요약자가 판단한 한계": "분석 메모",
    "해석 및 시사점": "분석 메모",
}
_HEADINGS = ("무엇을·어떻게", "연구 개요", "방법 상세", "방법론 세부", "방법론", "실험 설정", "실험 구성", "핵심 결과", "주요 결과",
             "저자가 명시한 한계", "연구 한계", "요약자의 해석", "분석 메모", "해석 및 시사점", "성능 비교")
_BRIEF_HEADINGS = ("무엇을 하려 했는가", "무엇을 하려고 했는가", "무엇을 하려 했다는가", "어떻게 했는가", "무엇을 보였는가",
                   "연구 목적", "방법론", "주요 결과")
_BRIEF_RE = re.compile(r"^-?\s*(" + "|".join(map(re.escape, _BRIEF_HEADINGS)) + r")\s*[:：]\s*(.*)$")
_HEADING_RE = re.compile(r"^(" + "|".join(map(re.escape, _HEADINGS)) + r")(?:\s*[:：]\s*(.*))?$")
_LABEL_RE = re.compile(r"^(\s*(?:[-•]\s*)*)(" + "|".join(map(re.escape, _LABEL_NAMES)) + r")(\s*[:：])")
_REF_RE = re.compile(r"\[(S\d{3,5}(?:\s*,\s*S\d{3,5})*)\]")


def heading_name(heading: str) -> str:
    """저장 분석 키를 바꾸지 않고 메일 항목명만 짧은 명사형으로 표시한다(2026-10-02 사용자 요청)."""
    return _LABEL_NAMES.get(heading, heading)


def nominal_line(line: str) -> str:
    """줄 앞의 알려진 항목명만 바꾼다. 문장 안의 표현·미지 라벨·콜론 뒤 근거를 고치지 않는다."""
    return _LABEL_RE.sub(lambda match: match[1] + heading_name(match[2]) + match[3], line, count=1)


def evidence_footer(text: str) -> str:
    """S번호는 원래 문장에도 남긴다. 맨 아래 목록은 검증 판정이나 큰 절이 아닌 작은 보조 정보다."""
    refs = list(dict.fromkeys(ref.strip() for match in _REF_RE.finditer(text) for ref in match[1].split(',')))
    if not refs:
        return ""
    return ('<div class="ph-evidence" style="font-size:11.5px;color:#526675;line-height:1.6;margin-top:8px;word-break:break-word;">'
            + '근거 · ' + escape(' · '.join(refs)) + '</div>')


def section(heading: str, body: str) -> str:
    """정렬은 실제 td에 지정한다. 메일이 CSS를 지워도 항목명은 같은 행의 칸 안에 남는다(2026-10-02 재지적)."""
    return ('<tr class="ph-section" style="border-top:1px solid #DDE5E9;">'
            f'<td class="ph-label" width="120" align="center" valign="middle" bgcolor="{INFO_BG}" '
            f'style="width:120px;text-align:center;vertical-align:middle;background-color:{INFO_BG};padding:12px;">'
            f'<div class="ph-section-title" style="background-color:{INFO_BG};color:#0B5F68;font-size:14px;'
            'font-weight:700;line-height:1.6;text-align:center;word-break:break-word;">' + escape(heading_name(heading)) + '</div></td>'
            '<td class="ph-detail-body" align="left" valign="top" bgcolor="#FFFFFF" '
            'style="text-align:left;vertical-align:top;background-color:#FFFFFF;padding:6px 12px;">'
            '<div class="ph-section-body" style="font-size:14px;line-height:1.8;color:#1E2B37;word-break:break-word;">'
            + body + '</div></td></tr>')


def document(body: str) -> str:
    """모든 절을 한 표로 묶는다. 처음 행의 항목명 폭을 따르므로 모든 행의 경계가 맞는다."""
    if not body:
        return ""
    return ('<table class="ph-document" role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            'style="width:100%;table-layout:fixed;border-collapse:collapse;margin:8px 0;">'
            '<tbody>' + body + '</tbody></table>')


def from_lines(lines: list[str], render_line: Callable[[str], str], *, brief: bool = False) -> str:
    """저장 메일의 알려진 절만 묶는다. 방법의 하위 키/값이나 알 수 없는 줄은 그대로 보존한다."""
    groups: list[tuple[str, list[str]]] = [("", [])]
    for line in lines:
        match = _HEADING_RE.fullmatch(line.strip()) or (_BRIEF_RE.fullmatch(line.strip()) if brief else None)
        if match:
            groups.append((match[1], [match[2]] if match[2] else []))
        else:
            groups[-1][1].append(line)
    preamble = ''.join(render_line(line) for line in groups[0][1])
    rows = ''.join(section(heading, ''.join(render_line(line) for line in content)) for heading, content in groups[1:])
    return preamble + document(rows) + evidence_footer('\n'.join(lines))


def info(heading: str, body: str, *, warning: bool = False) -> str:
    """정보 영역만 옅게 칠한다. 메일이 class 스타일을 지워도 bgcolor와 인라인 색이 남는다."""
    bg, border = ("#FFF4E5", "#C28B43") if warning else (INFO_BG, "#7394A6")
    kind = "ph-warning" if warning else "ph-info"
    return (f'<table class="{kind}" role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'bgcolor="{bg}" style="width:100%;background-color:{bg};border-collapse:collapse;margin:10px 0;">'
            f'<tr><td style="background-color:{bg};border-left:3px solid {border};padding:10px 12px;'
            'color:#1E2B37;font-size:13.5px;line-height:1.7;word-break:break-word;">'
            + (f'<div style="font-weight:700;color:#285269;margin-bottom:4px;">{escape(heading)}</div>' if heading else "")
            + body + '</td></tr></table>')
