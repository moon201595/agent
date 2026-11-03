"""⑤⑨ 회색 아래첨자 표준편차·계산 행·두 겹 구획 머리(2026-11-03 메일, 2610.41378 표 1 구조 재생).

그날 카드는 값 칸 alttext "43.17_{{\\color[rgb]{0.45,0.45,0.45}\\scriptscriptstyle 2.18}}" 를 그대로 찍었고, 숫자로 시작하지 않는 그 칸을 행 이름이
삼켰으며, 값 추출은 글자색 성분 0.45 를 성능값으로 저장했다. "Improvement"(우리 − 가장 강한 비교 방법) 행이 비교 방법으로 실렸고,
"모델 이름 | 설정 이름(나머지 전폭)" 구획 머리를 표 머리로 세어 모든 열 이름에 "Single agent" 가 붙고 작은 모델의 값이 큰 모델 결과 옆에 섞였다.
표는 같은 구조의 합성이다(원문 수치를 쓰지 않는다).
"""
import arxiv_tables as at
import paper_observations as po
import performance_results as pr
import research_frontier as rf


def _alt(value: str, sd: str) -> str:
    return f"{value}_{{{{\\color[rgb]{{0.45,0.45,0.45}}\\scriptscriptstyle {sd}}}}}"


def _cell(value: str, sd: str) -> str:
    alt = _alt(value, sd)
    return (f'<td><math alttext="{alt}" display="inline"><semantics><mn>x</mn>'
            f'<annotation encoding="application/x-tex">{alt}</annotation></semantics></math></td>')


def _html() -> str:
    def body(name: str, a: tuple[str, str], b: tuple[str, str]) -> str:
        return f"<tr><th></th><td>{name}</td>{_cell(*a)}{_cell(*b)}</tr>"

    def group(model: str, name: str) -> str:
        return f'<tr><th>{model}</th><td colspan="3">{name}</td></tr>'

    rows = [group("Big-LM", "Single agent"), body("Base", ("40.00", "1.00"), ("45.00", "1.00")),
            group("", "Parallel agents"), body("Rival", ("50.00", "2.00"), ("52.00", "2.00")),
            body("NewMethod", ("55.00", "1.50"), ("57.00", "1.50")),
            "<tr><th></th><td>Improvement</td><td>+5.00</td><td>+5.00</td></tr>",
            group("Small-<br>LM", "Single agent"), body("Base", ("20.00", "1.00"), ("22.00", "1.00")),
            group("", "Parallel agents"), body("Rival", ("30.00", "1.00"), ("33.00", "1.00")),
            body("NewMethod", ("28.00", "1.00"), ("31.00", "1.00")),
            "<tr><th></th><td>Improvement</td><td>-2.00</td><td>-2.00</td></tr>"]
    return ('<html><body><figure class="ltx_table" id="S4.T1"><figcaption>Table 1: Success rate on Toy-Shop. Entries show means, with standard '
            "deviations over three seeds in subscripts. Improvement is NewMethod minus the strongest baseline.</figcaption><table>"
            '<tr><th></th><td></td><td colspan="2">Toy-Shop</td></tr>'
            "<tr><th>Model</th><td>Method</td><td>SR</td><td>Oracle@3 SR</td></tr>" + "".join(rows) + "</table></figure></body></html>")


def _results():
    table = at.parse_tables(_html())[0]
    return table, pr.table_results([table], method="NewMethod", benchmarks=["Toy-Shop"],
                                   context="We evaluate NewMethod on Toy-Shop and report SR in Table 1.")


