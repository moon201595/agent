"""⑤⑨ 카드의 성능 비교 — 표 + 한 문장, 같은 구획 안에서만 비교, 새 논문의 인용 수 대신 코드 저장소(2026-10-13).

사용자 지적(10/13 메일 캡처): "논문 내 결과도 이 논문 표도 저걸 어떻게 보고 사용자가 이해를 해? 표 형태로 주던가, 어느 논문의 어느 방법이 어느 수치였는데
그거 대비 이건 어떻다 이런 식으로. 그리고 왜 최신 논문에 대한 인용수가 아직도 있어". 같은 카드에서 틀린 칸(GAIA Level 3 를 SWE-Bench 로)과 다른 백본
구획의 비교값이 섞여 나간 것도 확인했다 — 칸 병합은 `arxiv_tables`(Codex), 구획은 `performance_results.row_blocks` 가 맡는다.
"""
import digest
import paper_observations as po
import performance_results as pr
import research_frontier as rf


def _table():
    """2610.13485 표 1 의 꼴(백본 구획마다 같은 방법을 되풀이)을 합성한 것. 원문 문장·수치는 쓰지 않는다."""
    grid = [["Method", "Bench-A", "Bench-A", "SWE-Bench"],
            ["", "Level 1", "Average", ""],
            ["Backbone-X", "", "", ""],
            ["Base-1", "10.0", "20.0", "62.0"],
            ["Base-2", "11.0", "21.0", "70.0"],
            ["\\[4pt/2pt] Ours-Method", "12.0", "22.0", "71.0"],
            ["Backbone-3-Y", "", "", ""],
            ["Base-1", "30.0", "40.0", "53.0"],
            ["Base-2", "31.0", "41.0", "75.0"],
            ["Ours-Method", "32.0", "42.0", "73.0"]]
    return {"id": "S3.T1", "caption": "Table 1: Success rate (%) across backbones.", "grid": grid, "header_rows": 2}


