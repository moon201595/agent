"""⑤⑨ 대표값을 저자 문장으로 되살리고, 다른 구획 결과는 참고로만 싣는다(2026-10-13, HMA 2610.13494 재생).

HMA 표 2 의 핵심 열("Overall Metrics (%) / All (75)")은 머리에 지표 낱말이 없어 버려졌고, 왼쪽 부분집합 열(Lite 22과제)이 대표값이 됐다.
같은 구획에는 "Avg. of Single" 하나뿐이라 단일 에이전트·오픈소스 결과가 카드에서 안 보였다. 값 칸에 공백("80.3 ± 1.1")이 있어 카드가 표로도 안 그려졌다.
표·문장은 합성이다(원문 문장·수치를 쓰지 않는다).
"""
import digest
import paper_observations as po
import performance_results as pr
import research_frontier as rf
import saved_digest

CONTEXT = "Across 30 tasks in Toy-bench, NewMethod reaches a medal rate of 66.5%, above all single runs."


def _table(**over):
    grid = [["Method", "Medal Rate (%)", "Medal Rate (%)", "Overall (%)", "Overall (%)"],
            ["", "Easy (10)", "Hard (20)", "All (30)", "Gold"],
            ["Single baselines", "Single baselines", "Single baselines", "Single baselines", "Single baselines"],
            ["Solo-A", "70.0 \\pm 1.0", "40.0", "52.4 \\pm 1.6", "30.0"],
            ["Solo-B", "65.0", "45.0", "50.1", "28.0"],
            ["Open baselines", "Open baselines", "Open baselines", "Open baselines", "Open baselines"],
            ["Flow-Y", "60.0", "39.0", "49.0", "27.0"],
            ["Flow-X", "68.0", "41.0", "50.2 \\pm 1.2", "29.0"],
            ["NewMethod (Ours)", "NewMethod (Ours)", "NewMethod (Ours)", "NewMethod (Ours)", "NewMethod (Ours)"],
            ["Pair A-B", "Pair A-B", "Pair A-B", "Pair A-B", "Pair A-B"],
            ["Avg. of Solo", "67.5 \\pm 1.1", "42.5", "51.2 \\pm 1.0", "29.0"],
            ["NewMethod (ours)", "72.0 +4.5", "55.0", "66.5 +15.3", "35.0"],
            ["Pair C-D", "Pair C-D", "Pair C-D", "Pair C-D", "Pair C-D"],
            ["Avg. of Solo", "60.0", "35.0", "45.0", "20.0"],
            ["NewMethod (ours)", "64.0", "41.0", "50.0", "25.0"]]
    t = {"id": "S4.T2", "caption": "Table 2: Results on Toy-bench.", "grid": grid, "header_rows": 2}
    t.update(over)
    return t


def _run(table=None, context=CONTEXT, method="NewMethod"):
    results = pr.table_results([table or _table()], method=method, benchmarks=["Toy-bench", "Other-bench"], context=context)
    main = rf._main_results([r for r in results if r["own"] and r["direction"]], [])
    return results, main


