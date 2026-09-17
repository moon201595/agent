"""②단계(2026-09-12) — 탐색 차선. PROGRESS §8-97.

키워드에 안 걸려 탈락한 논문(no_core_hit)에서 로컬로 후보 용어를 찾고, 용어마다 증거
논문만 제안기에 보낸다. 각 테스트의 docstring 에 "무엇을 망가뜨리면 실패하는가"를 적었다.
"""
import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import http_client
import research_profile as rp
import run_profile_scan as rps
import scan_search          # 2026-09-17 분할: 검색 소스 모듈은 여기서 patch 한다
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
    monkeypatch.setattr(scan_search.s2_delta, "find_new_papers_since", fake_s2)
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


def test_주간_리뷰에_탐색_절이_붙고_관측_없는_기간은_미측정이다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 절이 안 붙는 것, 관측 없는 기간을 '없음(0)'으로 쓰는 것."""
    import storage, trend_report
    db = tmp_path / "t.db"; storage.init_storage(db); _profile(db)
    profile = rp.get_profile(db, "p")
    report = asyncio.run(trend_report.build(db, profile, None))
    assert "반복어: 관측 이력 없음 — 미측정" in report
    _run(db, monkeypatch, DROPPED + HIT)
    report = asyncio.run(trend_report.build(db, profile, None))
    assert "탐색 풀 4편" in report and "| spiking sensor | 4 |" in report


def test_최신_관측이_적중이면_탐색_풀에서_빠지고_반대는_들어온다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: no_core_hit 로 먼저 자르고 최신을 골라 '이제는 잡히는 논문'이
    옛 탈락 행으로 살아남는 것(외부 검토 2026-09-12). 반대 방향(적중 → 탈락)은 들어와야 한다."""
    db = tmp_path / "t.db"; _profile(db)
    a = {"arxiv_id": "a", "title": "spiking sensor alpha", "abstract": "", "published": _day(1)}
    b = {"arxiv_id": "b", "title": "target term beta", "abstract": "", "published": _day(1)}
    _run(db, monkeypatch, [a, b])                              # a 탈락, b 적중
    rp.create_profile(db, "p", "이름", core_topics=["spiking sensor"], core_weights={"spiking sensor": 1.0},
                      target_domain=["factory"], exclude=["banned"], max_items=2, s2_seeds=["x"])
    _run(db, monkeypatch, [a, b])                              # 키워드 교체: a 적중, b 탈락
    keys = {p["_paper_key"] for p in td.exploration_pool(db, "p", *_win())}
    assert keys == {"b"}, "a 는 이제 잡히므로 탐색 대상이 아니고, b 는 이제 안 잡히므로 대상이다"


