"""⑧⑨ 외부 신호(adoption_signals, 2026-09-30) — 관측값만, 판정어 없음, 성능과 섞지 않음."""
import sqlite3
from datetime import date, datetime, timezone

import adoption_signals as a


def test_line_is_observations_only_and_uses_official_or_author_repo():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: "높음·인기·우수" 같은 판정어를 붙이거나, 공식/저자 저장소가 아닌 별을 싣거나, 단위를 틀리면 실패한다."""
    sig = {"scholarly": {"citations": 42, "publication_date": "2025-01-01"}, "github": {"repo": "o/r", "stars": 1300},
           "github_tier": "official", "hub": {"page": True, "n_models": 2, "models": [{"downloads_30d": 27000, "likes": 3}]}}
    line = a.signals_line(sig, date(2026, 9, 30))
    assert line == "인용 42회 · 공식 GitHub ★1.3k · HF 모델 30일 다운로드 27k"
    for banned in ("높", "인기", "우수", "영향력 큼"):
        assert banned not in line
    assert "GitHub" not in a.signals_line({**sig, "github": {}}, date(2026, 9, 30))


def test_new_paper_says_not_yet_observed_and_failures_are_not_zero():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 새 논문의 인용 0 을 경과일 없이 적거나, 조회 실패·미관측을 0/없음으로 바꾸면 실패한다."""
    assert a.signals_line({"scholarly": {"citations": 0, "publication_date": "2026-09-20"}, "hub": {"page": False}},
                          date(2026, 9, 30)) == "신규 논문으로 아직 관측 없음(공개 10일)"
    fail = a.signals_line({"scholarly": {"error": "ConnectError"}, "hub": {"page": True, "n_models": 4, "models": [], "models_error": "X"}})
    assert "인용 조회 실패" in fail and "다운로드 조회 실패" in fail and " 0" not in fail
    assert a._sum_known([None, None]) is None and a._sum_known([None, 3]) == 3


def test_third_party_repo_stars_are_never_used(tmp_path, monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: ⑦ 사다리가 제3자·유사 구현으로 가른 저장소의 별을 이 논문 신호로 모으면 실패한다."""
    import code_ladder
    db = tmp_path / "p.db"
    for tier, want in (("third_party", ""), ("analogous", ""), ("author", "author"), ("official", "official")):
        monkeypatch.setattr(code_ladder, "get", lambda aid, d, _t=tier: {"tier": _t, "url": "https://github.com/o/r"})
        assert a._repo_of(db, "x")[1] == want


def test_collect_queries_once_per_day(tmp_path):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 같은 날 두 번째 프로필에서 외부 API 를 또 부르거나 스냅숏을 안 남기면 실패한다."""
    db = tmp_path / "p.db"
    calls = []

    def fake(url, headers=None):
        calls.append(url)
        return {"citationCount": 3, "publicationDate": "2026-09-16"} if "semanticscholar" in url else None
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    a.collect(db, "2609.1", get=fake, now=now)
    n = len(calls)
    a.collect(db, "2609.1", get=fake, now=now)
    assert len(calls) == n
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT collected_on, citation_count FROM adoption_snapshots").fetchone() == ("2026-09-30", 3)
