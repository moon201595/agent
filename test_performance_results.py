"""④⑤ 성능 결과(performance_results, 2026-09-30) — 표 교차 셀 결과(수치 비교용)와 원문 문장 근거(표시용)."""
from pathlib import Path

import pytest

import arxiv_tables
import performance_results as pr
import sentence_grounding

FIX = Path(__file__).parent / "fixtures" / "arxiv_tables"


# 원문 픽스처 중 CC BY 가 아닌 것(2609.21424 arXiv 기본 라이선스 · 2609.18623 CC BY-NC-SA)은 저장소에 올리지 않는다(.gitignore).
# 그 파일이 있는 로컬에서만 실제 값 테스트가 돈다 — 구조 계약은 합성 픽스처로 항상 돈다.
needs_local = lambda aid: pytest.mark.skipif(not (FIX / f"{aid}.html").exists(), reason=f"원문 픽스처 {aid} 는 로컬 전용")


def _results(aid: str, method: str, benchmarks: list[str]) -> list[dict]:
    tables = arxiv_tables.parse_tables((FIX / f"{aid}.html").read_text(encoding="utf-8"))
    return pr.table_results(tables, method=method, benchmarks=benchmarks)


def test_table_results_read_own_row_by_benchmark_and_metric():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 이 논문 행(Ours)을 못 찾거나, O-AUROC 머리(`O-AUROC(\\uparrow)`)를 지표로 못 읽거나,
    방향 표식을 지표 이름에 남기거나, 벤치마크 열을 뒤바꾸면 실패한다. 값은 픽스처 표를 직접 보고 적었다(Codex 파서 보고와 교차 확인)."""
    own = {(r["benchmark"], r["metric"]): r for r in _results("2609.35059", "", ["Anomaly-ShapeNet", "Real3D-AD"]) if r["own"]}
    assert own[("Real3D-AD", "O-AUROC")]["value"] == 87.2
    assert own[("Real3D-AD", "P-AUROC")]["value"] == 91.3
    assert own[("Anomaly-ShapeNet", "O-AUROC")]["value"] == 97.4
    assert all(r["direction"] == "higher" for r in own.values())


@needs_local("2609.21424")
def test_benchmark_in_row_label_and_mean_column_only():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 데이터셋 이름이 열이 아니라 행 앞 칸에 있는 표를 못 읽거나, Fold-0/1/2 를 평균과 따로
    관측값으로 쌓거나, 1-shot·5-shot 아래 이름이 같은 FB-IoU 두 열을 추측으로 채우면 실패한다."""
    rows = _results("2609.21424", "P$^3$-SAM", ["FSSD-12", "Surface Defects-4i"])
    own = {(r["benchmark"], r["metric"]): r["value"] for r in rows if r["own"]}
    assert own[("Surface Defects-4i", "mIoU(1-shot) / MEAN")] == 55.41
    assert own[("Surface Defects-4i", "mIoU(5-shot) / MEAN")] == 55.26
    assert not any("Fold" in m for _b, m in own)
    assert not any("FB-IoU" in r["metric"] for r in rows if r["locator"].startswith("S3.T1:"))
    assert not any(r["locator"].startswith("S3.T2:") for r in rows)        # TABLE II 는 절제 실험 — 변형 행은 경쟁 결과가 아니다


def test_baseline_rows_are_kept_as_not_own():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 같은 표의 기준선 행을 이 논문 결과로 표시하거나(own), 기준선을 버려 관측 DB 가 자라지 않으면 실패한다."""
    rows = _results("synthetic_bench2drive", "FIVE-VLA", ["Bench2Drive", "Fail2Drive"])
    simlingo = [r for r in rows if r["model"].startswith("SimLingo") and r["benchmark"] == "Bench2Drive" and r["metric"] == "DS"]
    assert simlingo and simlingo[0]["value"] == 85.07 and not simlingo[0]["own"]
    assert [r["value"] for r in rows if r["own"] and r["benchmark"] == "Bench2Drive" and r["metric"] == "DS"] == [90.95]


def test_unknown_benchmark_is_never_invented():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 원문 후보에 없는 벤치마크를 표 머리에서 지어내 결과를 만들면 실패한다."""
    assert _results("2609.35059", "", ["MVTec AD"]) == []


def test_direction_and_keys():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: ↓·\\downarrow·FID 를 높을수록 좋은 지표로 보거나, `MVTec AD`/`MVTecAD` 를 다른 키로 보면 실패한다."""
    assert pr.direction("FID") == "lower" and pr.direction("ADE \\downarrow") == "lower" and pr.direction("DS ↑") == "higher"
    assert pr.direction("Params") is None
    assert pr.norm_key("MVTec AD") == pr.norm_key("mvtec-ad") and pr.clean_label("O-AUROC(\\uparrow)") == "O-AUROC"


# ── 원문 문장 근거 — 표시용, 수치 비교에는 안 쓴다(Codex 검토 2026-09-30 재현 포함) ─────────────────────

TEXT = ("We propose FIVE-VLA for driving. "
        "Table 2 shows results on Bench2Drive with DS and SR. "
        "RAM Inference Mode DS SR 88.49 73.03 90.02 76.21 90.21 75.91 11.1 22.2 \\uparrow on Bench2Drive. "
        "For example, Bench2Drive SR drops from 73.03% to 64.39%, whereas our RAM reaches 77.27%. "
        "We evaluate on Fail2Drive [ 81 ] using DS in 2026.")
WANT_S = next(i for i, s in enumerate(sentence_grounding.segment_sentences(TEXT), 1) if "77.27%" in s)
CLAIMS = [{"index": 1, "sentence": "FIVE-VLA establishes a new SOTA on Bench2Drive and Fail2Drive.", "benchmarks": ["Bench2Drive", "Fail2Drive"]}]


def test_sentence_evidence_needs_own_result_metric_and_real_value():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 캡션 "Table 2"·인용 "[81]"·연도를 수치로 세거나, 깨진 표 줄을 고르거나, S번호가 어긋나면 실패한다."""
    perf = pr.performance_evidence(TEXT, CLAIMS, "text")
    assert len(perf) == 1 and perf[0]["benchmark"] == "Bench2Drive" and perf[0]["metric"] == "SR" and perf[0]["index"] == WANT_S


