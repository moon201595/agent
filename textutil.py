"""⑨ 공용 HTML 이스케이프 — 메일(`digest`)·화면(`review_app`)·경보 메일(`scripts/check_daily_mail`)이 각자 갖고 있던 세 사본을 하나로(2026-09-17).

논문 제목·초록·키워드에는 `&`·`<`·`>`·따옴표가 실제로 들어온다("A < B", "R&D", "don't") — 그대로 넣으면 레이아웃이 깨지거나 속성이 끊긴다.
다섯 글자를 다 바꾼다(작은따옴표 포함, `html.escape(quote=True)` 와 같다). 옛 `digest._esc` 는 `'` 를 안 바꿨는데 큰따옴표 속성만 써서 문제는
없었다 — 통일하면서 `'`→`&#x27;` 로 바뀌고 메일 클라이언트 표시는 같다.
"""
from __future__ import annotations

import html


def esc(value: object) -> str:
    """HTML 본문·속성 어디에 넣어도 안전한 문자열. None 은 빈 문자열이 아니라 'None' 이 된다 — 호출부가 걸러야 한다(옛 동작 유지)."""
    return html.escape(str(value), quote=True)
