"""요약이 원문의 몇 할을 봤는지 재고 표시한다 (§8-25, 2026-09-02).

실측 배경(2026-08-31): Groq 폴백은 청크 상한(32 × 3000자 ≈ 96,000자)에
걸리면 그 뒤를 통째로 안 본다. 저장된 12편 중 5편이 포화했고 188,412자
논문은 원문 문장의 40.2%만 보고 요약됐다.

**핵심은 ⑤ 검증이 이걸 못 잡는다는 것이다** — 검증기는 요약이 인용한
문장이 원문에 있는지만 보므로, 앞부분만 보고 쓴 요약도 pass_ratio 1.0 이
나온다. 그래서 따로 재서 나란히 보여줘야 오해가 없다.
"""

import storage
import sqlite3

import pytest

import digest
import server
import summarize_engine as engine


# ---------------------------------------------------------------- 커버리지 계산


def _long_text(sentences: int) -> str:
    return " ".join(f"This is sentence number {i} of the paper." for i in range(sentences))


def test_gemini_sees_everything_for_realistic_papers():
    """Gemini 는 30만자를 한 청크로 읽는다 — 실측 12편이 전부 1청크였다."""
    assert engine.coverage_ratio(_long_text(2000), "gemini") == 1.0


def test_groq_truncates_long_papers():
    """상한에 걸리면 뒤를 안 본다. 이게 §8-25 의 실체다."""
    ratio = engine.coverage_ratio(_long_text(4000), "groq")
    assert 0.0 < ratio < 1.0


def test_groq_sees_everything_for_short_papers():
    assert engine.coverage_ratio(_long_text(20), "groq") == 1.0


def test_coverage_counts_sentences_not_characters():
    """청크에는 [S번호] 태그가 붙어 있어 글자 수로 세면 100%를 넘는다
    (2026-08-31 실측에서 102.9% 가 나왔다)."""
    assert engine.coverage_ratio(_long_text(50), "groq") <= 1.0
    assert engine.coverage_ratio(_long_text(50), "gemini") <= 1.0


def test_empty_text_does_not_divide_by_zero():
    assert engine.coverage_ratio("", "groq") == 1.0


# ---------------------------------------------------------------- 다이제스트 표시


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    repro = tmp_path / "repro"
    repro.mkdir()
    # 실제 스키마를 쓴다(2026-09-04) — 손으로 다시 쓰면 실제 스키마가
    # 바뀔 때 픽스처만 뒤처진다(§8-52). storage 가 유일한 소유자다.
    storage.init_storage(db)
    monkeypatch.setattr(server, "DB_PATH", db)
    # 경로 소유자가 storage 로 옮겨갔다(2026-09-04) — 둘 다 패치해야
    # server 도구와 digest·review_core 양쪽이 같은 임시 DB 를 본다.
    monkeypatch.setattr(storage, "DB_PATH", db)
    monkeypatch.setattr(server, "REPRO_DIR", repro)
    return db


def _seed(db, arxiv_id, *, coverage, total=40, matched=40, eng="groq"):
    with sqlite3.connect(db) as con:
        con.execute("INSERT OR REPLACE INTO summaries "
                    "(arxiv_id, path, numbers_total, numbers_matched, engine, coverage_ratio) "
                    "VALUES (?,?,?,?,?,?)",
                    (arxiv_id, "", total, matched, eng, coverage))


def test_partial_coverage_is_shown(isolated_db):
    _seed(isolated_db, "p1", coverage=0.402)
    assert digest.coverage_label("p1") == "⚠ 원문 40%만 반영"


def test_full_coverage_is_silent(isolated_db):
    """전부 봤으면 굳이 말하지 않는다 — 매 항목에 붙으면 의미가 죽는다."""
    _seed(isolated_db, "p1", coverage=1.0)
    assert digest.coverage_label("p1") == ""


def test_near_full_coverage_is_silent(isolated_db):
    """문장 하나 차이로 라벨이 붙으면 노이즈가 된다."""
    _seed(isolated_db, "p1", coverage=0.995)
    assert digest.coverage_label("p1") == ""


def test_unknown_coverage_is_not_claimed_as_full(isolated_db):
    """엔진을 모르는 구형 요약은 커버리지도 NULL 이다 — 미실측을
    측정값처럼 쓰지 않는다(CLAUDE.md 8)."""
    _seed(isolated_db, "p1", coverage=None, eng=None)
    assert digest.coverage_label("p1") == ""


def test_verification_pass_and_partial_coverage_appear_together(isolated_db):
    """제일 중요한 회귀: "검증 40/40 통과"와 "원문 40%만 반영"이 **같이**
    보여야 한다. 검증만 보면 완벽해 보이는 게 이 문제의 핵심이다."""
    _seed(isolated_db, "p1", coverage=0.402, total=40, matched=40)
    paper = {"arxiv_id": "p1", "title": "긴 논문", "abstract": "x",
             "_score": {"priority": 1.0, "core_hits": [], "domain_hits": [], "venue_hit": None}}
    text = digest.generate_digest(
        {"papers": [paper], "candidates_found": 1}, "우리팀")
    assert "[검증 40/40 통과]" in text
    assert "⚠ 원문 40%만 반영" in text


def test_html_flags_partial_coverage_open(isolated_db):
    """"검증 통과"만 보고 요약을 그대로 믿으면 안 되는 상황이라 펼쳐 보낸다."""
    _seed(isolated_db, "p1", coverage=0.402)
    paper = {"arxiv_id": "p1", "title": "긴 논문", "abstract": "x",
             "_score": {"priority": 1.0, "core_hits": [], "domain_hits": [], "venue_hit": None}}
    html = digest.generate_digest_html({"papers": [paper], "candidates_found": 1}, "우리팀")
    assert "원문 40%만 반영" in html
    assert "<details open" in html
