"""⑤⑨ 비교 방법 → 원 논문 → 인기 지표(인용·별·HF), 카드의 "가장 많이 인용된 비교 방법 대비" (2026-10-13).

사용자 결정: 살아 있는 리더보드가 없으니 인용·HF·별 수로 "이 분야에서 많이 쓰이는 방법" 을 가리키고, 이 논문의 결과를 그 방법과 견준다.
HTML 은 LaTeXML 꼴을 합성한 것이다(실제 논문 문장 복사 없음).
"""
import json
from datetime import datetime, timedelta, timezone

import digest
import method_links as ml
import paper_observations as po
import research_frontier as rf

BIB = ('<ul class="ltx_biblist">'
       '<li id="bib.bib1" class="ltx_bibitem"><span class="ltx_bibblock">A. Author, B. Author.</span> '
       '<span class="ltx_bibblock">FooNet: A strong baseline for widgets.</span> '
       '<span class="ltx_bibblock">ICLR 2023. URL https://arxiv.org/abs/2301.00001.</span></li>'
       '<li id="bib.bib2" class="ltx_bibitem"><span class="ltx_bibblock">C. Author.</span> '
       '<span class="ltx_bibblock">BarSys²: Self-configuring systems.</span> '
       '<span class="ltx_bibblock">NeurIPS 2024.</span></li>'
       '<li id="bib.bib3" class="ltx_bibitem"><span class="ltx_bibblock">D. Author.</span> '
       '<span class="ltx_bibblock">Agent-computer interfaces.</span> <span class="ltx_bibblock">2024.</span></li>'
       '<li id="bib.bib4" class="ltx_bibitem"><span class="ltx_bibblock">E. Author.</span> '
       '<span class="ltx_bibblock">QuxNet revisited.</span> <span class="ltx_bibblock">2024. arXiv:2402.00004</span></li>'
       '<li id="bib.bib5" class="ltx_bibitem"><span class="ltx_bibblock">F. Author.</span> '
       '<span class="ltx_bibblock">QuxNet original.</span> <span class="ltx_bibblock">2023. arXiv:2302.00005</span></li>'
       '</ul>')


def cite(n: int) -> str:
    return f'<cite class="ltx_cite">(<a href="#bib.bib{n}" class="ltx_ref">Author, 2024</a>)</cite>'


HTML = (f"<p>We compare with FooNet {cite(1)} and BarSys<sup>2</sup> {cite(2)}. MiniTool {cite(3)} is a light agent. "
        f"QuxNet {cite(4)} improves on QuxNet {cite(5)}. "
        f"ZedNet {cite(1)} is strong; surveys of ZedNet <cite class=\"ltx_cite\">(<a href=\"#bib.bib1\">x</a>; <a href=\"#bib.bib3\">y</a>)</cite> agree.</p>"
        + BIB)


