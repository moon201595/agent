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
        con.execute("CREATE TABLE search_runs (profile_id TEXT, window_from TEXT, started_at TEXT)")
        con.execute("INSERT INTO search_runs VALUES ('p','2026-08-01','2026-10-01')")
        con.execute("CREATE TABLE mail_issues (issue_id TEXT, profile_id TEXT, sent_at TEXT, status TEXT)")
        con.execute("CREATE TABLE mail_issue_items (issue_id TEXT, paper_key TEXT)")
    return db


def _it(aid, published="2026-09-20", title="Some Paper"):
    return {"title": title, "arxiv_id": aid, "official": {"title": title, "published": published, "arxiv_id": aid, "doi": None}}


@pytest.mark.parametrize("rows, want", [
    ([], "not_retrieved"),
    ([("dropped", "no_core_hit")], "no_core_hit"),
    ([("dropped", "exclude_hit")], "excluded"),
    ([("reserve", None)], "ranked_out"),
    ([("content", None)], "not_delivered"),             # 선정됐지만 발송 기록이 없다 — 배달로 세지 않는다(Codex 최종 검토)
    ([("filtered", "already_shown")], "not_delivered"),
])
def test_gap_stage_uses_observations_before_discovery(tmp_path, rows, want):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 놓침 단계(검색 못 함·핵심어 미적중·제외·자리 탈락·이미 메일)를 뒤섞으면 실패한다."""
    db = _db(tmp_path)
    with sqlite3.connect(db) as con:
        con.executemany("INSERT INTO candidate_observations VALUES ('s1','p','2609.1','Some Paper',?,?,'2026-10-01T20:10:00+00:00')", rows)
    assert es.gap_stage(db, "p", _it("2609.1"), NOW) == (want, "s1")


def test_latest_observation_and_mail_ledger_decide_the_stage(tmp_path):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 예전 관측(reserve)이 발견 직전 관측(exclude_hit)을 이기거나(Codex 재현), 선정(content)을
    배달로 세거나, 발견 전 발송 기록이 있는데 놓침으로 세면 실패한다."""
    db = _db(tmp_path)
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO candidate_observations VALUES ('s1','p','2609.1','Some Paper','reserve',NULL,'2026-09-25T20:10:00+00:00')")
        con.execute("INSERT INTO candidate_observations VALUES ('s1','p','2609.1','Some Paper','dropped','exclude_hit','2026-10-01T20:10:00+00:00')")
        con.execute("INSERT INTO candidate_observations VALUES ('s1','p','2609.3','Other Paper','content',NULL,'2026-10-01T20:10:00+00:00')")
    assert es.gap_stage(db, "p", _it("2609.1"), NOW)[0] == "excluded"
    assert es.gap_stage(db, "p", _it("2609.3", title="Other Paper"), NOW)[0] == "not_delivered"
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO mail_issues VALUES ('i1','p','2026-10-01T21:00:00+00:00','sent')")
        con.execute("INSERT INTO mail_issue_items VALUES ('i1','2609.3')")
        con.execute("INSERT INTO mail_issues VALUES ('i2','p','2026-10-01T21:00:00+00:00','failed')")
        con.execute("INSERT INTO mail_issue_items VALUES ('i2','2609.1')")
    assert es.gap_stage(db, "p", _it("2609.3", title="Other Paper"), NOW)[0] == "already_captured"
    assert es.gap_stage(db, "p", _it("2609.1"), NOW)[0] == "excluded"                 # 실패한 발송은 배달이 아니다


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
    got = es.capture_summary(["not_retrieved", "no_core_hit", "ranked_out", "already_captured", "not_yet_evaluable", "excluded",
                              "not_delivered"])
    assert got == {"evaluable": 6, "not_yet_evaluable": 1, "retrieved": 5, "core_hit": 3, "delivered": 1}


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
        con.execute("INSERT INTO search_runs (run_id, profile_id, source, query, window_from, window_to, status, started_at) "
                    "VALUES ('base', 'p', 'arxiv', 'q', '2026-09-01', '2026-10-01', 'done', '2026-10-01')")
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



def test_performance_check_must_match_the_claimed_metric():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 정찰의 AUROC 주장을 같은 행의 F1 셀 값으로 "확인"하면 실패한다(Codex 최종 검토 재현)."""
    html = ('<figure class="ltx_table" id="T1"><figcaption>Results on MVTec AD.</figcaption><table>'
            '<tr><th>Method</th><th>AUROC</th><th>F1</th></tr><tr><td>Ours</td><td>80.0</td><td>60.0</td></tr></table></figure>')
    it = {"benchmark": {"name": "MVTec AD", "metric": "AUROC", "value": "60.0"},
          "official": {"arxiv_id": "2609.1", "title": "Method X"}}
    assert es.check_performance(it, lambda *a, **k: html, float("inf")) is False
    it["benchmark"]["value"] = "80.0"
    assert es.check_performance(it, lambda *a, **k: html, float("inf")) is True


def test_verify_cap_is_shared_across_profiles_and_marks_partial(tmp_path, monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 검증 상한을 앞에서부터 잘라 뒤쪽 프로필이 0편이 되거나, 검증 못 한 항목이 남았는데 done 으로 적으면
    실패한다(Codex 재현: 5·5·2·0 인데 done)."""
    import research_profile
    monkeypatch.setattr(es, "MAX_VERIFY", 12)   # 상한값 자체가 아니라 돌아가며 채우는 방식을 본다(2026-10-08 상한 12→30)
    db = _world(tmp_path)
    for pid in ("q", "r", "s"):
        research_profile.create_profile(db, pid, pid, core_topics=["defect detection"], s2_seeds=["defect detection"])
    ids = ["p", "q", "r", "s"]
    items = {pid: [{**ITEM, "arxiv_id": f"2609.{i}{j:04d}", "title": f"Surface Defect Paper {pid} {j}"} for j in range(5)]
             for i, pid in enumerate(ids, start=1)}
    official = lambda keys: [{"title": f"Surface Defect Paper {k}", "abstract": "a", "publicationDate": "2026-09-10", "venue": "v",
                              "externalIds": {}} for k in keys]
    by_title = {f"ARXIV:2609.{i}{j:04d}": f"{pid} {j}" for i, pid in enumerate(ids, start=1) for j in range(5)}
    seen = []

    def verdict(payload, timeout):
        got = json.loads(payload)["items"]
        seen.extend(x["profile"]["name"] for x in got)
        return {"items": [{"id": x["id"], "verdict": "verified", "relevant": True, "manufacturing_relation": "direct",
                           "claim_supported": True, "candidate_terms": [], "note": "ok"} for x in got]}
    res = es.run_weekly(db, NOW, scout=lambda p, t: _scout_json(items),
                        s2_batch=lambda keys: official([by_title[k] for k in keys]), verify=verdict, fetch=lambda *a, **k: "")
    counts = Counter(seen)
    assert sum(counts.values()) == es.MAX_VERIFY and all(counts[n] == 3 for n in ("비전", "q", "r", "s"))
    assert res["status"] == "partial" and "verify_cap:8" in res["error"]