def test_차선_선발은_support_독식을_막고_표기_변형은_후보에서_뺀다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 도메인·시드가 동률 깨기에 그쳐 범용 용어가 상위를 독식하는 것,
    기존 키워드의 하이픈·복수형 변형이 후보로 나가는 것, 스니펫이 앞 N 자라 용어가 잘리는 것."""
    db = tmp_path / "t.db"
    rp.create_profile(db, "p", "이름", core_topics=["vision-language model", "safety policy"],
                      core_weights={"vision-language model": 1.0, "safety policy": 1.0},
                      target_domain=["factory"], exclude=["banned"], max_items=2, s2_seeds=["x"])
    long_tail = "filler words here. " * 40 + "we use a spiking sensor at the end."   # 용어가 700자 뒤
    papers = ([{"arxiv_id": f"g{i}", "title": f"generic thing {i}", "abstract": "generic thing everywhere", "published": _day(1)} for i in range(6)]
              + [{"arxiv_id": f"h{i}", "title": f"other stuff {i}", "abstract": "other stuff abounds", "published": _day(1)} for i in range(6)]
              + [{"arxiv_id": f"j{i}", "title": f"plain noise {i}", "abstract": "plain noise persists", "published": _day(1)} for i in range(6)]
              + [{"arxiv_id": f"d{i}", "title": f"factory item {i}", "abstract": "spiking sensor in a factory line", "published": _day(1)} for i in range(4)]
              + [{"arxiv_id": f"v{i}", "title": f"vision language models {i}", "abstract": "", "published": _day(1)} for i in range(3)]
              + [{"arxiv_id": f"s{i}", "title": f"safety policies {i}", "abstract": "", "published": _day(1)} for i in range(3)]
              + [{"arxiv_id": "t1", "title": "tail paper", "abstract": long_tail, "published": _day(1)}])
    _run(db, monkeypatch, papers)
    profile = rp.get_profile(db, "p")
    pool = td.exploration_pool(db, "p", *_win())
    terms = td.discover(pool, profile)
    by = {t["term"]: t for t in terms}
    generic = [t for t in terms if t["term"].startswith("generic thing")]   # 3-gram 이 2-gram 을 흡수한다
    assert generic and generic[0]["lane"] == "support" and generic[0]["support"] == 6
    # support 만 보면 generic(6)·stuff(6)·plain(6) 이 세 자리를 다 가져가 spiking(5)은 못 들어온다
    assert "spiking sensor" in by and by["spiking sensor"]["lane"] == "domain", "도메인 축 자리는 도메인 비율 1등에게"
    # match-v2 뒤로 "vision language models" 는 키워드에 **잡힌다** — 탐색 풀에 없어야 한다
    assert not any(p["_paper_key"].startswith("v") for p in pool), "매처가 잡는 표기는 탈락 풀에 없다"
    # 매처가 아직 못 잡는 변형(-ies 복수)은 후보가 아니라 '표기 변형' 보고로 간다
    assert "safety policies" not in by
    var = td.known_variants(pool, profile)
    assert var and var[0]["term"] == "safety policies" and var[0]["known"] == "safety policy"
    # 스니펫: 용어가 초록 뒤쪽에 있어도 전송 조각 안에 있다
    tail = td.snippet(long_tail, "spiking sensor", 200)
    assert "spiking sensor" in tail and len(tail) <= 204 and tail.startswith("…")
    ev_texts = [e["abstract"] for e in by["spiking sensor"]["evidence"]]
    assert all("spiking sensor" in e for e in ev_texts)


def test_시드_축은_독립_시드_둘_이상이어야_하고_단일_시드_반복은_잡음_진단으로_간다():
    """이 테스트가 잡는 것: '시드로 들어왔는가' 비율로 한 시드의 관련도 잡음이 후보가 되는 것
    (실측 foreign language), 단일 시드 반복을 어디에도 보고하지 않는 것."""
    profile = {"core_topics": ["core term"], "core_weights": {"core term": 1.0}, "target_domain": [], "exclude": []}
    def paper(key, text, seeds):
        return {"_paper_key": key, "title": text, "abstract": "", "published": "2026-09-10", "day": 1,
                "domain_hits": [], "s2_seeds": seeds, "retrieval_sources": ["s2"]}
    pool = ([paper(f"n{i}", "foreign language teachers", ["seedA"]) for i in range(4)]        # 한 시드, 4편
            + [paper(f"b{i}", "spiking sensor arrays", ["seedA" if i % 2 else "seedB"]) for i in range(3)]  # 두 시드, 3편
            + [paper(f"g{i}", "generic thing everywhere", []) for i in range(5)])
    terms = td.discover(pool, profile)
    by = {t["term"]: t for t in terms}
    assert by["spiking sensor arrays"]["lane"] == "seed" and by["spiking sensor arrays"]["seed_breadth"] == 2
    assert "foreign language teachers" not in by or by["foreign language teachers"]["lane"] != "seed", "단일 시드는 시드 축 후보가 아니다"
    noise = td.single_seed_noise(pool, profile, top=20)
    assert noise and all(x["seed"] == "seedA" for x in noise) and "foreign language teachers" in [x["term"] for x in noise]
    assert not any(x["term"].startswith("spiking") for x in noise), "두 시드에서 반복된 말은 잡음 진단이 아니다"
    # 시드 연관 용어가 단일 시드뿐이면 시드 축은 비고 support 로 채운다 — 단일 시드를 '시드 축'으로 올리지 않는다
    only_single = ([paper(f"n{i}", "foreign language teachers", ["seedA"]) for i in range(4)]
                   + [paper(f"g{i}", "generic thing everywhere", []) for i in range(5)]
                   + [paper(f"h{i}", "other stuff abounds", []) for i in range(4)])
    picked = td.discover(only_single, profile)
    assert {t["lane"] for t in picked} == {"support"}, "단일 시드 용어는 시드 축 라벨을 달지 못한다"
    text = "\n".join(td.format_discovery(terms, len(pool), None, noise))
    assert "검색 잡음 진단" in text and "| foreign language teachers | 4 | seedA |" in text
    # 축에 후보가 없으면 자리를 버리지 않고 support 로 채우고, 라벨은 실제 축이다
    no_seed = [paper(f"g{i}", "generic thing everywhere", []) for i in range(5)] + [paper(f"h{i}", "other stuff abounds", []) for i in range(4)]
    picked = td.discover(no_seed, profile)
    assert len(picked) == 2 and {t["lane"] for t in picked} == {"support"}


def test_단일_시드_대표값은_정렬된_순서를_쓴다(monkeypatch):
    """2026-09-14 외부 검토. 이 테스트가 잡는 것: 대표 시드를 set 순회에 맡겨 실행마다
    다른 값을 보고하는 것. 운영 함수가 대표값 선택 헬퍼를 실제로 호출하는지도 확인한다."""
    class HashOrder(set):
        def __iter__(self):
            return iter(("zeta", "alpha"))
    assert td._representative_seed(HashOrder(("zeta", "alpha"))) == "alpha"
    profile = {"core_topics": ["core term"], "core_weights": {"core term": 1.0},
               "target_domain": [], "exclude": []}
    pool = [{"_paper_key": f"p{i}", "title": "foreign language teachers", "abstract": "",
             "published": "2026-09-10", "day": 1, "domain_hits": [], "s2_seeds": {"seed"}}
            for i in range(4)]
    monkeypatch.setattr(td, "_representative_seed", lambda _seeds: "sorted-representative")
    assert td.single_seed_noise(pool, profile)[0]["seed"] == "sorted-representative"
