"""ui_helpers.py — 운영 화면(review_app)이 쓰는 Streamlit 없는 보조 둘. 2026-09-16 `review_core.py` 에서 옮기고 그 파일은 지웠다.

review_core 는 옛 화면(검색·요약·검토·수동 재현)의 로직이었고, 2026-09-16 개편 뒤 화면이 부르는 것은 이 두 함수뿐이었다(Codex 구조 검토).
옛 ⑦ 재현 두 번째 시작점(`_summarize_target`)도 함께 사라져 시작점은 `batch_summarize._process_paper` 하나다(AGENTS.md).
옛 로직은 git 이력(커밋 3cc9ac3 이전)에 있다.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone


def run_async(coro):
    return asyncio.run(coro)


def _relative_time(iso_ts: str) -> str:
    """'2026-08-14T05:12:33+00:00' 같은 UTC ISO 문자열을 'N분 전' 식으로
    바꾼다. 참고 이미지의 "3분 전" 표시를 실제 타임스탬프로 계산한 것 —
    화면에 고정 문구를 박아넣지 않는다."""
    if not iso_ts:
        return ""
    try:
        ts = datetime.fromisoformat(iso_ts)
    except ValueError:
        return iso_ts
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - ts
    secs = delta.total_seconds()
    if secs < 60:
        return "방금 전"
    if secs < 3600:
        return f"{int(secs // 60)}분 전"
    if secs < 86400:
        return f"{int(secs // 3600)}시간 전"
    return f"{int(secs // 86400)}일 전"