def test_author_sentence_names_one_unlabeled_column_and_makes_it_the_main_result():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 저자가 지표 이름과 함께 말한 값의 열을 버리거나(옛 동작 — 왼쪽 부분집합 열 72.0 이 대표값),
    그 지표 이름을 이 논문 칸에만 붙여 같은 열의 기준선과 지표 키가 갈리거나, 지표 없는 다른 열(Gold)까지 지표로 읽거나, 근거 문장을 남기지 않으면 실패한다."""
    results, main = _run()
    assert [(m["model"], m["text"], m["metric"]) for m in main] == [("NewMethod (ours)", "66.5 +15.3", "medal rate / Overall (%) / All (30)")]
    assert main[0]["metric_source"] == "author_text" and "66.5%" in main[0]["metric_evidence"] and main[0]["author_value"] is True
    column = [r for r in results if r["locator"].endswith("c3")]
    assert len(column) == 8 and len({r["metric_key"] for r in column}) == 1          # 열 전체 — 기준선도 같은 지표
    assert [r["author_value"] for r in column if r["own"]] == [True, False]             # 그 값을 말한 칸만 대표값 후보로 앞세운다
    assert not [r for r in results if r["locator"].endswith("c4")]                      # Gold 열은 여전히 지표가 아니다
    # 같은 값이 지표 이름 있는 열(Easy)에도 있으면 저자 문장이 어느 칸을 말하는지 모른다 — 되살리지 않는다(Codex 검토 P2: 지표 열을 빼고 세면
    # 같은 값의 엉뚱한 열을 고른다).
    easy = _table()
    easy["grid"][1] = ["", "Easy", "Hard (20)", "All (30)", "Gold"]                    # 표본 수 검사에 먼저 걸리지 않게
    easy["grid"][11] = ["NewMethod (ours)", "66.5", "55.0", "66.5 +15.3", "35.0"]
    got = [r for r in pr.table_results([easy], method="NewMethod", benchmarks=["Toy-bench"], context=CONTEXT) if r.get("metric_source")]
    assert got == []
    assert [b["model"] for b in rf._baselines(results, main[0], "NewMethod")] == ["Avg. of Solo"]


def test_author_sentence_recovery_rejects_ambiguous_or_mismatched_evidence():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 과제 수가 다른 문장, 방법 이름 없는 문장, 벤치마크가 둘인 문장, 표에 없는 값, 지표 없는 열 두 곳에
    같은 값이 있는 표에서도 열 이름을 붙이면 실패한다(그때는 옛 동작 — 지표 열만 읽는다)."""
    def recovered(**kw):
        results, _ = _run(**kw)
        return [r for r in results if r.get("metric_source")]
    assert recovered()                                                                  # 대조군
    assert not recovered(context=CONTEXT.replace("30 tasks", "25 tasks"))
    assert not recovered(context=CONTEXT.replace("NewMethod", "the system"))
    assert not recovered(context=CONTEXT.replace("in Toy-bench", "in Toy-bench and Other-bench"))
    assert not recovered(context=CONTEXT.replace("66.5%", "66.0%"))
    twin = _table()
    twin["grid"] = [row[:4] + ([row[3]] if i >= 2 and row[0] == "NewMethod (ours)" else row[4:]) for i, row in enumerate(twin["grid"])]
    assert not recovered(table=twin)


