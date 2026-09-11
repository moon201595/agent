"""C단계(2026-09-11) — 적용 전 영향 분석 회귀. docs/ASTRA_PLAN_2026-09-10.md §7.

전부 운영 함수(profile_impact·research_profile·profile_scoring)를 부른다.
각 결함을 주입하면 실패하는지 돌연변이로 확인했다.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import profile_impact as pi
import research_profile as rp

BEFORE = {"core_topics": ["target term", "trend term"],
          "core_weights": {"target term": 1.0, "trend term": 0.6},
          "target_domain": ["factory"], "exclude": ["banned"], "s2_seeds": ["target term"], "max_items": 2}
START, END = datetime(2026, 9, 1, tzinfo=timezone.utc), datetime(2026, 9, 30, tzinfo=timezone.utc)


def _db(tmp_path):
    db = tmp_path / "c.db"
    rp.create_profile(db, "p", "이름", core_topics=BEFORE["core_topics"], core_weights=BEFORE["core_weights"],
                      target_domain=BEFORE["target_domain"], exclude=BEFORE["exclude"],
                      s2_seeds=BEFORE["s2_seeds"], max_items=2)
    return db


def _obs(db, key, title, abstract="", published="2026-09-09", source="arxiv", at="2026-09-09T05:00:00+00:00",
         seeds=None):
    prof = rp.get_profile(db, "p")
    scan = rp.begin_scan(db, "p", prof, started_at=at)
    # 신원은 key 하나로 고정한다(arxiv_id). source 는 라벨일 뿐이다 — 출처가 달라도
    # 같은 논문이어야 "합집합" 을 시험할 수 있다.
    p = {"arxiv_id": key, "doi": None, "title": title, "abstract": abstract,
         "published": published, "source": source,
         "_score": {"core_hits": ["x"], "domain_hits": [], "excluded": False, "tier_rank": 0, "date_precision": "day"}}
    if seeds: p["s2_seeds"] = seeds
    rp.record_observations(db, scan, "p", [p], core_signature="c", seed_signature="s", observed_at=at)
    rp.finish_scan(db, scan, observations=1)
    return scan


def test_스냅샷은_내용_해시이고_최신_관측이_이기며_출처는_합집합이다(tmp_path):
    """이 테스트가 잡는 것: 키 목록만 해시하는 것, 최신 행 선택 동률 규칙 부재,
    최신 행 값만 써서 이전 발견 경로를 지우는 것."""
    db = _db(tmp_path)
    _obs(db, "a", "target term v1", published="2026-09-01", at="2026-09-01T05:00:00+00:00", source="arxiv")
    s1 = pi.snapshot(db, "p", START, END)
    _obs(db, "a", "target term v2", published="2026-09-05", at="2026-09-05T05:00:00+00:00", source="s2", seeds=["S"])
    s2 = pi.snapshot(db, "p", START, END)
    assert s1["snapshot_id"] != s2["snapshot_id"], "제목·날짜가 바뀌면 스냅샷 ID 도 바뀐다"
    a = s2["papers"][0]
    assert a["title"] == "target term v2" and a["published"] == "2026-09-05"
    assert a["retrieval_sources"] == ["arxiv", "s2"] and a["s2_seeds"] == ["S"], "발견 경로는 합집합"
    assert pi.snapshot(db, "p", START, END)["content_sha256"] == s2["content_sha256"], "같은 입력이면 같은 해시"


def test_초록은_참조_보유_행에서_복원하고_손상은_개체로_대체하지_않는다(tmp_path):
    """이 테스트가 잡는 것: 참조 사슬 허용, 해시 검증 생략, 손상 시
    search_candidates 초록으로 폴백."""
    db = _db(tmp_path)
    holder = _obs(db, "a", "target term", abstract="the real abstract", at="2026-08-20T05:00:00+00:00")
    _obs(db, "a", "target term", abstract="the real abstract", at="2026-09-09T05:00:00+00:00")
    snap = pi.snapshot(db, "p", START, END)   # 보유 행은 기간 밖(8/20) — 복원에는 써도 된다
    assert snap["papers"][0]["abstract"] == "the real abstract"
    assert snap["papers"][0]["abstract_status"] == "restored" and snap["paper_count"] == 1
    # 보유 행의 초록을 손상시키면 해시가 안 맞는다 → corrupt, 개체 테이블로 대체 금지
    with sqlite3.connect(db) as con:
        con.execute("UPDATE candidate_observations SET abstract='tampered' WHERE scan_id=?", (holder,))
        con.execute("UPDATE search_candidates SET abstract='latest entity abstract'")
    snap = pi.snapshot(db, "p", START, END)
    assert snap["papers"][0]["abstract"] == "" and snap["abstract_corrupt"] == ["a"]
    # 참조 대상이 스냅샷 기간 **뒤**의 관측이면 미래 참조 — 누수라 거부한다(손상 표시)
    future = _obs(db, "a", "target term", abstract="the real abstract", at="2026-10-20T05:00:00+00:00")
    with sqlite3.connect(db) as con:
        con.execute("UPDATE candidate_observations SET abstract='the real abstract', abstract_ref=NULL WHERE scan_id=?", (future,))
        con.execute("UPDATE candidate_observations SET abstract=NULL, abstract_ref=? WHERE abstract IS NULL OR abstract_ref IS NOT NULL", (future,))
        con.execute("UPDATE candidate_observations SET abstract_ref=? WHERE scan_id<>? ", (future, future))
        con.execute("UPDATE candidate_observations SET abstract=NULL WHERE scan_id<>?", (future,))
    snap = pi.snapshot(db, "p", START, END)     # END=9/30 < 10/20
    assert snap["papers"][0]["abstract"] == "" and snap["papers"][0]["abstract_status"] == "future_ref"
    # 참조 대상이 아예 없어도(사슬·삭제) 개체 테이블로 대체하지 않는다
    with sqlite3.connect(db) as con:
        con.execute("UPDATE candidate_observations SET abstract_ref='nonexistent' WHERE abstract IS NULL")
    snap = pi.snapshot(db, "p", START, END)
    assert snap["papers"][0]["abstract"] == "" and snap["papers"][0]["abstract_status"] == "corrupt"


def test_보존_키를_운영_정렬이_그대로_쓴다(tmp_path):
    """이 테스트가 잡는 것: 복원 논문의 paper_key 를 제목으로 다시 만드는 것 —
    동률 순서·논문 대응이 운영과 어긋난다."""
    db = _db(tmp_path)
    prof = rp.get_profile(db, "p")
    scan = rp.begin_scan(db, "p", prof, started_at="2026-09-09T05:00:00+00:00")
    paper = {"arxiv_id": None, "doi": "10.1/x", "title": "Same Title", "abstract": "", "published": "2026-09-09",
             "source": "s2", "_score": {"core_hits": ["x"], "domain_hits": [], "excluded": False,
                                        "tier_rank": 0, "date_precision": "day"}}
    rp.record_observations(db, scan, "p", [paper], core_signature="c", seed_signature="s",
                           observed_at="2026-09-09T05:00:00+00:00")
    snap = pi.snapshot(db, "p", START, END)
    assert rp.paper_key(snap["papers"][0]) == "doi:10.1/x", "복원 논문은 제목이 아니라 보존 키로 식별한다"
    assert rp.paper_key({"title": "Same Title"}) != "doi:10.1/x"


def test_결과에_현재_시각_의존_값이_없고_재실행이_같다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 결과에 priority/recency 를 싣는 것."""
    db = _db(tmp_path)
    _obs(db, "a", "target term", published="2026-09-09T00:00:00Z")   # arXiv 형식이라 recency 가 실제로 계산된다
    snap = pi.snapshot(db, "p", START, END)
    after = dict(BEFORE, core_weights={"target term": 1.0, "trend term": 0.7})
    one = pi.impact(snap, BEFORE, after, 2)
    import profile_scoring
    monkeypatch.setattr(profile_scoring, "recency_score", lambda *a, **k: 0.0)   # 다른 날처럼
    two = pi.impact(snap, BEFORE, after, 2)
    assert one == two
    assert "priority" not in json.dumps(one) and "recency" not in json.dumps(one)