def test_methods_link_only_through_the_citation_the_authors_attached_and_a_matching_reference():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 이름 바로 뒤 인용이 아닌 참고문헌에 잇는 것, 참고문헌에 그 이름이 없는데 잇는 것(저자가 다른 논문을
    인용한 경우 — 실측 Mini-SWE→SWE-agent), 같은 이름에 서로 다른 참고문헌이 붙었는데 하나를 고르는 것(모르면 잇지 않는다), 위첨자("BarSys<sup>2</sup>"↔
    "BarSys2")·arXiv 주소 읽기를 놓치는 것."""
    links = ml.link_methods(HTML, ["FooNet", "BarSys2", "MiniTool", "QuxNet", "Unknown"])
    assert set(links) == {"FooNet", "BarSys2"}
    assert links["FooNet"]["arxiv"] == "2301.00001" and links["FooNet"]["title"] == "FooNet: A strong baseline for widgets"
    assert links["BarSys2"]["arxiv"] == "" and links["BarSys2"]["bib"] == "bib.bib2"
    # 한 곳은 단독 인용, 다른 곳은 여러 참고문헌을 함께 묶은 인용 — 묶음 인용은 근거로 세지 않고 단독 인용을 따른다(이름이 참고문헌에 없어서 잇지는 않는다).
    import method_links
    assert method_links.cited_bib(method_links._marked_body(HTML), "ZedNet") == "bib.bib1"
    assert ml.link_methods("<p>no bibliography</p>", ["FooNet"]) == {}


def _fake_get(calls):
    def get(url, headers=None):
        calls.append(url)
        if "search/match" in url:
            return {"data": [{"title": "BarSys²: Self-configuring systems", "citationCount": 40, "externalIds": {"ArXiv": "2405.00002"}}]}
        if "/paper/ARXIV:2301.00001" in url:
            return {"title": "FooNet: A strong baseline for widgets", "citationCount": 1200, "externalIds": {"ArXiv": "2301.00001"}}
        if "huggingface.co/api/papers/2301.00001" in url:
            return {"githubRepo": "https://github.com/o/foonet", "githubStars": 3400, "numTotalModels": 5, "upvotes": 9}
        if "huggingface.co/api/papers/" in url:
            return None
        return None
    return get


def _main(**kw):
    m = {"benchmark": "Bench", "metric": "SR", "text": "71.0", "value": 71.0, "model": "Ours", "direction": "higher",
         "caption": "Table 1: SR", "locator": "T1#0:r9c1",
         "peers": [{"model": "BarSys2", "text": "70.0", "value": 70.0}, {"model": "Mid", "text": "66.0", "value": 66.0},
                   {"model": "Low", "text": "65.0", "value": 65.0}, {"model": "FooNet", "text": "62.0", "value": 62.0}]}
    m["baselines"] = m["peers"][:3]
    m.update(kw)
    return m


def test_popularity_is_fetched_once_per_week_and_title_matches_must_be_near_exact(tmp_path):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 인용·별·HF 모델을 원 논문에서 못 가져오는 것, 7일 캐시를 무시하고 매일 다시 묻는 것(S2 초당 1회 한도),
    제목이 다른 S2 검색 결과를 받아들이는 것, 마감을 넘겨서도 조회하는 것, 조회 실패를 0 으로 적는 것."""
    calls, cache = [], tmp_path / "c.json"
    now = datetime(2026, 10, 13, tzinfo=timezone.utc)
    r = _main()
    ml.annotate(HTML, [r], get=_fake_get(calls), now=now, cache_path=cache)
    assert r["popularity"]["FooNet"] == {"arxiv": "2301.00001", "title": "FooNet: A strong baseline for widgets", "citations": 1200,
                                         "github_repo": "https://github.com/o/foonet", "github_stars": 3400, "hf_models": 5,
                                         "complete": True, "checked_on": "2026-10-13"}
    assert r["popularity"]["BarSys2"]["citations"] == 40 and r["popularity"]["BarSys2"]["github_stars"] is None
    n = len(calls)
    ml.annotate(HTML, [_main()], get=_fake_get(calls), now=now + timedelta(days=6), cache_path=cache)
    assert len(calls) == n                                                       # 7일 안이면 묻지 않는다
    ml.annotate(HTML, [_main()], get=_fake_get(calls), now=now + timedelta(days=8), cache_path=cache)
    assert len(calls) > n
    wrong = lambda url, headers=None: {"data": [{"title": "A totally different paper", "citationCount": 9999}]} if "match" in url else None
    r2 = _main()
    ml.annotate(HTML, [r2], get=wrong, now=now, cache_path=tmp_path / "w.json")
    assert r2["popularity"]["BarSys2"]["citations"] is None
    r3 = _main()
    ml.annotate(HTML, [r3], get=_fake_get([]), now=now, cache_path=tmp_path / "d.json", deadline=0.0, clock=lambda: 1.0)
    assert "popularity" not in r3
    assert ml.label({"citations": 12411, "github_stars": 7141, "hf_models": 67}) == "인용 12,411회 · ★7.1k · HF 모델 67"
    assert ml.label({"citations": None, "github_stars": 0}) == "" and ml.label(None) == ""
    assert json.loads(cache.read_text(encoding="utf-8"))


