"""digest.py 단위 테스트 — 네트워크 없이 돈다.

M2(2026-08-28)부터 digest.py가 ⑤ 검증·⑦ 재현 상태를 DB에서 읽으므로,
모든 테스트를 임시 DB·임시 REPRO_DIR로 격리한다(autouse 픽스처). 격리
전에는 테스트가 실제 프로덕션 DB를 읽어서 통과 여부가 그때그때 저장된
논문에 따라 달라졌다 — 실제로 "1706.03762"가 프로덕션에 있어서 그 논문의
진짜 검증 결과(28/31)가 테스트에 새어 들어왔다.
"""

import sqlite3

import pytest

import server
from digest import generate_digest


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """server.DB_PATH·REPRO_DIR를 임시 경로로 돌린다. digest.py는 이 둘만
    보므로(읽기 전용) 이것으로 프로덕션과 완전히 분리된다."""
    db = tmp_path / "test.db"
    repro = tmp_path / "repro"
    repro.mkdir()
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE summaries (arxiv_id TEXT PRIMARY KEY, "
                    "numbers_total INTEGER, numbers_matched INTEGER)")
        con.execute("CREATE TABLE repro_results (arxiv_id TEXT, repo_url TEXT, "
                    "success INTEGER, PRIMARY KEY (arxiv_id, repo_url))")
    monkeypatch.setattr(server, "DB_PATH", db)
    monkeypatch.setattr(server, "REPRO_DIR", repro)
    return {"db": db, "repro": repro}


def _seed_verification(db, arxiv_id, total, matched):
    with sqlite3.connect(db) as con:
        con.execute("INSERT OR REPLACE INTO summaries VALUES (?,?,?)",
                    (arxiv_id, total, matched))


def _seed_repro(db, arxiv_id, repo_url, success):
    with sqlite3.connect(db) as con:
        con.execute("INSERT OR REPLACE INTO repro_results VALUES (?,?,?)",
                    (arxiv_id, repo_url, int(success)))


def _scored_paper(arxiv_id, title, priority, core_hits=None, domain_hits=None,
                   venue_hit=None, abstract="", deep_status=None):
    paper = {
        "arxiv_id": arxiv_id, "title": title, "abstract": abstract,
        "_score": {
            "priority": priority, "core_hits": core_hits or [],
            "domain_hits": domain_hits or [], "venue_hit": venue_hit,
        },
    }
    if deep_status is not None:
        paper["deep_status"] = deep_status
    return paper


def _digest_for(paper):
    return generate_digest(
        {"papers": [paper], "candidates_found": 1, "excluded_count": 0, "unmatched_count": 0},
        "우리팀",
    )


def test_generate_digest_reports_no_papers_case():
    result = {"papers": [], "candidates_found": 12, "excluded_count": 3, "unmatched_count": 9}
    text = generate_digest(result, "우리팀")
    assert "우리팀" in text
    assert "새로 걸린 논문이 없습니다" in text
    assert "12" in text


def test_generate_digest_includes_title_arxiv_link_and_match_reason():
    paper = _scored_paper("1706.03762", "Attention Is All You Need", 1.5,
                           core_hits=["agent"], domain_hits=["robot hand"])

    text = _digest_for(paper)

    assert "Attention Is All You Need" in text
    assert "https://arxiv.org/abs/1706.03762" in text
    assert "agent" in text
    assert "robot hand" in text


def test_generate_digest_stars_scale_with_priority():
    high = _scored_paper("a", "높은 점수", 1.5)
    mid = _scored_paper("b", "중간 점수", 0.8)
    low = _scored_paper("c", "낮은 점수", 0.1)
    result = {"papers": [high, mid, low], "candidates_found": 3, "excluded_count": 0, "unmatched_count": 0}

    text = generate_digest(result, "p")

    assert "[★★★] 높은 점수" in text
    assert "[★★] 중간 점수" in text
    assert "[★] 낮은 점수" in text


def test_generate_digest_truncates_long_abstract_and_labels_it_excerpt_not_summary():
    long_abstract = "x" * 500
    paper = _scored_paper("a", "제목", 1.0, abstract=long_abstract)
    result = {"papers": [paper], "candidates_found": 1, "excluded_count": 0, "unmatched_count": 0}

    text = generate_digest(result, "p")

    assert "초록 발췌" in text  # "요약"이라고 쓰면 안 됨 — LLM 요약 아님
    assert "…" in text
    assert long_abstract not in text  # 잘렸는지 확인


def test_generate_digest_reports_filtered_counts_when_present():
    paper = _scored_paper("a", "제목", 1.0)
    result = {"papers": [paper], "candidates_found": 5, "excluded_count": 2, "unmatched_count": 2}

    text = generate_digest(result, "p")

    assert "제외 규칙 2건" in text
    assert "조건 불일치 2건" in text


# ---------------------------------------------------------------- M2: ⑤ 검증 · ⑦ 재현 상태 라벨


def test_repro_label_running_when_marker_present(isolated_db):
    """(a-1) .running 마커가 있으면 실행중 — repro_results에 행이 없어도
    "기록없음"과 구분돼야 한다(재현 도는 중에는 아직 행이 없다)."""
    (isolated_db["repro"] / "2608.27184.running").write_text("t")
    text = _digest_for(_scored_paper("2608.27184", "실행중 논문", 1.0))
    assert "[재현 ⏳ 실행중]" in text