def test_실효_씨앗과_arXiv_질의_변경은_shadow_로_보낸다(tmp_path):
    """이 테스트가 잡는 것: `s2_seeds` 필드 비교로 축소하는 것(빈 씨앗은 가중치 폴백),
    core 추가를 검색 판정에서 빼는 것."""
    db = _db(tmp_path)
    _obs(db, "a", "target term new term", published="2026-09-09")
    snap = pi.snapshot(db, "p", START, END)
    # 명시 씨앗 없이 가중치가 S2 경계를 넘는 경우 — 필드는 같고 실효 질의만 바뀐다
    b0 = dict(BEFORE, s2_seeds=[]); b1 = dict(b0, core_weights={"target term": 1.0, "trend term": 1.0})
    d = pi.diff_profiles(b0, b1)
    assert d["seed_changed"] and d["effective_seeds"]["after"] == ["target term", "trend term"]
    assert pi.gate(d, pi.impact(snap, b0, b1, 2), snap)[0] == pi.NEEDS_SHADOW
    # core 추가 — 로컬 적중이 있어도 arXiv 질의가 바뀌므로 shadow
    b2 = dict(BEFORE, core_topics=BEFORE["core_topics"] + ["new term"],
              core_weights={**BEFORE["core_weights"], "new term": 0.6})
    d2 = pi.diff_profiles(BEFORE, b2)
    status, reasons = pi.gate(d2, pi.impact(snap, BEFORE, b2, 2), snap)
    assert status == pi.NEEDS_SHADOW and "arxiv_query_changed" in reasons


