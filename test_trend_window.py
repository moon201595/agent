"""① 최근 7일 창 — 매일 메일에 기간 비교를 붙인 것(2026-09-18 사용자 요청).

그전까지 매일 메일의 동향은 **그날 실린 5편**만 보고 쓴 스냅숏이었고, 기간 비교는 월요일 주간 리뷰에만
있었다(그마저 9/21 이 첫 발송이라 한 번도 안 나갔다). 여기서 지키는 계약은 두 가지다 —
수치는 Python 이 세고, **직전 구간에 관측이 없으면 증감을 만들지 않는다**."""
from datetime import datetime, timedelta, timezone

import pytest

import digest
import research_profile
import trend_report

NOW = datetime(2026, 9, 18, 4, 0, tzinfo=timezone.utc)


def _profile(db, pid="p1"):
    import storage
    research_profile.init_db(db)
    storage.init_storage(db)     # observed_rows 가 summaries·papers 를 LEFT JOIN 한다
    # 동반어 가드가 걸린 키워드(`world model` 등)는 픽스처로 쓰지 않는다 — 가드 때문에 안 걸리는 것을
    # 창 집계의 결함으로 오해하게 된다(2026-09-18 실제로 그렇게 헤맸다).
    research_profile.create_profile(db, pid, "테스트", core_topics=["defect detection", "anomaly detection"])
    return research_profile.get_profile(db, pid)


def _seed(db, pid, rows):
    """rows: [(paper_key, 제목, 초록, first_seen)] — search_candidates 에 바로 넣는다."""
    import sqlite3
    with sqlite3.connect(db) as con:
        for key, title, abstract, seen in rows:
            con.execute(
                "INSERT OR REPLACE INTO search_candidates "
                "(profile_id, paper_key, arxiv_id, title, abstract, source, first_seen, last_seen) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (pid, key, key, title, abstract, "arxiv", seen, seen))


def test_no_previous_observations_means_no_delta(tmp_path):
    """이 테스트가 잡는 것: 직전 창이 비었을 때 0→N 을 '급증'으로 내보내는 것.
    2026-09-18 실측으로 team_robot·team_vision 은 수집 이력이 하루뿐이라 이 경로가 실제 운영 경로다."""
    db = tmp_path / "p.db"
    prof = _profile(db)
    _seed(db, "p1", [(f"a{i}", "Defect detection on wafers", "defect detection study", (NOW - timedelta(days=1)).isoformat())
                     for i in range(4)])
    mv = trend_report.window_movement(db, prof, end=NOW)
    assert mv is not None
    assert mv["comparable"] is False
    assert mv["papers"] == (4, 0)
    assert all(prev == 0 for _kw, _now, prev in mv["keywords"])
    # 메일 문안이 "비교하지 않았다"를 말해야 한다
    text = "\n".join(digest._window_section({"trend_window": mv}))
    assert "증감을 내지 않았다" in text
    assert "직전" in digest._window_html({"trend_window": mv})


def test_delta_is_reported_when_both_windows_have_observations(tmp_path):
    """이 테스트가 잡는 것: 두 구간 모두 관측이 있는데도 비교를 건너뛰는 것 · 증감 부호를 뒤집는 것."""
    db = tmp_path / "p.db"
    prof = _profile(db)
    recent = (NOW - timedelta(days=2)).isoformat()
    older = (NOW - timedelta(days=9)).isoformat()
    _seed(db, "p1", [(f"n{i}", "Defect detection paper", "defect detection", recent) for i in range(5)]
                    + [(f"o{i}", "Anomaly detection paper", "anomaly detection", older) for i in range(3)])
    mv = trend_report.window_movement(db, prof, end=NOW)
    assert mv["comparable"] is True
    assert mv["days_covered"] == (1, 1)
    moved = dict((kw, (now, prev)) for kw, now, prev in mv["keywords"])
    assert moved["defect detection"] == (5, 0)     # 이번 창에만 있다
    assert moved["anomaly detection"] == (0, 3)    # 직전 창에만 있다 — 줄어든 것도 보여 준다
    text = "\n".join(digest._window_section({"trend_window": mv}))
    # 2026-09-20 개편: 숫자 덩어리를 상승/하락으로 가르고 `▲▼` 를 붙였다. 색만으로 뜻을 나르지 않는다 —
    # 다크 모드나 색을 지우는 클라이언트에서도 화살표는 남는다.
    assert "▲ defect detection" in text and "+5" in text
    assert "▼ anomaly detection" in text and "−3" in text     # 빼기는 U+2212
    assert text.index("상승") < text.index("하락")


def test_narrative_context_carries_terms_but_never_numbers(tmp_path):
    """이 테스트가 잡는 것: 서술 프롬프트에 편수·증감 수치를 흘리는 것(모델이 그 숫자를 문장에 옮기면
    우리가 센 값인지 지어낸 값인지 독자가 못 가른다) · 비교 불가한데 '늘었다'고 말하게 두는 것."""
    ctx = trend_report._movement_context(
        {"comparable": True, "days": 7, "terms": [("agentic rl", 6, 2), ("shrinking", 1, 9)]})
    assert "agentic rl" in ctx
    assert "shrinking" not in ctx          # 줄어든 말은 "늘어난 말" 맥락에 넣지 않는다
    assert "6" not in ctx and "2" not in ctx
    assert trend_report._movement_context({"comparable": False, "terms": [("x", 5, 0)]}) == ""
    assert trend_report._movement_context(None) == ""


def test_window_section_is_absent_without_data():
    """이 테스트가 잡는 것: 창 집계가 없을 때 빈 절 머리만 메일에 남기는 것."""
    assert digest._window_section({}) == []
    assert digest._window_html({}) == ""


def test_reserve_aggregate_reaches_both_renderers():
    """이 테스트가 잡는 것: 자리 밖 후보 집계를 평문에만 싣고 HTML 메일에서 빠뜨리는 것
    (§8-70 이 그 사고였다 — 주간 리뷰가 평문에만 있고 HTML 에는 통째로 없었다) ·
    창 집계가 없는 날 자리 밖 절까지 같이 사라지는 것."""
    result = {"reserve_terms": {"count": 431, "terms": [("agentic rl", 9), ("tool use", 4)]}}
    text = "\n".join(digest._window_section(result))
    html = digest._window_html(result)
    for rendered in (text, html):
        assert "431" in rendered and "agentic rl" in rendered
    # 창 집계가 같이 있어도 두 절이 한 번씩만 나온다
    both = digest._window_section({**result, "trend_window": {
        "days": 7, "papers": (10, 0), "days_covered": (2, 0), "comparable": False,
        "keywords": [("defect detection", 10, 0)], "terms": []}})
    assert "\n".join(both).count("자리에 못 든 후보") == 1
    assert "최근 7일 흐름" in "\n".join(both)


def test_reserve_section_absent_when_nothing_was_held_back():
    """이 테스트가 잡는 것: reserve 가 0편인 날 '0편에서 자주 나온 말' 빈 절을 남기는 것."""
    assert digest._window_section({"reserve_terms": {"count": 0, "terms": []}}) == []
    assert digest._window_html({"reserve_terms": {"count": 0, "terms": []}}) == ""