def test_repro_label_success_when_any_attempt_succeeded(isolated_db):
    """(a-2) 여러 후보 중 하나라도 성공이면 성공 — docker_runner가 후보를
    순서대로 시도하고 성공하면 멈추는 구조라 실패 행이 함께 남는다."""
    _seed_repro(isolated_db["db"], "p1", "https://github.com/a/failed", success=False)
    _seed_repro(isolated_db["db"], "p1", "https://github.com/a/ok", success=True)
    text = _digest_for(_scored_paper("p1", "성공 논문", 1.0))
    assert "[재현 ✓]" in text


def test_repro_label_failed_when_all_attempts_failed(isolated_db):
    """(a-3) 실측 재현: M1 종단 테스트에서 2608.27184가 후보 2개 모두 실패
    (no_target, run)했다 — 그 상태가 이렇게 보여야 한다."""
    _seed_repro(isolated_db["db"], "p1", "https://github.com/a/one", success=False)
    _seed_repro(isolated_db["db"], "p1", "https://github.com/a/two", success=False)
    text = _digest_for(_scored_paper("p1", "실패 논문", 1.0))
    assert "[재현 ✗]" in text


def test_repro_label_none_when_no_record(isolated_db):
    """(a-4) 기록 자체가 없음 — 실패(✗)와 절대 같게 표시하면 안 된다."""
    text = _digest_for(_scored_paper("p1", "기록없음 논문", 1.0))
    assert "[재현 –]" in text
    assert "[재현 ✗]" not in text


def test_verification_label_shows_flag_count(isolated_db):
    """(b) 불일치가 있으면 ⚠ flag k건을 병기한다."""
    _seed_verification(isolated_db["db"], "p1", total=31, matched=28)
    text = _digest_for(_scored_paper("p1", "flag 있는 논문", 1.0))
    assert "[검증 28/31 통과]" in text
    assert "⚠ flag 3건" in text


def test_verification_label_no_flag_when_all_matched(isolated_db):
    """실측 재현: M1 종단 테스트의 2608.27184가 43/43이었다."""
    _seed_verification(isolated_db["db"], "p1", total=43, matched=43)
    text = _digest_for(_scored_paper("p1", "완전 통과 논문", 1.0))
    assert "[검증 43/43 통과]" in text
    assert "flag" not in text


def test_verification_missing_data_never_renders_as_pass(isolated_db):
    """(c) 검증 데이터가 없는 논문을 통과처럼 보이게 하면 안 된다
    (CLAUDE.md 8)."""
    text = _digest_for(_scored_paper("p1", "검증 안 된 논문", 1.0))
    assert "[검증 데이터 없음]" in text
    assert "통과" not in text


def test_zero_numbers_never_renders_as_pass(isolated_db):
    """(c-2) numbers_total=0은 VerificationReport.pass_ratio가 1.0을 돌려주는
    자리라 그대로 쓰면 "완벽 통과"로 둔갑한다 — 실제 저장된 요약 52편 중
    1편이 이 경우라 가상의 위험이 아니다."""
    _seed_verification(isolated_db["db"], "p1", total=0, matched=0)
    text = _digest_for(_scored_paper("p1", "수치 없는 논문", 1.0))
    assert "[검증할 수치 없음]" in text
    assert "0/0" not in text
    assert "통과" not in text


def test_deep_failed_paper_falls_back_to_unverified_label(isolated_db):
    """(d) Deep 처리가 실패한 논문만 예전의 "미검증 · 초록 기반"으로 남고,
    실패 사유를 한 줄 붙인다."""
    paper = _scored_paper("p1", "실패 논문", 1.0,
                           deep_status="failed: Gemini·Groq 둘 다 실패")
    text = _digest_for(paper)
    assert "[미검증 · 초록 기반]" in text
    assert "Gemini·Groq 둘 다 실패" in text
    assert "[검증" not in text  # 검증 라벨은 안 붙는다


def test_skipped_paper_still_shows_real_db_status(isolated_db):
    """이미 요약된 논문(deep_status=skipped)은 DB에 실제 결과가 있으므로
    "미검증"이 아니라 그 결과를 보여줘야 한다."""
    _seed_verification(isolated_db["db"], "p1", total=10, matched=10)
    paper = _scored_paper("p1", "스킵된 논문", 1.0, deep_status="skipped: 이미 요약 저장됨")
    text = _digest_for(paper)
    assert "[검증 10/10 통과]" in text
    assert "[미검증 · 초록 기반]" not in text


def test_digest_generation_survives_missing_db(monkeypatch, tmp_path):
    """DB 파일이 아예 없는 환경(새 클론)에서도 다이제스트 생성은 계속돼야
    한다 — 단, 통과가 아니라 "데이터 없음"으로 떨어진다."""
    monkeypatch.setattr(server, "DB_PATH", tmp_path / "does_not_exist.db")
    monkeypatch.setattr(server, "REPRO_DIR", tmp_path / "no_repro")
    text = _digest_for(_scored_paper("p1", "DB 없는 논문", 1.0))
    assert "[검증 데이터 없음]" in text
    assert "[재현 –]" in text