def test_old_out_reaction_outside_the_brief_still_blocks_external_keyword():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 브리프 창(28일)·상한 밖의 관심 밖 반응이 외부 근거 키워드 추가를 막지 못하면 실패한다(Codex 재현)."""
    b = _brief({"E1": "not_retrieved", "E2": "no_core_hit"}, texts={"E1": TXT, "E2": TXT})
    b.out_texts = [f"An old paper about {TERM} that the user marked as out of interest."]
    assert am.validate([_add(["E1", "E2"])], b)[1][0]["reason"] == "conflicts_user_reaction"


# ── 관심 분야의 떠오르는 연구축(2026-10-08) ───────────────────────────────────────────

AXIS = "visual anomaly reasoning"


def _axis_paper(i, published="2026-09-20"):
    return {"title": f"Visual Anomaly Reasoning for Inspection {i}", "arxiv_id": f"2609.2222{i}", "url": f"https://arxiv.org/abs/2609.2222{i}",
            "published": published}


def _axis_raw(items, axes):
    return json.dumps({"profiles": items, "axes": axes})


def test_sanitize_axes_keeps_only_new_specific_terms_with_two_papers():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 이미 보는 키워드(겹침 포함)·우산어·근거 1편짜리를 연구축으로 받거나, 한국어가 아닌 요약을 싣으면 실패한다."""
    profiles = [{"id": "p", "name": "비전", "core": ["defect detection"], "exclude": ["MRI"]}]
    papers = [_axis_paper(0), _axis_paper(1)]
    axes = {"p": [{"term": AXIS, "summary_ko": "결함 원인을 설명하는 이상 탐지", "papers": papers}]}
    got = es.sanitize_axes(_axis_raw({}, axes), profiles)["p"]
    assert [a["term"] for a in got] == [AXIS] and got[0]["summary"] == "결함 원인을 설명하는 이상 탐지"
    # 프로필당 상한(2)에 가리지 않게 거를 용어는 하나씩 따로 본다 — 겹치는 키워드, 제외어, 우산어
    for bad in ("defect detection models", "MRI slice segmentation", "deep learning"):
        assert es.sanitize_axes(_axis_raw({}, {"p": [{"term": bad, "summary_ko": "요약", "papers": papers}]}), profiles)["p"] == [], bad
    one = {"p": [{"term": AXIS, "summary_ko": "요약", "papers": papers[:1]}]}
    assert es.sanitize_axes(_axis_raw({}, one), profiles)["p"] == []
    english = {"p": [{"term": AXIS, "summary_ko": "explains anomalies", "papers": papers}]}
    assert es.sanitize_axes(_axis_raw({}, english), profiles)["p"][0]["summary"] == ""
    assert es.sanitize_axes("not json", profiles) == {}


def _axis_world(tmp_path):
    db = _world(tmp_path)
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE IF NOT EXISTS search_candidates (profile_id TEXT, paper_key TEXT, title TEXT, abstract TEXT, first_seen TEXT)")
        con.execute("CREATE TABLE IF NOT EXISTS search_runs (run_id TEXT, profile_id TEXT, window_from TEXT)")
        con.execute("INSERT INTO search_runs (run_id, profile_id, source, query, window_from, window_to, status, started_at) "
                    "VALUES ('r1', 'p', 'arxiv', 'q', '2026-09-01', '2026-09-02', 'done', '2026-09-02')")
        for i, seen in enumerate(["2026-09-25", "2026-10-01", "2026-09-01"]):
            con.execute("INSERT INTO search_candidates (profile_id, paper_key, title, abstract, first_seen, last_seen) VALUES ('p', ?, ?, 'x', ?, ?)",
                        (f"k{i}", f"Visual anomaly reasoning paper {i}", seen + "T00:00:00+00:00", seen + "T00:00:00+00:00"))
    return db


