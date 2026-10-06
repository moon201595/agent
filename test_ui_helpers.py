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
    nav = src[src.index("with st.sidebar:"):src.index("_page = st.session_state.nav_page")]
    assert "st.rerun" not in nav
    assert "on_click=_go" in nav
    at = AppTest.from_file("review_app.py", default_timeout=120).run()
    for key in ("papers", "system", "profile", "activity", "overview"):
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


def test_overview_renders_the_mail_order_with_escaped_text(tmp_path, monkeypatch):
    """2026-10-01 개편: 개요는 메일 순서(연구 흐름 → 핵심 논문 → 이번 주)로 그리고, 흐름은 두 칸 격자다.
    망가뜨리면 실패하는 것: 개요가 예외로 죽는 것(빈 DB 테스트는 이 경로를 안 탄다) · 흐름 격자·이름·깊이 칩을 빠뜨리는 것 ·
    메일에서 뺀 검증 수치를 다시 싣는 것 · 논문 제목·흐름 글을 이스케이프 없이 HTML 에 넣는 것(외부 논문 글은 비신뢰 입력이다)."""
    from datetime import datetime, timezone
    import mail_ledger
    import narrative_store
    import research_profile
    import server
    import storage
    from streamlit.testing.v1 import AppTest
    db = tmp_path / "ui.db"
    research_profile.create_profile(db, "p", "비전 — 검사", ["defect detection"], core_weights={"defect detection": 1.0})
    storage.init_storage(db)
    paper = {"arxiv_id": "2609.00001", "title": "FLASH <script>x</script>",
             "_score": {"priority": 1.0, "core_hits": ["defect detection"], "domain_hits": [], "venue_hit": None}}
    mail_ledger.record_issue(db, "i1", "p", "S", [paper], 1, 1, when=datetime.now(timezone.utc))
    narrative_store.save(db, "p", "daily", "■ 오늘의 한 줄\n두 연구가 같은 문제를 다룬다 [P1:A][P2:A].\n\n"
                         "■ 1. 합성 결함 <b>흐름</b>\n둘 다 합성 결함을 쓴다 [P1:A][P2:A].\n"
                         "- FLASH <script>x</script> · 원문 분석 [P1:R]\n- Other · 초록 기반 [P2:A]\n\n"
                         "■ 우리 연구에서 볼 것\n- 현장 조건을 확인한다 (해석)", engine="codex")
    monkeypatch.setattr(server, "DB_PATH", db)
    at = AppTest.from_file("review_app.py", default_timeout=120).run()
    assert not at.exception
    html = "\n".join(m.value for m in at.markdown)
    assert "<div class='rm-grid2'><div class='rm-card rm-thread'>" in html     # 스타일 블록에도 같은 글자가 있다 — 태그로 본다
    assert "합성 결함 &lt;b&gt;흐름&lt;/b&gt;" in html
    assert "원문 분석" in html and "오늘의 핵심 논문 1편" in html and "우리 연구에서 볼 것" in html and "갈래 1" in html
    assert "<script>" not in html and "FLASH &lt;script&gt;" in html
    assert "검증 통과" not in html
    assert html.index("오늘의 연구 흐름") < html.index("오늘의 핵심 논문")


def test_paper_links_and_summary_lines_escape_and_only_link_safe_targets():
    """핵심 논문 표·논문 상세는 외부 논문 글(제목·요약)을 HTML 로 옮긴다. 망가뜨리면 실패하는 것: 제목·한국어 제목·요약 줄을 이스케이프 없이 넣는 것 ·
    javascript: 같은 주소를 링크로 만드는 것 · 앱 안 상세 링크(?paper=…)를 새 탭으로 여는 것 · 요약 항목명 굵게·근거 번호 표시를 잃는 것."""
    import review_app
    html = review_app._papers_table_html([{"position": 1, "title": "T <b>", "title_ko": "한국어 <i>", "depth": "원문 분석", "core_hits": ["kw"],
                                           "link": "javascript:alert(1)", "paper_id": "", "published": "2026-09-29"}], "p")
    assert "T &lt;b&gt;" in html and "한국어 &lt;i&gt;" in html and "javascript" not in html and "원문 분석 완료" in html
    inner = review_app._title_link("X", "2609.00001", "team_robot")
    assert "href='?paper=2609.00001&amp;profile=team_robot'" in inner and "target='_self'" in inner
    assert "href='https://arxiv.org/abs/1'" in review_app._title_link("X", "", None, "https://arxiv.org/abs/1")
    line = review_app._summary_line_html("- 무엇을 했는가 : <script>x</script> [S0006, S0007]")
    assert "<b>무엇을 했는가</b>" in line and "&lt;script&gt;" in line and "<span class='rm-ref'>[S0006, S0007]</span>" in line


