"""⑤⑨ "+Ours" 행·여러 자기 행·향상폭 괄호(2026-10-14 사용자 메일, WARP-VLA 2610.11508 표 I).

원 표: 기준 모델 행 바로 아래 "+Ours" 행이 "66.5 +25.2" 처럼 값과 **기준 대비 향상폭**을 함께 적는다. 카드는 (1) 둘을 한 값처럼 붙여 써서 사용자가
합(91.7)으로 읽었고, (2) "+Ours" 가 무엇에 붙인 것인지 안 보였고, (3) 같은 열의 자기 행 둘 중 첫 행(66.5)만 실어 "AnyCamVLA 보다 5.8 낮다" 고만 했다
— 더 좋은 자기 결과(π0.5 + Ours 78.3)는 6.0 높다. 표·값은 합성이다.
"""
import digest
import paper_observations as po
import performance_results as pr
import research_frontier as rf


def _table():
    grid = [["Baselines", "Bench-A", "Bench-A"],
            ["Baselines", "Canonical", "Perturbed SR"],
            ["Rival-1", "97.0", "72.3"],
            ["Rival-2", "92.0", "62.3"],
            ["Base-G", "92.1", "41.3"],
            ["+Ours", "94.1", "66.5 +25.2"],
            ["\\pi_{0.5}", "95.2", "39.2"],
            ["+Ours", "98.2", "78.3 +39.1"]]
    return {"id": "S3.T1", "caption": "TABLE I: Success rates (%) under perturbations on Bench-A.", "grid": grid, "header_rows": 2}


def _main(table=None):
    t = table or _table()
    rows = pr.table_results([t], method="NewVLA", benchmarks=["Bench-A"])
    main = rf._main_results([r for r in rows if r["own"] and r["direction"]], [])
    main = [rf._best_own(rows, r) for r in main]
    for r in main:
        r["own_rows"] = rf._own_siblings(rows, r)
        r["peers"] = rf._baselines(rows, r, "NewVLA", limit=None)
        r["baselines"] = r["peers"][:3]
        r["compare"] = {"status": "none"}
    return rows, main


def test_plus_rows_are_named_after_the_row_above_and_keep_its_value():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: "+Ours" 를 바로 위 기준 모델 이름 없이 두거나, 다른 행(위의 위·다른 구획)을 기준으로 잇거나,
    증감의 기준(같은 열 기준 행 값)을 잃으면 실패한다."""
    rows, _ = _main()
    own = [(r["model"], r["text"], r.get("gain"), r.get("gain_base_model"), r.get("gain_base_text")) for r in rows if r["own"] and r["locator"].endswith("c2")]
    assert own == [("Base-G + Ours", "66.5 +25.2", "+25.2", "Base-G", "41.3"), ("\\pi_{0.5} + Ours", "78.3 +39.1", "+39.1", "\\pi_{0.5}", "39.2")]
    # "+A" 바로 위가 또 "+" 행이면(누적 표) 무엇에 더한 것인지 모른다 — 이름을 잇지 않는다.
    chain = _table()
    chain["grid"][6] = ["+Extra", "95.2", "39.2"]
    names = [r["model"] for r in pr.table_results([chain], method="NewVLA", benchmarks=["Bench-A"]) if r["locator"].endswith("r7c2")]
    assert names == ["+Ours"]
    # 바로 위가 구획 머리면 기준 행이 아니다.
    blocked = _table()
    blocked["grid"][4] = ["Group-B", "Group-B", "Group-B"]
    names = [r["model"] for r in pr.table_results([blocked], method="NewVLA", benchmarks=["Bench-A"]) if r["locator"].endswith("r5c2")]
    assert names == ["+Ours"]


def test_best_own_row_is_the_main_result_and_the_others_are_listed():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 같은 열의 자기 행 중 첫 행(66.5)을 대표로 두어 더 좋은 자기 결과(78.3)를 가리거나, 나머지 자기 행을
    카드에서 빼거나, 낮을수록 좋은 지표에서 큰 값을 고르거나, 저자가 문장으로 말한 값(author_value)을 바꾸면 실패한다."""
    rows, main = _main()
    assert [(m["model"], m["text"]) for m in main] == [("\\pi_{0.5} + Ours", "78.3 +39.1")]
    assert [(r["model"], r["text"]) for r in main[0]["own_rows"]] == [("Base-G + Ours", "66.5 +25.2")]
    first = next(r for r in rows if r["own"] and r["locator"].endswith("r5c2"))
    assert rf._best_own(rows, {**first, "author_value": True})["text"] == "66.5 +25.2"
    lower = [{**r, "direction": "lower"} for r in rows]
    first_low = next(r for r in lower if r["own"] and r["locator"].endswith("r7c2"))
    assert rf._best_own(lower, first_low)["text"] == "66.5 +25.2"