def test_reference_rows_come_only_from_blocks_without_this_papers_row():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 이 논문 행이 있는 다른 구획(Pair C-D — 이 논문의 다른 설정)의 값을 참고로 싣거나,
    자기 행의 값이 결측이라 추출 결과에 없을 때 그 구획을 "자기 행 없음" 으로 보거나, 이름만 있는 자기 구획 머리 때문에 앞 구획을 빼거나, 구획마다 가장 좋은 값 하나가 아니거나(Flow-Y 49.0 이 먼저 나와도 Flow-X 50.2),
    참고 결과를 기준선(peers·baselines)에 섞으면 실패한다."""
    t = _table()
    results, main = _run(t)
    refs = rf._references(results, main[0], "NewMethod", [t])
    assert [(r["model"], r["text"], r["block"]) for r in refs] == [("Solo-A", "52.4 \\pm 1.6", "Single baselines"),
                                                                    ("Flow-X", "50.2 \\pm 1.2", "Open baselines")]
    assert all(r["model"] != "Avg. of Solo" for r in refs)                              # Pair C-D 는 자기 행이 있다
    # 이름만 있는 구획 머리 "NewMethod (Ours)"(HMA 표의 꼴)는 뒤 구획의 시작이다 — 앞 구획(Open baselines)을 자기 구획으로 치면 Flow-X 를 잃는다.
    assert t["grid"][8][0] == "NewMethod (Ours)"
    # 자기 행 값이 결측("–")이라 추출되지 않아도 원본 행 이름으로 그 구획을 뺀다(외부 검토 지적).
    t2 = _table()
    t2["grid"][14] = ["NewMethod (ours)", "–", "–", "–", "–"]
    r2, m2 = _run(t2)
    assert all(r["block"] != "Pair C-D" for r in rf._references(r2, m2[0], "NewMethod", [t2]))
    # 구획이 없는 표·전치 표는 참고 결과가 없다.
    assert rf._references(results, {**main[0], "block_id": ""}, "NewMethod", [t]) == []
    assert rf._references(results, {**main[0], "orientation": "transposed"}, "NewMethod", [t]) == []


def _main_with_refs():
    t = _table()
    results, main = _run(t)
    m = main[0]
    m["peers"] = rf._baselines(results, m, "NewMethod", limit=None)
    m["baselines"] = m["peers"][:3]
    m["references"] = rf._references(results, m, "NewMethod", [t])
    m["compare"] = {"status": "none"}
    return m


def test_card_shows_references_without_a_difference_sentence_and_renders_as_table_after_restore():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 참고 행에 "평가 조건 미검증 · 직접 우열 비교 불가" 를 안 붙이거나, 참고 결과와의 차이 문장을 쓰거나,
    공백 든 값("51.2 ± 1.0")이나 새 행 종류 때문에 카드가 표가 아닌 줄 나열로 그려지거나, 저장 평문에서 되살린 메일이 같은 표가 아니면 실패한다."""
    m = _main_with_refs()
    heading, rows = po.sections({"_frontier": {"main": [m], "external": {"status": "done", "competitors": []}}})[0]
    texts = [t for t, _ in rows]
    assert texts[1:5] == ["이 논문 · NewMethod (ours) · 66.5 (+15.3) · 표 2 Pair A-B 구획 · 괄호는 Avg. of Solo 51.2 ± 1.0 대비 증감", "같은 표 · Avg. of Solo · 51.2 ± 1.0",
                         "다른 구획 참고 · Solo-A · 52.4 ± 1.6 · 표 2 Single baselines 구획 · 평가 조건 미검증 · 직접 우열 비교 불가",
                         "다른 구획 참고 · Flow-X · 50.2 ± 1.2 · 표 2 Open baselines 구획 · 평가 조건 미검증 · 직접 우열 비교 불가"]
    sentences = [t for t in texts if t.startswith(("→ ", po.LEAD_PREFIX)) and "높다" in t]
    # 열 머리 "Overall (%)" 라 단위가 확인된다 — 값에 %, 차이에 %p. 오차 꼬리 앞에 % 를 붙인다("51.2% ± 1.0").
    assert sentences == ["요약: 표 2 Pair A-B 구획에서 NewMethod (ours) 66.5%은(는) Avg. of Solo 51.2% ± 1.0보다 15.3%p 높다(더 좋음)."]
    p = {"arxiv_id": "2610.00001", "title": "Study", "link": "https://arxiv.org/abs/2610.00001", "deep_status": "abstract_only",
         "abstract_brief": "요지", "_frontier": {"main": [m]}, "_score": {}}
    scan = {"papers": [p], "total_found": 1}
    plain = digest.generate_digest(scan, "팀")
    html = digest.generate_digest_html(scan, "팀")
    restored = saved_digest.render_html(plain, "팀", [{"title": "Study", "link": p["link"]}])
    start = html.index('<div style="font-size:13.5px;font-weight:700;color:#0B5F68;">' + heading)
    box = html[start:html.index("</table>", start)]
    assert "<table" in box and ">다른 구획 참고</td>" in box
    for lead, tail in (("66.5", "(+15.3)"), ("51.2", "± 1.0"), ("52.4", "± 1.6")):                 # 앞 수치와 꼬리는 다른 칸 — 앞 수치끼리 세로로 맞는다
        assert f">{lead}</td><td " in box and f">{tail}</td>" in box
    assert box in restored