def test_임계값_미설정이면_eligible_로_가지_않고_설정하면_held_가_산다(tmp_path):
    """이 테스트가 잡는 것: 미설정 규칙을 기본 통과로 처리하는 것, 경계 연산자 오류."""
    db = _db(tmp_path)
    for i in range(4):
        _obs(db, f"t{i}", "target term", published=f"2026-09-0{i+1}")
    _obs(db, "r", "trend term", published="2026-09-09")
    snap = pi.snapshot(db, "p", START, END)
    # 계층 변경만(씨앗 명시라 실효 씨앗 불변, arXiv 질의 불변) — 검색 영향 없는 순수 순위 변경
    after = dict(BEFORE, core_weights={"target term": 0.6, "trend term": 1.0})
    d = pi.diff_profiles(BEFORE, after); imp = pi.impact(snap, BEFORE, after, 2)
    # 전: t3(9/4), t2(9/3). 후: r 가 tier0 로 올라 1위, t3 는 2위에 남는다 → 이탈은 t2 하나
    assert imp["topk_left"] == ["t2"] and imp["topk_entered"] == ["r"]
    status, reasons = pi.gate(d, imp, snap)
    assert status == pi.INSUFFICIENT and reasons[0].startswith("apply_rules_unconfigured")
    rules = {"max_core_changes": 5, "max_topk_left_ratio": 0.5, "max_exclude_risk": 0.5}
    assert pi.gate(d, imp, snap, rules)[0] == pi.ELIGIBLE, "이탈 1/2 = 0.5 는 한도 '초과'가 아니다"
    status, reasons = pi.gate(d, imp, snap, {**rules, "max_topk_left_ratio": 0.4})
    assert status == pi.HELD and reasons[0].startswith("rank_impact_exceeds_limit")


def test_새_core_가_0편이면_묶음_합계로_숨길_수_없다(tmp_path):
    db = _db(tmp_path)
    _obs(db, "a", "target term with alpha thing", published="2026-09-09")
    snap = pi.snapshot(db, "p", START, END)
    after = dict(BEFORE, core_topics=BEFORE["core_topics"] + ["alpha thing", "ghost term"],
                 core_weights={**BEFORE["core_weights"], "alpha thing": 0.6, "ghost term": 0.6})
    imp = pi.impact(snap, BEFORE, after, 2)
    assert imp["added_core_hits"] == {"alpha thing": 1, "ghost term": 0}