def test_row_blocks_keep_comparisons_inside_one_backbone_and_strip_latex_spacing():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 백본 구획 머리 행을 못 알아봐 다른 구획의 값을 비교 대상으로 섞거나(10/13 메일 실측),
    행 이름의 `[4pt/2pt]` 조판 찌꺼기를 방법 이름으로 내보내면 실패한다."""
    t = _table()
    x, y = ("2:Backbone-X", "Backbone-X"), ("6:Backbone-3-Y", "Backbone-3-Y")
    assert pr.row_blocks(t) == {3: x, 4: x, 5: x, 7: y, 8: y, 9: y}             # 구획 머리 행(2·6)은 본문 행이 아니다
    assert pr.model_label("[4pt/2pt] POrchestra") == "POrchestra" and pr.model_label("[2pt] A") == "A"
    assert pr.model_label("Method [12]") == "Method [12]"                       # 조판 꼴이 아닌 괄호는 그대로
    results = pr.table_results([t], method="Ours-Method", benchmarks=["SWE-Bench"])
    swe = [r for r in results if r["bench_key"] == pr.norm_key("SWE-Bench")]
    own = [r for r in swe if r["own"]]
    assert [(r["model"], r["text"], r.get("block")) for r in own] == [("Ours-Method", "71.0", "Backbone-X"), ("Ours-Method", "73.0", "Backbone-3-Y")]
    base = rf._baselines(swe, own[0], "Ours-Method")
    assert [(b["model"], b["text"]) for b in base] == [("Base-2", "70.0"), ("Base-1", "62.0")]   # 75.0(다른 구획)은 들어오면 안 된다
    # 구획 머리를 표 폭 전체로 병합한 표 — 파서가 같은 글을 모든 열에 채운다(2610.13485 실측, 클래스형 colspan 을 읽게 된 뒤).
    spanned = {**t, "grid": [row if r < 2 or any(row[1:]) else [row[0]] * len(row) for r, row in enumerate(t["grid"])]}
    assert pr.row_blocks(spanned) == pr.row_blocks(t)
    # Codex 검토 P1: 병합된 구획 머리 "Backbone-3-Y" 의 3 을 값으로 저장했다 — 머리 행의 칸은 결과에 하나도 없어야 한다.
    got = pr.table_results([spanned], method="Ours-Method", benchmarks=["SWE-Bench"])
    assert not any(r["locator"].endswith((f":r2c{c}", f":r6c{c}")) for r in got for c in range(4))
    assert 3.0 not in [r["value"] for r in got]


def _main(**kw):
    m = {"benchmark": "SWE-Bench", "metric": "Success rate", "text": "71.0", "value": 71.0, "model": "Ours-Method",
         "direction": "higher", "locator": "S3.T1#0:r5c3", "caption": "Table 1: Success rate", "block": "Backbone-X",
         "baselines": [{"model": "Base-2", "text": "70.0", "value": 70.0}, {"model": "Base-1", "text": "62.0", "value": 62.0}]}
    m.update(kw)
    return m


def test_card_says_how_this_paper_compares_to_the_best_baseline_in_the_same_block():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 같은 표·같은 구획의 가장 좋은 비교 방법 대비 차이와 지표 방향을 한 문장으로 말하지 않거나,
    낮을수록 좋은 지표에서 "더 좋음/더 나쁨" 을 뒤집거나, 방향을 모르는데 대소를 말하거나, 소수 자릿수를 원 표와 다르게 쓰면 실패한다."""
    heading, rows = po.sections({"_frontier": {"main": [_main()], "external": {"status": "done", "competitors": []}}})[0]
    assert heading == "성능 비교 · SWE-Bench · Success rate"
    texts = [t for t, _ in rows]
    # 결론 한 줄이 표 위에(2026-10-15 사용자: 표를 먼저 보면 무엇과 무엇을 견주는지 모른다).
    assert texts[0] == "요약: 표 1 Backbone-X 구획에서 Ours-Method 71.0은(는) Base-2 70.0보다 1.0 높다(더 좋음)."
    assert texts[1:4] == ["이 논문 · Ours-Method · 71.0 · 표 1 Backbone-X 구획", "같은 표 · Base-2 · 70.0", "같은 표 · Base-1 · 62.0"]
    # 웹 검색이 후보를 하나도 내지 않았으면 "찾지 못했다" — "찾았지만 확인 못 함" 과 가른다(2026-10-14 WARP-VLA).
    assert "→ 저자가 비교하지 않은 같은 벤치마크·지표의 다른 결과를 웹 검색에서 찾지 못했다." in texts
    _, unverified = po.sections({"_frontier": {"main": [_main()], "external": {"status": "done", "competitors": [{"status": "not_found", "model": "X"}]}}})[0]
    assert "→ 저자가 비교하지 않은 더 최근 결과를 웹에서 찾았지만 출처 표에서 확인된 것이 없다." in [t for t, _ in unverified]
    lower = _main(direction="lower", text="0.85", value=0.85, baselines=[{"model": "B", "text": "0.80", "value": 0.80}])
    same = lambda m, o: po.difference_sentence(m, o, "표 1에서", same_column=True)
    other = lambda m, o: po.difference_sentence(m, o, "외부와", same_column=False)
    assert "0.05 높다(더 나쁨)" in same(lower, lower["baselines"][0])
    assert same(_main(direction=None), {"model": "B", "text": "70.0", "value": 70.0}) == ""
    assert "같다" in same(_main(), {"model": "B", "text": "71.0", "value": 71.0})
    assert other(_main(metric="RMSE", text="0.8", value=0.8), {"model": "B", "text": "80", "value": 80.0}) == ""


