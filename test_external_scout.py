"""② 주간 외부 정찰(external_scout, 2026-09-30) — 정찰 의견은 버리고 사실만, 신원은 S2 공식 자료로, 놓침은 발견 직전 스캔 기준으로."""
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone

import pytest

import agent_maintenance as am
import external_scout as es

NOW = datetime(2026, 10, 5, 20, 0, tzinfo=timezone.utc)


def _scout_json(items_by_profile):
    return "잡글 ```json\n" + json.dumps({"profiles": items_by_profile}) + "\n```"


ITEM = {"title": "Zero-Shot Surface Defect Segmentation with Vision Foundation Models", "arxiv_id": "2609.11111v2",
        "url": "https://arxiv.org/abs/2609.11111", "source_type": "conference", "venue": "CVPR 2026", "published": "2026-09-10",
        "contribution": "c", "change_from_prior": "d", "manufacturing_use": "surface inspection",
        "code_url": "https://github.com/o/r", "benchmark": {"name": "MVTec AD", "metric": "AUROC", "value": "99.2"},
        "evidence_urls": ["https://arxiv.org/abs/2609.11111", "https://someblog.example/post"],
        "read_priority": "매우 높음", "score": 9.5, "recommendation": "must read"}


def test_sanitize_drops_opinions_and_unsafe_urls():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 정찰의 읽을 우선순위·점수·추천을 다음 단계로 넘기거나(자기 의견을 자기 근거로 다시 읽는 순환),
    허용 밖 URL 을 남기거나, arXiv 버전·DOI 정규화를 빠뜨리거나, 묻지 않은 프로필·상한 초과 항목을 받으면 실패한다."""
    raw = _scout_json({"p": [ITEM] * 7, "stranger": [ITEM]})
    out = es.sanitize(raw, ["p"])
    assert list(out) == ["p"] and len(out["p"]) == es.MAX_PER_PROFILE
    it = out["p"][0]
    assert set(it) == set(es._ITEM_FIELDS)
    assert "read_priority" not in json.dumps(it) and "must read" not in json.dumps(it)
    assert it["arxiv_id"] == "2609.11111" and it["evidence_urls"] == ["https://arxiv.org/abs/2609.11111"]
    with pytest.raises(ValueError):
        es.sanitize("no json here", ["p"])


def test_identity_needs_matching_official_title():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 실제 ID 에 엉뚱한 제목을 붙인 정찰 환각을 신원 확인으로 통과시키면 실패한다."""
    items = [dict(es.sanitize(_scout_json({"p": [ITEM]}), ["p"])["p"][0]),
             {**es.sanitize(_scout_json({"p": [ITEM]}), ["p"])["p"][0], "title": "A Totally Different Paper About Robots"}]
    official = {"title": "Zero-Shot Surface Defect Segmentation with Vision Foundation Models", "abstract": "We propose ...",
                "publicationDate": "2026-09-10", "venue": "CVPR", "externalIds": {"ArXiv": "2609.11111"}}
    es.identify(items, lambda ids: [official for _ in ids])
    assert items[0]["identity"] == "verified" and items[0]["official"]["abstract"] == "We propose ..."
    assert items[1]["identity"] == "unverified"


def _db(tmp_path):
    db = tmp_path / "p.db"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE scan_runs (scan_id TEXT, profile_id TEXT, started_at TEXT)")
        con.execute("CREATE TABLE candidate_observations (scan_id TEXT, profile_id TEXT, paper_key TEXT, title TEXT, outcome TEXT, "
                    "filter_reason TEXT, observed_at TEXT)")
        con.executemany("INSERT INTO scan_runs VALUES (?,?,?)", [("s1", "p", "2026-10-01T20:00:00+00:00"),
                                                                ("s2", "p", "2026-10-09T20:00:00+00:00")])
    return db


def _it(aid, published="2026-09-20", title="Some Paper"):
    return {"title": title, "arxiv_id": aid, "official": {"title": title, "published": published, "arxiv_id": aid, "doi": None}}


