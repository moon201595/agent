"""⑤⑨ 서식만 입힌 수식 칸·설정 표시 열·인용 붙은 방법 이름(2026-10-19 메일, 2610.22791 표 1 재생).

그날 카드는 값 칸 alttext "\\mathbf{71.6}{\\scriptstyle\\,\\bm{\\pm}\\,\\mathbf{0.9}}" 를 그대로 찍었고("무슨 스크립트"), 숫자로 시작하지 않는
그 칸을 행 이름이 삼켜 "HPO (ours) ✓ \\mathbf{71.6}…" 이 방법 이름이 됐다. 본문 "on Meta-Harness" 가 Meta-Harness 를 벤치마크 후보로 만들어
같은 구획의 가장 강한 비교 행이 버려졌다. 표·문장은 합성이다(원문 문장·수치를 쓰지 않는다).
"""
import arxiv_tables as at
import paper_observations as po
import performance_results as pr
import research_frontier as rf

BOLD = r"\mathbf{71.0}{\scriptstyle\,\bm{\pm}\,\mathbf{0.8}}"
PLAIN = r"62.0{\scriptstyle\,\pm\,1.1}"


def _cell(alt: str) -> str:
    return f'<td><math alttext="{alt}" display="inline"><semantics><mn>x</mn><annotation encoding="application/x-tex">{alt}</annotation></semantics></math></td>'


def _html() -> str:
    rows = [("Base", "✘", "✘", r"58.0{\scriptstyle\,\pm\,0.2}"),
            ("Setting A", None, None, None),
            ("Tune-X", "✘", "✓", PLAIN),
            ('Rival-Net <cite>(Kim et al., 2026)</cite>', "✓", "✘", r"68.5{\scriptstyle\,\pm\,1.9}"),
            ("NewMethod (ours)", "✓", "✓", BOLD)]
    body = []
    for name, a, b, v in rows:
        if a is None:
            body.append(f'<tr><td colspan="4">{name}</td></tr>')
        else:
            body.append(f"<tr><td>{name}</td><td>{a}</td><td>{b}</td>{_cell(v)}</tr>")
    return ('<html><body><figure class="ltx_table" id="S3.T1"><figcaption>Table 1: Pass@1 on Toy-Verified.</figcaption><table>'
            "<thead><tr><th>Method</th><th>Opt A</th><th>Opt B</th><th>Toy-Verified</th></tr></thead><tbody>"
            + "".join(body) + "</tbody></table></figure></body></html>")


def test_math_number_text_only_rewrites_formatted_numbers():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 굵게·글자 크기·좁은 띄어쓰기·묶음 괄호를 걷지 못해 "\\scriptstyle" 이 메일에 나가거나,
    수치가 아닌 수식("\\tau^{2}"·"x_{t}"·"\\frac{1}{2}")까지 바꿔 원문에 없는 글을 만들면 실패한다."""
    assert at.math_number_text(BOLD) == "71.0 ± 0.8"
    assert at.math_number_text(PLAIN) == "62.0 ± 1.1"
    assert at.math_number_text(r"\underline{65.0}") == "65.0"
    assert at.math_number_text(r"45.2\%") == "45.2%"
    for formula in (r"\tau^{2}", "x_{t}", r"\frac{1}{2}", r"\text{DAPO}", "12/20"):
        assert at.math_number_text(formula) is None
    # 저장된 옛 값(그날 DB 에 남은 alttext)도 카드에서 읽힌다.
    assert po.latex_display(BOLD) == "71.0 ± 0.8"
    assert po.latex_display(r"\tau^{2}") == "τ²"


def test_marks_and_math_cells_do_not_leak_into_row_labels():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 굵은 수식 값 칸이 수치로 안 읽혀 행 이름이 값을 삼키거나, 설정 표시(✓/✘)가 방법 이름에 붙으면 실패한다."""
    table = at.parse_tables(_html())[0]
    labels = [at.row_label(table, r) for r in range(table["header_rows"], len(table["grid"]))]
    assert labels == ["Base", "Setting A", "Tune-X", "Rival-Net (Kim et al., 2026)", "NewMethod (ours)"]
    assert table["grid"][-1][-1] == "71.0 ± 0.8"