def test_comparison_row_grammar_keeps_old_rows_and_accepts_spaced_values():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 값 칸을 다시 공백 없는 덩어리로 좁히거나, 옛 행(출처 칸 있음·없음)을 다르게 나누면 실패한다."""
    match = lambda s: digest._COMPARISON_ROW_RE.fullmatch(s).groups()
    assert match("같은 표 · Avg. of Single · 80.3 ± 1.1") == ("같은 표", "Avg. of Single", "80.3 ± 1.1", None)
    assert match("이 논문 · HMA (ours) · 78.2 +8.0 · 표 2 X 구획") == ("이 논문", "HMA (ours)", "78.2 +8.0", "표 2 X 구획")
    assert match("같은 표 · ReAct · 62.0 · 인용 12,411회") == ("같은 표", "ReAct", "62.0", "인용 12,411회")
    assert match("외부 논문 · GaussVLA · 100 · Table 2 열 \"SR\"") == ("외부 논문", "GaussVLA", "100", "Table 2 열 \"SR\"")


# ── 2026-10-14 Codex 읽기 전용 검토(P2 9) 회귀 ─────────────────────────────────────────────────────────

def _simple(rows, header=("Method", "Score (%)", "Extra (%)"), caption="Table 3: Results on Toy-bench."):
    return {"id": "S4.T3", "caption": caption, "grid": [list(header), *[list(r) for r in rows]], "header_rows": 1}


def _recovered(table, context, method="NewMethod"):
    return [r for r in pr.table_results([table], method=method, benchmarks=["Toy-bench", "Other-bench"], context=context) if r.get("metric_source")]


def test_author_value_must_belong_to_this_method_and_be_one_whole_number_in_one_cell():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 값 앞에 비교·관계절이 있어 다른 방법의 값일 수 있는 문장을 받거나("…outperforms Rival, which achieves"),
    "66.5e-1" 을 66.5 로 읽거나, 다른 설정의 자기 행 두 칸에 같은 값이 있는데 하나를 고르거나, 시간 열·캡션 지표와 충돌하는 열·백분율 표시 없는 열에
    지표를 붙이면 실패한다. 대조군(정상 문장·정상 표)은 되살린다."""
    good = _simple([["Rival", "60.0", "50.0"], ["NewMethod", "70.0", "66.5"]], header=("Method", "Score (%)", "Overall (%)"))
    ok = "NewMethod reaches an accuracy of 66.5% on Toy-bench."
    assert [r["metric"] for r in _recovered(good, ok) if r["own"]] == ["accuracy / Overall (%)"]               # 대조군
    assert not _recovered(good, "NewMethod outperforms Rival, which achieves an accuracy of 66.5% on Toy-bench.")
    assert not _recovered(good, "On Toy-bench, Rival is compared with NewMethod at an accuracy of 66.5%.")
    assert not _recovered(good, "NewMethod and Rival reach an accuracy of 66.5% on Toy-bench.")
    assert not _recovered(good, "NewMethod lags behind Rival, at an accuracy of 66.5% on Toy-bench.")
    # 값이 이미 지표 이름 있는 열에만 있으면 그 열은 이미 읽힌다 — 다른 지표 이름으로 덮지 않는다.
    assert not _recovered(_simple([["NewMethod", "66.5", "70.0"]], header=("Method", "Success rate (%)", "Overall (%)")), ok)
    assert not _recovered(_simple([["NewMethod", "70.0", "66.5e-1"]], header=("Method", "Score (%)", "Overall (%)")), ok)
    two = _simple([["NewMethod full", "70.0", "66.5"], ["NewMethod small", "61.0", "66.5"]], header=("Method", "Score (%)", "Overall (%)"))
    assert not _recovered(two, ok)
    assert not _recovered(_simple([["NewMethod", "70.0", "66.5"]], header=("Method", "Score (%)", "Time (%)")), ok)
    assert not _recovered(_simple([["NewMethod", "70.0", "66.5"]], header=("Method", "Score", "Overall")), ok)       # 백분율 표시 없음
    assert not _recovered(_simple([["NewMethod", "70.0", "66.5"]], header=("Method", "Score (%)", "Overall (%)"),
                                  caption="Table 3: Success rate on Toy-bench."), ok)                                   # 캡션 지표와 충돌