def test_card_compares_with_the_most_cited_method_even_outside_the_top_three():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 가장 많이 인용된 비교 방법이 성능 상위 3개 밖이라고 표에서 빠지는 것, 그 방법과의 차이 문장이 없는 것,
    원 논문 링크를 잃는 것, 인용 수를 모르는 방법을 "가장 많이 인용됨" 으로 고르는 것, 같은 방법이면 문장을 두 번 쓰는 것."""
    r = _main(popularity={"FooNet": {"arxiv": "2301.00001", "citations": 1200, "github_stars": 3400, "hf_models": 5},
                          "BarSys2": {"arxiv": "2405.00002", "citations": 40}, "Low": {"citations": None}},
              popularity_checked=2, popularity_total=4)
    rows = po.sections({"_frontier": {"main": [r], "external": {}}})[0][1]
    texts = dict(rows)
    assert "같은 표 · FooNet · 62.0 · 인용 1,200회 · ★3.4k · HF 모델 5" in texts
    assert texts["같은 표 · FooNet · 62.0 · 인용 1,200회 · ★3.4k · HF 모델 5"] == "https://arxiv.org/abs/2301.00001"
    # 4개 중 인용 수를 확인한 것은 2개(FooNet·BarSys2) — 확인한 범위를 문장에 밝힌다(Codex 검토 P2).
    line = ("→ 이 표의 비교 방법 4개 중 원 논문 인용 수를 확인한 2개에서 가장 많이 인용된 방법과 견주면 Ours 71.0은(는) FooNet(인용 1,200회) 62.0보다 "
            "9.0 높다(더 좋음). 지표는 높을수록 좋음.")
    assert texts[line] == ""
    html = digest._render_observation_sections(po.sections({"_frontier": {"main": [r], "external": {}}}))
    assert "<table" in html and 'href="https://arxiv.org/abs/2301.00001"' in html and "인용 1,200회" in html
    same = _main(popularity={"BarSys2": {"arxiv": "2405.00002", "citations": 40}})
    texts = [t for t, _ in po.sections({"_frontier": {"main": [same], "external": {}}})[0][1]]
    assert sum("가장 많이 인용" in t for t in texts) == 1 and any("가장 많이 인용된 방법이기도 하다(인용 40회)" in t for t in texts)
    none = _main(popularity={"Low": {"citations": None}})
    assert not any("가장 많이 인용" in t for t, _ in po.sections({"_frontier": {"main": [none], "external": {}}})[0][1])


def test_analyze_keeps_all_same_block_peers_and_calls_the_method_lookup(tmp_path):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 비교 방법을 성능 상위 3개로만 잘라 인기 지표 후보에서 가장 많이 인용된 방법을 놓치는 것,
    인기 지표 조회가 analyze 에서 불리지 않는 것, 그 조회의 예외가 메일 자료를 깨는 것."""
    import storage
    db = tmp_path / "p.db"
    storage.init_storage(db)
    grid = [["Method", "SR"], ["Alpha", "60.0"], ["Beta", "61.0"], ["Gamma", "62.0"], ["Delta", "63.0"], ["Ours", "70.0"]]
    tables = [{"id": "T1", "caption": "Table 1: Success rate (SR) on Bench.", "grid": grid, "header_rows": 1}]
    paper = {"arxiv_id": "2610.99999", "title": "Ours: a method", "abstract": "We evaluate on Bench."}
    seen = []
    import performance_results as pr
    orig = pr.benchmark_candidates
    try:
        pr.benchmark_candidates = lambda text, claims: ["Bench"]
        rf.analyze(db, [paper], tables_of=lambda aid: tables, run_external=lambda t: {}, signals_of=lambda aid: {},
                   methods_of=lambda aid, rows: seen.append((aid, [p["model"] for p in rows[0]["peers"]])), status_path=tmp_path / "s.jsonl")
        assert seen == [("2610.99999", ["Delta", "Gamma", "Beta", "Alpha"])]
        assert [b["model"] for b in paper["_frontier"]["main"][0]["baselines"]] == ["Delta", "Gamma", "Beta"]
        boom = dict(paper)
        rf.analyze(db, [boom], tables_of=lambda aid: tables, run_external=lambda t: {}, signals_of=lambda aid: {},
                   methods_of=lambda aid, rows: (_ for _ in ()).throw(RuntimeError("x")), status_path=tmp_path / "s.jsonl")
        assert boom["_frontier"]["main"]
    finally:
        pr.benchmark_candidates = orig