def test_card_shows_latex_table_cells_as_readable_symbols_but_keeps_values():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: arXiv 수식 칸의 LaTeX 원문("\\pm"·"\\leftrightarrow"·"\\tau^{2}")을 카드 머리·행·구획·문장에 그대로 찍거나,
    모르는 명령("\\top")을 비슷한 기호로 지어내거나, 보이는 글자를 바꾸면서 비교 값·자릿수를 바꾸면 실패한다(2026-10-13 2610.13494 재생)."""
    m = _main(benchmark="\\tau^{2}-Telecom", text="84.8 +4.5", value=84.8, block="Claude Opus 5 \\leftrightarrow GPT-5.6-sol",
              baselines=[{"model": "Avg. of Single", "text": "80.3 \\pm 1.1", "value": 80.3}])
    heading, rows = po.sections({"_frontier": {"main": [m], "external": {"status": "done", "competitors": []}}})[0]
    texts = [t for t, _ in rows]
    assert heading == "성능 비교 · τ²-Telecom · Success rate"
    # 증감 확인 표시(gain)가 없는 꼬리 "+4.5" 는 원 글 그대로다 — 괄호·설명은 산수로 확인된 칸만(test_card_plus_rows).
    assert texts[1:3] == ["이 논문 · Ours-Method · 84.8 +4.5 · 표 1 Claude Opus 5 ↔ GPT-5.6-sol 구획", "같은 표 · Avg. of Single · 80.3 ± 1.1"]
    assert "요약: 표 1 Claude Opus 5 ↔ GPT-5.6-sol 구획에서 Ours-Method 84.8 +4.5은(는) Avg. of Single 80.3 ± 1.1보다 4.5 높다(더 좋음)." in texts
    assert not any("\\" in t for t in [heading, *texts])
    assert po.latex_display("a\\top b \\mathrm{X}") == "a\\top b X"                     # 모르는 명령(\\top)은 남기고 글자 묶음만 푼다
    # 2026-10-15 TSRL 카드: 띄어쓰기가 사라진 "SFT\\toDAPO", 글자 아래첨자 "TSRL {}_{\\text{DAPO}}" 가 그대로 찍혔다.
    assert po.latex_display("SFT\\toDAPO") == "SFT → DAPO" and po.latex_display("TSRL {}_{\\text{DAPO}}") == "TSRL (DAPO)"
    assert po.latex_display("\\pi_{0.5} + Ours") == "π0.5 + Ours" and po.latex_display("\\topK") == "\\topK"
    assert m["text"] == "84.8 +4.5" and m["baselines"][0]["text"] == "80.3 \\pm 1.1"      # 원 값은 그대로


def test_comparison_box_renders_as_a_table_and_falls_back_to_lines_for_unknown_rows():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 비교 상자가 표로 안 그려지거나, 이 논문 행을 강조하지 않거나, 외부 출처 링크를 잃거나,
    행 문법이 안 맞는 옛 저장 글을 표로 억지로 그려 칸을 지어내면 실패한다(그때는 줄 나열)."""
    ext = {"status": "done", "competitors": [{"status": "verified", "same_task": True, "column_path": ["SR"], "table_label": "Table 2",
                                               "model": "Newer", "text": "74.0", "value": 74.0, "source_url": "https://arxiv.org/abs/2610.00009",
                                               "differences": ["백본 다름"], "task_note": "같은 지표"}]}
    sections = po.sections({"_frontier": {"main": [_main()], "external": ext}})
    html = digest._render_observation_sections(sections)
    assert "<table" in html and "background-color:#EEF6F7" in html and ">Ours-Method</td>" in html
    assert 'href="https://arxiv.org/abs/2610.00009"' in html and "Newer ↗" in html
    assert "평가 조건이 같은지 확인되지 않아 차이를 말하지 않는다" in html and "74.0보다" not in html
    legacy = [(po.COMPARISON_PREFIX + "SWE-Bench · SR", [("SWE-Bench · SR 53.8 — 논문 표 X 행 (S3.T1#0:r10c3)", "")])]
    assert "<table" not in digest._render_observation_sections(legacy)
    # 일부 행만 문법에 맞아도 표로 그리지 않는다 — 맞지 않는 줄을 조용히 버리면 저장 글의 내용이 사라진다.
    head = po.COMPARISON_PREFIX + "SWE-Bench · SR"
    mixed = [(head, [("이 논문 · A · 71.0 · 표 1", ""), ("알 수 없는 옛 줄", "")])]
    html = digest._render_observation_sections(mixed)
    assert "<table" not in html and "알 수 없는 옛 줄" in html
    no_own = [(head, [("같은 표 · B · 70.0", "")])]
    assert "<table" not in digest._render_observation_sections(no_own)            # 이 논문 행이 없으면 비교 표가 아니다