@pytest.mark.parametrize("rows, want", [
    ([], "not_retrieved"),
    ([("dropped", "no_core_hit")], "no_core_hit"),
    ([("dropped", "exclude_hit")], "excluded"),
    ([("reserve", None)], "ranked_out"),
    ([("dropped", "no_core_hit"), ("content", None)], "already_captured"),
    ([("filtered", "already_shown")], "already_captured"),
])
def test_gap_stage_uses_observations_before_discovery(tmp_path, rows, want):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 놓침 단계(검색 못 함·핵심어 미적중·제외·자리 탈락·이미 메일)를 뒤섞으면 실패한다."""
    db = _db(tmp_path)
    with sqlite3.connect(db) as con:
        con.executemany("INSERT INTO candidate_observations VALUES ('s1','p','2609.1','Some Paper',?,?,'2026-10-01T20:10:00+00:00')", rows)
    assert es.gap_stage(db, "p", _it("2609.1"), NOW) == (want, "s1")


def test_gap_stage_has_no_time_leak_and_marks_not_yet_evaluable(tmp_path):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 발견 **뒤**의 관측으로 "에이전트도 찾았다"고 하거나(시점 누수), 마지막 스캔 뒤에 나온 논문을
    놓침으로 세면 실패한다."""
    db = _db(tmp_path)
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO candidate_observations VALUES ('s2','p','2609.1','Some Paper','content',NULL,'2026-10-09T20:10:00+00:00')")
    assert es.gap_stage(db, "p", _it("2609.1"), NOW)[0] == "not_retrieved"
    assert es.gap_stage(db, "p", _it("2609.2", published="2026-10-04"), NOW)[0] == "not_yet_evaluable"


def test_capture_counts_three_stages():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 검색·프로필·배달 capture 를 하나로 뭉개거나 판정 불가 항목을 분모에 넣으면 실패한다."""
    got = es.capture_summary(["not_retrieved", "no_core_hit", "ranked_out", "already_captured", "not_yet_evaluable", "excluded"])
    assert got == {"evaluable": 5, "not_yet_evaluable": 1, "retrieved": 4, "core_hit": 2, "delivered": 1}


def test_verdict_terms_must_be_in_the_official_text():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: Claude 가 공식 제목·초록에 없는 용어나 우산어를 후보로 내도 통과시키면 실패한다."""
    items = [{"id": "E1", "official": {"title": "Zero-shot defect segmentation", "abstract": "We use vision foundation models."}}]
    es.apply_verdicts(items, {"items": [{"id": "E1", "verdict": "verified", "relevant": True, "manufacturing_relation": "direct",
                                         "claim_supported": True, "candidate_terms": ["zero-shot defect segmentation",
                                                                                      "quantum inspection", "deep learning"],
                                         "note": "ok"}]})
    assert items[0]["verified"]["candidate_terms"] == ["zero-shot defect segmentation"]


def _world(tmp_path):
    import research_profile
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "w.db"
    research_profile.init_db(db)
    research_profile.create_profile(db, "p", "비전", core_topics=["defect detection"], s2_seeds=["defect detection"])
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE IF NOT EXISTS scan_runs (scan_id TEXT, profile_id TEXT, started_at TEXT)")
        con.execute("INSERT INTO scan_runs (scan_id, profile_id, started_at, profile_snapshot, policy_version) "
                    "VALUES ('s1','p','2026-10-01T20:00:00+00:00','{}','t')")
    return db