@pytest.mark.parametrize("sentence", [
    "We report AUROC on MVTec AD using 10.5% of the training samples.",
    "Previous methods obtain 99.1% AUROC on MVTec AD.",
    "AUROC is widely used on MVTec AD, and our model was trained for 12.5 hours on it with extra augmentation steps.",
])
def test_numbers_that_are_not_this_papers_score_are_rejected(sentence):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 데이터 비율·남의 결과·지표와 먼 숫자를 이 논문 성능 근거로 받으면 실패한다."""
    assert pr.performance_evidence(sentence, [{"benchmarks": ["MVTec AD"]}], "text") == []


def test_abstract_evidence_has_no_sentence_number_and_quote_centers_on_benchmark():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 초록 문장 번호를 S번호로 달거나, 긴 문장을 앞에서 잘라 벤치마크가 인용에서 사라지면 실패한다."""
    perf = pr.performance_evidence("Our model reaches 99.1% AUROC on MVTec AD.", [{"benchmarks": ["MVTec AD"]}], "abstract")
    assert perf[0]["index"] is None
    long = "Extensive experiments " + "validate our framework " * 12 + "with gains of 7.4 in AUROC on Real3D-AD over prior methods."
    q = pr.performance_evidence(long, [{"benchmarks": ["Real3D-AD"]}], "text")[0]["sentence"]
    assert "Real3D-AD" in q and q.startswith("…")


def test_broken_table_line_is_never_sentence_evidence():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: PDF 표가 깨진 줄을 근거로 싣거나 1인칭을 품었다고 멀쩡한 문장보다 앞세우면 실패한다."""
    junk = "Our RAM DS SR 88.49 73.03 90.02 76.21 90.21 75.91 11.1 on Bench2Drive. "
    perf = pr.performance_evidence(junk + "Bench2Drive SR is 77.27% for our method.", [{"benchmarks": ["Bench2Drive"]}], "text")
    assert perf and "77.27%" in perf[0]["sentence"]
    assert pr.performance_evidence(junk, [{"benchmarks": ["Bench2Drive"]}], "text") == []


def test_method_name_only_from_title_prefix():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 제목에 없는 기법 이름을 지어내면 실패한다."""
    assert pr.method_name("FIVE-VLA: Fast and Effective Driving") == "FIVE-VLA"
    assert pr.method_name("A Study of Driving Policies") == ""


def test_ablation_tables_and_cross_dataset_cells_are_not_results():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 절제 실험 표의 변형 행(이름이 있어도)을 결과로 쌓거나, `A → B` 교차 데이터셋 셀을
    한쪽 벤치마크 값으로 붙이면 실패한다."""
    ablation = {"id": "T9", "caption": "Ablation study of different strategies on Real3D-AD.", "header_rows": 1,
                "grid": [["Variant", "O-AUROC"], ["w/o memory module", "80.1"], ["Full model (Ours)", "87.2"]]}
    assert pr.table_results([ablation], method="", benchmarks=["Real3D-AD"]) == []
    rows = _results("2609.35059", "", ["Anomaly-ShapeNet", "Real3D-AD"])
    assert rows and not any("rightarrow" in r["metric"] or "Real3D-AD" in r["metric"] for r in rows)



def test_same_figure_two_tables_get_distinct_locators_and_caption_metric_mean_column():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 한 figure 의 두 표가 같은 id 라 셀 키가 겹쳐 하나가 덮이거나(Codex 재현), 지표가 캡션에만
    있는 표(`AUROC results on Real3D-AD`, 열 Method/Airplane/Mean)의 평균 열을 못 읽으면 실패한다."""
    a = {"id": "S1.T1", "caption": "DS on Bench2Drive", "header_rows": 1, "grid": [["Method", "DS"], ["Ours", "90.0"]]}
    b = {"id": "S1.T1", "caption": "SR on Bench2Drive", "header_rows": 1, "grid": [["Method", "SR"], ["Ours", "70.0"]]}
    rows = pr.table_results([a, b], method="", benchmarks=["Bench2Drive"])
    assert len({r["locator"] for r in rows}) == 2
    cap = {"id": "T", "caption": "AUROC results on Real3D-AD", "header_rows": 1,
           "grid": [["Method", "Airplane", "Mean"], ["Ours", "0.8", "0.9"]]}
    got = pr.table_results([cap], method="", benchmarks=["Real3D-AD"])
    assert [(r["metric"], r["value"]) for r in got] == [("AUROC / Mean", 0.9)]