def test_colored_subscript_is_read_as_mean_and_error():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 글자색(\\color·\\textcolor)을 걷지 않아 alttext 가 메일에 나가거나, 수치 아래첨자를 오차로 읽지 않거나,
    수치가 아닌 아래첨자(x_{t}·F_{1})까지 바꿔 원문에 없는 글을 만들면 실패한다."""
    assert at.math_number_text(_alt("43.17", "2.18")) == "43.17 ± 2.18"
    assert at.math_number_text(r"\mathbf{45.50}_{{\color[rgb]{0.45,0.45,0.45}\scriptscriptstyle 1.73}}") == "45.50 ± 1.73"
    assert at.math_number_text(r"\textcolor{gray}{2.5}") == "2.5"
    for formula in ("x_{t}", "F_{1}", r"\tau^{2}"):
        assert at.math_number_text(formula) is None
    assert po.latex_display(_alt("43.17", "2.18")) == "43.17 ± 2.18"          # DB 에 남은 옛 값도 카드에서 읽힌다


def test_values_are_means_not_color_components_and_improvement_is_not_a_method():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 글자색 성분 0.45 를 성능값으로 저장하거나, 행 이름이 값 칸을 삼키거나, "Improvement" 계산 행을
    방법으로 남기면 실패한다."""
    _, results = _results()
    assert results and all(r["value"] != 0.45 for r in results)
    assert not any("Improvement" in r["model"] or "color" in r["model"] + r["text"] for r in results)
    own = sorted((r["value"], r["text"]) for r in results if r["own"] and r["metric"] == "SR")
    assert own == [(28.0, "28.00 ± 1.00"), (55.0, "55.00 ± 1.50")]
    assert pr._DERIVED_ROW_RE.fullmatch("Rel. Improvement (%)") and not pr._DERIVED_ROW_RE.fullmatch("GAIN")


def test_model_and_setting_group_rows_split_blocks_not_headers():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: "모델 | 설정(나머지 전폭)" 행을 표 머리로 세어 열 이름에 "Single agent" 가 붙거나, 바깥 모델 이름을
    잇지 않아 작은 모델의 자기 값(28.00)이 큰 모델 구획의 비교에 섞이거나, 다른 구획의 Base 가 같은 구획 비교 방법이 되면 실패한다."""
    table, results = _results()
    assert table["header_rows"] == 2 and table["group_rows"] == [2, 4, 8, 10]
    assert sorted({label for _, label in pr.row_blocks(table).values()}) == [
        "Big-LM · Parallel agents", "Big-LM · Single agent", "Small-LM · Parallel agents", "Small-LM · Single agent"]
    assert {r["metric"] for r in results} == {"SR", "Oracle@3 SR"}
    main = rf._main_results([r for r in results if r["own"] and r["direction"]], [])[0]
    assert main["value"] == 55.0 and main["block"] == "Big-LM · Parallel agents"
    assert [b["model"] for b in rf._baselines(results, main, "NewMethod", limit=None)] == ["Rival"]
    assert rf._own_siblings(results, main) == []


def _table(rows: str, head: str = "<tr><th>Method</th><td>Toy-Shop</td><td>Toy-Mart</td></tr>") -> dict:
    return at.parse_tables('<html><body><figure class="ltx_table" id="S1.T3"><figcaption>Table 3: Toy-Shop.</figcaption><table>'
                           + head + rows + "</table></figure></body></html>")[0]


def test_group_row_needs_numeric_row_below_to_leave_the_header():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 아래가 수치 행이 아닌데도("Setting | Accuracy (%)" 아래 "| SR | Oracle SR") 그 행을 본문 구획으로 끊어
    열 이름이 잘리면 실패한다 — 첫 칸에 글이 있고 아래 첫 칸이 빈 꼴이라 수치 조건만이 이 행을 머리로 남긴다."""
    table = _table('<tr><th>Setting</th><td colspan="2">Accuracy (%)</td></tr><tr><th></th><td>SR</td><td>Oracle SR</td></tr>'
                   "<tr><th></th><td>50.0</td><td>52.0</td></tr>")
    assert table["header_rows"] == 3


