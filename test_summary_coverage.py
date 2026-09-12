"""요약이 원문의 몇 할을 봤는지 재고 표시한다 (§8-25, 2026-09-02).

실측 배경(2026-08-31): Groq 폴백은 청크 상한(32 × 3000자 ≈ 96,000자)에
걸리면 그 뒤를 통째로 안 본다. 저장된 12편 중 5편이 포화했고 188,412자
논문은 원문 문장의 40.2%만 보고 요약됐다.

**핵심은 ⑤ 검증이 이걸 못 잡는다는 것이다** — 검증기는 요약이 인용한
문장이 원문에 있는지만 보므로, 앞부분만 보고 쓴 요약도 pass_ratio 1.0 이
나온다. 그래서 따로 재서 나란히 보여줘야 오해가 없다.
"""

import asyncio
import sqlite3

import storage

import pytest

import digest
import server
import summarize_engine as engine


# ---------------------------------------------------------------- 커버리지 계산


def _long_text(sentences: int) -> str:
    return " ".join(f"This is sentence number {i} of the paper." for i in range(sentences))


def test_gemini_sees_everything_for_realistic_papers():
    """Gemini 는 30만자를 한 청크로 읽는다 — 실측 12편이 전부 1청크였다."""
    assert engine.planned_coverage_ratio(_long_text(2000), "gemini") == 1.0


def test_groq_truncates_long_papers():
    """상한에 걸리면 뒤를 안 본다. 이게 §8-25 의 실체다."""
    ratio = engine.planned_coverage_ratio(_long_text(4000), "groq")
    assert 0.0 < ratio < 1.0


def test_groq_sees_everything_for_short_papers():
    assert engine.planned_coverage_ratio(_long_text(20), "groq") == 1.0


def test_coverage_counts_sentences_not_characters():
    """청크에는 [S번호] 태그가 붙어 있어 글자 수로 세면 100%를 넘는다
    (2026-08-31 실측에서 102.9% 가 나왔다)."""
    assert engine.planned_coverage_ratio(_long_text(50), "groq") <= 1.0
    assert engine.planned_coverage_ratio(_long_text(50), "gemini") <= 1.0


def test_empty_text_does_not_divide_by_zero():
    assert engine.planned_coverage_ratio("", "groq") == 1.0


# ------------------------------------------- 계획값 ≠ 실측값 (§8-70, 2026-09-07)
#
# 예전 이름은 `coverage_ratio` 였고 docstring 은 "원문의 몇 할을 **실제로**
# 봤는가"라고 주장했다. 그런데 인자가 (원문, 엔진) 뿐이라 **어느 청크가
# 성공했는지를 받을 방법이 아예 없었다** — 중간 청크가 실패해 부분 요약이
# 나온 경우에도 1.0 이었다. 재지 않은 값을 잰 값이라 부르는 것이므로 규칙 8
# 위반이고, 아래 두 테스트가 그 구분을 못박는다.


def test_mid_chunk_failure_lowers_the_measured_coverage(monkeypatch):
    """**이게 예전 계산으로는 절대 안 잡히던 경우다.**

    청크 2 에서 실패해 앞부분만 보고 요약이 끝났는데, 저장 시점 재계산은
    "이 설정이면 다 덮는다"만 보므로 1.0 을 돌려줬다.
    """
    paper = _long_text(400)

    async def call_single(client, chunk, template):
        return "### 결론\n요약"

    async def call_addendum(client, chunk):
        raise RuntimeError("Groq 일일 한도 소진")

    summary, coverage = asyncio.run(engine._summarize_chunked(
        client=None, paper_text=paper, template="t",
        call_single=call_single, call_addendum=call_addendum,
        chunk_size=2000, max_chunks=32, chunk_delay=0.0, label="Groq",
    ))
    assert summary.startswith("### 결론")
    assert 0.0 < coverage < 1.0, "첫 청크만 봤는데 커버리지가 1.0 이면 안 된다"
    # 같은 입력에 대한 계획값은 1.0 이다 — 둘이 다르다는 것이 이 고침의 요지다.
    assert engine.planned_coverage_ratio(paper, "groq") == 1.0


def test_all_chunks_succeeding_measures_full_coverage():
    """끝까지 읽었으면 실측도 1.0 이다 — 실패 때만 낮아진다."""
    paper = _long_text(400)

    async def call_single(client, chunk, template):
        return "### 결론\n요약"

    async def call_addendum(client, chunk):
        return "추가 결과"

    _summary, coverage = asyncio.run(engine._summarize_chunked(
        client=None, paper_text=paper, template="t",
        call_single=call_single, call_addendum=call_addendum,
        chunk_size=2000, max_chunks=32, chunk_delay=0.0, label="Groq",
    ))
    assert coverage == 1.0


