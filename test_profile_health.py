"""⑪단계(2026-09-12) — 프로필 건강 지표: "적용 후 악화"의 정의. PROGRESS §8-94.

운영 경로(scan_profile)로 관측을 만든 뒤 지표를 센다. 각 테스트의 docstring 에
"무엇을 망가뜨리면 실패하는가"를 적었고 돌연변이로 확인했다.
"""
import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import http_client
import profile_health as ph
import research_profile as rp
import run_profile_scan as rps


def _profile(db, core=("target term", "trend term"), weights=None, exclude=("banned",), k=2):
    rp.create_profile(db, "p", "이름", core_topics=list(core),
                      core_weights=weights or {"target term": 1.0, "trend term": 0.6},
                      exclude=list(exclude), max_items=k, s2_seeds=["target term"])


def _run(db, monkeypatch, papers):
    seen = []

    async def fake_get(client, params):
        seen.append(params["start"])
        class R: text = "<x/>"
        return R()

    async def fake_s2(client, keywords, since, until, limit=100):
        return {"papers": [], "status": "done", "query": "S2", "keywords_failed": 0}
    monkeypatch.setattr(http_client, "throttled_arxiv_get", fake_get)
    monkeypatch.setattr(http_client, "parse_arxiv_feed", lambda _: {0: papers}.get(seen[-1], []))
    monkeypatch.setattr(rps.s2_delta, "find_new_papers_since", fake_s2)
    return asyncio.run(rps.scan_profile(db, "p", None))