def test_run_weekly_end_to_end_and_fail_open(tmp_path):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 정찰→신원→놓침→검증→저장 흐름이 끊기거나, 같은 주에 다시 부르거나, 정찰·검증 실패가 예외로
    올라가 주간 관리를 막거나, 검증이 없는데 외부 근거를 브리프에 넣으면 실패한다."""
    db = _world(tmp_path)
    items = [{**ITEM, "arxiv_id": f"2609.1111{i}", "title": f"Zero-Shot Surface Defect Segmentation Study {i}"} for i in range(2)]
    official = lambda ids: [{"title": f"Zero-Shot Surface Defect Segmentation Study {i}", "publicationDate": "2026-09-10",
                             "abstract": "We propose zero-shot surface defect segmentation for factories.", "venue": "CVPR",
                             "externalIds": {"ArXiv": f"2609.1111{i}"}} for i in range(len(ids))]
    verdict = lambda payload, t: {"items": [{"id": x["id"], "verdict": "verified", "relevant": True, "manufacturing_relation": "direct",
                                             "claim_supported": True, "candidate_terms": ["zero-shot surface defect segmentation"],
                                             "note": "근거 일치"} for x in json.loads(payload)["items"]]}
    got = es.run_weekly(db, NOW, scout=lambda p, t: _scout_json({"p": items}), verify=verdict, s2_batch=official,
                        fetch=lambda *a, **k: "")
    assert got["status"] == "done" and got["verified_count"] == 2
    assert json.loads(got["capture_json"])["p"]["evaluable"] == 2
    assert es.run_weekly(db, NOW, scout=lambda p, t: pytest.fail("같은 주에 다시 불렀다"))["status"] == "already_ran"
    ev = es.evidence_for_brief(db, "p", NOW)
    assert len(ev) == 2 and ev[0]["gap_stage"] == "not_retrieved" and "surface inspection" not in json.dumps(ev)

    db2 = _world(tmp_path / "b")
    assert es.run_weekly(db2, NOW, scout=lambda p, t: (_ for _ in ()).throw(TimeoutError()))["status"] == "failed"
    part = es.run_weekly(db2, NOW, force=True, scout=lambda p, t: _scout_json({"p": items}), s2_batch=official,
                         verify=lambda p, t: (_ for _ in ()).throw(TimeoutError()), fetch=lambda *a, **k: "")
    assert part["status"] == "partial" and es.evidence_for_brief(db2, "p", NOW) == []


# ── 주간 관리의 검증 규칙 — 외부 근거는 조건부로만 키워드를 더한다 ─────────────────────────────────

def _brief(external, reactions=None, texts=None):
    texts = dict(texts or {})
    b = am.Brief(data={"profile": {"core": [{"id": "K1", "term": "defect detection", "weight": 1.0, "origin": "user", "hits_28d": 3}],
                                   "seeds": [], "target_domain": [], "exclude": []},
                       "reactions": [], "trend": {"records": []}, "external": {"items": []}},
                 texts=texts, reactions=reactions or {}, keyword_ids={"K1": "defect detection"}, base_revision=1,
                 external={e: {"gap_stage": s} for e, s in external.items()})
    return b


TERM = "zero-shot anomaly segmentation"
TXT = f"Paper on {TERM} for factories."


def _add(ev, weight=0.6):
    return {"op": "add_keyword", "term": TERM, "weight": weight, "evidence": ev, "reason": "외부 정찰이 반복 확인"}


def test_external_only_keyword_needs_two_missed_papers_and_a_low_weight():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 외부 논문 한 편으로, 또는 이미 잡고 있던(already_captured) 논문으로 키워드를 더하거나,
    외부 근거만으로 사용자 주 관심과 같은 가중치를 주면 실패한다."""
    two = _brief({"E1": "not_retrieved", "E2": "no_core_hit"}, texts={"E1": TXT, "E2": TXT})
    ok, bad = am.validate([_add(["E1", "E2"])], two)
    assert ok and ok[0]["basis"] == "external" and not bad
    one = _brief({"E1": "not_retrieved", "E2": "already_captured"}, texts={"E1": TXT, "E2": TXT})
    assert am.validate([_add(["E1", "E2"])], one)[1][0]["reason"] == "external_not_missed_twice"
    assert am.validate([_add(["E1", "E2"], weight=1.0)], two)[1][0]["reason"] == "external_weight_too_high"
    no_term = _brief({"E1": "not_retrieved", "E2": "not_retrieved"}, texts={"E1": TXT, "E2": "unrelated text"})
    assert am.validate([_add(["E1", "E2"])], no_term)[1][0]["reason"] == "external_not_missed_twice"


def test_user_out_reaction_beats_external_discovery():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 사용자가 관심 밖으로 표시한 논문의 말을 외부 발견만으로 키워드에 넣으면 실패한다(규칙 1)."""
    b = _brief({"E1": "not_retrieved", "E2": "no_core_hit"}, reactions={"R1": Counter(out=1)},
               texts={"E1": TXT, "E2": TXT, "R1": TXT})
    assert am.validate([_add(["E1", "E2"])], b)[1][0]["reason"] == "conflicts_user_reaction"


def test_external_evidence_cannot_raise_an_existing_weight():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 외부 발견만으로 기존 키워드 가중치를 올리면 실패한다(올리는 것은 반응·동향만)."""
    b = _brief({"E1": "not_retrieved", "E2": "no_core_hit"}, texts={"E1": "defect detection x", "E2": "defect detection y"})
    act = {"op": "set_weight", "term": "defect detection", "weight": 1.5, "evidence": ["E1", "E2"], "reason": "외부"}
    assert am.validate([act], b)[1][0]["reason"] == "no_liked_evidence"