def test_new_papers_show_their_code_repository_not_citation_counts():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 공개 며칠 된 논문 카드에 인용·HF·별 수를 다시 싣거나, 공식 저장소 링크를 잃으면 실패한다."""
    sig = {"github_tier": "official", "github": {"repo": "owner/project", "stars": 5},
           "scholarly": {"citations": 3, "influential": 1}, "hub": {"page": True, "n_models": 2}}
    sections = po.sections({"_signals": sig})
    assert sections == [(po.CODE_HEADING, [("공식 저장소 · owner/project", "https://github.com/owner/project")])]
    text = str(sections)
    assert "인용" not in text and "HF" not in text and "★" not in text



def test_review_cases_never_produce_a_wrong_or_overstated_comparison():
    """2026-10-13 Codex 독립 검토(P1·P2). 이 테스트가 무엇을 망가뜨리면 실패하는가:
    명시 % 값을 ×100 해 우열을 뒤집는 것(P1), ".85" 의 자릿수를 0으로 읽어 다른 값을 "같다" 고 하는 것, 지수 표기에 자릿수 문장을 쓰는 것(P2),
    방법 이름의 " · " 가 값 칸으로 밀리는 것(P2), 평가 조건 일부만 대조된 외부 값에 "4가지 대조함"·차이 문장을 쓰는 것(P2)."""
    pct = _main(text="0.8%", value=0.8, metric="Success rate")
    assert "0.4 낮다(더 나쁨)" in po.difference_sentence(pct, {"model": "B", "text": "1.2%", "value": 1.2}, "표 1에서", same_column=True)
    assert po.difference_sentence(pct, {"model": "B", "text": "80.0", "value": 80.0}, "외부와", same_column=False) == ""
    assert "0.05 높다" in po.difference_sentence(_main(text=".85", value=0.85), {"model": "B", "text": ".80", "value": 0.80}, "x", same_column=True)
    assert "0.001 높다" in po.difference_sentence(_main(text="71.001", value=71.001), {"model": "B", "text": "71.0", "value": 71.0}, "x", same_column=True)
    assert "0.10 높다" in po.difference_sentence(_main(text=".85", value=0.85), {"model": "B", "text": ".75", "value": 0.75}, "x", same_column=True)
    # 표시 자릿수에서 차이가 사라지는 값(반올림된 칸) — "같다" 가 아니라 자릿수를 늘려 말한다.
    assert "0.04 높다" in po.difference_sentence(_main(text="70.0", value=70.04), {"model": "B", "text": "70.0", "value": 70.0}, "x", same_column=True)
    assert po.difference_sentence(_main(text="1.2e-3", value=0.0012), {"model": "B", "text": "1.1e-3", "value": 0.0011}, "x", same_column=True) == ""
    dotted = po.sections({"_frontier": {"main": [_main(model="Alpha · Beta")], "external": {}}})
    html = digest._render_observation_sections(dotted)
    assert ">Alpha·Beta</td>" in html and ">71.0</td>" in html
    partial = {"status": "verified", "same_task": True, "column_path": ["SR"], "table_label": "Table 2", "model": "Newer", "text": "74.0",
               "value": 74.0, "source_url": "https://arxiv.org/abs/2610.00009", "same_conditions": True, "differences": [],
               "matched_conditions": ["split"], "unverified_differences": 1}
    text = str(po.sections({"_frontier": {"main": [_main()], "external": {"status": "done", "competitors": [partial]}}}))
    assert "4가지 대조함" not in text and "74.0보다" not in text and "확인되지 않아 차이를 말하지 않는다" in text
    one = {**partial, "unverified_differences": 0}                                   # 미검증 차이는 없지만 조건 하나만 대조
    assert not po.conditions_verified(one) and "4가지 대조함" not in str(po.sections({"_frontier": {"main": [_main()], "external": {"competitors": [one]}}}))
    full = {**partial, "matched_conditions": ["dataset_version", "split", "protocol", "training_setting"], "unverified_differences": 0}
    text = str(po.sections({"_frontier": {"main": [_main()], "external": {"status": "done", "competitors": [full]}}}))
    assert "4가지 대조함" in text and "Newer 74.0보다 3.0 낮다(더 나쁨)" in text
    point = rf.reading_point({"_frontier": {"main": [{**_main(), "compare": {"status": "first"}}], "external": {"competitors": [full]}}})
    assert "평가 조건 4가지를 출처 인용으로 대조한 외부 결과" in point and "일부 조건" not in point


def test_baselines_require_the_same_benchmark_metric_and_block_and_survive_single_header_transposed_tables():
    """Codex 검토 P1·P2. 이 테스트가 무엇을 망가뜨리면 실패하는가: 같은 열이라는 이유로 다른 벤치마크 행을 비교 대상으로 고르는 것(Dataset 열이 있는 표),
    결측 표시뿐인 행·떨어져 되풀이되는 같은 이름 구획을 경계로 못 보는 것, 단일 머리 전치 표에서 비교 대상을 모두 버리는 것."""
    mixed = {"id": "T1", "caption": "Table 1: Accuracy (%)", "header_rows": 1,
             "grid": [["Method", "Accuracy"], ["OurNet Bench-A", "85.0"], ["Base Bench-B", "95.0"], ["Old Bench-A", "80.0"]]}
    res = pr.table_results([mixed], method="OurNet", benchmarks=["Bench-A", "Bench-B"])
    own = [r for r in res if r["own"]][0]
    assert [b["model"] for b in rf._baselines(res, own, "OurNet")] == ["Old"]
    t = {"id": "T2", "caption": "Table 2: Accuracy (%) on Bench-A", "header_rows": 1,
         "grid": [["Method", "Accuracy"], ["Results", ""], ["Base", "80.0"], ["Backbone-Y", "-"], ["OurNet", "85.0"],
                  ["Results", ""], ["Late", "99.0"]]}
    blocks = pr.row_blocks(t)
    assert blocks[2][0] != blocks[4][0] and blocks[2][0] != blocks[6][0] and blocks[2][1] == blocks[6][1] == "Results"
    # 떨어져 되풀이되는 같은 이름 구획은 다른 구획이다 — 이름으로 비교하면 Base 가 비교 대상으로 들어온다.
    repeated = {**t, "grid": [["Method", "Accuracy"], ["Results", ""], ["Base", "80.0"], ["Ablation", ""], ["X", "70.0"],
                              ["Results", ""], ["OurNet", "85.0"]]}
    res = pr.table_results([repeated], method="OurNet", benchmarks=["Bench-A"])
    assert rf._baselines(res, [r for r in res if r["own"]][0], "OurNet") == []
    res = pr.table_results([t], method="OurNet", benchmarks=["Bench-A"])
    own = [r for r in res if r["own"]][0]
    assert rf._baselines(res, own, "OurNet") == []
    row = lambda model, own, col: {"model": model, "own": own, "locator": f"T3#0:r2c{col}", "value": 70.0 + col, "text": f"{70 + col}.0",
                                   "bench_key": "b", "metric_key": "m", "orientation": "transposed"}
    results = [row("Base", False, 1), row("OurNet", True, 2)]
    assert [b["model"] for b in rf._baselines(results, {**results[1], "direction": "higher"}, "OurNet")] == ["Base"]