def test_author_claims_are_deduplicated_and_capped(monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 같은 저자 문장이 수천 번 되풀이된 비신뢰 원문에서 주장마다 표 전체를 다시 훑거나(호출 수가 주장 수에
    비례), 문장 분리를 표마다 다시 하면 실패한다."""
    import sentence_grounding
    calls = {"segment": 0}
    real = sentence_grounding.segment_sentences
    def counting(text):
        calls["segment"] += 1
        return real(text)
    monkeypatch.setattr(sentence_grounding, "segment_sentences", counting)
    pr._sentences.cache_clear()
    tables = [_simple([["NewMethod", "70.0", "66.5"]], header=("Method", "Score (%)", "Overall (%)"), caption=f"Table {i}: Results.") for i in range(1, 6)]
    context = " ".join(f"NewMethod reaches an accuracy of {50 + i % 40}.5% on Toy-bench. We report results on Toy-bench in Table {1 + i % 5}."
                       for i in range(3000))
    looked = []
    real_lead = pr._lead_number
    monkeypatch.setattr(pr, "_lead_number", lambda text: looked.append(text) or real_lead(text))
    pr._author_metric_columns(tables, method="NewMethod", benchmarks=["Toy-bench"], context=context)
    assert calls["segment"] == 1
    assert 0 < len(looked) <= pr.MAX_AUTHOR_CLAIMS * 10                          # 주장 상한 × 이 논문 칸 10개
    claims = {(m["v1"]) for s in real(context) for m in pr._AUTHOR_VALUE_RE.finditer(s)}
    assert len(claims) == 40 and pr.MAX_AUTHOR_CLAIMS < len(claims)                  # 상한이 실제로 걸리는 입력이다


def test_table_reference_is_read_per_clause():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 한 문장의 다른 절(";" 뒤)에서 부른 표에 앞 절의 벤치마크를 잇거나, 한 절이 표 둘을 부를 때 잇거나,
    정상 문장에서 잇지 못하면 실패한다."""
    ref = lambda ctx, cap: pr._referenced_benchmark(ctx, cap, ["Toy-bench"])
    assert ref("We report accuracy on Toy-bench in Table 8.", "Table 8: Comparison.") == "Toy-bench"                      # 대조군
    assert ref("We report accuracy on Toy-bench in Table 1; Table 8 lists the training configuration.", "Table 8: Comparison.") == ""
    assert ref("We report accuracy on Toy-bench in Table 1 and Table 8.", "Table 8: Comparison.") == ""
    assert ref("We evaluate on Toy-bench; Table 8 lists the training configuration.", "Table 8: Comparison.") == ""


def test_blank_own_row_still_marks_its_block_and_spanned_header_does_not():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 칸이 모두 빈 자기 행(빈 `<td>`)을 구획 머리로만 보고 그 앞 구획의 기준선을 "다른 구획 참고" 로 싣거나,
    칸 전체를 같은 이름으로 채운 병합 머리 때문에 앞 구획까지 빼면 실패한다."""
    t = _table()
    results, main = _run(t)
    blank = _table()
    blank["grid"][7:7] = [["NewMethod (ours)", "", "", "", ""]]                    # Open baselines 구획 안의 빈 자기 행
    r2, m2 = _run(blank)
    assert [r["model"] for r in rf._references(r2, m2[0], "NewMethod", [blank])] == ["Solo-A"]
    assert "Flow-X" in [r["model"] for r in rf._references(results, main[0], "NewMethod", [t])]   # 병합 머리 "NewMethod (Ours)" 는 앞 구획을 안 뺀다


def test_comparison_rows_need_a_numeric_value_cell():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 값 칸 문법을 아무 글이나 받게 넓혀 방법 이름 안의 " · " 를 값·출처로 잘못 나누거나,
    값이 아닌 줄을 표의 행으로 그리면 실패한다."""
    match = lambda s: (m := digest._COMPARISON_ROW_RE.fullmatch(s)) and m.groups()
    assert match("같은 표 · Alpha · Beta Model · 62.0") == ("같은 표", "Alpha · Beta Model", "62.0", None)
    assert match("같은 표 · ReAct ·  · 인용 12회") is None
    assert match("같은 표 · X · 71.0† · 표 1") == ("같은 표", "X", "71.0†", "표 1")
    assert match("같은 표 · X · 0.667 (0.186)") == ("같은 표", "X", "0.667 (0.186)", None)