def test_gain_is_shown_in_parentheses_with_its_base_and_values_stay_aligned():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 향상폭을 값에 붙여 써서 합으로 읽히게 하거나(사용자 지적), 괄호가 무엇 대비인지 안 쓰거나,
    차이 문장에 향상폭을 섞거나, 메일 표에서 앞 수치와 괄호를 한 칸에 넣어 숫자 열이 어긋나거나, 로마 숫자 표 번호를 "논문 표" 로 뭉개면 실패한다."""
    _, main = _main()
    heading, rows = po.sections({"_frontier": {"main": main, "external": {"status": "done", "competitors": []}}})[0]
    texts = [t for t, _ in rows]
    # 요약 한 줄이 표 위에(2026-10-15) — 단위가 표에서 확인되면(%) 값에 %, 차이에 %p.
    assert texts[0] == "요약: 표 I에서 π0.5 + Ours 78.3%은(는) Rival-1 72.3%보다 6.0%p 높다(더 좋음)."
    assert texts[1:4] == ["이 논문 · π0.5 + Ours · 78.3 (+39.1) · 표 I · 괄호는 π0.5 39.2 대비 증감",
                         "이 논문 · Base-G + Ours · 66.5 (+25.2) · 괄호는 Base-G 41.3 대비 증감",
                         "같은 표 · Rival-1 · 72.3"]
    assert po.row_value({"text": "77.3 -1.5"}) == "77.3 -1.5"                       # 증감이 확인 안 된 꼬리는 원 글 그대로
    assert po.row_value({"text": "77.3 -1.5", "gain": "-1.5"}) == "77.3 (-1.5)"
    assert po.row_value({"text": "80.3 \\pm 1.1"}) == "80.3 ± 1.1" and po.row_value({"text": "72.3"}) == "72.3"
    html = digest._render_observation_sections([(heading, rows)])
    assert ">78.3</td><td " in html and ">(+39.1)</td>" in html and ">72.3</td><td " in html
    assert 'colspan="2"' in html


def test_analyze_promotes_the_best_own_row_and_orders_the_rest(tmp_path, monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 실제 진입점 `analyze` 가 첫 자기 행을 대표값으로 그대로 두거나(가장 좋은 자기 행으로 바꾸는
    호출을 빼면), 나머지 자기 행을 좋은 순서로 싣지 않으면 실패한다 — `_best_own` 만 직접 부르는 테스트로는 진입점의 호출이 지켜지지 않는다."""
    import storage
    db = tmp_path / "p.db"
    storage.init_storage(db)
    t = _table()
    t["grid"] += [["Base-H", "90.0", "30.0"], ["+Ours", "91.0", "70.0 +40.0"]]
    paper = {"arxiv_id": "2610.99998", "title": "NewVLA: a method", "abstract": "We evaluate on Bench-A."}
    monkeypatch.setattr(pr, "benchmark_candidates", lambda text, claims: ["Bench-A"])
    rf.analyze(db, [paper], tables_of=lambda aid: [t], run_external=lambda targets: {}, signals_of=lambda aid: {},
               methods_of=lambda aid, rows: None, status_path=tmp_path / "s.jsonl")
    main = paper["_frontier"]["main"][0]
    assert (main["model"], main["text"]) == ("\\pi_{0.5} + Ours", "78.3 +39.1")
    assert [(r["model"], r["text"]) for r in main["own_rows"]] == [("Base-H + Ours", "70.0 +40.0"), ("Base-G + Ours", "66.5 +25.2")]