def test_metric_header_above_method_rows_is_not_a_group():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 첫 칸이 방법 이름인 표에서 "Method | Accuracy (%)(병합)" 지표 머리를, 아래가 수치라는 이유만으로
    본문 구획으로 끊어 열 경로에서 Accuracy 가 사라지면 실패한다(아래 첫 칸이 "Rival" — 같은 이름도 빈칸도 아니다)."""
    table = _table('<tr><th>Method</th><td colspan="2">Accuracy (%)</td></tr><tr><th>Rival</th><td>50.0</td><td>52.0</td></tr>')
    assert table["header_rows"] == 2 and at.column_path(table, 1) == ["Toy-Shop", "Accuracy (%)"]


def test_status_only_method_row_does_not_open_a_block():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 결과가 상태어뿐인 방법 행("FailedMethod | OOM(병합)")을 구획 머리로 보아 뒤의 이 논문 행과 앞의
    비교 방법(Rival)을 다른 구획으로 가르면 실패한다."""
    table = _table('<tr><th>Rival</th><td>50.0</td><td>52.0</td></tr><tr><th>FailedMethod</th><td colspan="2">OOM</td></tr>'
                   "<tr><th>NewMethod</th><td>55.0</td><td>57.0</td></tr>")
    blocks = pr.row_blocks(table)
    assert blocks[1] == blocks[3]


def test_missing_method_row_does_not_replace_the_outer_model_name():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 결측 방법 행("FailedBaseline | - | -")이 바깥 이름을 덮어써 다음 설정 구획이
    "FailedBaseline · Parallel agents" 가 되면 실패한다 — 바깥 이름은 명시적 구획 머리(모델 이름)에서만 바뀐다."""
    table = _table('<tr><th>Big-LM</th><td colspan="2">Single agent</td></tr><tr><th></th><td>50.0</td><td>52.0</td></tr>'
                   "<tr><th>FailedBaseline</th><td>-</td><td>-</td></tr>"
                   '<tr><th></th><td colspan="2">Parallel agents</td></tr><tr><th></th><td>55.0</td><td>57.0</td></tr>')
    assert pr.row_blocks(table)[len(table["grid"]) - 1][1] == "Big-LM · Parallel agents"


def test_plain_integer_subscript_is_not_turned_into_an_error_and_delta_ours_is_a_method():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 꾸밈 없는 정수 첨자("87.2_{1}" — 각주·순위일 수 있다)를 "± 1" 오차로 만들거나 본 값을 못 읽거나,
    이름이 Δ 인 이 논문 방법("Δ (Ours)")을 계산 행으로 빼면 실패한다."""
    assert at.math_number_text("87.2_{1}") == "87.2 ₁" and at.cell_number("87.2 ₁") == 87.2
    assert at.math_number_text(r"45_{\scriptstyle 2}") == "45 ± 2"
    assert not pr._DERIVED_ROW_RE.fullmatch("Δ (Ours)") and pr._DERIVED_ROW_RE.fullmatch(r"\Delta (TRACE - )")


def test_unit_row_with_empty_first_cell_stays_in_the_header():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: "(빈칸) | Accuracy (%)(전폭)" 단위 행 바로 아래가 수치 행일 때 그 행을 본문 구획으로 끊어
    지표 이름(Accuracy)이 열 경로에서 사라지면 실패한다 — 구획 머리는 첫 칸에 바깥 이름이 있을 때만이다."""
    html = ('<html><body><figure class="ltx_table" id="S1.T2"><figcaption>Table 2: Toy-Shop.</figcaption><table>'
            '<tr><th>Method</th><td>Toy-Shop</td><td>Toy-Mart</td></tr><tr><th></th><td colspan="2">Accuracy (%)</td></tr>'
            "<tr><th>Rival</th><td>50.0</td><td>52.0</td></tr><tr><th>NewMethod</th><td>55.0</td><td>57.0</td></tr></table></figure></body></html>")
    table = at.parse_tables(html)[0]
    assert table["header_rows"] == 2
    assert at.column_path(table, 1) == ["Toy-Shop", "Accuracy (%)"]
