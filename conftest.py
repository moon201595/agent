"""테스트는 임시 DB 를 매번 새로 만든다 — schema_guard(§8-98)의 "빈 DB 가 아니면 DDL 을 안
돌린다" 규칙은 운영 DB 를 지키기 위한 것이지 테스트를 막기 위한 것이 아니다. 여기서 허용
플래그를 켠다. 가드 자체를 검사하는 테스트는 monkeypatch.delenv 로 다시 끈다."""
import os

os.environ.setdefault("PAPER_HARNESS_APPLY_DDL", "1")

import pytest


@pytest.fixture(autouse=True)
def _no_real_feedback_config(monkeypatch):
    """운영 `.env` 에 반응 버튼 설정이 들어간 뒤(2026-09-15)로는 테스트가 실제 웹앱 URL·비밀키로 링크를 서명하고 실패 출력에
    그 URL 을 찍었다. 테스트는 기본으로 설정 없음에서 돌고, 필요한 테스트만 가짜 값을 넣는다(test_feedback_links.configured)."""
    import summarize_engine as engine
    for name in ("FEEDBACK_WEBAPP_URL", "FEEDBACK_HMAC_SECRET", "FEEDBACK_PAGE_URL"):
        monkeypatch.setitem(engine.ENV, name, "")


@pytest.fixture(autouse=True)
def _no_real_github_search(monkeypatch):
    """테스트가 GitHub 검색을 실제로 부르지 않게 한다(2026-09-16 실측: 스캔 테스트가 ⑦ 사다리를 거쳐 gh 를 세 번 불렀고 운영 DB 에 행을
    남겼다). 검색이 필요한 테스트는 자기 가짜로 다시 monkeypatch 한다 — 그러면 이 기본값을 덮는다."""
    import code_finder

    def refuse(query, limit=5):
        raise AssertionError(f"테스트에서 실제 GitHub 검색 호출: {query!r} — 가짜로 막아라")
    # 검색 함수 자체를 검사하는 테스트는 이 이름으로 원본을 부른다(gh 경로 처리 회귀 — 2026-09-16).
    monkeypatch.setattr(code_finder, "_real_github_search", code_finder.github_search, raising=False)
    monkeypatch.setattr(code_finder, "github_search", refuse)
