"""④⑤ 실제 표 발췌로 자기 결과·애매한 결과의 경계를 고정한다."""
import os
from pathlib import Path

import arxiv_tables as at
import performance_results as pr
import research_frontier as rf

FIX = Path(os.environ.get("EXTRACTION_FIXTURES", str(Path(__file__).parent / "fixtures/arxiv_tables")))


def tables(aid):
    """재배포 권한이 없는 발췌(arXiv 기본 라이선스 — .gitignore)는 로컬에만 있다. 없으면 그 실제 값 테스트만 건너뛴다(2609.21424 와 같은 규칙)."""
    import pytest
    path = FIX / f"{aid}-excerpt.html"
    if not path.exists():
        pytest.skip(f"{aid} 발췌는 로컬 전용(재배포 권한)")
    return at.parse_tables(path.read_text())


# arXiv:2610.12914 본문 발췌 — arXiv 기본 라이선스라 표 발췌 파일은 로컬 전용이다(.gitignore). 이 두 문장은 테스트 입력으로만 둔다.
OGPO_CONTEXT = (
    "On LIBERO, OGPO and PPO are initialized from the same few-shot policy, obtained with limited downstream demonstrations. "
    "Starting from this backbone, OGPO improves the average success rate from 77.1% to 97.1%.")


def test_actual_implicit_metric_and_ambiguity():
    """본문 지표 보충을 빼거나 Spatial을 평균으로 읽거나 상충 지표를 허용하면 실패한다."""
    ts = tables("2610.12914")
    assert pr.table_results(ts, method="OGPO", benchmarks=["LIBERO"]) == []
    rows = pr.table_results(ts, method="OGPO", benchmarks=["LIBERO"], context=OGPO_CONTEXT)
    own = [r for r in rows if r["own"]]
    assert [(r["metric"], r["value"]) for r in own] == [("success rate / Avg.", 97.1)]
    assert own[0]["locator"].endswith("c6")
    mixed = [{**ts[0], "caption": "LIBERO success rate and accuracy."}]
    assert not pr.table_results(mixed, method="OGPO", benchmarks=["LIBERO"], context=OGPO_CONTEXT)
    assert len(pr.table_results(ts + mixed, method="OGPO", benchmarks=["LIBERO"], context=OGPO_CONTEXT)) == len(rows)
    assert not pr.table_results(ts, method="OGPO", benchmarks=["LIBERO"],
                                context=OGPO_CONTEXT + " We report accuracy on LIBERO.")


def test_actual_transposed_cell_and_baseline_axis():
    """축 교환·평균 우선·과제별 키·기준선의 같은 행/백본 제한을 망가뜨리면 실패한다."""
    ts = tables("2610.12861")
    rows = pr.table_results(ts, method="", benchmarks=["LIBERO-Core"])
    own = [r for r in rows if r["own"]]
    main = rf._main_results(own, [])[0]
    assert (main["benchmark"], main["metric"], main["value"]) == ("LIBERO-Core", "success rates / Average", 53.0)
    assert "MiniVLA" in main["model"] and "ECL(Ours)" in main["model"]
    assert {r["value"] for r in rf._baselines(rows, main)} == {57.2, 40.8, 44.8}
    assert len({r["metric_key"] for r in own}) == 2
    assert {r["value"] for r in own if "Task 0" in r["metric"]} == {80.0, 75.6}
    for row in ts[0]["grid"][:ts[0]["header_rows"]]:
        row[:] = [v.replace("ECL(Ours)", "ECL") for v in row]
    assert sum(r["own"] for r in pr.table_results(ts, method="ECL", benchmarks=["LIBERO-Core"])) == 4
    ts[0]["grid"][-1][0] = "Unknown category"
    assert not pr.table_results(ts, method="ECL", benchmarks=["LIBERO-Core"])


def test_actual_unnamed_task_stays_unstructured():
    """TAME-Nav 캡션을 벤치마크로 지어내거나 이름 없는 SR을 비교 키로 넣으면 실패한다."""
    assert not pr.table_results(tables("2610.12877"), method="TAME-Nav", benchmarks=[])


def test_actual_fraction_is_not_numerator():
    """Human2Dex의 19/20을 19점으로 읽는 기존 파서 결함을 복원하면 실패한다."""
    assert at.cell_number("19/20") is None
    assert not pr.table_results(tables("2610.13119"), method="Human2Dex", benchmarks=["Egg Carton", "Open Drawer", "Linker O6"])


