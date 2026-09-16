"""⑦ 코드 사다리(2026-09-16) — 공식 → 저자 연관 → 제3자 → 유사 구현 → 없음. GitHub 검색은 가짜로 막는다.

각 테스트의 docstring 에 "무엇을 망가뜨리면 실패하는가"를 적었다.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import code_finder
import code_ladder as cl
import digest
import storage


@pytest.fixture
def db(tmp_path, monkeypatch):
    store = tmp_path / "s.db"
    storage.init_storage(store)
    monkeypatch.setattr(storage, "DB_PATH", store)
    import server
    monkeypatch.setattr(server, "DB_PATH", store)
    return store


def _paper(db, aid, authors):
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO papers (arxiv_id, title, authors, fetched_at) VALUES (?,?,?,?)",
                    (aid, f"paper {aid}", json.dumps(authors), datetime.now(timezone.utc).isoformat()))


def _repro(db, aid, url, source, success, stage="run"):
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO repro_results (arxiv_id, repo_url, source, confidence, success, stage, created_at) "
                    "VALUES (?,?,?,?,?,?,?)", (aid, url, source, "x", int(success), stage, datetime.now(timezone.utc).isoformat()))


def _fake_search(results: dict[str, list]):
    calls = []

    def search(query, limit=5):
        calls.append(query)
        return [code_finder.RepoCandidate(url=u, source="github_search", confidence="unconfirmed", stars=s, full_name=u.split("github.com/")[1],
                                          description=d) for u, s, d in results.get(query, [])]
    return search, calls


def test_tiers_come_from_repro_rows_and_author_surname(db, monkeypatch):
    """이 테스트가 잡는 것: 저자가 적은 저장소를 제3자로 낮추는 것, 소유자에 저자 성이 든 저장소를 저자 연관으로 못 올리는 것,
    성공한 시도가 있는데 실패한 시도의 저장소를 앞세우는 것, 저장소가 있는 논문에서 GitHub 검색을 또 쓰는 것."""
    search, calls = _fake_search({})
    monkeypatch.setattr(code_finder, "github_search", search)
    _paper(db, "a1", ["Jane Kimberly", "Bob Zhangwei"])
    _repro(db, "a1", "https://github.com/someone/paper-a1", "github_search", False, "clone")
    _repro(db, "a1", "https://github.com/zhangwei-lab/a1-code", "github_search", True)
    assert cl.resolve("a1", ["robot manipulation"])["tier"] == "author"
    _paper(db, "a2", ["X Y"])
    _repro(db, "a2", "https://github.com/authors/official", "in_text", False, "build")
    _repro(db, "a2", "https://github.com/other/named-alike", "github_search", True)
    r = cl.resolve("a2", ["kw"])
    assert r["tier"] == "third_party" and r["full_name"] == "other/named-alike"      # 성공한 행이 우선한다
    _paper(db, "a3", ["X Y"])
    _repro(db, "a3", "https://github.com/authors/official", "in_text", False, "build")
    assert cl.resolve("a3", ["kw"])["tier"] == "official"
    assert calls == []
    # 이름만 맞고 clone·설치 대상도 없던 제3자 저장소(실측 'Rastaman4e/-1')는 없는 셈 — 유사 구현 검색으로 내려간다
    _paper(db, "a4", ["X Y"])
    _repro(db, "a4", "https://github.com/Rastaman4e/-1", "github_search", False, "no_target")
    _repro(db, "a4", "https://github.com/x/paper-a4-clone-404", "github_search", False, "clone")
    assert cl.resolve("a4", ["kw"])["tier"] == "none" and calls == ["kw"]


def test_analogous_search_uses_keywords_skips_non_code_and_caches(db, monkeypatch):
    """이 테스트가 잡는 것: 저장소가 없을 때 검색을 안 하는 것(사용자 피드백: 비슷한 거라도 찾아라), awesome-목록·도구 저장소를 참고 구현으로
    내는 것, 별점 낮은 것을 고르는 것, 한 달 안에 같은 검색을 반복해 GitHub 한도를 쓰는 것, 첫 검색어에서 찾았는데 둘째 검색어까지 쓰는 것."""
    search, calls = _fake_search({
        "visual anomaly detection": [("https://github.com/x/awesome-anomaly-detection", 9000, "list"),
                                     ("https://github.com/openvinotoolkit/anomalib", 4200, "An anomaly detection library"),
                                     ("https://github.com/y/small", 12, "toy")],
        "MVTec AD": [("https://github.com/z/mvtec", 100, "")],
    })
    monkeypatch.setattr(code_finder, "github_search", search)
    _paper(db, "b1", ["A B"])
    _repro(db, "b1", "https://github.com/authors/gone", "in_text", False, "clone")
    # 저장소를 찾은 시도가 있으면(in_text) official 이다 — 404 여도 저자가 적은 링크라는 사실은 남는다
    assert cl.resolve("b1", ["visual anomaly detection"])["tier"] == "official"
    _paper(db, "b2", ["A B"])
    r = cl.resolve("b2", ["visual anomaly detection", "MVTec AD"])
    assert r["tier"] == "analogous" and r["full_name"] == "openvinotoolkit/anomalib" and r["stars"] == 4200
    assert r["query"] == ["visual anomaly detection"] and calls == ["visual anomaly detection"]
    cl.resolve("b2", ["visual anomaly detection", "MVTec AD"])
    assert calls == ["visual anomaly detection"]                       # 캐시 — 다시 검색하지 않는다
    r2 = cl.resolve("b2", ["MVTec AD", "visual anomaly detection"])   # 주 키워드가 바뀌면 다시 찾는다(외부 검토 2026-09-16)
    assert calls == ["visual anomaly detection", "MVTec AD"] and r2["full_name"] == "z/mvtec"
    calls.clear()
    cl.resolve("b2", ["MVTec AD"])
    assert calls == []
    stale = datetime.now(timezone.utc) - timedelta(days=cl.REFRESH_DAYS + 1)
    with sqlite3.connect(db) as con:
        con.execute("UPDATE code_ladder SET searched_at=? WHERE arxiv_id='b2'", (stale.isoformat(),))
    cl.resolve("b2", ["MVTec AD"])
    assert calls == ["MVTec AD"]


def test_none_tier_keeps_queries_and_mail_lines_never_claim_official(db, monkeypatch):
    """규칙 7. 이 테스트가 잡는 것: 유사 구현을 공식처럼 쓰는 것, '없음'에서 검색어를 안 남기는 것, 공식 코드 논문에 군더더기 줄을 붙이는 것."""
    search, calls = _fake_search({})
    monkeypatch.setattr(code_finder, "github_search", search)
    _paper(db, "c1", ["A B"])
    r = cl.resolve("c1", ["obscure task", "another"])
    assert r["tier"] == "none" and r["query"] == ["obscure task", "another"]
    assert "찾지 못했다" in cl.mail_line("c1") and "obscure task" in cl.mail_line("c1")
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO code_ladder (arxiv_id, tier, url, full_name, stars, query, description, searched_at) "
                    "VALUES ('c2','analogous','https://github.com/o/r','o/r',4200,'[\"visual anomaly detection\"]','',?)",
                    (datetime.now(timezone.utc).isoformat(),))
        con.execute("INSERT INTO code_ladder (arxiv_id, tier, url, full_name, stars, query, description, searched_at) "
                    "VALUES ('c3','official','https://github.com/a/b','a/b',NULL,'[]','',?)", (datetime.now(timezone.utc).isoformat(),))
    line = cl.mail_line("c2")
    assert "o/r" in line and "★4,200" in line and "재현한 것이 아니다" in line and "공식" not in line.split("·")[0].replace("저장소는 없음", "")
    assert cl.mail_line("c3") == "" and digest.code_ladder_line("c3") == ""
    assert digest.code_ladder_line("c2") == line


def test_digest_shows_ladder_line_in_both_mails(db, monkeypatch):
    """이 테스트가 잡는 것: 사다리 줄이 평문·HTML 한쪽에만 실리는 것, HTML 이스케이프 없이 싣는 것."""
    cl.init_db(db)
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO code_ladder (arxiv_id, tier, url, full_name, stars, query, description, searched_at) "
                    "VALUES ('2609.00001','third_party','https://github.com/o/r','o/<r>',NULL,'[]','',?)",
                    (datetime.now(timezone.utc).isoformat(),))
    paper = {"arxiv_id": "2609.00001", "title": "T", "abstract": "a", "deep_status": "ok",
             "_score": {"priority": 1.0, "core_hits": ["alpha"], "domain_hits": [], "venue_hit": None}}
    result = {"papers": [paper], "candidates_found": 1}
    text = digest.generate_digest(result, "P")
    html = digest.generate_digest_html(result, "P")
    assert "코드: 이름이 맞는 제3자 저장소 o/<r>" in text
    assert "o/&lt;r&gt;" in html and "o/<r>" not in html