def test_marks_stay_when_they_are_the_only_difference_between_rows():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 이름이 같은 절제 행("Ours ✓ ✘"·"Ours ✓ ✓")에서도 표시를 지워 두 행을 같은 이름으로 만들면 실패한다."""
    table = {"grid": [["Method", "A", "B", "Score"], ["Ours", "✓", "✘", "60.0"], ["Ours", "✓", "✓", "70.0"], ["Other", "✘", "✘", "50.0"]],
             "header_rows": 1}
    assert [at.row_label(table, r) for r in (1, 2, 3)] == ["Ours ✓ ✘", "Ours ✓ ✓", "Other"]


def test_cited_method_named_like_a_benchmark_candidate_stays_a_baseline():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 인용이 붙은 방법 이름("Rival-Net (Kim et al., 2026)")을 본문 문구 때문에 벤치마크로 보고 그 행을
    버려, 요약이 가장 강한 비교 방법 대신 약한 행(Tune-X)과 견주면 실패한다. 인용 없는 행 이름 속 벤치마크(교차 데이터셋)는 그대로 버린다."""
    table = at.parse_tables(_html())[0]
    results = pr.table_results([table], method="NewMethod", benchmarks=["Toy-Verified", "Rival-Net"],
                               context="We report pass@1 on Toy-Verified, and improvements on Rival-Net.")
    models = {r["model"] for r in results}
    assert "Rival-Net (Kim et al., 2026)" in models
    main = rf._main_results([r for r in results if r["own"] and r["direction"]], [])[0]
    main["peers"] = rf._baselines(results, main, "NewMethod", limit=None)
    main["baselines"], main["compare"] = main["peers"][:3], {"status": "none"}
    main["references"] = rf._references(results, main, "NewMethod", [table])
    _, rows = po.sections({"_frontier": {"main": [main], "external": {"status": "done", "competitors": []}}})[0]
    texts = [t for t, _ in rows]
    assert texts[0].startswith(po.LEAD_PREFIX) and "Rival-Net (Kim et al., 2026) 68.5 ± 1.9보다 2.5 높다" in texts[0]
    assert not any("scriptstyle" in t or "\\" in t for t in texts)
    # 구획 머리 위의 기준 행은 구획 이름이 없다 — 빈 이름("표 1  구획")을 찍지 않는다.
    assert "다른 구획 참고 · Base · 58.0 ± 0.2 · 표 1 구획 밖 행 · 평가 조건 미검증 · 직접 우열 비교 불가" in texts
    assert pr._cited_as_benchmark("Rival-Net", "Rival-Net → Toy-Verified")
    assert not pr._cited_as_benchmark("Rival-Net", "Rival-Net [12]")


def test_review_followups_error_percent_header_math_and_cited_datasets():
    """이 테스트가 무엇을 망가뜨리면 실패하는가(Codex 검토 P2 셋): 오차 뒤 % 를 버려 단위를 잃거나, 머리 칸의 서식 숫자("\\mathbf{1}"·"\\mathbf{5}")를
    수치로 보고 머리 경계를 앞당기거나, 인용만 보고 전이 행·행에 적힌 인용 붙은 데이터셋을 방법 이름으로 보면 실패한다."""
    assert at.math_number_text(r"71.0\pm0.8\%") == "71.0 ± 0.8%"
    html = ('<html><body><figure class="ltx_table" id="S1.T1"><table><tbody>'
            '<tr><td>Method</td><td colspan="2">Accuracy</td></tr>'
            f'<tr><td></td>{_cell(r"\mathbf{1}")}{_cell(r"\mathbf{5}")}</tr>'
            '<tr><td>Ours</td><td>61.0</td><td>70.0</td></tr></tbody></table></figure></body></html>')
    table = at.parse_tables(html)[0]
    assert table["header_rows"] == 2 and at.column_path(table, 2) == ["Accuracy", "5"]
    assert pr._cited_as_benchmark("Rival-Net", "Ours Rival-Net [12] → Toy-Verified")
    assert pr._cited_as_benchmark("Rival-Net", "Rival-Net [12] Ours")
    assert not pr._cited_as_benchmark("Rival-Net", "Rival-Net (Kim et al., 2026)")


def test_mark_rule_on_a_large_table_counts_names_once():
    """이 테스트가 무엇을 망가뜨리면 실패하는가(Codex 검토 P3): 표시가 있는 행마다 표 전체 이름을 다시 세어 2,000행 표에서 수백만 번 도는 것으로 돌아가면 실패한다."""
    import time
    grid = [["Method", "Opt", "Score"]] + [[f"Model-{i}", "✓", f"{i}.0"] for i in range(1999)]
    table = {"grid": grid, "header_rows": 1}
    started = time.monotonic()
    labels = {c["row"]: c["row_label"] for c in at.find_cells(table, lambda _l: True, lambda _p: True)}
    assert (labels[1], labels[2]) == ("Model-0", "Model-1") and len(labels) == 1999
    assert time.monotonic() - started < 5