def test_activity_calendar_selects_a_day_and_shows_its_mail(tmp_path, monkeypatch):
    """2026-10-01 사용자: 날짜별 펼침 목록 대신 달력에서 눌러 본다. 망가뜨리면 실패하는 것: 활동 기록이 예외로 죽는 것 · 메일 보낸 날을
    점으로 안 찍는 것 · 날짜를 눌러도 오른쪽이 그날로 안 바뀌는 것 · 처음 열 때 마지막 메일 날이 아닌 날을 보여 주는 것."""
    from datetime import datetime, timezone
    import mail_ledger
    import research_profile
    import server
    import storage
    from streamlit.testing.v1 import AppTest
    db = tmp_path / "act.db"
    research_profile.create_profile(db, "p", "비전 — 검사", ["defect detection"])
    storage.init_storage(db)
    paper = lambda t: {"arxiv_id": t, "title": f"Paper {t}", "_score": {"priority": 1.0, "core_hits": ["defect detection"],   # noqa: E731
                                                                     "domain_hits": [], "venue_hit": None}}
    mail_ledger.record_issue(db, "i1", "p", "첫 메일", [paper("a")], 1, 1, when=datetime(2026, 9, 21, 21, tzinfo=timezone.utc))   # 9/22 KST
    mail_ledger.record_issue(db, "i2", "p", "둘째 메일", [paper("b")], 1, 1, when=datetime(2026, 9, 30, 21, tzinfo=timezone.utc))  # 10/1 KST
    monkeypatch.setattr(server, "DB_PATH", db)
    at = AppTest.from_file("review_app.py", default_timeout=120).run()
    at.button(key="nav_activity").click().run()
    assert not at.exception
    html = "\n".join(m.value for m in at.markdown)
    assert "2026. 10" in html and "2026년 10월 1일 (목)" in html and "둘째 메일" in html     # 처음엔 마지막 메일 날
    assert "●" in at.button(key="cal_2026-10-01").label
    at.button(key="calnav_prev").click().run()
    assert "●" in at.button(key="cal_2026-09-22").label and "●" not in at.button(key="cal_2026-09-23").label
    at.button(key="cal_2026-09-22").click().run()
    html = "\n".join(m.value for m in at.markdown)
    assert "2026년 9월 22일 (화)" in html and "첫 메일" in html and "둘째 메일" not in html


def test_paper_detail_page_opens_from_its_link(tmp_path, monkeypatch):
    """제목 링크(?paper=ID)로 들어오면 논문 상세(시안: 경로·제목·칩·요약 절 카드·기본 정보). 망가뜨리면 실패하는 것: 주소의 논문을 무시하고
    개요를 그리는 것 · 요약 절을 카드로 안 나누는 것 · 수식을 고정폭으로 안 감싸는 것 · 없는 논문에 예외로 죽는 것."""
    import research_profile
    import server
    import sqlite3
    import storage
    from streamlit.testing.v1 import AppTest
    db = tmp_path / "d.db"
    research_profile.create_profile(db, "p", "로봇 — 팀", ["robot"])
    storage.init_storage(db)
    md = tmp_path / "s.md"
    md.write_text("### 연구 개요\n- 무엇을 하려 했는가 : 협력 <b> [S0006]\n### 방법 상세\n- 학습 목적함수 : $\\mathcal{L}$ 를 쓴다\n", encoding="utf-8")
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO papers (arxiv_id, title, authors, published, categories, abstract) VALUES (?,?,?,?,?,?)",
                    ("2609.00001", "Paper <T>", '["A", "B"]', "2026-09-29", '["cs.RO"]', "abs"))
        con.execute("INSERT INTO summaries (arxiv_id, path, engine, coverage_ratio) VALUES (?,?,?,?)", ("2609.00001", str(md), "gemini", 1.0))
    monkeypatch.setattr(server, "DB_PATH", db)
    at = AppTest.from_file("review_app.py", default_timeout=120)
    at.query_params["paper"] = "2609.00001"
    at.run()
    assert not at.exception
    html = "\n".join(m.value for m in at.markdown)
    assert "Paper &lt;T&gt;" in html and "논문 상세" in html and ">연구 개요<" in html and ">방법론<" in html
    assert "<b>연구 목적</b>" in html and "협력 &lt;b&gt;" in html and "<code class='rm-math'>" in html
    at2 = AppTest.from_file("review_app.py", default_timeout=120)
    at2.query_params["paper"] = "nope"
    at2.run()
    assert not at2.exception