def test_brief_carries_verified_external_evidence_with_official_text(tmp_path):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 검증된 외부 근거가 주간 브리프에 E 로 안 들어가거나, 검증용 글이 공식 초록이 아니라 정찰 문장이면 실패한다."""
    db = _world(tmp_path)
    items = [{**ITEM, "arxiv_id": "2609.11110", "title": "Zero-Shot Surface Defect Segmentation Study 0"}]
    official = lambda ids: [{"title": "Zero-Shot Surface Defect Segmentation Study 0", "publicationDate": "2026-09-10",
                             "abstract": "We propose zero-shot surface defect segmentation.", "venue": "CVPR",
                             "externalIds": {"ArXiv": "2609.11110"}}]
    verdict = lambda payload, t: {"items": [{"id": "E1", "verdict": "verified", "relevant": True, "manufacturing_relation": "direct",
                                             "claim_supported": True, "candidate_terms": [], "note": "ok"}]}
    es.run_weekly(db, NOW, scout=lambda p, t: _scout_json({"p": items}), verify=verdict, s2_batch=official, fetch=lambda *a, **k: "")
    b = am.build_brief(db, "p", NOW)
    assert b.external == {"E1": {"gap_stage": "not_retrieved"}}
    assert b.texts["E1"].startswith("Zero-Shot Surface Defect Segmentation Study 0. We propose")
    assert "surface inspection" not in json.dumps(b.data["external"])          # 정찰이 쓴 제조 적용 문장은 브리프에 없다
    assert b.has_signal()


def test_monday_mail_shows_what_was_missed_without_scout_opinions(tmp_path):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 월요일 메일에 capture 수치·놓친 연구 목록이 빠지거나, 정찰이 쓴 제조 적용 문장·평가가 실리거나,
    외부 정찰이 없는 주에 빈 절이 생기면 실패한다."""
    import digest
    db = _world(tmp_path)
    items = [{**ITEM, "arxiv_id": "2609.11110", "title": "Zero-Shot Surface Defect Segmentation Study 0"}]
    official = lambda ids: [{"title": "Zero-Shot Surface Defect Segmentation Study 0", "publicationDate": "2026-09-10",
                             "abstract": "We propose zero-shot surface defect segmentation.", "venue": "CVPR",
                             "externalIds": {"ArXiv": "2609.11110"}}]
    verdict = lambda payload, t: {"items": [{"id": "E1", "verdict": "verified", "relevant": True, "manufacturing_relation": "direct",
                                             "claim_supported": True, "candidate_terms": [], "note": "ok"}]}
    es.run_weekly(db, NOW, scout=lambda p, t: _scout_json({"p": items}), verify=verdict, s2_batch=official, fetch=lambda *a, **k: "")
    sc = es.mail_summary(db, "p", NOW)
    assert sc["missed"][0]["stage"] == "검색 소스가 못 가져옴" and sc["missed"][0]["link"] == "https://arxiv.org/abs/2609.11110"
    html = digest._external_scout_html({"external_scout": sc})
    text = "\n".join(digest._external_scout_lines({"external_scout": sc}))
    for body in (html, text):
        assert "Zero-Shot Surface Defect Segmentation Study 0" in body and "검색 소스가 가져온 것 0" in body
        assert "surface inspection" not in body and "must read" not in body
    assert digest._external_scout_html({}) == "" and digest._external_scout_lines({}) == []


def test_s2_rate_limit_falls_back_to_arxiv_and_reports_partial_honestly(monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: S2 429 한 번으로 전부 미확인이 되거나(첫 실전 실측: 17편 전부), arXiv 원 출처로 확인하지 않거나,
    신원 조회가 실패해 확인 못 한 항목이 남았는데 "done" 으로 적으면 실패한다."""
    monkeypatch.setattr(es.time, "sleep", lambda s: None)
    calls = []

    def s2(ids):
        calls.append(ids)
        raise RuntimeError("Client error '429 Too Many Requests'")
    items = [{"title": "Memory as Plans: World-Action Modeling", "arxiv_id": "2609.11561", "doi": None},
             {"title": "A DOI Only Journal Paper", "arxiv_id": None, "doi": "10.1109/tii.2026.1"}]
    errors = es.identify(items, s2, lambda ids: {"2609.11561": {"title": "Memory as Plans: World-Action Modeling",
                                                                 "abstract": "abs", "published": "2026-09-12T00:00:00Z"}})
    assert len(calls) == 2 and errors == ["s2:RuntimeError"]
    assert items[0]["identity"] == "verified" and items[0]["official"]["source"] == "arxiv"
    assert items[0]["official"]["published"] == "2026-09-12"
    assert items[1]["identity"] == "unverified"