def test_axes_flow_to_mail_with_python_checked_evidence_and_keep_capture_comparable(tmp_path):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 근거 논문을 정찰 항목과 같은 신원·검증 경로로 보내지 않거나, 공식 초록에 용어가 없는 논문까지
    근거로 세거나, 근거 논문을 "외부 정찰 대비 수집률" 분모에 섞거나, 우리 후보의 최근·직전 편수를 못 세면 실패한다."""
    db = _axis_world(tmp_path)
    items = [{**ITEM, "arxiv_id": f"2609.1111{i}", "title": f"Zero-Shot Surface Defect Segmentation Study {i}"} for i in range(2)]
    third = {**_axis_paper(2), "title": "Inspection Robots in Smart Factories 2"}     # 공식 제목·초록 어디에도 용어가 없는 근거
    axes = {"p": [{"term": AXIS, "summary_ko": "결함 원인을 설명하는 이상 탐지", "papers": [_axis_paper(0), _axis_paper(1), third]}]}

    def official(ids):
        rows = []
        for key in ids:
            aid = key.split(":", 1)[1]
            if aid.startswith("2609.2222"):
                i = int(aid[-1])
                abstract = "We study visual anomaly reasoning for factories." if i < 2 else "A different topic."
                title = f"Visual Anomaly Reasoning for Inspection {i}" if i < 2 else "Inspection Robots in Smart Factories 2"
                rows.append({"title": title, "abstract": abstract, "publicationDate": "2026-09-20",
                             "venue": "arXiv", "externalIds": {"ArXiv": aid}})
            else:
                i = int(aid[-1])
                rows.append({"title": f"Zero-Shot Surface Defect Segmentation Study {i}", "abstract": "zero-shot surface defect segmentation",
                             "publicationDate": "2026-09-10", "venue": "CVPR", "externalIds": {"ArXiv": aid}})
        return rows
    sent = []

    def verdict(payload, t):
        got = json.loads(payload)["items"]
        sent.extend(got)
        return {"items": [{"id": x["id"], "verdict": "verified", "relevant": True, "manufacturing_relation": "direct",
                           "claim_supported": True, "candidate_terms": [], "note": "근거 일치"} for x in got]}
    res = es.run_weekly(db, NOW, scout=lambda p, t: _axis_raw({"p": items}, axes), verify=verdict, s2_batch=official, fetch=lambda *a, **k: "")
    assert res["status"] == "done"
    assert any(x["axes"] == [AXIS] for x in sent)                              # 근거 논문도 Claude 검증을 탔다
    assert json.loads(res["capture_json"])["p"]["evaluable"] == 2              # 정찰 항목만 분모
    sc = es.mail_summary(db, "p", NOW)
    assert sc["capture"]["evaluable"] == 2 and sc["verified"] == 2
    # 정찰 설명은 판정어가 없으면 싣는다. 미포착 내역은 근거 논문의 놓침 단계, 분모는 같은 창에 우리 검색에 처음 들어온 후보 전체(2026-10-08 외부 검토).
    assert sc["axes"] == [{"term": AXIS, "summary": "결함 원인을 설명하는 이상 탐지", "papers": 2, "recent": 2, "previous": 1,
                           "recent_total": 2, "previous_total": 1, "missed": {"not_retrieved": 2},
                           "evidence": [{"title": "Visual Anomaly Reasoning for Inspection 1", "link": "https://arxiv.org/abs/2609.22221", "stage": "검색 소스가 못 가져옴"},
                                        {"title": "Visual Anomaly Reasoning for Inspection 0", "link": "https://arxiv.org/abs/2609.22220", "stage": "검색 소스가 못 가져옴"}]}]


def test_axis_needs_two_verified_papers_and_old_papers_are_not_misses(tmp_path):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: Claude 가 근거 하나를 rejected 했는데 연구축을 남기거나, 관측 시작(가장 이른 검색 창) 전에 나온
    논문을 "검색이 못 가져옴"으로 세면 실패한다 — 그건 놓친 것이 아니라 잡을 기회가 없던 것이다."""
    db = _axis_world(tmp_path)
    axes = {"p": [{"term": AXIS, "summary_ko": "요약", "papers": [_axis_paper(0), _axis_paper(1, "2026-08-01")]}]}
    official = lambda ids: [{"title": f"Visual Anomaly Reasoning for Inspection {k[-1]}", "abstract": "visual anomaly reasoning",
                             "publicationDate": "2026-09-20" if k.endswith("0") else "2026-08-01", "venue": "arXiv",
                             "externalIds": {"ArXiv": k.split(":", 1)[1]}} for k in ids]
    verdict = lambda payload, t: {"items": [{"id": x["id"], "verdict": "rejected" if x["official"]["published"] == "2026-08-01" else "verified",
                                             "relevant": True, "manufacturing_relation": "direct", "claim_supported": True,
                                             "candidate_terms": [], "note": "판정"} for x in json.loads(payload)["items"]]}
    es.run_weekly(db, NOW, scout=lambda p, t: _axis_raw({"p": []}, axes), verify=verdict, s2_batch=official, fetch=lambda *a, **k: "")
    with sqlite3.connect(db) as con:
        stages = dict(con.execute("SELECT published_at, gap_stage FROM external_observations").fetchall())
    assert stages["2026-08-01"] == "before_monitoring" and stages["2026-09-20"] == "not_retrieved"
    sc = es.mail_summary(db, "p", NOW)
    assert sc is not None and sc["axes"] == []


