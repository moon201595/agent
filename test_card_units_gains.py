"""⑤⑨ 합성 표만으로 단위·반복 수·증감의 근거 경계를 검증한다."""
import pytest

import performance_results as pr


def _table(value="48.0 (+6.0)", header="success rate", caption="Bench-Z results.", rows=None):
    return {"id": "T1", "caption": caption, "header_rows": 1,
            "grid": [["Method", header]] + (rows or [["Base-A", "42.0"], ["Ours", value]])}


def _own(table):
    return next(r for r in pr.table_results([table], method="Novel", benchmarks=["Bench-Z"]) if r["own"])


@pytest.mark.parametrize("value,gain", [("48.0 (+6.0)", "+6.0"), ("48.0 (+6.0%)", "+6.0"),
                                       ("48.0(+6.0)", "+6.0"), ("48.0 +6.0", "+6.0"),
                                       ("36.0 (−6.0)", "-6.0"), ("36.0 (-6.0%)", "-6.0")])
def test_signed_gain_forms(value, gain):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 괄호·기존 공백·음수 증감 인식이나 기준 값 보존을 빼면 실패한다."""
    row = _own(_table(value))
    assert (row.get("gain"), row.get("gain_base_model"), row.get("gain_base_text")) == (gain, "Base-A", "42.0")


@pytest.mark.parametrize("value", ["48.0 (6.0)", "48.0 (+5.0)", "48.0 (+6.0", "48.0 +6.0)",
                                   "48.0 (±6.0)", "48.0 (+6.0) extra"])
def test_non_gain_or_malformed(value):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 무부호 분산·산수 불일치·깨진 괄호를 증감으로 받으면 실패한다."""
    row = _own(_table(value))
    assert not any(k.startswith("gain") for k in row)


def test_gain_base_boundaries():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 중복 기준·다른 구획을 받거나 +Ours 바로 위 기준을 무시하면 실패한다."""
    for rows in [
        [["Base-A", "42.0"], ["Base-B", "42.0"], ["Ours", "48.0 (+6.0)"]],
        [["Base-A", "42.0"], ["Group-X", "Group-X"], ["Ours", "48.0 (+6.0)"]],
        [["Base-A", "42.0"], ["Base-B", "40.0"], ["+Ours", "48.0 (+6.0)"]],
    ]:
        assert "gain" not in _own(_table(rows=rows))
    row = _own(_table(rows=[["Base-A", "42.0"], ["Base-B", "42.0"], ["+Ours", "48.0 (+6.0)"]]))
    assert row.get("gain_base_model") == "Base-B"
    assert _own(_table("48.04 (+6.0)"))["gain"] == "+6.0"
    assert "gain" not in _own(_table("48.06 (+6.0)"))


@pytest.mark.parametrize("header,value,caption,unit", [
    ("success rate (%)", "48.0", "Bench-Z results.", "%"),
    ("success rate", "48.0%", "Bench-Z results.", "%"),
    ("success rate", "48.0 (+6.0%)", "Bench-Z results.", "%"),
    ("success rate", "48.0", "Bench-Z: average success rate (%), accuracy.", "%"),
    ("SR", "48.0", "Bench-Z: success rate (%).", "%"),
    ("accuracy", "48.0", "Bench-Z: success rate (%), accuracy.", None),
    ("success rate", "48.0", "Bench-Z: success rate and accuracy (%).", None),
    ("success rate", "48.0", "Bench-Z: success rate in percent.", None),
    ("success rate", "48.0", "Bench-Z results.", None),
])
def test_unit_direct_evidence(header, value, caption, unit):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 단위 근거 세 경로를 빼거나 다른 지표의 %를 전파하면 실패한다."""
    row = _own(_table(value, header, caption))
    assert row.get("unit") == unit
    if unit is None:
        assert "unit" not in row


def test_unit_column_path_and_score_separation():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 상위 열 머리의 %를 놓치거나 점수 열에 성공률 단위를 붙이면 실패한다."""
    t = {"id": "T2", "caption": "Bench-Z: average score and success rate (%).", "header_rows": 2,
         "grid": [["Method", "Bench-Z", "Bench-Z", "Bench-Z (%)"],
                  ["Method", "average score", "success rate", "accuracy"],
                  ["Ours", "52.0", "48.0", "44.0"]]}
    rows = pr.table_results([t], method="Novel", benchmarks=["Bench-Z"])
    assert {r["locator"] for r in rows} == {"T2#0:r2c2", "T2#0:r2c3"}  # 점수 열은 기존 추출기가 지원하지 않는다.
    assert next(r for r in rows if r["locator"].endswith("c2"))["unit"] == "%"
    assert next(r for r in rows if r["locator"].endswith("c3"))["unit"] == "%"


@pytest.mark.parametrize("phrase,count", [
    ("averaged over 7 random seeds", 7), ("averaged over 9 runs", 9),
    ("AVERAGED OVER 11 trials", 11), ("averaged over 4 random trials", 4),
    ("averaged over seven seeds", None), ("averaged over 2.5 runs", None),
    ("averaged over 0 seeds", None), ("averaged over -2 runs", None),
    ("averaged over 7 seeds; averaged over 9 runs", None),
    ("averaged over 7 seeds; averaged over many trials", None),
    ("7 random seeds", None), ("averaged over 7 datasets", None),
])
def test_averaged_over_unique_count(phrase, count):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 반복 수를 잃거나 복수·비정수·문자·잘못된 대상을 받으면 실패한다."""
    rows = pr.table_results([_table(caption="Bench-Z results (" + phrase + ").")],
                            method="Novel", benchmarks=["Bench-Z"])
    assert len(rows) == 2
    for row in rows:
        assert row.get("averaged_over") == count
        if count is None:
            assert "averaged_over" not in row
        else:
            assert type(row["averaged_over"]) is int


def test_metadata_survives_long_caption_and_transposed_table():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 잘린 캡션으로 단위를 찾거나 전치 표의 메타데이터 경로를 빼면 실패한다."""
    t = {"id": "T3", "caption": "Bench-Z benchmark. " + "Synthetic context. " * 12
         + "success rate (%), averaged over 8 runs.", "header_rows": 1,
         "grid": [["Method", "Base-A", "Ours"], ["Task 1", "42.0", "48.0"],
                  ["Average", "42.0", "48.0"]]}
    rows = pr.table_results([t], method="Novel", benchmarks=["Bench-Z"])
    assert len(rows) == 4
    assert all(r.get("unit") == "%" and r.get("averaged_over") == 8 for r in rows)