def test_gain_needs_arithmetic_and_plus_names_only_own_rows_after_a_real_model():
    """이 테스트가 무엇을 망가뜨리면 실패하는가(2026-10-15 Codex 검토): 부호 붙은 둘째 수치를 산수 확인 없이 증감으로 단정하거나(하한·둘째 지표),
    맞는 기준이 둘 이상인데 하나를 고르거나, 평균 행을 기준 모델로 잇거나, 비교 행 "+NewVLA-S" 를 "Base + NewVLA-S" 로 바꿔 자기 변형 제외를
    빠져나가게 하면 실패한다."""
    t = _table()
    t["grid"][5] = ["+Ours", "94.1", "66.5 +20.0"]                                     # 66.5 − 41.3 ≠ 20.0
    r = next(x for x in pr.table_results([t], method="NewVLA", benchmarks=["Bench-A"]) if x["locator"].endswith("r5c2"))
    assert "gain" not in r and r["model"] == "Base-G + Ours"
    # "+" 행이 아닌 자기 행: 같은 구획의 비교 행 중 산수가 맞는 것이 하나일 때만.
    flat = {"id": "T2", "caption": "Table 2: Bench-A.", "header_rows": 1,
            "grid": [["Method", "SR"], ["Rival-1", "50.0"], ["Rival-2", "60.0"], ["NewVLA", "70.0 +10.0"]]}
    got = next(x for x in pr.table_results([flat], method="NewVLA", benchmarks=["Bench-A"]) if x["own"])
    assert (got.get("gain"), got.get("gain_base_model")) == ("+10.0", "Rival-2")
    twin = {**flat, "grid": flat["grid"][:2] + [["Rival-3", "60.0"], ["Rival-2", "60.0"], ["NewVLA", "70.0 +10.0"]]}
    twin["grid"][1] = ["Rival-1", "60.0"]
    assert "gain" not in next(x for x in pr.table_results([twin], method="NewVLA", benchmarks=["Bench-A"]) if x["own"])
    avg = _table()
    avg["grid"][4] = ["Average", "92.1", "41.3"]
    assert next(x for x in pr.table_results([avg], method="NewVLA", benchmarks=["Bench-A"]) if x["locator"].endswith("r5c2"))["model"] == "+Ours"
    variant = _table()
    variant["grid"][5] = ["+NewVLA-S", "94.1", "66.5"]
    rows = pr.table_results([variant], method="NewVLA", benchmarks=["Bench-A"])
    assert next(x for x in rows if x["locator"].endswith("r5c2"))["model"] == "+NewVLA-S"
    main = next(x for x in rows if x["own"] and x["locator"].endswith("r7c2"))
    assert "+NewVLA-S" not in [b["model"] for b in rf._baselines(rows, main, "NewVLA", limit=None)]


