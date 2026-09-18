"""테스트는 임시 DB 를 매번 새로 만든다 — schema_guard(§8-98)의 "빈 DB 가 아니면 DDL 을 안
돌린다" 규칙은 운영 DB 를 지키기 위한 것이지 테스트를 막기 위한 것이 아니다. 여기서 허용
플래그를 켠다. 가드 자체를 검사하는 테스트는 monkeypatch.delenv 로 다시 끈다."""
import os
import tempfile

os.environ.setdefault("PAPER_HARNESS_APPLY_DDL", "1")
# **테스트 세션의 데이터 디렉터리를 운영 `data/` 에서 뗀다**(2026-09-16, Codex 구조 검토 ④-2). 그전엔 DDL 플래그만 켜서,
# `server.py` 가 import 되며 도는 `_init_storage()` 와 `storage.DB_PATH` 기본값으로 가는 코드가 운영 DB 에 표·행을 만들 수 있었다
# (9/16 실측: code_ladder 표 + 행 3개). 사용자가 PAPER_HARNESS_DATA 를 직접 준 경우는 존중한다. 픽스처(`data/fixtures/…`)는 저장소
# 상대 경로 리터럴이라 영향이 없다.
os.environ.setdefault("PAPER_HARNESS_DATA", tempfile.mkdtemp(prefix="paper-harness-test-"))

import pytest


@pytest.fixture(autouse=True)
def _no_real_feedback_config(monkeypatch):
    """운영 `.env` 에 반응 버튼 설정이 들어간 뒤(2026-09-15)로는 테스트가 실제 웹앱 URL·비밀키로 링크를 서명하고 실패 출력에
    그 URL 을 찍었다. 테스트는 기본으로 설정 없음에서 돌고, 필요한 테스트만 가짜 값을 넣는다(test_feedback_links.configured)."""
    import summarize_engine as engine
    for name in ("FEEDBACK_WEBAPP_URL", "FEEDBACK_HMAC_SECRET", "FEEDBACK_PAGE_URL"):
        monkeypatch.setitem(engine.ENV, name, "")


@pytest.fixture(autouse=True)
def _no_real_shadow_search(monkeypatch):
    """주간 에이전트가 적용 직전에 격리 검색(shadow_search)을 부른다(2026-09-17). 테스트가 실제 arXiv·S2 를 치면 안 된다 —
    검색 함수를 덮으면 그 아래를 가짜로 대는 다른 테스트가 깨지므로(첫 시도에서 74개), **호출 지점**(`agent_maintenance.shadow_of`)만 끈다.
    격리 검색 자체는 test_shadow_search 가 자기 가짜로, 연결은 test_agent_maintenance 가 shadow_of 를 다시 살려 검증한다."""
    import agent_maintenance
    if not hasattr(agent_maintenance, "_real_shadow_of"):
        agent_maintenance._real_shadow_of = agent_maintenance.shadow_of      # 원 함수 보관 — 연결을 검증하는 테스트가 되살린다
    monkeypatch.setattr(agent_maintenance, "shadow_of", lambda *a, **k: None)


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


@pytest.fixture(autouse=True)
def _no_real_codex_cli(monkeypatch):
    """테스트가 구독 CLI(Codex)를 실제로 부르지 않게 한다.

    2026-09-18 실측: 동향 서술 엔진을 Codex 우선으로 바꾼 직후 전체 pytest 가 **진짜 codex 를 호출했다**
    (로그에 `[동향] 서술 엔진 codex (574자)`). 옛 경로(`summarize_engine`)만 가짜로 덮던 테스트들이
    새 경로를 안 덮었기 때문이다. 사용 한도를 테스트가 태우고 결과가 실행마다 달라진다.

    막는 자리는 **CLI 탐색**이다 — `codex` 를 못 찾은 것으로 만들면 `narrative_engine._codex` 가
    조용히 다음 엔진으로 넘어간다(설치 안 된 컴퓨터와 같은 경로). Codex 경로 자체를 보는 테스트는
    `shutil.which` 를 자기 가짜로 다시 덮으므로(테스트 픽스처가 autouse 뒤에 걸린다) 영향받지 않는다.
    다른 실행 파일 탐색(docker 등)은 그대로 통과시킨다."""
    import shutil

    real_which = shutil.which

    def which(cmd, *args, **kwargs):
        if cmd == "codex":
            return None
        return real_which(cmd, *args, **kwargs)
    monkeypatch.setattr(shutil, "which", which)
