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


# 시드별로 돌려줄 가짜 결과: 'world model' 시드는 잡음(random forest)과 진짜 하나를 같이 데려온다
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


def test_공유_시드는_한_번만_검색하고_운영_기록은_건드리지_않는다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 두 팔이 같은 시드를 두 번 부르는 것, 바뀌지 않은 arXiv 질의를 두 번 부르거나
    아예 안 부르는 것(손실이 합집합 기준이어야 한다), search_runs·search_candidates·profile_shown·
    revision 에 무언가 쓰는 것."""
    db = tmp_path / "t.db"; before = _profile(db, ["target term", "world model"])
    after = {**before, "s2_seeds": ["target term"]}
    calls = []; _fake(monkeypatch, calls)
    snap = _tables(db)
    out = asyncio.run(sh.run_shadow(db, "p", before, after, None))
    s2_calls = sorted(c for c in calls if c[0] == "s2")
    assert s2_calls == [("s2", ("target term",)), ("s2", ("world model",))], "시드마다 한 번"
    assert sum(1 for c in calls if c[0] == "arxiv") == 1, "안 바뀐 arXiv 질의는 한 번만 — 두 팔이 공유(합집합 기준 손실)"
    assert _tables(db) == snap, "격리 — 운영 표에 아무것도 안 쓴다"
    assert out["status"] == "done" and out["arms"]["seeds_removed"] == ["world model"]
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM shadow_runs").fetchone()[0] == 1


def test_지표는_잃은_적격과_줄어든_잡음을_가른다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 잃은 적격 논문을 안 세는 것, 잡음을 적격과 섞어 세는 것, 상위 K 겹침을
    안 재는 것, 시드별 수율을 안 남기는 것."""
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


def test_arXiv_가_어차피_잡는_논문은_시드_제거의_손실이_아니다(tmp_path, monkeypatch):
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


def _analysis(db, before, after, extra_obs=None):
    """관측 하나로 분석을 세운다(스냅샷엔 스캔 기록이 필요하다)."""
    sid = rp.begin_scan(db, "p", before)
    rows = [{"arxiv_id": "t1", "title": "target term study", "abstract": "", "published": "2026-09-10T00:00:00Z",
             "outcome": "content", "rank_pos": 1, "_hits": {"core_hits": ["target term"], "exclude_hits": [], "domain_hits": []}}]
    rows += extra_obs or []
    rp.record_observations(db, sid, "p", rows, core_signature="c", seed_signature="s")
    rp.finish_scan(db, sid, observations=len(rows))
    snap = profile_impact.snapshot(db, "p", datetime(2020, 1, 1, tzinfo=timezone.utc), datetime(2030, 1, 1, tzinfo=timezone.utc))
    return profile_impact.analyze_and_store(db, snap, before, after, 2)


def test_예산은_arXiv_에도_강제되고_유지율_분모는_baseline_상위_집합이다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: arXiv 호출이 예산을 넘겨도 기다리는 것, partial 원인을 안 남기는 것,
    적격이 K 보다 적을 때 K 로 나눠 같은 집합인데 1.0 이 안 되는 것."""
    db = tmp_path / "t.db"; before = _profile(db, ["target term", "world model"])
    after = {**before, "s2_seeds": ["target term"]}
    _fake(monkeypatch, [])

    async def slow_arxiv(client, query, since, **kw):
        await asyncio.sleep(5)
        return {"papers": [], "status": "done", "until": "", "query": query}
    monkeypatch.setattr(find_new_papers, "find_new_papers_since", slow_arxiv)
    import time as _t
    t0 = _t.monotonic()
    out = asyncio.run(sh.run_shadow(db, "p", before, after, None, budget_s=0.5))
    assert _t.monotonic() - t0 < 4, "예산 0.5초를 넘겨 5초를 기다리면 안 된다"
    assert out["status"] == "partial" and out["partial_reason"] == "arxiv_budget"
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT partial_reason, policy_version FROM shadow_runs").fetchone() == ("arxiv_budget", rp.RANK_POLICY_VERSION)
    # 유지율 분모
    assert sh.compare({"keys": ["a"], "eligible_keys": ["a"], "topk": ["a"], "excluded": 0, "no_core": 0},
                      {"keys": ["a"], "eligible_keys": ["a"], "topk": ["a"], "excluded": 0, "no_core": 0}, 6)["topk_overlap"] == 1.0
    assert sh.compare({"keys": [], "eligible_keys": [], "topk": [], "excluded": 0, "no_core": 0},
                      {"keys": [], "eligible_keys": [], "topk": [], "excluded": 0, "no_core": 0}, 6)["topk_overlap"] is None