def test_계층_번호_변경과_가중치_변경을_가른다(tmp_path):
    """이 테스트가 잡는 것: tier_rank 번호만 보고 '강등'이라 쓰는 것."""
    db = _db(tmp_path)
    _obs(db, "a", "trend term", published="2026-09-09")
    snap = pi.snapshot(db, "p", START, END)
    after = dict(BEFORE, core_topics=BEFORE["core_topics"] + ["mid term"],
                 core_weights={**BEFORE["core_weights"], "mid term": 0.8})
    mv = pi.impact(snap, BEFORE, after, 2)["weight_moves"]["a"]
    assert mv["weight"] == (0.6, 0.6) and mv["tier_rank"] == (1, 2), "가중치 같고 번호만 밀렸다"


def test_제외어_위험률은_제외_전_적격_차집합이_분모다(tmp_path):
    """이 테스트가 잡는 것: 최종 적격에서 재는 것(정의상 0), 분모 0 을 0.0 으로 채우는 것."""
    db = _db(tmp_path)
    _obs(db, "n1", "new term paper", published="2026-09-09")
    _obs(db, "n2", "new term but banned", published="2026-09-09")
    snap = pi.snapshot(db, "p", START, END)
    after = dict(BEFORE, core_topics=BEFORE["core_topics"] + ["new term"],
                 core_weights={**BEFORE["core_weights"], "new term": 0.6})
    r = pi.impact(snap, BEFORE, after, 2)["exclude_risk"]
    assert (r["numerator"], r["denominator"], r["rate"]) == (1, 2, 0.5)
    r0 = pi.impact(snap, BEFORE, dict(BEFORE, core_weights={"target term": 1.0, "trend term": 0.7}), 2)["exclude_risk"]
    assert r0["rate"] is None and r0["note"] == "no_new_core_eligible_candidates"


def test_소비_집합은_전후_동일하게_빼고_전체_분석과_구분한다(tmp_path):
    db = _db(tmp_path)
    _obs(db, "old", "target term", published="2026-09-09")
    _obs(db, "new", "target term", published="2026-09-08")
    snap = pi.snapshot(db, "p", START, END)
    after = dict(BEFORE, core_weights={"target term": 1.0, "trend term": 0.7})
    imp = pi.impact(snap, BEFORE, after, 1, consumed_keys={"old"})
    assert imp["rank_moves"]["old"]["after"] == 1, "전체 분석에는 소비된 논문이 있다"
    assert imp["delivery_view"]["topk_after"] == ["new"], "전달 후보 분석은 소비 집합을 뺀다"


def test_저장은_입력_해시로_재사용을_막고_같은_입력만_돌려쓴다(tmp_path):
    """이 테스트가 잡는 것: after·K·규칙이 바뀐 뒤 옛 판정을 재사용하는 것."""
    db = _db(tmp_path)
    _obs(db, "a", "target term", published="2026-09-09")
    snap = pi.snapshot(db, "p", START, END)
    after = dict(BEFORE, core_weights={"target term": 1.0, "trend term": 0.7})
    one = pi.analyze_and_store(db, snap, BEFORE, after, 2)
    two = pi.analyze_and_store(db, snap, BEFORE, after, 2)
    assert two["reused"] and two["analysis_id"] == one["analysis_id"]
    three = pi.analyze_and_store(db, snap, BEFORE, after, 3)
    assert not three["reused"] and three["analysis_id"] != one["analysis_id"]
    four = pi.analyze_and_store(db, snap, BEFORE, after, 2, rules={"max_core_changes": 1})
    assert not four["reused"]
    five = pi.analyze_and_store(db, snap, BEFORE, after, 2, consumed_keys={"a"})
    six = pi.analyze_and_store(db, snap, BEFORE, after, 2, consumed_keys=set())
    assert not five["reused"] and not six["reused"], "소비 집합만 바뀌어도 옛 전달 분석을 재사용하면 안 된다"
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM impact_analyses").fetchone()[0] == 5
    bad = pi.analyze_and_store(db, snap, BEFORE, dict(BEFORE, core_topics=[]), 2)
    assert bad["gate_status"] == pi.INVALID and "structure:empty_core" in bad["reasons_json"]
    dis = pi.analyze_and_store(db, snap, BEFORE, dict(BEFORE, exclude=[]), 2)
    assert dis["gate_status"] == pi.INVALID and "disallowed:exclude_changed" in dis["reasons_json"]