def test_web_search_asks_for_the_fields_most_adopted_reference_method():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 외부 웹 검색이 더 최근 결과만 찾고 그 분야에서 가장 널리 쓰이는 기준 방법의 결과는 찾지 않으면 실패한다."""
    import external_evidence as ee
    prompt = ee._prompt([{"paper_id": "p", "title": "t", "method": "m", "benchmark": "b", "metric": "x", "value": "1"}])
    assert "most widely adopted" in prompt and "already compared methods" in prompt



# ── 2026-10-13 Codex 검토(P2 6 · P3 2) 회귀 ───────────────────────────────────────────────────────

def test_review_linking_rejects_neighbouring_names_substrings_and_other_versions():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: "Base and FooNet (cite)" 에서 Base 가 FooNet 의 인용을 집는 것, "base"↔"baseline"·"DINO"↔"DINOv2"
    같은 부분 문자열, "SAM"↔"SAM 2"·"GPT"↔"GPT-4" 같은 다른 판을 같은 방법으로 잇는 것. 붙은 이름("Skill-MAS"·"MAS 2")은 계속 잇는다."""
    html = (f"<p>Base and FooNet {cite(1)}. SAM {cite(6)}. DINO {cite(7)}. GPT {cite(8)}.</p>" + BIB.replace("</ul>", "")
            + '<li id="bib.bib6" class="ltx_bibitem"><span class="ltx_bibblock">G.</span> <span class="ltx_bibblock">SAM 2: Segment anything in videos.</span></li>'
            + '<li id="bib.bib7" class="ltx_bibitem"><span class="ltx_bibblock">H.</span> <span class="ltx_bibblock">DINOv2: Learning robust features.</span></li>'
            + '<li id="bib.bib8" class="ltx_bibitem"><span class="ltx_bibblock">I.</span> <span class="ltx_bibblock">GPT-4 technical report.</span></li></ul>')
    links = ml.link_methods(html, ["Base", "FooNet", "SAM", "DINO", "GPT"])
    assert set(links) == {"FooNet"}
    # 옆 낱말이 마침 그 참고문헌 제목에 있는 경우 — 사이에 낱말이 끼면 그 인용은 옆 낱말의 것이 아니다.
    assert ml.link_methods(f"<p>Widgets and FooNet {cite(1)}.</p>" + BIB, ["Widgets"]) == {}
    assert ml.name_in_title("MAS2", "MAS 2 : Self-generative systems") and ml.name_in_title("Skill-MAS", "Skill-mas: Evolving meta-skill")
    assert ml.name_in_title("GPT-4", "GPT-4 technical report") and not ml.name_in_title("base", "A strong baseline")