def test_reading_point_and_cached_search_wording():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 읽을 포인트에 "78.3 +39.1" 을 그대로 써서 합으로 읽히게 하거나, 다른 논문 기준 검색 기록을 다시 쓴
    빈 결과를 이 논문의 검색 결과처럼("저자가 비교하지 않은 … 찾지 못했다") 말하면 실패한다."""
    _, main = _main()
    point = rf.reading_point({"_frontier": {"main": main, "external": None}})
    assert "78.3 (+39.1)를 보고" in point and "78.3 +39.1" not in point
    cached = {"status": "done", "competitors": [], "cached_from": "2610.00001", "checked_on": "2026-10-13"}
    texts = [t for t, _ in po.sections({"_frontier": {"main": main, "external": cached}})[0][1]]
    assert "→ 같은 벤치마크·지표의 최근 웹 검색 기록(2026-10-13 다른 논문 기준)을 다시 썼고, 거기서 이 카드에 실을 결과는 없었다." in texts
    assert not any("찾지 못했다" in t for t in texts)


def test_heading_states_unit_and_runs_and_external_rows_stay_short():
    """이 테스트가 무엇을 망가뜨리면 실패하는가(2026-10-15 사용자: "값이 어떤 값인지 하나도 모르겠다"): 표에서 확인된 단위(%)·반복 횟수(3회 평균)를
    상자 머리에 쓰지 않거나, 단위가 없는데 % 를 붙이거나, 외부 논문 행에 조건 차이를 전부 늘어놓아(셋째부터는 "외 N가지") 표가 무너지게 하면 실패한다."""
    _, main = _main()
    m = {**main[0], "unit": "%", "averaged_over": 3}
    ext = {"status": "done", "competitors": [{"status": "verified", "same_task": True, "model": "Other", "text": "95.0", "value": 95.0,
                                              "table_label": "Table 1", "column_path": ["SR"], "source_url": "https://arxiv.org/abs/2609.00001",
                                              "differences": ["백본 다름", "단계 상한 다름", "시드 수 다름"]}]}
    heading, rows = po.sections({"_frontier": {"main": [m], "external": ext}})[0]
    # 반복 횟수는 이 논문 표의 것 — 외부 행은 출처 열 이름으로 척도를 읽는다(Codex 검토 P2).
    assert heading == "성능 비교 · Bench-A · Perturbed SR (%) · 이 논문 표는 3회 평균"
    assert '외부 논문 · Other · 95.0 · Table 1 열 "SR" · 조건 다름: 백본 다름, 단계 상한 다름 외 1가지' in [t for t, _ in rows]
    bare = po.sections({"_frontier": {"main": [{**main[0], "averaged_over": 1, "unit": None}], "external": {}}})[0][0]
    assert bare == "성능 비교 · Bench-A · Perturbed SR"                      # 단위 근거 없음·1회는 쓰지 않는다


def test_review_units_need_both_rows_subscripts_stay_math_and_cited_names_are_clean():
    """이 테스트가 무엇을 망가뜨리면 실패하는가(2026-10-15 Codex 검토): 비교 행의 단위가 확인되지 않았는데 대표 행 단위(%)를 두 값에 다 붙이거나,
    "x_{t}"·"F_{max}" 같은 수식 아래첨자를 방법 변형 괄호로 바꾸거나, 최다 인용 문장에 LaTeX 이름을 그대로 쓰거나, 요약 줄에서 방향 문구를 문자열로
    지워 다른 문장까지 건드리면 실패한다."""
    m = {"model": "Ours", "text": "80.0", "value": 80.0, "direction": "higher", "unit": "%"}
    assert "%" not in po.difference_sentence(m, {"model": "B", "text": "0.7", "value": 0.7}, "표 1에서", same_column=True)
    assert "80.0%은(는) B 70.0%보다 10.0%p" in po.difference_sentence(m, {"model": "B", "text": "70.0", "value": 70.0, "unit": "%"},
                                                                     "표 1에서", same_column=True)
    assert po.latex_display("x_{t}") == "x_{t}" and po.latex_display("F_{max}") == "F_{max}" and po.latex_display("TSRL_{\\text{DAPO}}") == "TSRL (DAPO)"
    assert not po.difference_sentence(m, {"model": "B", "text": "70.0", "value": 70.0}, "표 1에서", same_column=True, direction_note=False).endswith("좋음.")
    _, main = _main()
    r = {**main[0], "baselines": [{"model": "SFT\\toDAPO", "text": "72.3", "value": 72.3}], "peers": [{"model": "SFT\\toDAPO", "text": "72.3", "value": 72.3}],
         "popularity": {"SFT\\toDAPO": {"citations": 50}, "Other": {"citations": 1}}, "popularity_checked": 2, "popularity_total": 2}
    lead = po.sections({"_frontier": {"main": [r], "external": {}}})[0][1][0][0]
    assert "SFT → DAPO은(는)" in lead and "\\to" not in lead
