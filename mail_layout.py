"""⑨ 메일 배치 — 짧은 흐름은 나란히, 긴 분석은 넓게 읽도록 표로 묶는다."""
from __future__ import annotations

from html import escape

from mail_document import CANVAS_BG


def box(content: str) -> str:
    """메일이 CSS 일부를 지워도 표의 경계와 안쪽 여백은 남긴다."""
    return ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            'style="width:100%;border-collapse:separate;background-color:#FFFFFF;border:1px solid #E3E8EC;'
            'border-radius:10px;"><tr><td style="padding:12px 14px;font-size:14.5px;line-height:1.75;'
            'color:#1E2B37;word-break:break-word;">' + content + '</td></tr></table>')


def columns(contents: list[str]) -> str:
    """넓으면 반씩 쓴다. 스타일이 없어도 좁은 두 칸 대신 전체 폭 한 칸으로 읽힌다."""
    if not contents:
        return ""
    if len(contents) != 2:
        return ''.join('<div style="margin:12px 0;">' + box(c) + '</div>' for c in contents)
    cells = []
    for i, content in enumerate(contents):
        mso = ('<!--[if mso]><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
               '<td width="50%" valign="top"><![endif]-->' if i == 0
               else '<!--[if mso]></td><td width="50%" valign="top"><![endif]-->')
        cells.append(mso + '<table class="ph-col" role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                     'border="0" style="display:inline-table;vertical-align:top;width:100%;'
                     'border-collapse:collapse;"><tr><td style="padding:6px 4px;">' + box(content) + '</td></tr></table>')
    return ('<div style="font-size:0;text-align:left;margin:6px -4px;">' + ''.join(cells)
            + '<!--[if mso]></td></tr></table><![endif]--></div>')


def frame(content: str) -> str:
    """UI처럼 제한된 회색 바탕 위에 흰 카드를 둔다. 바깥 폭은 늘리지 않는다(2026-10-02 시안)."""
    return ('<style>@media screen and (min-width:760px){.ph-col{width:50%!important}}'
            '@media screen and (max-width:600px){.ph-label{width:88px!important;padding:8px 6px!important}.ph-detail-body{padding:6px 8px!important}.ph-main{padding:12px 10px!important}.ph-canvas{padding:4px!important}}</style>'
            '<table class="ph-page" role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            'bgcolor="#FFFFFF" style="width:100%;background-color:#FFFFFF;"><tr><td align="center">'
            '<!--[if mso]><table role="presentation" width="1056" cellpadding="0" cellspacing="0"><tr><td><![endif]-->'
            '<table class="ph-shell" role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'bgcolor="{CANVAS_BG}" style="width:100%;max-width:1056px;background-color:{CANVAS_BG};">'
            '<tr><td class="ph-canvas" align="center" style="padding:8px;">'
            '<!--[if mso]><table role="presentation" width="100%"><tr><td><![endif]-->'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="width:100%;max-width:1040px;background-color:{CANVAS_BG};">'
            '<tr><td class="ph-main" style="padding:16px;color:#1E2B37;font-size:15px;line-height:1.75;'
            "font-family:'Noto Sans KR','Apple SD Gothic Neo','Malgun Gothic',Arial,sans-serif;\">"
            + content + '</td></tr></table><!--[if mso]></td></tr></table><![endif]--></td></tr></table>'
            '<!--[if mso]></td></tr></table><![endif]--></td></tr></table>')


def stats(values: list[tuple[str, str]]) -> str:
    """이미 있는 지표만 흰 칸에 표시하여 회색 바탕에서도 숫자 경계가 보이게 한다."""
    cells = ''.join(
        '<td valign="top" style="padding:0 4px;">'
        '<table class="ph-stat" role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'bgcolor="#FFFFFF" style="width:100%;background-color:#FFFFFF;border:1px solid #E3E8EC;border-radius:6px;">'
        '<tr><td style="padding:12px;font-size:12px;color:#526675;word-break:break-word;">'
        + escape(label) + '<div style="margin-top:6px;font-size:19px;font-weight:700;color:#1E2B37;">'
        + escape(value) + '</div></td></tr></table></td>' for label, value in values)
    return ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            'style="width:100%;border-collapse:collapse;margin:16px 0 4px;"><tr>' + cells + '</tr></table>')