def test_review_s2_title_match_never_picks_among_versions_or_mismatched_ids():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: S2 검색의 첫 후보가 다른 판(v2)인데 고르는 것, 비슷한 후보가 여럿인데 하나를 고르는 것,
    arXiv 직접 조회가 다른 논문을 돌려줬는데 받는 것."""
    entry = {"title": "FooNet v1: A strong baseline for widgets", "arxiv": "", "text": "x"}
    both = lambda url, headers=None: {"data": [{"title": "FooNet v2: A strong baseline for widgets", "citationCount": 9, "externalIds": {"ArXiv": "2"}},
                                               {"title": "FooNet v1: A strong baseline for widgets", "citationCount": 5, "externalIds": {"ArXiv": "1"}}]}
    assert ml._resolve(entry, both)["arxiv"] == "1"
    only_v2 = lambda url, headers=None: {"data": [{"title": "FooNet v2: A strong baseline for widgets", "citationCount": 9}]}
    assert ml._resolve(entry, only_v2)["found"] is False
    plain = {"title": "QuxNet: widgets at scale", "arxiv": "", "text": "x"}
    two_close = lambda url, headers=None: {"data": [{"title": "QuxNet: widgets at scales", "citationCount": 1},
                                                    {"title": "QuxNet: widget at scale", "citationCount": 2}]}
    assert ml._resolve(plain, two_close)["found"] is False
    other = lambda url, headers=None: {"title": "Unrelated", "citationCount": 1, "externalIds": {"ArXiv": "9999.99999"}}
    assert ml._resolve({"title": "FooNet", "arxiv": "2301.00001", "text": "x"}, other)["found"] is False
    same_title_other_id = lambda url, headers=None: {"title": "FooNet", "citationCount": 1, "externalIds": {"ArXiv": "9999.99999"}}
    assert ml._resolve({"title": "FooNet", "arxiv": "2301.00001", "text": "x"}, same_title_other_id)["found"] is False


def test_review_failures_retry_next_day_and_never_erase_good_values(tmp_path):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: S2 못 찾음·HF 실패를 7일 캐시에 넣어 일주일 동안 빈칸으로 두는 것, 일시 실패가 지난주의 정상 값을 빈칸으로 덮는 것,
    호출 전의 전역 마감(adoption_signals._DEADLINE)을 지워 버리는 것."""
    import adoption_signals
    now = datetime(2026, 10, 13, tzinfo=timezone.utc)
    cache = tmp_path / "c.json"
    down = lambda url, headers=None: (_ for _ in ()).throw(TimeoutError()) if "huggingface" in url else _fake_get([])(url, headers)
    r = _main()
    ml.annotate(HTML, [r], get=down, now=now, cache_path=cache)
    assert r["popularity"]["FooNet"]["citations"] == 1200 and r["popularity"]["FooNet"]["complete"] is False
    calls = []
    ml.annotate(HTML, [_main()], get=_fake_get(calls), now=now + timedelta(days=1), cache_path=cache)
    assert any("huggingface" in c for c in calls)                                 # 하루 뒤 다시 묻는다
    stored = json.loads(cache.read_text(encoding="utf-8"))
    assert stored["arxiv:2301.00001"]["github_stars"] == 3400
    flaky = lambda url, headers=None: {"data": []} if "match" in url else (None if "huggingface" in url else _fake_get([])(url, headers))
    later = _main()
    ml.annotate(HTML, [later], get=lambda url, headers=None: (_ for _ in ()).throw(TimeoutError()) if "ARXIV" in url else flaky(url, headers),
                now=now + timedelta(days=9), cache_path=cache)
    assert later["popularity"]["FooNet"]["citations"] == 1200                     # 오래된 정상 값을 지우지 않는다
    # S2 는 되고 HF 페이지만 비었을 때 — 새 인용 수는 받고, 지난주 별 수는 빈칸으로 덮지 않는다.
    hf_empty = lambda url, headers=None: (None if "huggingface" in url else
                                         {"title": "FooNet: A strong baseline for widgets", "citationCount": 1300, "externalIds": {"ArXiv": "2301.00001"}}
                                         if "ARXIV:2301.00001" in url else _fake_get([])(url, headers))
    merged = _main()
    ml.annotate(HTML, [merged], get=hf_empty, now=now + timedelta(days=20), cache_path=cache)
    assert merged["popularity"]["FooNet"]["citations"] == 1300 and merged["popularity"]["FooNet"]["github_stars"] == 3400
    adoption_signals._DEADLINE = 12345.0
    try:
        ml.annotate(HTML, [_main()], get=_fake_get([]), now=now, cache_path=tmp_path / "x.json", deadline=10**9, clock=lambda: 0.0)
        assert adoption_signals._DEADLINE == 12345.0
    finally:
        adoption_signals._DEADLINE = None


def test_review_ninth_peer_is_looked_up_and_scope_is_stated(tmp_path):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 성능 순 앞 8개로 잘라 9번째의 유명 방법을 조회하지 않는 것, 인용 수를 확인한 범위를 세지 않는 것."""
    peers = [{"model": f"Weak{i}", "text": f"{70 - i}.0", "value": 70.0 - i} for i in range(8)] + [{"model": "FooNet", "text": "50.0", "value": 50.0}]
    r = _main(peers=peers, baselines=peers[:3])
    ml.annotate(HTML, [r], get=_fake_get([]), now=datetime(2026, 10, 13, tzinfo=timezone.utc), cache_path=tmp_path / "c.json")
    assert r["popularity"]["FooNet"]["citations"] == 1200 and r["popularity_checked"] == 1 and r["popularity_total"] == 9
    texts = [t for t, _ in po.sections({"_frontier": {"main": [r], "external": {}}})[0][1]]
    assert any("이 표의 비교 방법 9개 중 원 논문 인용 수를 확인한 1개에서 가장 많이 인용된 방법과 견주면" in t for t in texts)


def test_review_cache_keys_keep_different_references_apart_and_hostile_html_stays_linear():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: arXiv 없는 다른 참고문헌("AB: C" 와 "A: BC")이 같은 캐시 키를 쓰는 것,
    비신뢰 HTML 의 이름 반복·닫히지 않은 인용 태그에 제곱 시간이 걸리는 것."""
    import time as _time
    assert ml._key({"text": "X. AB: C. 2024", "arxiv": ""}) != ml._key({"text": "X. A: BC. 2024", "arxiv": ""})
    hostile = "<p>" + "FooNet " * 60000 + "<cite>" * 20000 + "</p>" + BIB
    start = _time.monotonic()
    ml.link_methods(hostile, ["FooNet"])
    assert _time.monotonic() - start < 5.0