def test_axes_render_in_plain_html_and_saved_mail_identically():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 연구축 절이 평문·HTML 중 하나에서 빠지거나, 형식 수정본 역파싱이 그 줄을 못 읽어 외부 정찰 절 전체가
    옛 줄 렌더러로 떨어지면 실패한다."""
    import digest
    import saved_digest
    sc = {"verified": 2, "capture": {"evaluable": 2, "retrieved": 1, "core_hit": 1, "delivered": 0}, "missed": [],
          "axes": [{"term": AXIS, "summary": "결함 원인을 설명하는 이상 탐지 (설명형)", "papers": 2, "recent": 2, "previous": 1,
                    "recent_total": 40, "previous_total": 31, "missed": {"not_retrieved": 1, "no_core_hit": 1}},
                   {"term": "event camera inspection", "summary": "", "papers": 3, "recent": None, "previous": None,
                    "recent_total": None, "previous_total": None, "missed": {}}]}
    sc["watch"] = []
    for axis in sc["axes"]:
        axis["evidence"] = []
    lines = digest._external_scout_lines({"external_scout": sc})
    assert any(digest.AXES_HEADING in line for line in lines) and "성장세는 확인하지 않음" in digest.AXES_HEADING
    html = digest._external_scout_html({"external_scout": sc})
    for row in ("정찰 설명: 결함 원인을 설명하는 이상 탐지 (설명형)", "근거 논문 2편 · 미포착 2편(검색 못 가져옴 1 · 핵심어 불일치 1)",
                "우리 검색 관측: 최근 4주 2/40편 · 직전 4주 1/31편", "근거 논문 3편"):
        assert row in html and any(row in line for line in lines), row
    assert "떠오르" not in html
    assert saved_digest._scout_from_lines(lines) == sc
    assert saved_digest._scout_html(lines) == html
    assert saved_digest._scout_from_lines(lines) == {**sc, "missed": []}
    assert saved_digest._scout_html(lines) == html


def _axis_verdict(payload, timeout):
    return {"items": [{"id": x["id"], "verdict": "verified", "relevant": True,
                       "manufacturing_relation": "indirect", "claim_supported": False,
                       "candidate_terms": [AXIS], "note": "관심 분야의 근거"}
                      for x in json.loads(payload)["items"]]}


def _axis_official(ids):
    return [{"title": _axis_paper(int(k[-1]))["title"], "abstract": AXIS,
             "publicationDate": "2026-09-20", "externalIds": {"ArXiv": k.split(":", 1)[1]}}
            for k in ids]


@pytest.mark.parametrize("axes", [None, [], {"p": 3}, {"p": [{"term": AXIS, "papers": 3}]},
                                     {"p": [{"term": AXIS, "papers": {"a": 1}}]}])
def test_malformed_axes_preserve_paper_scout(tmp_path, axes):
    """연구축 자료형 오류를 논문 정찰까지 전파하거나 정상 논문을 잃으면 실패한다."""
    db = _axis_world(tmp_path)
    got = es.run_weekly(db, NOW, scout=lambda *a: _axis_raw({"p": [_axis_paper(0)]}, axes),
                        s2_batch=_axis_official, verify=_axis_verdict)
    assert got["status"] == "done" and got["verified_count"] == 1
    assert es.mail_summary(db, "p", NOW)["verified"] == 1


def test_axis_only_profile_and_bad_evidence_urls(tmp_path):
    """profiles 에 빈 프로필이 생략됐거나 URL 목록이 잘못돼도 유효한 연구축 신원 검증을 잃으면 실패한다."""
    db = _axis_world(tmp_path)
    papers = [{**_axis_paper(i), "evidence_urls": {"bad": True}} for i in (0, 1)]
    got = es.run_weekly(db, NOW, scout=lambda *a: _axis_raw({}, {"p": [{"term": AXIS, "papers": papers}]}),
                        s2_batch=_axis_official, verify=_axis_verdict)
    assert got["status"] == "done"
    assert es.mail_summary(db, "p", NOW)["axes"][0]["papers"] == 2


def test_official_aliases_merge_before_verification_and_brief(tmp_path):
    """arXiv·DOI 중복을 2편으로 세거나 병합 때 정찰 출처·축·기존 주장을 잃으면 실패한다."""
    db = _axis_world(tmp_path)
    first = {**_axis_paper(0), "contribution": "original claim", "source_type": "conference"}
    alias = {**first, "arxiv_id": None, "url": None, "doi": "10.1234/alias", "contribution": None, "source_type": "other"}
    captured = []

    def verify(payload, timeout):
        captured.extend(json.loads(payload)["items"])
        return _axis_verdict(payload, timeout)

    # 두 응답의 ID 가 서로 연결되지 않아도 같은 공식 제목은 같은 논문이다.
    def official(ids):
        return [{"title": first["title"], "abstract": AXIS, "publicationDate": "2026-09-20",
                 "externalIds": {"DOI": "10.1234/alias"} if k.startswith("DOI:") else {"ArXiv": "2609.22220"}}
                for k in ids]

    got = es.run_weekly(db, NOW, scout=lambda *a: _axis_raw({"p": [first]},
                        {"p": [{"term": AXIS, "papers": [alias, first]}]}), s2_batch=official, verify=verify)
    assert got["candidate_count"] == 1 and len(captured) == 1
    assert captured[0]["axes"] == [AXIS]
    assert captured[0]["scout_claims"]["contribution"] == "original claim"
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT source_type, source_url FROM external_observations").fetchone() == (
            "conference", "https://arxiv.org/abs/2609.22220")
    ev = es.evidence_for_brief(db, "p", NOW)
    assert len(ev) == 1 and ev[0]["from_scout"] is True and ev[0]["axes"] == [AXIS]
    sc = es.mail_summary(db, "p", NOW)
    assert sc["verified"] == 1 and sc["axes"] == [] and sc["capture"]["evaluable"] == 1


def _save_axis_observation(db, oid, key, at, *, run="current", term=AXIS, verdict="verified", title=None):
    official = {"title": title or f"Visual anomaly reasoning study {key}", "abstract": AXIS,
                "published": "2026-09-20", "arxiv_id": key}
    evidence = {"identity": "verified", "official": official, "axes": [term] if term else [],
                "from_scout": True, "axis_summaries": {AXIS: "최고 성능 100점 추천"}}
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO external_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (oid, run, "p", key, official["title"], "arxiv", None, "2026-09-20", "not_retrieved", "s1",
                     json.dumps(evidence), json.dumps({"verdict": verdict, "relevant": True}), at))


def test_brief_uses_latest_identity_and_latest_axis_run_only(tmp_path):
    """7일 경계·이전 회차·미래 자료를 이번 연구축에 섞거나 최신 거절을 옛 승인으로 덮으면 실패한다."""
    db = _axis_world(tmp_path)
    es.init_db(db)
    _save_axis_observation(db, "boundary", "k0", "2026-09-28T20:00:00+00:00", run="old")
    _save_axis_observation(db, "old", "k1", "2026-09-30T20:00:00+00:00", run="old")
    _save_axis_observation(db, "new", "k1", NOW.isoformat(), term=None)
    _save_axis_observation(db, "stale", "k2", "2026-10-01T20:00:00+00:00", run="old")
    _save_axis_observation(db, "fresh", "k3", NOW.isoformat())
    _save_axis_observation(db, "future", "k4", "2026-10-06T00:00:00+00:00", run="future")
    _save_axis_observation(db, "rejected-old", "k5", "2026-10-01T20:00:00+00:00", run="old")
    _save_axis_observation(db, "rejected-new", "k5", NOW.isoformat(), verdict="rejected")
    ev = es.evidence_for_brief(db, "p", NOW)
    assert {e["paper_key"] for e in ev} == {"k1", "k2", "k3"}
    assert {e["paper_key"] for e in ev if e["axes"]} == {"k3"}
    assert es.emerging_axes(db, "p", ev, NOW) == []


def test_axis_counts_bound_time_and_hide_unverified_prose(tmp_path):
    """미래·시간대 경계 후보를 잘못 세거나 공식 날짜 밖 근거·자유 요약의 판정어를 메일에 싣으면 실패한다."""
    db = _axis_world(tmp_path)
    es.init_db(db)
    for i in (0, 1):
        _save_axis_observation(db, f"e{i}", f"a{i}", NOW.isoformat())
    with sqlite3.connect(db) as con:
        for key, at in [("future", "2026-10-06T00:00:00+00:00"),
                        ("offset", "2026-09-08T02:00:00+09:00")]:  # UTC 9/7 17:00 은 직전 창이다.
            con.execute("INSERT INTO search_candidates (profile_id,paper_key,title,abstract,first_seen,last_seen) VALUES ('p',?,?,?, ?,?)",
                        (key, AXIS, "", at, at))
    sc = es.mail_summary(db, "p", NOW)
    assert sc["axes"] == [{"term": AXIS, "summary": "", "papers": 2, "recent": 2, "previous": 2,
                           "recent_total": 2, "previous_total": 2, "missed": {"not_retrieved": 2},
                           "evidence": [{"title": "Visual anomaly reasoning study a1", "link": "https://arxiv.org/abs/a1", "stage": "검색 소스가 못 가져옴"},
                                        {"title": "Visual anomaly reasoning study a0", "link": "https://arxiv.org/abs/a0", "stage": "검색 소스가 못 가져옴"}]}]   # 판정어 섞인 설명은 버린다
    ev = es.evidence_for_brief(db, "p", NOW)
    ev[0]["published"] = "2025-01-01"
    assert es.emerging_axes(db, "p", ev, NOW) == []
    ev[0]["published"] = "2026-10-06"
    assert es.emerging_axes(db, "p", ev, NOW) == []
    with sqlite3.connect(db) as con:
        con.execute("DROP TABLE search_candidates")
    axes = es.mail_summary(db, "p", NOW)["axes"]
    assert axes[0]["recent"] is None and axes[0]["previous"] is None


def test_before_monitoring_uses_only_prior_windows_and_official_dates(tmp_path):
    """미래 검색 창·빈 창·정찰이 적은 날짜로 오래된 논문을 놓침 근거로 바꾸면 실패한다."""
    db = _axis_world(tmp_path)
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO search_runs (run_id,profile_id,source,query,window_from,window_to,status,started_at) "
                    "VALUES ('future','p','arxiv','q','2020-01-01','2026-10-10','done','2026-10-10')")
    assert es.gap_stage(db, "p", _it("old", "2026-08"), NOW)[0] == "before_monitoring"
    assert es.gap_stage(db, "p", _it("unknown-day", "2026-09"), NOW)[0] == "not_yet_evaluable"
    assert es.gap_stage(db, "p", {**_it("old", ""), "published": "2026-09-20"}, NOW)[0] == "not_yet_evaluable"
    with sqlite3.connect(db) as con:
        con.execute("UPDATE search_runs SET window_from='' WHERE run_id!='future'")
    assert es.gap_stage(db, "p", _it("old", "2026-08-01"), NOW)[0] == "not_yet_evaluable"


def test_axis_evidence_reuses_existing_keyword_rules(tmp_path):
    """실제 브리프 E 근거로 추가 규칙을 우회하거나 before_monitoring 1편을 놓친 2편으로 세면 실패한다."""
    db = _axis_world(tmp_path)
    axes = {"p": [{"term": AXIS, "papers": [_axis_paper(0), _axis_paper(1)]}]}
    es.run_weekly(db, NOW, scout=lambda *a: _axis_raw({"p": []}, axes),
                  s2_batch=_axis_official, verify=_axis_verdict)
    brief = am.build_brief(db, "p", NOW)
    ev = list(brief.external)
    action = {"op": "add_keyword", "term": AXIS, "weight": 0.7, "evidence": ev, "reason": "외부 논문 근거"}
    assert len(ev) == 2
    assert len(am.validate([action], brief)[0]) == 1
    assert am.validate([{**action, "weight": 0.8}], brief)[0] == []
    with sqlite3.connect(db) as con:
        con.execute("UPDATE external_observations SET gap_stage='before_monitoring' WHERE paper_key='2609.22220'")
    assert am.validate([action], am.build_brief(db, "p", NOW))[0] == []


def test_verify_missing_and_database_failure_do_not_escape(tmp_path, monkeypatch):
    """검증 항목 누락을 done 으로 숨기거나 DB 조회 오류가 정찰 밖으로 전파되면 실패한다."""
    db = _axis_world(tmp_path)
    got = es.run_weekly(db, NOW, scout=lambda *a: _scout_json({"p": [_axis_paper(0)]}),
                        s2_batch=_axis_official, verify=lambda *a: {"items": []})
    assert got["status"] == "partial" and es.evidence_for_brief(db, "p", NOW) == []
    monkeypatch.setattr(es, "gap_stage", lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError()))
    got = es.run_weekly(db, NOW, force=True, scout=lambda *a: _scout_json({"p": [_axis_paper(0)]}),
                        s2_batch=_axis_official, verify=_axis_verdict)
    assert got["status"] == "failed" and got["error"] == "scout_processing:OperationalError"
    assert got["scout_calls"] == 1 and got["verify_calls"] == 0


def test_real_verify_caps_cover_four_profiles_without_losing_scout_items(tmp_path):
    """실제 30항목 상한이 없어지거나 연구축 때문에 기존 20편이 밀리거나 초과를 done 으로 기록하면 실패한다."""
    import research_profile
    db = _axis_world(tmp_path)
    ids = ["p", "q", "r", "s"]
    for pid in ids[1:]:
        research_profile.create_profile(db, pid, pid, core_topics=["defect detection"])
    papers, axes, official_by_id = {}, {}, {}
    for i, pid in enumerate(ids):
        all_papers = [{"title": f"Visual anomaly reasoning study {pid} {j}", "arxiv_id": f"2609.{i}{j:04d}"}
                      for j in range(11)]
        papers[pid] = all_papers[:5]
        axes[pid] = [{"term": AXIS, "papers": all_papers[5:8]},
                     {"term": "causal defect reasoning", "papers": all_papers[8:]}]
        for paper in all_papers:
            official_by_id["ARXIV:" + paper["arxiv_id"]] = {
                "title": paper["title"], "abstract": AXIS, "publicationDate": "2026-09-20",
                "externalIds": {"ArXiv": paper["arxiv_id"]}}
    calls = []

    def verify(payload, timeout):
        assert timeout == (es.VERIFY_TIMEOUT_S if not calls else es.AXIS_VERIFY_TIMEOUT_S) and len(payload) <= 120_000
        calls.append(json.loads(payload)["items"])
        return _axis_verdict(payload, timeout)

    got = es.run_weekly(db, NOW, scout=lambda *a: _axis_raw(papers, axes),
                        s2_batch=lambda keys: [official_by_id[k] for k in keys], verify=verify)
    # 최대 적재(4×(정찰 5 + 연구축 2×근거 3) = 44)가 두 호출로 나뉘어 하나도 빠지지 않는다(2026-10-08 — 예전 단일 30 상한은 14항목을 버렸다).
    assert [len(c) for c in calls] == [20, 24] and got["status"] == "done" and got["verify_calls"] == 2
    assert all(not e["axes"] for e in calls[0]) and all(e["axes"] for e in calls[1])
    assert sorted(Counter(e["profile"]["name"] for e in calls[0]).values()) == [5, 5, 5, 5]

    # 연구축 쪽 호출이 실패해도 논문 정찰 검증은 남는다 — 한 호출이면 둘 다 사라졌다.
    db2 = _axis_world(tmp_path / "iso")
    for pid in ids[1:]:
        research_profile.create_profile(db2, pid, pid, core_topics=["defect detection"])
    state = {"n": 0}

    def flaky(payload, timeout):
        state["n"] += 1
        if state["n"] == 2:
            raise TimeoutError()
        return _axis_verdict(payload, timeout)
    got = es.run_weekly(db2, NOW, scout=lambda *a: _axis_raw(papers, axes),
                        s2_batch=lambda keys: [official_by_id[k] for k in keys], verify=flaky)
    assert got["status"] == "partial" and "axis_verify:TimeoutError" in got["error"] and got["verified_count"] == 20


def test_verify_payload_size_is_bounded(tmp_path, monkeypatch):
    """항목 수만 제한하고 긴 프로필 때문에 검증 입력 크기를 무제한으로 보내면 실패한다."""
    db = _axis_world(tmp_path)
    monkeypatch.setattr(es, "_profiles", lambda db: [{"id": "p", "name": "비전", "domain": [], "core": ["x" * 70000]}])
    payloads = []

    def verify(payload, timeout):
        payloads.append(payload)
        return _axis_verdict(payload, timeout)

    got = es.run_weekly(db, NOW, scout=lambda *a: _scout_json({"p": [_axis_paper(0), _axis_paper(1)]}),
                        s2_batch=_axis_official, verify=verify)
    assert got["status"] == "partial" and got["error"] == "verify_cap:1"
    assert len(payloads) == 1 and len(payloads[0]) <= 120_000
    assert len(json.loads(payloads[0])["items"]) == 1


def test_axes_require_official_date_and_corrupt_cache_is_ignored(tmp_path):
    """정찰이 쓴 날짜로 공식 날짜 누락을 메우거나 손상된 저장 JSON 때문에 브리프가 중단되면 실패한다."""
    db = _axis_world(tmp_path)
    def official(keys):
        return [{**row, "publicationDate": None} for row in _axis_official(keys)]
    axes = {"p": [{"term": AXIS, "papers": [_axis_paper(0), _axis_paper(1)]}]}
    es.run_weekly(db, NOW, scout=lambda *a: _axis_raw({"p": []}, axes), s2_batch=official, verify=_axis_verdict)
    assert es.mail_summary(db, "p", NOW)["axes"] == []
    with sqlite3.connect(db) as con:
        con.execute("UPDATE external_observations SET verified_json='{broken'")
    assert es.evidence_for_brief(db, "p", NOW) == []


def test_prompt_includes_keywords_after_twenty_five_and_axis_lines_round_trip():
    """키워드 26번째부터 숨겨 이미 보는 연구축을 다시 찾게 하거나 기존 저장 메일을 못 읽으면 실패한다."""
    import digest
    import saved_digest
    prompt = es.scout_prompt([{"id": "p", "name": "비전", "core": [f"topic {i}" for i in range(26)], "domain": []}], NOW)
    assert "topic 25" in prompt
    # 옛 한 줄 형식은 메일로 나간 적이 없어(연구축은 10/12 첫 운행) 역파싱을 두지 않는다 — 대신 깨진 줄이면 절 전체가 줄 렌더러로 떨어지는지 본다.
    sc = {"verified": 0, "capture": {"evaluable": 0, "retrieved": 0, "core_hit": 0, "delivered": 0}, "missed": [],
          "axes": [{"term": AXIS, "summary": "", "papers": 2, "recent": 2, "previous": 1, "recent_total": None, "previous_total": None,
                    "missed": {}}]}
    sc["watch"] = []
    for axis in sc["axes"]:
        axis["evidence"] = []
    lines = digest._external_scout_lines({"external_scout": sc})
    assert saved_digest._scout_from_lines(lines) == sc
    with pytest.raises(ValueError):
        saved_digest._scout_from_lines([*lines, "    근거 논문 2편 · 미포착 3편(검색 못 가져옴 1)"])


def test_alias_merge_does_not_move_scout_papers_behind_axis_only_items(tmp_path, monkeypatch):
    """별칭 병합 뒤 순서가 바뀌어 기존 정찰 논문이 검증 상한 밖으로 밀리면 실패한다."""
    db = _axis_world(tmp_path)
    monkeypatch.setattr(es, "MAX_VERIFY", 5)
    papers = [_axis_paper(i) for i in range(5)]
    alias = {**papers[0], "arxiv_id": None, "url": None, "doi": "10.1234/alias"}
    axes = {"p": [{"term": AXIS, "papers": [_axis_paper(5), _axis_paper(6), alias]}]}
    calls = []

    def official(keys):
        return _axis_official(["ARXIV:2609.22220" if k.startswith("DOI:") else k for k in keys])

    def verify(payload, timeout):
        calls.append({x["official"]["title"] for x in json.loads(payload)["items"]})
        return _axis_verdict(payload, timeout)

    result = es.run_weekly(db, NOW, scout=lambda *a: _axis_raw({"p": papers}, axes), s2_batch=official, verify=verify)
    # 별칭(DOI)으로 다시 들어온 정찰 논문은 정찰 쪽 호출에 남고, 연구축 근거로만 들어온 2편만 두 번째 호출로 간다.
    assert calls[0] == {p["title"] for p in papers} and len(calls[1]) == 2 and not calls[0] & calls[1]
    assert result["status"] == "done"


def test_empty_new_run_does_not_resurrect_old_axes(tmp_path):
    """최신 정찰이 0건인데 최근 7일 안의 이전 연구축을 이번 결과로 되살리면 실패한다."""
    from datetime import timedelta
    db = _axis_world(tmp_path)
    axes = {"p": [{"term": AXIS, "papers": [_axis_paper(0), _axis_paper(1)]}]}
    es.run_weekly(db, NOW - timedelta(days=1), scout=lambda *a: _axis_raw({"p": []}, axes),
                  s2_batch=_axis_official, verify=_axis_verdict)
    assert es.mail_summary(db, "p", NOW)["axes"][0]["papers"] == 2
    got = es.run_weekly(db, NOW, force=True, scout=lambda *a: _scout_json({"p": []}),
                        s2_batch=_axis_official, verify=_axis_verdict)
    assert got["status"] == "done" and got["candidate_count"] == 0
    assert es.mail_summary(db, "p", NOW)["axes"] == []


def test_same_instant_retry_uses_last_inserted_verdict(tmp_path):
    """같은 시각의 강제 재시도에서 무작위 관측 ID 순서가 최신 거절을 덮으면 실패한다."""
    db = _axis_world(tmp_path)
    es.init_db(db)
    _save_axis_observation(db, "z-old", "k0", NOW.isoformat())
    _save_axis_observation(db, "a-new", "k0", NOW.isoformat(), verdict="rejected")
    assert es.evidence_for_brief(db, "p", NOW) == []


def _adopt_axis(db, now=NOW):
    return es.run_weekly(db, now, force=True,
                         scout=lambda *a: _axis_raw({"p": []}, {"p": [{"term": AXIS, "papers": [_axis_paper(0), _axis_paper(1)]}]}),
                         s2_batch=_axis_official, verify=_axis_verdict)


def test_brief_axes_reuse_e_ids_and_shared_counts(tmp_path):
    """축의 E 번호가 같은 브리프 논문과 다르거나 메일·브리프 편수 및 미포착 규칙이 갈라지면 실패한다."""
    db = _axis_world(tmp_path)
    _adopt_axis(db)
    with sqlite3.connect(db) as con:
        con.execute("UPDATE external_observations SET gap_stage='not_delivered' WHERE paper_key='2609.22221'")
    brief = am.build_brief(db, "p", NOW)
    axes = brief.data["external"]["axes"]
    assert axes == [{"term": AXIS, "evidence": ["E1", "E2"], "verified_papers": 2, "missed_papers": 1,
                     "missed_stages": {"not_retrieved": 1}, "watch": {"first_week": "2026-W41", "status": "watching"}}]
    assert axes[0]["evidence"] == [e["id"] for e in brief.data["external"]["items"]]
    assert all(AXIS in brief.texts[eid].lower() for eid in axes[0]["evidence"])
    emerging = es.emerging_axes(db, "p", es.evidence_for_brief(db, "p", NOW), NOW)[0]
    assert emerging["papers"] == axes[0]["verified_papers"] == 2
    assert emerging["missed"] == axes[0]["missed_stages"] == {"not_retrieved": 1}


def test_axis_mail_evidence_prefers_missed_then_latest_and_limits_two(tmp_path, monkeypatch):
    """미포착보다 이미 발송 논문을 먼저 고르거나 발표일 순서·최대 두 편·한국어 단계·기존 링크 규칙이 깨지면 실패한다."""
    db = _axis_world(tmp_path)
    rows = [{"paper_key": key, "title": title, "abstract": AXIS, "published": date, "axes": [AXIS],
             "gap_stage": stage, "from_scout": False}
            for key, title, date, stage in [
                ("2609.99990", "Captured newest", "2026-10-05", "already_captured"),
                ("2609.99991", "Missed oldest", "2026-09-01", "not_retrieved"),
                ("doi:10.1234/new", "Missed newest", "2026-10-01", "no_core_hit"),
                ("2609.99992", "Missed second", "2026-09-25", "ranked_out"),
                ("2609.99993", "Wrong term", "2026-10-04", "not_retrieved")]]
    rows[-1]["abstract"] = "unrelated topic"
    monkeypatch.setattr(es, "evidence_for_brief", lambda *a: rows)
    got = es.mail_summary(db, "p", NOW)["axes"][0]
    assert got["papers"] == 4
    assert got["evidence"] == [
        {"title": "Missed newest", "link": "https://doi.org/10.1234/new", "stage": "가져왔으나 핵심어에 안 걸림"},
        {"title": "Missed second", "link": "https://arxiv.org/abs/2609.99992", "stage": "관련인데 자리에서 밀림"}]


def test_watchlist_survives_empty_week_dormancy_and_profile_adoption(tmp_path):
    """이번 회차가 비어도 지난 채택을 남기고 4주 무근거·핵심어 편입 상태 및 8주 경계를 지키지 못하면 실패한다."""
    from datetime import timedelta
    import research_profile
    db = _axis_world(tmp_path)
    _adopt_axis(db)
    expected = {"term": AXIS, "first_week": "2026-W41", "last_evidence_week": "2026-W41", "papers_total": 2,
                "new_this_week": 0, "status": "watching"}
    later = NOW + timedelta(days=7)
    es.run_weekly(db, later, scout=lambda *a: _axis_raw({"p": []}, {}), s2_batch=_axis_official, verify=_axis_verdict)
    assert es.watchlist(db, "p", later) == [expected]
    assert es.watched_terms(db, "p", later) == [AXIS]
    mail = es.mail_summary(db, "p", later)
    assert mail["axes"] == [] and mail["watch"] == [expected]
    dormant = NOW + timedelta(days=28)
    assert es.watchlist(db, "p", dormant) == [{**expected, "status": "dormant"}]
    assert es.watched_terms(db, "p", dormant) == []
    research_profile.create_profile(db, "p", "비전", core_topics=["visual-anomaly reasoning"])
    assert es.watchlist(db, "p", dormant) == [{**expected, "status": "in_profile"}]
    assert es.mail_summary(db, "p", dormant)["watch"][0]["status"] == "in_profile"
    assert es.watchlist(db, "p", NOW + timedelta(weeks=8)) == []


def test_watch_response_allowlist_and_verification_path(tmp_path):
    """목록 밖 축을 받거나 추적 근거가 공식 신원·검증을 건너뛰거나 정찰 수집률 분모에 섞이면 실패한다."""
    from datetime import timedelta
    db = _axis_world(tmp_path)
    _adopt_axis(db)
    now = NOW + timedelta(days=7)
    prompts, calls, identified = [], [], []
    def scout(prompt, timeout):
        prompts.append(prompt)
        return json.dumps({"profiles": {"p": []}, "watch": {"p": [
            {"term": AXIS, "papers": [_axis_paper(2), _axis_paper(3), _axis_paper(4)]},
            {"term": "unknown research direction", "papers": [_axis_paper(5)]}]}})
    def official(keys):
        identified.extend(keys)
        return [{**row, "publicationDate": "2026-10-08"} for row in _axis_official(keys)]
    def verify(payload, timeout):
        calls.append(json.loads(payload)["items"])
        return _axis_verdict(payload, timeout)
    got = es.run_weekly(db, now, scout=scout, s2_batch=official, verify=verify)
    assert got["status"] == "done" and got["verify_calls"] == 1
    assert '"status": "watching"' in prompts[0] and AXIS in prompts[0]
    assert 'last_scout_at: 2026-10-05T20:00:00+00:00' in prompts[0]
    assert identified == ["ARXIV:2609.22222", "ARXIV:2609.22223"]
    assert len(calls) == 1 and len(calls[0]) == 2 and all(e["axes"] == [AXIS] for e in calls[0])
    with sqlite3.connect(db) as con:
        rows = con.execute("SELECT evidence_json, gap_stage FROM external_observations WHERE run_id=?", (got["run_id"],)).fetchall()
    assert all(json.loads(e)["from_scout"] is False for e, stage in rows)
    assert all(stage == "not_yet_evaluable" for e, stage in rows)
    assert json.loads(got["capture_json"])["p"]["evaluable"] == 0
    assert es.watchlist(db, "p", now) == [{"term": AXIS, "first_week": "2026-W41", "last_evidence_week": "2026-W42",
                                          "papers_total": 4, "new_this_week": 2, "status": "watching"}]
    assert es.mail_summary(db, "p", now)["watch"] == []  # 이번 연구축으로 이미 표시한다.


def test_watched_terms_is_read_only_and_returns_empty_on_errors(tmp_path, monkeypatch):
    """조회가 DB를 새로 만들거나 초기화하거나 선택적 일일 대조에서 예외를 전파하면 실패한다."""
    import research_profile
    missing = tmp_path / "missing.db"
    assert es.watched_terms(missing, "p", NOW) == [] and not missing.exists()
    db = _axis_world(tmp_path)
    _adopt_axis(db)
    monkeypatch.setattr(research_profile, "init_db", lambda *a: pytest.fail("watch initialized schema"))
    assert es.watched_terms(db, "p", NOW) == [AXIS]
    monkeypatch.setattr(es, "watchlist", lambda *a: (_ for _ in ()).throw(RuntimeError("broken")))
    assert es.watched_terms(db, "p", NOW) == []


def test_watch_reobserved_papers_do_not_refresh_new_evidence(tmp_path):
    """같은 논문 재관측을 새 근거로 세어 휴면을 무한 연장하거나 미래·오프셋 경계를 잘못 다루면 실패한다."""
    from datetime import timedelta
    db = _axis_world(tmp_path)
    _adopt_axis(db)
    later = NOW + timedelta(weeks=4)
    _adopt_axis(db, later)
    assert es.watchlist(db, "p", later) == [{"term": AXIS, "first_week": "2026-W41", "last_evidence_week": "2026-W41",
                                            "papers_total": 2, "new_this_week": 0, "status": "dormant"}]
    assert es.watchlist(db, "p", NOW - timedelta(seconds=1)) == []
    assert es.watchlist(db, "p", NOW.astimezone(timezone(timedelta(hours=9))))[0]["new_this_week"] == 2
    # 최신 거절은 옛 승인을 복원하지 않지만, 과거 회차의 채택 사실 자체는 남는다.
    with sqlite3.connect(db) as con:
        con.execute("UPDATE external_observations SET verified_json=? WHERE discovered_at=? AND paper_key='2609.22221'",
                    (json.dumps({"verdict": "rejected"}), later.isoformat()))
    assert es.watchlist(db, "p", later)[0]["papers_total"] == 1


def test_watch_cap_covers_new_axes_and_three_watches_per_profile(tmp_path, monkeypatch):
    """추적 응답이 프로필당 세 축·축당 두 편 상한을 넘거나 옛 24편 검증 상한 때문에 최대 적재를 버리면 실패한다."""
    db = _axis_world(tmp_path)
    import research_profile
    ids = ["p", "q", "r", "s"]
    terms = ["causal visual reasoning", "sensor fusion planning", "latent action reasoning", "unknown surplus term"]
    for pid in ids[1:]:
        research_profile.create_profile(db, pid, pid, core_topics=["defect detection"])
    monkeypatch.setattr(es, "watchlist", lambda *a: [{"term": term, "status": "watching", "first_week": "2026-W40",
                                                    "last_evidence_week": "2026-W40", "papers_total": 2, "new_this_week": 0}
                                                   for term in terms])
    paper_lists, axes, watches, official = {}, {}, {}, {}
    for i, pid in enumerate(ids):
        papers = [{"title": f"Inspection research {pid} {j}", "arxiv_id": f"2610.{i}{j:04d}"} for j in range(17)]
        paper_lists[pid] = papers[:5]
        axes[pid] = [{"term": AXIS, "papers": papers[5:8]}, {"term": "causal defect reasoning", "papers": papers[8:11]}]
        watches[pid] = [{"term": t, "papers": papers[11+2*j:13+2*j]} for j, t in enumerate(terms[:3])]
        watches[pid].append({"term": terms[3], "papers": [_axis_paper(9)]})
        for paper in papers:
            official["ARXIV:" + paper["arxiv_id"]] = {"title": paper["title"], "abstract": AXIS, "publicationDate": "2026-10-01",
                                                       "externalIds": {"ArXiv": paper["arxiv_id"]}}
    calls = []
    def scout(prompt, timeout):
        assert terms[3] not in prompt
        return json.dumps({"profiles": paper_lists, "axes": axes, "watch": watches})
    def verify(payload, timeout):
        calls.append(json.loads(payload)["items"])
        return _axis_verdict(payload, timeout)
    got = es.run_weekly(db, NOW, scout=scout, s2_batch=lambda keys: [official[k] for k in keys], verify=verify)
    assert got["status"] == "done" and [len(c) for c in calls] == [20, 48]
    assert all(len(c) == 12 for c in [[e for e in calls[1] if e["profile"]["name"] == name] for name in ["비전", "q", "r", "s"]])


def test_watch_keeps_historical_axis_when_rescout_omits_axis_tag(tmp_path):
    """일반 논문 정찰 재관측에 axes가 없다는 이유로 과거 근거 편수를 지우면 실패한다."""
    from datetime import timedelta
    db = _axis_world(tmp_path)
    _adopt_axis(db)
    later = NOW + timedelta(days=7)
    es.run_weekly(db, later, scout=lambda *a: _scout_json({"p": [_axis_paper(0)]}),
                  s2_batch=_axis_official, verify=_axis_verdict)
    assert es.watchlist(db, "p", later) == [{"term": AXIS, "first_week": "2026-W41", "last_evidence_week": "2026-W41",
                                            "papers_total": 2, "new_this_week": 0, "status": "watching"}]


def test_watch_rejected_evidence_does_not_accumulate(tmp_path):
    """추적 목록에 있다는 이유만으로 Claude가 거절한 새 근거를 누적 편수에 넣으면 실패한다."""
    from datetime import timedelta
    db = _axis_world(tmp_path)
    _adopt_axis(db)
    later = NOW + timedelta(days=7)
    def reject(payload, timeout):
        result = _axis_verdict(payload, timeout)
        for item in result["items"]:
            item["verdict"] = "rejected"
        return result
    got = es.run_weekly(db, later,
                        scout=lambda *a: json.dumps({"profiles": {}, "watch": {"p": [{"term": AXIS, "papers": [_axis_paper(2)]}]}}),
                        s2_batch=_axis_official, verify=reject)
    assert got["verify_calls"] == 1 and got["verified_count"] == 0
    assert es.watchlist(db, "p", later)[0]["papers_total"] == 2
    assert es.watchlist(db, "p", later)[0]["new_this_week"] == 0
