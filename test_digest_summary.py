"""다이제스트가 **검증된 요약 본문**을 싣는지 (2026-08-31 추가).

이전까지 메일에는 제목 + 초록 발췌만 실렸다. 논문마다 ④가 만들고 ⑤가 검증한
요약이 data/summaries/ 에 멀쩡히 있는데도 안 실려서, 받는 사람이 결국 arXiv를
다시 열어야 했다.

기존 test_digest.py 의 픽스처는 summaries 테이블에 path 컬럼이 없어서 이
경로를 전혀 밟지 않는다(조용히 초록 발췌로 폴백한다). 그래서 파일을 나눴다.
"""

import sqlite3

import pytest

import digest
import server
from digest import generate_digest, generate_digest_html

SUMMARY_MD = """### 기본정보
- 제목 : Quality Inspection of PCB Pin Insertion
- 저자/소속 : A. Author / Some University

### 연구 개요
- 무엇을 하려 했는가 : PCB 핀 삽입 공정의 정렬 불량을 자동 검사하려 했다.
- 어떻게 했는가 : U-Net 분할 뒤 기하 특징을 뽑아 로지스틱 회귀로 판정했다.
- 무엇을 보였는가 : 산업 데이터셋과 공개 데이터셋에서 유효성을 입증했다.

### 방법 상세
- 입력과 출력 : 고해상도 이미지를 받아 정상/불량을 낸다.

### 논문의 한계점
- 저자가 밝힌 한계 : 데이터셋이 균일해 일반화를 단정할 수 없다고 기술했다.
- 요약자가 판단한 한계 : 1장에 18초가 걸려 실시간 인라인 검사에는 지연이 길다.

### 결론
① 한 줄 요약 : U-Net 분할과 기하 특징으로 PCB 핀 정렬 불량을 판정한다.
② 문제의식 : 기존 연구는 표면 실장 부품에 편중돼 있었다.
③ 방법 상세 : 패치 분할 후 윤곽선 기하 특징을 로지스틱 회귀에 넣는다.
④ 결과 :
- 산업 데이터셋에서 0.990의 ROC-AUC를 달성했다 [S0153].
- 공개 데이터셋에서 1.000의 ROC-AUC를 기록했다 [S0168].

### 파싱 품질 노트
- 추출 방식 : 문장별 태그 기반
"""


