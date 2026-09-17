"""ui_helpers 의 상대 시간 표시 — Streamlit 없이 돈다. review_core 시절(§8-31)의 테스트 중 살아남은 함수 것만 남겼다(2026-09-16)."""
from datetime import datetime, timedelta, timezone

import pytest

import ui_helpers as app


def _iso(**kw):
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()


@pytest.mark.parametrize("kw,want", [
    ({"seconds": 5}, "방금 전"),
    ({"minutes": 7}, "7분 전"),
    ({"hours": 3}, "3시간 전"),
    ({"days": 2}, "2일 전"),
])
def test_relative_time_buckets(kw, want):
    assert app._relative_time(_iso(**kw)) == want


def test_relative_time_treats_naive_timestamp_as_utc():
    """DB 에 타임존 없이 저장된 값이 섞여 있다 — 로컬로 해석하면 9시간이
    어긋나 "9시간 전"이 "방금 전"으로 보인다."""
    naive = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    assert app._relative_time(naive) == "방금 전"


def test_relative_time_passes_through_unparseable():
    """못 읽는 값을 빈칸으로 만들면 화면에서 정보가 사라진다 — 원문을 그대로 둔다."""
    assert app._relative_time("언제인지 모름") == "언제인지 모름"
    assert app._relative_time("") == ""



def test_review_app_imports_cleanly():
    """2026-09-17: b792b1f 가 `review_app._render_history` 첫 줄 들여쓰기를 깨뜨렸는데(IndentationError) 어느 테스트도 화면 모듈을
    import 하지 않아 pytest 는 green 이었다 — 운영 화면은 뜨지 않았을 것이다. 이 테스트는 그 구멍을 막는다: 화면 모듈이 문법·import 수준에서
    깨지면 실패한다(Streamlit 은 import 만으로는 서버를 띄우지 않는다)."""
    import importlib
    mod = importlib.import_module("review_app")
    assert callable(mod._render_history)


def test_nav_switches_page_without_explicit_rerun():
    """2026-09-17 사용자 지적 "운영 현황·논문 DB·시스템 오가는 게 느리다". 원인은 `if st.button(): ...; st.rerun()` — 클릭 재실행 뒤
    또 한 번 재실행이라 전환마다 스크립트가 두 번 돌았다. 지금은 on_click 콜백이 재실행 앞에서 nav_page 를 바꾼다.
    망가뜨리면 실패하는 것: (a) 내비 블록에 st.rerun 을 다시 넣는 것 (b) 클릭해도 페이지가 안 바뀌는 것 (c) 어느 페이지든 그리다 예외."""
    import inspect
    import review_app
    from streamlit.testing.v1 import AppTest
    src = inspect.getsource(review_app)
    nav = src[src.index("with st.sidebar:"):src.index('if st.session_state.nav_page == "papers":')]
    assert "st.rerun" not in nav
    assert "on_click=_go" in nav
    at = AppTest.from_file("review_app.py", default_timeout=120).run()
    for key in ("papers", "system", "research"):
        at.button(key=f"nav_{key}").click().run()
        assert at.session_state.nav_page == key
        assert not at.exception


def test_new_profile_defaults_to_five_items(tmp_path):
    """2026-09-17 사용자 결정: 다이제스트 기본 8편은 너무 많다 → 5. 화면 폼과 create_profile 기본값이 같은 상수를 본다.
    망가뜨리면 실패하는 것: 어느 한쪽이 숫자를 다시 박아 넣어 둘이 갈리는 것."""
    import inspect
    import research_profile
    import review_app
    assert research_profile.DEFAULT_MAX_ITEMS == 5
    assert "research_profile.DEFAULT_MAX_ITEMS" in inspect.getsource(review_app._render_profile_form)
    db = tmp_path / "p.db"
    research_profile.init_db(db)
    research_profile.create_profile(db, "p1", "이름", core_topics=["x"])
    assert research_profile.get_profile(db, "p1")["max_items"] == 5
