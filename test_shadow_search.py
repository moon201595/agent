"""⑦단계(2026-09-12) — shadow 검색. PROGRESS §8-100. 검색은 가짜로 바꾸고 격리·계측·게이트를 본다.
각 테스트: 무엇을 망가뜨리면 실패하는가."""
import asyncio
import json
import sqlite3
from datetime import datetime, timezone

import find_new_papers
import profile_impact
import research_profile as rp
import s2_delta
import shadow_search as sh


def _profile(db, seeds):
    rp.create_profile(db, "p", "이름", core_topics=["target term", "world model"],
                      core_weights={"target term": 1.0, "world model": 1.0},
                      target_domain=["factory"], exclude=["banned"], max_items=2, s2_seeds=seeds)
    return rp.get_profile(db, "p")


def _paper(key, title, seeds=None):
    return {"arxiv_id": key, "title": title, "abstract": "", "published": "2026-09-10T00:00:00Z",
            "source": "s2", "s2_seeds": seeds or []}


# 씨앗별로 돌려줄 가짜 결과: 'world model' 씨앗은 잡음(random forest)과 진짜 하나를 같이 데려온다
FAKE = {
    "target term": [_paper("t1", "target term study"), _paper("t2", "target term again")],
    "world model": [_paper("w1", "target term robot world model for manipulation"),   # 적격·적중 2개 → 상위
                    _paper("n1", "random forest risk stratification"), _paper("n2", "world bank cohort"),
                    _paper("b1", "world model banned")],
}


def _fake(monkeypatch, calls):
    async def fake_s2(client, keywords, since, until, limit=100, budget_s=300.0):
        calls.append(("s2", tuple(keywords)))
        return {"papers": [dict(p) for k in keywords for p in FAKE.get(k, [])], "status": "done"}

    async def fake_arxiv(client, query, since, **kw):
        calls.append(("arxiv", query))
        return {"papers": [], "status": "done", "until": datetime.now(timezone.utc).isoformat(), "query": query}
    monkeypatch.setattr(s2_delta, "find_new_papers_since", fake_s2)
    monkeypatch.setattr(find_new_papers, "find_new_papers_since", fake_arxiv)


def _tables(db):
    with sqlite3.connect(db) as con:
        return {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                for t in ("search_runs", "search_candidates", "profile_shown", "scan_runs", "profile_revisions")}


def test_공유_씨앗은_한_번만_검색하고_운영_기록은_건드리지_않는다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 두 팔이 같은 씨앗을 두 번 부르는 것, 바뀌지 않은 arXiv 질의를 두 번 부르거나
    아예 안 부르는 것(손실이 합집합 기준이어야 한다), search_runs·search_candidates·profile_shown·
    revision 에 무언가 쓰는 것."""
    db = tmp_path / "t.db"; before = _profile(db, ["target term", "world model"])
    after = {**before, "s2_seeds": ["target term"]}
    calls = []; _fake(monkeypatch, calls)
    snap = _tables(db)
    out = asyncio.run(sh.run_shadow(db, "p", before, after, None))
    s2_calls = sorted(c for c in calls if c[0] == "s2")
    assert s2_calls == [("s2", ("target term",)), ("s2", ("world model",))], "씨앗마다 한 번"
    assert sum(1 for c in calls if c[0] == "arxiv") == 1, "안 바뀐 arXiv 질의는 한 번만 — 두 팔이 공유(합집합 기준 손실)"
    assert _tables(db) == snap, "격리 — 운영 표에 아무것도 안 쓴다"
    assert out["status"] == "done" and out["arms"]["seeds_removed"] == ["world model"]
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM shadow_runs").fetchone()[0] == 1


def test_지표는_잃은_적격과_줄어든_잡음을_가른다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 잃은 적격 논문을 안 세는 것, 잡음을 적격과 섞어 세는 것, 상위 K 겹침을
    안 재는 것, 씨앗별 수율을 안 남기는 것."""
    db = tmp_path / "t.db"; before = _profile(db, ["target term", "world model"])
    after = {**before, "s2_seeds": ["target term"]}
    _fake(monkeypatch, [])
    out = asyncio.run(sh.run_shadow(db, "p", before, after, None, store=False))
    d = out["metrics"]["diff"]
    assert d["eligible_before"] == 3 and d["eligible_after"] == 2 and d["eligible_lost"] == ["w1"]
    assert d["noise_before"] == 3 and d["noise_after"] == 0, "random forest·world bank·banned 가 사라진다"
    assert d["unique_to_baseline"] == 4 and d["unique_to_candidate"] == 0
    assert d["topk_overlap"] == 0.5 and d["topk_left"] == ["w1"]
    ps = out["metrics"]["baseline"]["per_seed"]["world model"]
    assert ps == {"returned": 4, "eligible": 1, "noise": 3}
    assert "world model" not in out["metrics"]["candidate"]["per_seed"]
    text = "\n".join(sh.format_shadow(out))
    assert "제거 ['world model']" in text and "잃은 적격 논문: w1" in text
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM sqlite_master WHERE name='shadow_runs'").fetchone()[0] == 0, "dry-run 은 표도 안 만든다"