def test_no_content_addendum_still_counts_as_seen():
    """읽고 "추가할 내용 없음"이라 답한 청크는 **본 것**이다 — 안 본 구간과
    구분해야 한다. 이걸 섞으면 실측이 실제보다 낮아진다."""
    paper = _long_text(400)

    async def call_single(client, chunk, template):
        return "### 결론\n요약"

    async def call_addendum(client, chunk):
        return engine._ADDENDUM_NO_CONTENT

    _summary, coverage = asyncio.run(engine._summarize_chunked(
        client=None, paper_text=paper, template="t",
        call_single=call_single, call_addendum=call_addendum,
        chunk_size=2000, max_chunks=32, chunk_delay=0.0, label="Groq",
    ))
    assert coverage == 1.0


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


def _seed(db, arxiv_id, *, coverage, total=40, matched=40, eng="groq", kind="measured"):
    with sqlite3.connect(db) as con:
        con.execute("INSERT OR REPLACE INTO summaries "
                    "(arxiv_id, path, numbers_total, numbers_matched, engine, "
                    "coverage_ratio, coverage_kind) VALUES (?,?,?,?,?,?,?)",
                    (arxiv_id, "", total, matched, eng, coverage, kind))


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
    """"검증 통과"만 보고 요약을 그대로 믿으면 안 되는 상황 — 경고 칩이 보여야 한다.
    2026-09-12 부터 토글은 전부 닫힌 채로 가므로(사용자 요청) 펼침으로 알리지 않는다."""
    _seed(isolated_db, "p1", coverage=0.402)
    paper = {"arxiv_id": "p1", "title": "긴 논문", "abstract": "x",
             "_score": {"priority": 1.0, "core_hits": [], "domain_hits": [], "venue_hit": None}}
    html = digest.generate_digest_html({"papers": [paper], "candidates_found": 1}, "우리팀")
    assert "원문 40%만 반영" in html
    assert "<details open" not in html


# ------------------------------------------- 값의 뜻을 같이 저장한다 (§8-70)


def test_legacy_rows_do_not_borrow_the_measured_wording(isolated_db):
    """**2026-09-07 이전에 저장된 행은 전부 계획값이다.**

    coverage_kind 가 NULL 인 행에 "원문 40%만 반영"이라고 쓰면, 고치기 전의
    거짓말이 레거시 데이터를 통해 그대로 이어진다. 숫자는 보여주되 그게
    실측이 아니라는 것을 말한다(규칙 8).
    """
    _seed(isolated_db, "p1", coverage=0.402, kind=None)
    label = digest.coverage_label("p1")
    assert "40%" in label
    assert "실측 아님" in label


def test_planned_rows_say_so(isolated_db):
    _seed(isolated_db, "p1", coverage=0.402, kind="planned")
    assert "실측 아님" in digest.coverage_label("p1")


def test_measured_rows_keep_the_plain_wording(isolated_db):
    _seed(isolated_db, "p1", coverage=0.402, kind="measured")
    assert digest.coverage_label("p1") == "⚠ 원문 40%만 반영"


def test_save_summary_records_measured_when_the_caller_measured_it(isolated_db, tmp_path, monkeypatch):
    """호출부가 실측값을 주면 그대로 'measured' 로 남는다 — 저장 시점에
    다시 계산하지 않는다."""
    import asyncio as _asyncio
    import json as _json

    src = tmp_path / "p.txt"
    src.write_text(_long_text(400), encoding="utf-8")
    with sqlite3.connect(isolated_db) as con:
        con.execute("INSERT OR REPLACE INTO papers (arxiv_id, title, text_path) VALUES (?,?,?)",
                    ("p9", "t", str(src)))
    monkeypatch.setattr(server, "SUMMARY_DIR", tmp_path)

    _json.loads(_asyncio.run(server.save_summary(server.SaveSummaryInput(
        arxiv_id="p9", markdown="### 결론\n요약", engine="groq", coverage=0.42))))

    with sqlite3.connect(isolated_db) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT coverage_ratio, coverage_kind FROM summaries "
                          "WHERE arxiv_id='p9'").fetchone()
    assert abs(row["coverage_ratio"] - 0.42) < 1e-9
    assert row["coverage_kind"] == "measured"


def test_save_summary_marks_the_recomputed_value_as_planned(isolated_db, tmp_path, monkeypatch):
    """실측값 없이 엔진만 알면 계획 상한을 저장하되 **그렇게 라벨한다**.
    이게 §8-70 이 지적한 바로 그 값이다 — 중간에 끊긴 요약도 1.0 이 나온다."""
    import asyncio as _asyncio
    import json as _json

    src = tmp_path / "p.txt"
    src.write_text(_long_text(400), encoding="utf-8")
    with sqlite3.connect(isolated_db) as con:
        con.execute("INSERT OR REPLACE INTO papers (arxiv_id, title, text_path) VALUES (?,?,?)",
                    ("p8", "t", str(src)))
    monkeypatch.setattr(server, "SUMMARY_DIR", tmp_path)

    _json.loads(_asyncio.run(server.save_summary(server.SaveSummaryInput(
        arxiv_id="p8", markdown="### 결론\n요약", engine="groq"))))

    with sqlite3.connect(isolated_db) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT coverage_kind FROM summaries WHERE arxiv_id='p8'").fetchone()
    assert row["coverage_kind"] == "planned"