def _day(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _scan_ids(db):
    with sqlite3.connect(db) as con:
        return [r[0] for r in con.execute("SELECT scan_id FROM scan_runs ORDER BY started_at")]


PAPERS = [
    {"arxiv_id": "a1", "title": "target term study", "abstract": "", "published": _day(1)},
    {"arxiv_id": "a2", "title": "trend term study", "abstract": "", "published": _day(1)},
    {"arxiv_id": "a3", "title": "target term older", "abstract": "", "published": _day(5)},
    {"arxiv_id": "x1", "title": "nothing relevant", "abstract": "", "published": _day(1)},
    {"arxiv_id": "b1", "title": "target term but banned", "abstract": "", "published": _day(1)},
]


def test_스캔당_지표는_당시_스냅샷으로_재채점하고_분모를_가른다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 상위 K 와 적격 풀을 섞는 것, 제외된 논문을 적격에 넣는 것,
    최신일 계산이 상위 K 밖 논문을 보는 것, 지연을 상위 K 밖에서 재는 것."""
    db = tmp_path / "t.db"; _profile(db)
    _run(db, monkeypatch, PAPERS)
    m = ph.scan_metrics(db, _scan_ids(db)[0])
    assert m["observations"] == 5 and m["eligible"] == 3 and m["topk"] == 2 and m["k"] == 2
    assert m["anchor_basis"] == "frozen" and m["anchors"] == 2 and m["auto"] == [] and m["mode"] == "as_observed"
    # auto 가 없으면 anchor 적중은 정의상 전부 — 재채점이 스냅샷을 쓰는지 여기서 드러난다
    assert m["anchor_share_topk"] == 1.0 and m["anchor_share_eligible"] == 1.0
    # 상위 2 = a1(계층0·1일) + a3(계층0·5일) — 계층이 날짜를 이긴다(rank-tuple-v1). a2(계층1)는 3위.
    assert m["top_tier_share_topk"] == 1.0
    assert m["freshness_topk"] == 0.5 and m["mean_delay_days_topk"] == 3.0
    assert m["exclude_collision_auto"] is None and m["new_eligible_from_auto"] is None, "auto 없음 = 미측정"
    assert m["keyword_hits"] == {"target term": 3, "trend term": 1}, "제외된 논문도 적중 셈에는 들어간다"


def test_auto_키워드는_anchor_밖으로_갈리고_충돌과_이득을_센다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: anchor 를 현재 프로필 전부로 잡아 auto 를 못 가르는 것,
    제외어 충돌 분모가 auto 적중이 아닌 것, 이득 셈이 anchor 도 걸린 논문을 세는 것."""
    db = tmp_path / "t.db"; _profile(db)
    _run(db, monkeypatch, PAPERS)                     # 첫 스캔 = anchor 스냅샷
    rp.create_profile(db, "p", "이름", core_topics=["target term", "trend term", "nothing"],
                      core_weights={"target term": 1.0, "trend term": 0.6, "nothing": 0.6},
                      exclude=["banned"], max_items=2, s2_seeds=["target term"], origin="advisor")
    papers = PAPERS + [{"arxiv_id": "n1", "title": "nothing banned here", "abstract": "", "published": _day(1)}]
    _run(db, monkeypatch, papers)
    m = ph.scan_metrics(db, _scan_ids(db)[1])
    assert m["auto"] == ["nothing"], "advisor revision 의 키워드는 anchor 가 아니다"
    # 첫 스캔은 **당시 스냅샷**으로 센다 — 현재 프로필로 세면 'nothing' 이 거기도 나타난다
    first = ph.scan_metrics(db, _scan_ids(db)[0])
    assert first["auto"] == [] and set(first["keyword_hits"]) == {"target term", "trend term"}
    assert m["new_eligible_from_auto"] == 1, "x1 만 auto 덕에 적격(n1 은 제외됨, 나머지는 anchor 적중)"
    assert m["exclude_collision_auto"] == 0.5, "auto 적중 2편(x1, n1) 중 n1 이 제외어 충돌"
    assert m["anchor_share_eligible"] < 1.0


def test_직전_프로필_대비_상위K_유지는_prev_profile_로_재채점한다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: H7 을 현재 상위 K 끼리 비교해 항상 1.0 이 되는 것."""
    db = tmp_path / "t.db"; _profile(db)
    _run(db, monkeypatch, PAPERS)
    sid = _scan_ids(db)[0]
    # 얼린 행이 있으면 인자로 준 prev_profile 은 무시한다 — 얼린 값이 기준이다
    assert ph.scan_metrics(db, sid, prev_profile={"core_topics": ["trend term"], "core_weights": {},
                                                  "exclude": [], "target_domain": [], "max_items": 2})["topk_retention_vs_prev"] is None
    with sqlite3.connect(db) as con:                       # 얼리기 전 스캔을 흉내
        con.execute("DELETE FROM scan_health WHERE scan_id=?", (sid,))
    same = ph.scan_metrics(db, sid, prev_profile={"core_topics": ["target term", "trend term"],
                                                  "core_weights": {"target term": 1.0, "trend term": 0.6},
                                                  "exclude": ["banned"], "target_domain": [], "max_items": 2})
    assert same["topk_retention_vs_prev"] == 1.0
    # 직전 프로필이 trend 를 최상위로 뒀다면 상위 2 는 {a2, a1} → 현재 {a1, a3} 와 하나만 겹친다
    flipped = ph.scan_metrics(db, sid, prev_profile={"core_topics": ["trend term", "target term"],
                                                     "core_weights": {"trend term": 1.0, "target term": 0.6},
                                                     "exclude": ["banned"], "target_domain": [], "max_items": 2})
    assert flipped["topk_retention_vs_prev"] == 0.5
    assert ph.scan_metrics(db, sid)["topk_retention_vs_prev"] is None, "직전 프로필 없으면 미측정"
    assert ph.scan_metrics(db, sid)["mode"] == "partial", "적중은 저장됐지만 anchor/H7 는 재계산 — 한 모드가 아니다"


def _row(i, **over):
    """assess 용 합성 스캔 — i 일째. 모드는 as_observed."""
    base = {"started_at": (datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(days=i)).isoformat(),
            "mode": "as_observed", "anchor_share_topk": 1.0, "exclude_collision_auto": 0.0,
            "top_tier_share_topk": 1.0, "topk_retention_vs_prev": 1.0, "freshness_topk": 1.0,
            "new_eligible_from_auto": 2}
    return {**base, **over}


def test_판정은_미설정이면_하지_않고_기준선이_짧아도_하지_않는다():
    """이 테스트가 잡는 것: 규칙이 None 인데 판정을 내리는 것, 기준선 1스캔으로 악화를
    선언하는 것, 14회가 찼는데 14일이 안 지난 것을 기준선으로 받는 것, 걸린 사유를
    하나만 남기는 것, 평균으로 한 번 튄 값에 걸리는 것."""
    good = [_row(i) for i in range(14)]                      # 14회 · 13일 간격
    bad = [_row(20 + i, anchor_share_topk=0.2, exclude_collision_auto=0.6) for i in range(3)]
    assert ph.assess(good, bad)["status"] == ph.UNCONFIGURED
    rules = {"min_anchor_share_topk": 0.5, "max_exclude_collision_auto": 0.3}
    assert ph.assess(good[:5], bad, rules)["status"] == ph.INSUFFICIENT_BASELINE
    assert ph.assess([_row(0)] * 14, bad, rules)["status"] == ph.INSUFFICIENT_BASELINE, "14회여도 같은 날이면 기준선이 아니다"
    good = [_row(i) for i in range(15)]                      # 15회 · 14일
    assert ph.assess(good, [], rules)["status"] == ph.INSUFFICIENT_RECENT
    ok = ph.assess(good, [_row(20 + i) for i in range(3)], rules)
    assert ok["status"] == ph.OK and ok["reasons"] == [] and ok["baseline_days"] == 14
    det = ph.assess(good, bad, rules)
    assert det["status"] == ph.DETERIORATED and len(det["reasons"]) == 2, det["reasons"]
    # 최근 창은 중앙값 — 한 번 튄 값으로 악화를 선언하지 않는다(평균이면 충돌 0.33 > 0.3 로 걸린다)
    spike = _row(22, anchor_share_topk=0.0, exclude_collision_auto=1.0)
    assert ph.assess(good, [_row(20), _row(21), spike], rules)["status"] == ph.OK
    # 보조 규칙: 기준선 대비 하락 폭
    drop = ph.assess(good, [_row(20 + i, top_tier_share_topk=0.5) for i in range(3)], {"max_drop_from_baseline": 0.3})
    assert drop["status"] == ph.DETERIORATED and "top_tier_share_topk fell" in drop["reasons"][0]


def test_규칙이_요구하는_지표가_미측정이면_통과가_아니다():
    """이 테스트가 잡는 것: None 지표를 건너뛰고 OK 를 내는 것(fail-closed 위반),
    이득 지표를 판정에서 안 보는 것, 재현 모드가 섞인 창을 판정하는 것."""
    good = [_row(i) for i in range(15)]
    recent_none = [_row(20 + i, topk_retention_vs_prev=None) for i in range(3)]
    r = ph.assess(good, recent_none, {"min_topk_retention": 0.7})
    assert r["status"] == ph.UNMEASURED_REQUIRED and "topk_retention_vs_prev unmeasured" in r["reasons"][0]
    assert ph.assess(good, recent_none, {"min_anchor_share_topk": 0.5})["status"] == ph.OK, "요구하지 않는 지표의 미측정은 막지 않는다"
    # 이득: auto 가 아무것도 안 가져오면 악화
    zero = [_row(20 + i, new_eligible_from_auto=0) for i in range(3)]
    r = ph.assess(good, zero, {"min_new_eligible_from_auto": 1})
    assert r["status"] == ph.DETERIORATED and "new_eligible_from_auto=0.000 <" in r["reasons"][0]
    # 모드 섞임
    mixed = good[:-1] + [_row(14, mode="recomputed")]
    assert ph.assess(mixed, [_row(20 + i) for i in range(3)], {"min_anchor_share_topk": 0.5})["status"] == ph.MIXED_MODES
    # 최근 창도 표본이 있어야 한다 — 1회면 한 번 튄 값이 곧 중앙값이다
    assert ph.assess(good, [_row(20, anchor_share_topk=0.0)], {"min_anchor_share_topk": 0.5})["status"] == ph.INSUFFICIENT_RECENT
    # 보조 규칙(max_drop)도 못 쟀으면 통과가 아니다
    r = ph.assess(good, [_row(20 + i, freshness_topk=None) for i in range(3)], {"max_drop_from_baseline": 0.3})
    assert r["status"] == ph.UNMEASURED_REQUIRED and "freshness_topk unmeasured" in " ".join(r["reasons"])


def test_적중은_관측_시점_값을_쓰고_없을_때만_다시_센다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 저장된 적중을 두고 현재 코드로 다시 세는 것(가드가 나중에
    들어가면 과거 지표가 바뀐다), 옛 관측을 recomputed 로 표시하지 않는 것."""
    db = tmp_path / "t.db"; _profile(db)
    _run(db, monkeypatch, PAPERS)
    sid = _scan_ids(db)[0]
    assert ph.scan_metrics(db, sid)["mode"] == "as_observed"
    # 관측 뒤에 채점 규칙이 바뀌어도(여기서는 저장된 적중을 비워 흉내) 과거 지표는 그대로다
    with sqlite3.connect(db) as con:
        con.execute("UPDATE candidate_observations SET core_hits='[]' WHERE paper_key='a1'")
    m = ph.scan_metrics(db, sid)
    assert m["keyword_hits"]["target term"] == 2, "저장된 적중(a1 제외)을 그대로 읽어야 한다"
    with sqlite3.connect(db) as con:                       # 옛 관측: 적중 컬럼 없음
        con.execute("UPDATE candidate_observations SET core_hits=NULL, exclude_hits=NULL, domain_hits=NULL")
    m = ph.scan_metrics(db, sid)
    assert m["mode"] == "partial" and m["hits_mode"] == "recomputed" and m["keyword_hits"]["target term"] == 3
    assert "partial" in "\n".join(ph.format_health([m]))
    with sqlite3.connect(db) as con:
        con.execute("DELETE FROM scan_health")
    assert ph.scan_metrics(db, sid)["mode"] == "recomputed", "적중도 얼림도 없으면 recomputed"


def test_상위K_유지율은_운영_경로에서_직전_스냅샷이_다를_때_계산된다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: series() 가 prev_profile 을 안 넘겨 H7 이 늘 미측정인 것(처음
    구현의 결함), 프로필이 안 바뀌었는데 1.0 을 채우는 것, 직전이 아니라 아무 스냅샷을 잡는 것."""
    db = tmp_path / "t.db"; _profile(db)
    _run(db, monkeypatch, PAPERS)
    _run(db, monkeypatch, PAPERS)                                  # 같은 프로필 두 번
    rp.create_profile(db, "p", "이름", core_topics=["target term", "nothing"],
                      core_weights={"target term": 1.0, "nothing": 1.0},
                      exclude=["banned"], max_items=2, s2_seeds=["target term"])
    _run(db, monkeypatch, PAPERS)                                  # 바뀐 프로필
    now = datetime.now(timezone.utc)
    rows = ph.series(db, "p", now - timedelta(days=1), now + timedelta(days=1))
    assert [r["topk_retention_vs_prev"] for r in rows[:2]] == [None, None], "프로필이 안 바뀐 스캔은 미측정"
    assert rows[2]["topk_retention_vs_prev"] == 0.5, "옛 프로필 상위 {a1,a3} vs 새 {a1,x1}(같은 날·같은 계층은 키 순)"
    assert ph.previous_profile(db, "p", rows[2]["scan_id"])["core_weights"] == {"target term": 1.0, "trend term": 0.6}


def test_창_집계는_관측_없는_스캔을_0으로_채우지_않는다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 관측 0인 스캔이 지표 0.0 으로 들어와 중앙값을 끌어내리는 것."""
    db = tmp_path / "t.db"; _profile(db)
    _run(db, monkeypatch, PAPERS)
    _run(db, monkeypatch, [])                          # 관측 없는 스캔
    now = datetime.now(timezone.utc)
    rows = ph.series(db, "p", now - timedelta(days=1), now + timedelta(days=1))
    assert len(rows) == 1 and rows[0]["topk"] == 2
    text = "\n".join(ph.format_health(rows))
    assert "스캔 1회" in text and "미설정" in text and "auto 없음" in text
    assert "\n".join(ph.format_health([])).endswith("미측정")


def test_주간_리뷰에_건강_지표_절이_붙고_실패해도_리뷰는_나간다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 절이 안 붙는 것, 지표 예외가 리뷰 전체를 죽이는 것."""
    import storage, trend_report
    db = tmp_path / "t.db"; storage.init_storage(db); _profile(db)
    _run(db, monkeypatch, PAPERS)
    profile = rp.get_profile(db, "p")
    report = asyncio.run(trend_report.build(db, profile, None))
    assert "■ 프로필 건강 지표" in report and "anchor 2개" in report
    monkeypatch.setattr(ph, "series", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    report = asyncio.run(trend_report.build(db, profile, None))
    assert "■ 주간 동향 리뷰" in report and "건강 지표: 집계 실패" in report


def test_사람이_설정만_고쳐_저장해도_자동_키워드는_anchor_가_되지_않는다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: user revision 의 core 를 통째로 anchor 로 잡아 자동 키워드가
    소급해서 anchor 가 되는 것(외부 검토 시나리오), 얼린 anchor 가 뒤 이력에 따라 바뀌는 것."""
    db = tmp_path / "t.db"; _profile(db)
    _run(db, monkeypatch, PAPERS)                                           # 1일: 사람 A,B
    rp.create_profile(db, "p", "이름", core_topics=["target term", "trend term", "nothing"],
                      core_weights={"target term": 1.0, "trend term": 0.6, "nothing": 0.6},
                      exclude=["banned"], max_items=2, s2_seeds=["target term"], origin="advisor")
    _run(db, monkeypatch, PAPERS)                                           # 10일: X 자동 추가
    mid = _scan_ids(db)[1]
    assert ph.scan_metrics(db, mid)["auto"] == ["nothing"]
    rp.create_profile(db, "p", "이름", core_topics=["target term", "trend term", "nothing"],
                      core_weights={"target term": 1.0, "trend term": 0.6, "nothing": 0.6},
                      exclude=["banned"], max_items=3, s2_seeds=["target term"])   # 20일: 사람이 설정만 저장(origin=user)
    _run(db, monkeypatch, PAPERS)
    prov = rp.keyword_provenance(db, "p")
    assert prov["nothing"]["origin"] == "advisor", "처음 나타난 revision 의 origin 이 남는다"
    assert ph.scan_metrics(db, mid)["auto"] == ["nothing"], "과거 스캔의 auto 가 소급해서 바뀌면 안 된다"
    assert ph.scan_metrics(db, _scan_ids(db)[2])["auto"] == ["nothing"], "이후 스캔에서도 auto 다"
    # 얼리기 전 스캔이라도 그 시점 이력으로 도출하면 같은 답이다
    with sqlite3.connect(db) as con:
        con.execute("DELETE FROM scan_health")
    assert ph.scan_metrics(db, mid)["auto"] == ["nothing"]


def test_H7_반사실은_스캔_시점에_얼려_뒤의_채점_변경에_흔들리지_않는다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 얼린 retention 을 두고 지표 계산 시점의 채점 코드로 다시 세는 것,
    freeze 가 직전(다른) 프로필을 못 찾는 것."""
    db = tmp_path / "t.db"; _profile(db)
    _run(db, monkeypatch, PAPERS)
    rp.create_profile(db, "p", "이름", core_topics=["target term", "nothing"],
                      core_weights={"target term": 1.0, "nothing": 1.0},
                      exclude=["banned"], max_items=2, s2_seeds=["target term"])
    _run(db, monkeypatch, PAPERS)
    sid = _scan_ids(db)[1]
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        row = dict(con.execute("SELECT * FROM scan_health WHERE scan_id=?", (sid,)).fetchone())
    assert row["retention"] == 0.5 and row["prev_profile_sha"] and json.loads(row["prev_topk"]) == ["a1", "a3"]
    # 채점 코드가 뒤에 바뀌어도(여기서는 score_and_rank 를 빈 결과로 흉내) 얼린 값이 남는다
    monkeypatch.setattr(ph.profile_scoring, "score_and_rank", lambda *a, **k: {"papers": []})
    assert ph.scan_metrics(db, sid)["topk_retention_vs_prev"] == 0.5


def test_얼린_값은_재호출로도_바뀌지_않는다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: record_scan_health 가 INSERT OR REPLACE 라 두 번째 freeze 가
    처음 값을 조용히 덮는 것(외부 검토 2026-09-12)."""
    db = tmp_path / "t.db"; _profile(db)
    _run(db, monkeypatch, PAPERS)
    rp.create_profile(db, "p", "이름", core_topics=["target term", "nothing"],
                      core_weights={"target term": 1.0, "nothing": 1.0},
                      exclude=["banned"], max_items=2, s2_seeds=["target term"])
    _run(db, monkeypatch, PAPERS)
    sid = _scan_ids(db)[1]
    with sqlite3.connect(db) as con:
        first = con.execute("SELECT retention, prev_topk, anchor_terms FROM scan_health WHERE scan_id=?", (sid,)).fetchone()
    assert first[0] == 0.5
    # 채점 코드가 바뀐 뒤 같은 스캔을 다시 얼리려 해도 처음 값이 남는다
    monkeypatch.setattr(ph.profile_scoring, "score_and_rank", lambda *a, **k: {"papers": []})
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        obs = [dict(r) for r in con.execute("SELECT * FROM candidate_observations WHERE scan_id=?", (sid,))]
    for o in obs:
        o["arxiv_id"] = o["paper_key"]
    out = ph.freeze_scan(db, sid, "p", obs, rp.get_profile(db, "p"))
    assert out["written"] is False
    with sqlite3.connect(db) as con:
        again = con.execute("SELECT retention, prev_topk, anchor_terms FROM scan_health WHERE scan_id=?", (sid,)).fetchone()
    assert again == first


def test_얼리기_실패는_관측_저장_실패로_기록되지_않는다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: freeze_scan 이 record_observations 와 같은 try 안에 있어
    얼리기 예외가 scan_runs.observation_error 로 남는 것."""
    db = tmp_path / "t.db"; _profile(db)
    monkeypatch.setattr(ph, "freeze_scan", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    _run(db, monkeypatch, PAPERS)
    with sqlite3.connect(db) as con:
        n, err = con.execute("SELECT observations, observation_error FROM scan_runs").fetchone()
        frozen = con.execute("SELECT count(*) FROM scan_health").fetchone()[0]
    assert n == 5 and err is None, "관측은 저장됐고 실패로 기록되면 안 된다"
    assert frozen == 0
