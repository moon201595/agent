"""②단계(2026-09-12) — 탐색 차선. PROGRESS §8-97.

키워드에 안 걸려 탈락한 논문(no_core_hit)에서 로컬로 후보 용어를 찾고, 용어마다 증거
논문만 제안기에 보낸다. 각 테스트의 docstring 에 "무엇을 망가뜨리면 실패하는가"를 적었다.
"""
import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import http_client
import profile_advisor as adv
import research_profile as rp
import rule_advisor
import run_profile_scan as rps
import term_discovery as td


def _profile(db):
    rp.create_profile(db, "p", "이름", core_topics=["target term"], core_weights={"target term": 1.0},
                      target_domain=["factory"], exclude=["banned"], max_items=2, s2_seeds=["target term"])


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


def _win():
    now = datetime.now(timezone.utc)
    return now - timedelta(days=1), now + timedelta(days=1)


# 탈락 논문 4편이 "spiking sensor" 를 공유하고, 1편은 factory(도메인)에도 걸린다.
DROPPED = [{"arxiv_id": f"d{i}", "title": f"spiking sensor study {i}",
            "abstract": "We build a spiking sensor for edge devices." + (" In a factory." if i == 1 else ""),
            "published": _day(1)} for i in range(4)]
HIT = [{"arxiv_id": "h1", "title": "target term with spiking sensor", "abstract": "spiking sensor everywhere", "published": _day(1)}]
BANNED = [{"arxiv_id": "b1", "title": "spiking sensor banned", "abstract": "", "published": _day(1)}]


def test_탐색_풀은_탈락_논문만이고_적중과_제외는_안_들어온다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 적중 논문·제외 논문이 탐색 풀에 섞이는 것, 같은 논문이 스캔마다
    두 번 세어지는 것, 도메인 적중이 안 붙는 것."""
    db = tmp_path / "t.db"; _profile(db)
    _run(db, monkeypatch, DROPPED + HIT + BANNED)
    _run(db, monkeypatch, DROPPED)
    pool = td.exploration_pool(db, "p", *_win())
    assert {p["_paper_key"] for p in pool} == {"d0", "d1", "d2", "d3"}
    assert [p["domain_hits"] for p in pool if p["_paper_key"] == "d1"] == [["factory"]]
    assert all(p["abstract"] for p in pool), "초록은 abstract_ref 를 따라 복원된다"


def test_발견은_아는_말과_우산_용어를_빼고_증거는_풀_안의_논문만_준다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 현재 키워드·도메인의 변형을 후보로 내는 것, 'large language models'
    같은 우산 용어를 내는 것, min_papers 미만을 내는 것, 증거 키가 풀 밖인 것."""
    db = tmp_path / "t.db"; _profile(db)
    papers = DROPPED + [{"arxiv_id": "g1", "title": "large language models everywhere",
                         "abstract": "large language models, large language models.", "published": _day(1)},
                        {"arxiv_id": "g2", "title": "large language models again", "abstract": "", "published": _day(1)},
                        {"arxiv_id": "g3", "title": "large language models thrice", "abstract": "", "published": _day(1)},
                        {"arxiv_id": "k1", "title": "smart factory floor", "abstract": "", "published": _day(1)},
                        {"arxiv_id": "k2", "title": "smart factory floor", "abstract": "", "published": _day(1)},
                        {"arxiv_id": "k3", "title": "smart factory floor", "abstract": "", "published": _day(1)}]
    _run(db, monkeypatch, papers)
    pool = td.exploration_pool(db, "p", *_win())
    profile = rp.get_profile(db, "p")
    terms = td.discover(pool, profile, {"top_terms": 10})
    names = [t["term"] for t in terms]
    assert "spiking sensor" in names
    assert not any("language models" in n for n in names), "우산 용어는 후보가 아니다"
    assert not any("factory" in n for n in names), "도메인 어휘의 변형은 이미 아는 말이다"
    spk = next(t for t in terms if t["term"] == "spiking sensor")
    assert spk["support"] == 4 and spk["domain_papers"] == 1
    assert [e["key"] for e in spk["evidence"]] == ["d1", "d0"], "증거는 도메인 적중 → 키 순, 기본 2편"
    assert td.discover(pool, profile, {"min_papers": 5}) == [], "min_papers 미만은 우연이다"