def test_arXiv_가_어차피_잡는_논문은_씨앗_제거의_손실이_아니다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: S2 팔끼리만 비교해 arXiv 경로로도 오는 논문을 '잃었다'고 세는 것."""
    db = tmp_path / "t.db"; before = _profile(db, ["target term", "world model"])
    after = {**before, "s2_seeds": ["target term"]}
    calls = []; _fake(monkeypatch, calls)
    async def arxiv_has_w1(client, query, since, **kw):
        return {"papers": [dict(FAKE["world model"][0], source="arxiv")], "status": "done",
                "until": datetime.now(timezone.utc).isoformat(), "query": query}
    monkeypatch.setattr(find_new_papers, "find_new_papers_since", arxiv_has_w1)
    out = asyncio.run(sh.run_shadow(db, "p", before, after, None, store=False))
    d = out["metrics"]["diff"]
    assert d["eligible_lost"] == [] and d["eligible_before"] == d["eligible_after"] == 3, "w1 은 arXiv 로도 온다"
    assert d["noise_before"] == 3 and d["noise_after"] == 0


def test_검색_변경이_없으면_건너뛰고_실패는_실패로_남는다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 변경 없는 실험에 API 를 쓰는 것, 검색 예외를 done 으로 남기는 것."""
    db = tmp_path / "t.db"; before = _profile(db, ["target term"])
    calls = []; _fake(monkeypatch, calls)
    out = asyncio.run(sh.run_shadow(db, "p", before, dict(before), None))
    assert out["status"] == "skipped" and calls == []

    async def boom(*a, **k): raise RuntimeError("s2 down")
    monkeypatch.setattr(s2_delta, "find_new_papers_since", boom)
    out = asyncio.run(sh.run_shadow(db, "p", before, {**before, "s2_seeds": []}, None))
    assert out["status"] == "failed" and "s2 down" in out["error"]
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT status FROM shadow_runs").fetchone()[0] == "failed"


def test_게이트는_shadow_가_있어야_보류를_넘고_규칙_미설정이면_증거부족이다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: shadow 없이 needs_shadow 를 넘는 것, shadow 규칙 None 인데 eligible 을 내는 것,
    한도를 넘었는데 held 가 아닌 것, 다른 변경안의 shadow 를 붙이는 것(해시 불일치)."""
    db = tmp_path / "t.db"; before = _profile(db, ["target term", "world model"])
    after = {**before, "s2_seeds": ["target term"]}
    _fake(monkeypatch, [])
    # 관측이 있어야 분석이 선다 — 스냅샷을 만들려면 스캔 기록이 필요하므로 최소 한 번 관측을 넣는다
    sid = rp.begin_scan(db, "p", before)
    rp.record_observations(db, sid, "p", [{"arxiv_id": "t1", "title": "target term study", "abstract": "", "published": "2026-09-10T00:00:00Z",
                                            "outcome": "content", "rank_pos": 1, "_hits": {"core_hits": ["target term"], "exclude_hits": [], "domain_hits": []}}],
                           core_signature="c", seed_signature="s")
    rp.finish_scan(db, sid, observations=1)
    snap = profile_impact.snapshot(db, "p", datetime(2020, 1, 1, tzinfo=timezone.utc), datetime(2030, 1, 1, tzinfo=timezone.utc))
    an = profile_impact.analyze_and_store(db, snap, before, after, 2)
    assert an["gate_status"] == profile_impact.NEEDS_SHADOW
    shadow = asyncio.run(sh.run_shadow(db, "p", before, after, None, analysis_id=an["analysis_id"]))
    r = profile_impact.regate_with_shadow(db, an["analysis_id"], shadow)
    assert r["gate_status"] == profile_impact.INSUFFICIENT and any("shadow_rules_unconfigured" in x for x in r["reasons"])
    rules = {"max_core_changes": 3, "max_topk_left_ratio": 1.0, "max_exclude_risk": 1.0}
    srules = {"max_eligible_lost": 0, "min_topk_overlap": 0.9, "max_noise_after": 5}
    r = profile_impact.regate_with_shadow(db, an["analysis_id"], shadow, rules, srules)
    assert r["gate_status"] == profile_impact.HELD and any("shadow_eligible_lost:1>0" in x for x in r["reasons"])
    r = profile_impact.regate_with_shadow(db, an["analysis_id"], shadow, rules, {**srules, "max_eligible_lost": 1, "min_topk_overlap": 0.5})
    assert r["gate_status"] == profile_impact.ELIGIBLE and r["reasons"][0].startswith("shadow:")
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT gate_status FROM impact_analyses WHERE analysis_id=?", (an["analysis_id"],)).fetchone()[0] == profile_impact.ELIGIBLE
    other = {**shadow, "after_hash": "x"}
    assert profile_impact.regate_with_shadow(db, an["analysis_id"], other, rules, srules) == {"updated": False, "reason": "hash_mismatch"}