@pytest.fixture(autouse=True)
def db_with_summary_path(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    repro = tmp_path / "repro"
    repro.mkdir()
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE summaries (arxiv_id TEXT PRIMARY KEY, path TEXT, "
                    "numbers_total INTEGER, numbers_matched INTEGER)")
        con.execute("CREATE TABLE repro_results (arxiv_id TEXT, repo_url TEXT, "
                    "success INTEGER, PRIMARY KEY (arxiv_id, repo_url))")
        con.execute("CREATE TABLE papers (arxiv_id TEXT PRIMARY KEY, is_retracted INTEGER)")
    monkeypatch.setattr(server, "DB_PATH", db)
    monkeypatch.setattr(server, "REPRO_DIR", repro)
    return {"db": db, "dir": tmp_path}


def _seed(env, arxiv_id, markdown=SUMMARY_MD, *, write_file=True, total=10, matched=10):
    path = env["dir"] / f"{arxiv_id}.md"
    if write_file:
        path.write_text(markdown, encoding="utf-8")
    with sqlite3.connect(env["db"]) as con:
        con.execute("INSERT OR REPLACE INTO summaries VALUES (?,?,?,?)",
                    (arxiv_id, str(path), total, matched))
    return path


def _paper(arxiv_id, *, abstract="초록 원문이 여기 들어간다. " * 20, deep_status=None):
    p = {"arxiv_id": arxiv_id, "title": "PCB 핀 삽입 검사", "abstract": abstract,
         "_score": {"priority": 0.9, "core_hits": ["defect detection"],
                    "domain_hits": ["PCB"], "venue_hit": None}}
    if deep_status is not None:
        p["deep_status"] = deep_status
    return p


def _result(papers, **kw):
    base = {"papers": papers, "candidates_found": 100,
            "excluded_count": 0, "unmatched_count": 0}
    base.update(kw)
    return base


# ------------------------------------------------------------------ 요약 본문 탑재

def test_text_digest_carries_verified_summary_not_just_title(db_with_summary_path):
    """핵심 회귀: 메일에 제목만 가고 요약이 안 실리던 문제."""
    _seed(db_with_summary_path, "p1")
    text = generate_digest(_result([_paper("p1")]), "우리팀")
    assert "한 줄 요약 : U-Net 분할과 기하 특징으로 PCB 핀 정렬 불량을 판정한다." in text
    assert "무엇을·어떻게 :" in text
    assert "U-Net 분할 뒤 기하 특징을 뽑아 로지스틱 회귀로 판정했다." in text
    assert "핵심 결과 :" in text
    assert "0.990의 ROC-AUC" in text
    assert "한계 :" in text
    assert "18초" in text


def test_summary_replaces_abstract_excerpt_when_present(db_with_summary_path):
    _seed(db_with_summary_path, "p1")
    text = generate_digest(_result([_paper("p1")]), "우리팀")
    assert "초록 발췌" not in text


def test_falls_back_to_abstract_when_summary_file_is_missing(db_with_summary_path):
    """파일이 사라진 걸 '빈 요약'으로 보여주면 안 된다 — 정직하게 발췌로 떨어진다."""
    _seed(db_with_summary_path, "p1", write_file=False)
    text = generate_digest(_result([_paper("p1")]), "우리팀")
    assert "초록 발췌" in text
    assert "한 줄 요약" not in text


def test_falls_back_to_abstract_when_no_summary_row(db_with_summary_path):
    text = generate_digest(_result([_paper("p1")]), "우리팀")
    assert "초록 발췌" in text


def test_failed_deep_layer_paper_never_shows_summary(db_with_summary_path):
    """Deep Layer 가 실패한 논문은 검증된 요약이 없다 — 있는 척하면 안 된다."""
    _seed(db_with_summary_path, "p1")
    text = generate_digest(
        _result([_paper("p1", deep_status="failed: 한도 초과")]), "우리팀")
    assert "한 줄 요약" not in text
    assert "[미검증 · 초록 기반]" in text


def test_malformed_summary_falls_back_instead_of_showing_nothing(db_with_summary_path):
    """템플릿이 바뀌어 절을 못 찾아도 빈칸을 보내지 않는다."""
    _seed(db_with_summary_path, "p1", markdown="아무 형식도 없는 텍스트")
    text = generate_digest(_result([_paper("p1")]), "우리팀")
    assert "초록 발췌" in text


# ------------------------------------------------------------------ HTML 판 동등성

def test_html_carries_the_same_summary(db_with_summary_path):
    """HTML 을 차단한 사람만 다른 메일을 받는 상황이 되면 안 된다."""
    _seed(db_with_summary_path, "p1")
    html = generate_digest_html(_result([_paper("p1")]), "우리팀")
    assert "U-Net 분할과 기하 특징으로 PCB 핀 정렬 불량을 판정한다." in html
    assert "핵심 결과" in html
    assert "0.990의 ROC-AUC" in html


def test_html_stays_under_gmail_limit_with_full_summaries(db_with_summary_path):
    """기존 클리핑 테스트는 요약 없는 픽스처라 이 위험을 못 잡는다.
    실제 요약(논문당 수천 자)을 8편 실었을 때가 진짜 최악의 경우다."""
    long_md = SUMMARY_MD.replace(
        "PCB 핀 삽입 공정의 정렬 불량을 자동 검사하려 했다.",
        "PCB 핀 삽입 공정의 정렬 불량을 자동 검사하려 했다. " * 60,
    ).replace(
        "산업 데이터셋에서 0.990의 ROC-AUC를 달성했다 [S0153].",
        "산업 데이터셋에서 0.990의 ROC-AUC를 달성했다 [S0153]. " * 60,
    )
    papers = []
    for i in range(8):
        aid = f"p{i}"
        _seed(db_with_summary_path, aid, markdown=long_md, total=31, matched=28)
        papers.append(_paper(aid, abstract="x" * 3000))
    html = generate_digest_html(_result(papers), "우리팀")
    assert len(html.encode("utf-8")) < 102_400


# ------------------------------------------------------------------ 동향 집계

def test_trend_line_reports_counts_over_all_candidates(db_with_summary_path):
    """상위 5편이 아니라 후보 전체 기준이어야 '이번 주 무엇이 늘었나'가 답해진다."""
    _seed(db_with_summary_path, "p1")
    result = _result([_paper("p1")],
                     core_hit_counts={"physical AI": 18, "defect detection": 12})
    text = generate_digest(result, "우리팀")
    assert "이번 창의 동향 신호" in text
    assert "physical AI 18" in text
    assert "defect detection 12" in text


def test_trend_line_absent_when_no_counts(db_with_summary_path):
    """구형 scan_result(집계 없음)에서도 다이제스트가 깨지지 않는다."""
    _seed(db_with_summary_path, "p1")
    text = generate_digest(_result([_paper("p1")]), "우리팀")
    assert "이번 창의 동향 신호" not in text


def test_trend_line_in_html(db_with_summary_path):
    _seed(db_with_summary_path, "p1")
    html = generate_digest_html(
        _result([_paper("p1")], core_hit_counts={"on-sensor computing": 4}), "우리팀")
    assert "이번 창의 동향 신호" in html
    assert "on-sensor computing 4" in html


def test_results_section_written_as_paragraph_is_captured(db_with_summary_path):
    """실측(2026-08-31): "④ 결과 :" 뒤에 불릿 없이 문단으로 쓴 요약이 있다.
    불릿만 보던 파서는 그 논문의 결과를 통째로 빠뜨렸다 — 메일에서 제일
    중요한 줄이 사라지는 셈이라 두 형식을 다 받아야 한다."""
    md = SUMMARY_MD.replace(
        "④ 결과 :\n- 산업 데이터셋에서 0.990의 ROC-AUC를 달성했다 [S0153].\n"
        "- 공개 데이터셋에서 1.000의 ROC-AUC를 기록했다 [S0168].",
        "④ 결과 : 고정 위치 베이스라인 대비 118.5%의 총 전송률 향상을 보였다.")
    _seed(db_with_summary_path, "p1", markdown=md)
    text = generate_digest(_result([_paper("p1")]), "우리팀")
    assert "핵심 결과 :" in text
    assert "118.5%의 총 전송률 향상" in text


def test_bullet_form_results_still_work(db_with_summary_path):
    _seed(db_with_summary_path, "p1")
    text = generate_digest(_result([_paper("p1")]), "우리팀")
    assert "0.990의 ROC-AUC" in text
    assert "1.000의 ROC-AUC" in text