def test_제안기_입력에는_용어와_증거만_나가고_편수는_안_나간다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: support·도메인 편수가 프롬프트에 새는 것(규칙 4·R5), 증거 키가
    sent_paper_keys 에 없어 R3 검증에서 죽는 것, 프롬프트 v2 에 탐색 절이 안 붙는 것."""
    db = tmp_path / "t.db"; _profile(db)
    _run(db, monkeypatch, DROPPED + HIT)
    profile = rp.get_profile(db, "p")
    import profile_impact
    snap = profile_impact.snapshot(db, "p", *_win())
    terms = td.discover(td.exploration_pool(db, "p", *_win()), profile)
    sent = adv.build_input(snap, profile, adv.select_papers(snap, profile, limit=adv.DELIVERY_PAPERS), terms)
    spk = next(t for t in sent["exploration"] if t["term"] == "spiking sensor")   # 'edge devices' 와 동률 → 용어 순
    assert set(spk) == {"term", "papers"}
    assert all(set(p) == adv.SENT_FIELDS for p in spk["papers"])
    assert {"d1", "d0"} <= set(sent["sent_paper_keys"]) and "h1" in sent["sent_paper_keys"]
    prompt = adv.render_prompt(sent)
    assert "## 탐색 후보 용어" in prompt and "- 용어: spiking sensor" in prompt and "[d1]" in prompt
    assert "support" not in prompt and "4편" not in prompt
    assert adv.PROMPT_VERSION == "profile_advisor_v2"
    # 증거 키로 낸 제안은 R3 를 통과한다
    proposals = [{"action": "add_core_term", "term": "spiking sensor", "proposed_tier": 1.0,
                  "evidence_paper_keys": ["d1", "d0"], "reason": "r", "ambiguity_risks": []}]
    validated = adv.validate_proposals({"decision": "propose", "proposals": proposals}, sent, profile)
    assert validated and not validated[0].get("errors"), validated


def test_규칙_제안기도_탐색_용어를_같은_입력에서_본다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: R 이 exploration 을 무시해 F/R/A 비교에서 A 만 탈락 후보를 보는 것,
    증거가 min_support 미만인 탐색 용어를 내는 것, max_proposals 를 넘기는 것."""
    db = tmp_path / "t.db"; _profile(db)
    _run(db, monkeypatch, DROPPED + HIT)
    profile = rp.get_profile(db, "p")
    import profile_impact
    snap = profile_impact.snapshot(db, "p", *_win())
    terms = td.discover(td.exploration_pool(db, "p", *_win()), profile)
    sent = adv.build_input(snap, profile, adv.select_papers(snap, profile, limit=adv.DELIVERY_PAPERS), terms)
    out = rule_advisor.propose(sent, profile)
    mine = [p for p in out["proposals"] if p["term"] == "spiking sensor"]
    assert mine and mine[0]["evidence_paper_keys"] == ["d1", "d0"] and mine[0]["reason"].startswith("rule:exploration")
    thin = {**sent, "exploration": [{"term": "lonely term", "papers": [{"key": "d0", "title": "", "abstract": ""}]}]}
    assert not any(p["term"] == "lonely term" for p in rule_advisor.propose(thin, profile)["proposals"])
    many = {**sent, "exploration": [{"term": f"t{i} x", "papers": [{"key": "d0", "title": "", "abstract": ""},
                                                                   {"key": "d1", "title": "", "abstract": ""}]} for i in range(6)]}
    assert len(rule_advisor.propose(many, profile)["proposals"]) <= rule_advisor.DEFAULT_RULES["max_proposals"]


def test_주간_리뷰에_탐색_절이_붙고_관측_없는_기간은_미측정이다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 절이 안 붙는 것, 관측 없는 기간을 '없음(0)'으로 쓰는 것."""
    import storage, trend_report
    db = tmp_path / "t.db"; storage.init_storage(db); _profile(db)
    profile = rp.get_profile(db, "p")
    report = asyncio.run(trend_report.build(db, profile, None))
    assert "반복어: 관측 이력 없음 — 미측정" in report
    _run(db, monkeypatch, DROPPED + HIT)
    report = asyncio.run(trend_report.build(db, profile, None))
    assert "탐색 풀 4편" in report and "spiking sensor: 4편" in report


def test_탐색이_실패해도_주간_제안은_대표_논문만으로_간다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 탐색 예외가 run_weekly 를 죽이는 것."""
    db = tmp_path / "t.db"; _profile(db)
    _run(db, monkeypatch, DROPPED + HIT)
    monkeypatch.setattr(td, "exploration_pool", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = asyncio.run(adv.run_weekly(db, "p", None, *_win()))
    assert out["status"] in ("budget_unknown", "no_client", "proposed", "no_change", "skipped"), out
    with sqlite3.connect(db) as con:
        row = con.execute("SELECT sent_input_json FROM advisor_runs ORDER BY rowid DESC LIMIT 1").fetchone()
    assert row is None or row[0] is None or json.loads(row[0]).get("exploration") in (None, [])
