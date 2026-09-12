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
    assert "first_scan_snapshot" in m["anchor_basis"] and m["anchors"] == 2 and m["auto"] == []
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


def test_판정은_미설정이면_하지_않고_기준선이_짧아도_하지_않는다():
    """이 테스트가 잡는 것: 규칙이 None 인데 판정을 내리는 것, 기준선 1스캔으로 악화를
    선언하는 것, 걸린 사유를 하나만 남기는 것."""
    good = {"anchor_share_topk": 1.0, "exclude_collision_auto": 0.0, "top_tier_share_topk": 1.0,
            "topk_retention_vs_prev": 1.0, "freshness_topk": 1.0}
    bad = {**good, "anchor_share_topk": 0.2, "exclude_collision_auto": 0.6}
    assert ph.assess([good] * 20, [bad] * 3)["status"] == ph.UNCONFIGURED
    rules = {"min_anchor_share_topk": 0.5, "max_exclude_collision_auto": 0.3}
    assert ph.assess([good] * 5, [bad] * 3, rules)["status"] == ph.INSUFFICIENT_BASELINE
    assert ph.assess([good] * 14, [], rules)["status"] == ph.INSUFFICIENT_RECENT
    ok = ph.assess([good] * 14, [good] * 3, rules)
    assert ok["status"] == ph.OK and ok["reasons"] == []
    det = ph.assess([good] * 14, [bad] * 3, rules)
    assert det["status"] == ph.DETERIORATED and len(det["reasons"]) == 2, det["reasons"]
    # 최근 창은 중앙값 — 한 번 튄 값으로 악화를 선언하지 않는다(평균이면 충돌 0.33 > 0.3 로 걸린다)
    spike = {**good, "anchor_share_topk": 0.0, "exclude_collision_auto": 1.0}
    assert ph.assess([good] * 14, [good, good, spike], rules)["status"] == ph.OK
    # 보조 규칙: 기준선 대비 하락 폭
    drop = ph.assess([good] * 14, [{**good, "top_tier_share_topk": 0.5}] * 3, {"max_drop_from_baseline": 0.3})
    assert drop["status"] == ph.DETERIORATED and "top_tier_share_topk fell" in drop["reasons"][0]


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