def test_method_opening_and_observed_noise():
    """직접 제안 이름 보충·다중 이름 거부·실제 진단 잡음 필터를 제거하면 실패한다."""
    # arXiv:2610.13058·2610.12832 초록의 직접 제안 문구 발췌(라이선스 미확인).
    assert pr.method_name("Travel Back To Move Forward: Retrospective Exploration", "We introduce\nReTRAIL\n, a framework") == "ReTRAIL"
    assert pr.method_name("VLMs are Native Action Models", "In this work, we introduce EASG, an Explicit Action-Space Grounding framework") == "EASG"
    assert pr.method_name("Title", "We propose a framework") == ""
    assert pr.method_name("Title", "We propose ABC, a method. We introduce XYZ, a framework.") == ""
    # diag.json에 실제로 나온 후보들을 claims 입력으로 재생한다.
    noise = ["Neural Information Processing Systems", "Learning Representations", "Task Group 1", "Task Group 2", "Tasks 1", "CPU", "TRL", "Qwen2", "GPT-5", "HuggingFace Transformers"]
    assert pr.benchmark_candidates("", [{"benchmarks": noise + ["LIBERO"]}]) == ["LIBERO"]
    assert "LIBERO-Core" in pr.benchmark_candidates("Per-task success rates on the ten-task LIBERO-Core benchmark.")


def test_actual_caption_metric_and_unknown_columns():
    """캡션 지표 적용·설정별 키를 빼거나 미지원 ASR 열을 accuracy로 바꾸면 실패한다."""
    ts = tables("2610.13058")
    rows = pr.table_results(ts, method="ReTRAIL", benchmarks=["MineSweeper", "ALFWorld"])
    main = rf._main_results([r for r in rows if r["own"]], [])
    assert [(r["metric"], r["value"]) for r in main] == [("success rates / P@1", 58.8), ("success rates / P@1", 92.7)]
    ts = tables("2610.13105")
    rows = pr.table_results(ts, method="SOAP", benchmarks=["WW-AG", "WW-HC"])
    assert [(r["metric"], r["value"]) for r in rf._main_results([r for r in rows if r["own"]], [])] == [("accuracy", 47.62), ("accuracy", 34.48)]
    ts[0]["grid"][0][2] = "ASR"
    assert not any(r["locator"].endswith("c2") for r in pr.table_results(ts, method="SOAP", benchmarks=["WW-AG", "WW-HC"]))


def test_analyze_passes_context_to_extraction(monkeypatch, tmp_path):
    """추출기 단독만 고치고 운행 analyze에서 본문을 넘기지 않으면 실패한다."""
    import frontier_store as fs
    monkeypatch.setattr(rf, "_paper_row", lambda *a: (OGPO_CONTEXT, "OGPO: Title", "2026-10-12", "text"))
    recorded = []
    monkeypatch.setattr(fs, "record", lambda db, aid, rows, *a: recorded.extend(rows))
    monkeypatch.setattr(fs, "compare", lambda *a: {"status": "incomparable"})
    monkeypatch.setattr(rf, "_external", lambda *a, **k: None)
    papers = [{"arxiv_id": "2610.12914", "title": "OGPO: Title", "_sota_claims": [{"benchmarks": ["LIBERO"]}]}]
    rf.analyze(tmp_path / "unused.db", papers, tables_of=lambda _: tables("2610.12914"), signals_of=lambda _: {})
    assert papers[0]["_frontier"]["main"][0]["value"] == 97.1
    assert any(r["own"] for r in recorded)


def test_actual_metrics_in_separate_clauses():
    """MATH의 Solve Rate와 MBPP의 pass@1을 같은 지표로 합치면 실패한다."""
    # arXiv:2610.13064 평가 지표 문장 발췌. 제공 HTML의 라이선스 미확인.
    context = "For GSM8K, MATH and GAIA, we report the Solve Rate (%); for HumanEval and MBPP, we report pass@1."
    rows = pr.table_results(tables("2610.13064"), method="CB-Orchestrator", benchmarks=["MATH", "MBPP"], context=context)
    own = [r for r in rows if r["own"]]
    assert [(r["benchmark"], r["metric"], r["value"]) for r in own] == [("MATH", "Solve Rate", 53.87), ("MBPP", "pass@1", 84.05)]


def test_inferred_metric_is_not_spread_to_subtask_columns_without_an_average():
    """본문에서 보충한 지표는 평균 열·벤치마크 이름 열에만 붙인다(2026-10-12 Claude 검토). 평균 열이 있으면 아래 범주 열 규칙이 하위 과제 열을 따로 버려
    이 경계를 가리므로, 평균 열을 지운 실제 표로 본다. 무엇을 망가뜨리면 실패하는가: 하위 과제 열(Spatial·Object…)에 본문 지표를 씌워 읽는 것."""
    ts = tables("2610.12914")
    t = ts[0]
    avg = next(c for c in range(len(t["grid"][0])) if at.column_path(t, c)[-1].startswith("Avg"))
    no_avg = {**t, "grid": [row[:avg] + row[avg + 1:] for row in t["grid"]]}
    rows = pr.table_results([no_avg], method="OGPO", benchmarks=["LIBERO"], context=OGPO_CONTEXT)
    assert not [r for r in rows if r["own"]]
