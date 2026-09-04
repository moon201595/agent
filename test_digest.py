"""digest.py 단위 테스트 — 네트워크 없이 돈다.

M2(2026-08-28)부터 digest.py가 ⑤ 검증·⑦ 재현 상태를 DB에서 읽으므로,
모든 테스트를 임시 DB·임시 REPRO_DIR로 격리한다(autouse 픽스처). 격리
전에는 테스트가 실제 프로덕션 DB를 읽어서 통과 여부가 그때그때 저장된
논문에 따라 달라졌다 — 실제로 "1706.03762"가 프로덕션에 있어서 그 논문의
진짜 검증 결과(28/31)가 테스트에 새어 들어왔다.
"""

import storage
import sqlite3

import pytest

import digest
import server
from digest import generate_digest


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """DB_PATH·REPRO_DIR를 임시 경로로 돌린다. digest.py는 이 둘만
    보므로(읽기 전용) 이것으로 프로덕션과 완전히 분리된다.

    2026-09-04: 경로 소유자가 storage 로 옮겨가 둘 다 패치한다 — digest 는
    이제 storage 만 보고, server 쪽은 아직 server 를 보는 다른 모듈용이다."""
    db = tmp_path / "test.db"
    repro = tmp_path / "repro"
    repro.mkdir()
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE summaries (arxiv_id TEXT PRIMARY KEY, "
                    "numbers_total INTEGER, numbers_matched INTEGER)")
        # stage·attempt·fail_detail 은 실제 스키마에 있는데 예전 픽스처엔
        # 없었다(2026-09-01 추가). 그 탓에 repro_label 이 여기서는 늘
        # 구형 폴백 경로로만 돌아, 사유별 라벨이 테스트를 통과해도 실제로는
        # 한 줄도 안 밟히는 상태였다 — test_digest_summary.py 를 따로 만든
        # 것과 같은 종류의 함정이다.
        con.execute("CREATE TABLE repro_results (arxiv_id TEXT, repo_url TEXT, "
                    "success INTEGER, stage TEXT, attempt INTEGER, "
                    "fail_detail TEXT, PRIMARY KEY (arxiv_id, repo_url))")
        # M5: 철회 상태는 papers 에 산다.
        con.execute("CREATE TABLE papers (arxiv_id TEXT PRIMARY KEY, is_retracted INTEGER)")
    monkeypatch.setattr(server, "DB_PATH", db)
    monkeypatch.setattr(storage, "DB_PATH", db)
    monkeypatch.setattr(server, "REPRO_DIR", repro)
    monkeypatch.setattr(storage, "REPRO_DIR", repro)
    return {"db": db, "repro": repro}


def _seed_verification(db, arxiv_id, total, matched):
    with sqlite3.connect(db) as con:
        con.execute("INSERT OR REPLACE INTO summaries VALUES (?,?,?)",
                    (arxiv_id, total, matched))


def _seed_repro(db, arxiv_id, repo_url, success, stage=None, attempt=1, fail_detail=None):
    """stage/fail_detail 을 안 주면 구형 행(2026-09-01 이전 29건)을 흉내낸다 —
    하위 호환 경로도 계속 테스트된다."""
    with sqlite3.connect(db) as con:
        con.execute("INSERT OR REPLACE INTO repro_results VALUES (?,?,?,?,?,?)",
                    (arxiv_id, repo_url, int(success), stage, attempt, fail_detail))


def _scored_paper(arxiv_id, title, priority, core_hits=None, domain_hits=None,
                   venue_hit=None, abstract="", deep_status=None, top_core_weight=None):
    """top_core_weight 를 안 주면 core_hits 유무로 적당히 채운다 — 별점을
    안 보는 테스트들이 매번 이 값을 신경 쓰지 않아도 되게."""
    hits = core_hits or []
    paper = {
        "arxiv_id": arxiv_id, "title": title, "abstract": abstract,
        "_score": {
            "priority": priority, "core_hits": hits,
            "domain_hits": domain_hits or [], "venue_hit": venue_hit,
            "top_core_weight": (top_core_weight if top_core_weight is not None
                                else (1.0 if hits else 0.0)),
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


def test_generate_digest_stars_separate_target_from_trend():
    """2026-09-02: 별점 기준을 총점에서 **핵심 키워드 최대 가중치**로 옮겼다.
    주장("별점이 단계별로 갈린다")은 그대로고 눈금만 바뀌었다.

    총점 기준이 두 가지로 깨져 있었다. ★★★ 문턱 1.0 이 도달 불가능했고
    (저장된 35편 최댓값 0.745), 최신성(±0.15)이 계층 정보를 덮어
    "표적어 단독"(0.500~0.645)이 "동향어+도메인"(0.523~0.545)과 겹쳤다 —
    같은 별점인데 하나는 우리 표적 도메인이고 하나는 아니었다.

    가중치 **합**이 아니라 **최댓값**을 쓰는 이유: 합으로는 동향어 두 개
    (0.6+0.6=1.2)가 표적어 하나(1.0)보다 커서 순서가 뒤집힌다."""
    target_multi = _scored_paper("a", "표적 복수", 0.9,
                                 core_hits=["defect detection", "NPU"], top_core_weight=1.0)
    target_one = _scored_paper("b", "표적 단독", 0.6,
                               core_hits=["defect detection"], top_core_weight=1.0)
    trend_multi = _scored_paper("c", "동향 복수", 0.8,
                                core_hits=["sim-to-real", "neuromorphic"], top_core_weight=0.6)

    result = {"papers": [target_multi, target_one, trend_multi], "candidates_found": 3}
    text = generate_digest(result, "p")

    assert "[★★★] 표적 복수" in text
    assert "[★★] 표적 단독" in text
    # 총점이 더 높아도(0.8 > 0.6) 표적어가 없으면 ★ 다 — 이게 핵심이다
    assert "[★] 동향 복수" in text


def test_stars_ignore_recency_driven_score_changes():
    """같은 논문이 며칠 지났다고 별점이 떨어지면 안 된다 — 별점은 순위가
    아니라 분류이고, 한 다이제스트 안의 논문은 어차피 다 최신이다."""
    fresh = _scored_paper("a", "오늘", 0.645, core_hits=["defect detection"],
                          top_core_weight=1.0)
    older = _scored_paper("b", "닷새 전", 0.500, core_hits=["defect detection"],
                          top_core_weight=1.0)
    text = generate_digest({"papers": [fresh, older], "candidates_found": 2}, "p")
    assert "[★★] 오늘" in text
    assert "[★★] 닷새 전" in text


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
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "does_not_exist.db")
    monkeypatch.setattr(server, "REPRO_DIR", tmp_path / "no_repro")
    text = _digest_for(_scored_paper("p1", "DB 없는 논문", 1.0))
    assert "[검증 데이터 없음]" in text
    assert "[재현 –]" in text


# ---------------------------------------------------------------- M3: HTML 다이제스트


def _html_for(papers, **kw):
    from digest import generate_digest_html
    result = {"papers": papers, "candidates_found": kw.get("candidates", len(papers)),
              "excluded_count": kw.get("excluded", 0), "unmatched_count": kw.get("unmatched", 0)}
    return generate_digest_html(result, "우리팀")


def test_html_opens_details_for_flagged_paper(isolated_db):
    """(a) ⑤ flag가 있는 항목은 <details open>으로 펼쳐 보낸다."""
    _seed_verification(isolated_db["db"], "p1", total=31, matched=28)
    html = _html_for([_scored_paper("p1", "flag 논문", 1.0)])
    assert "<details open" in html


def test_html_opens_details_for_failed_repro(isolated_db):
    """재현 실패도 주의 대상이라 펼친다."""
    _seed_verification(isolated_db["db"], "p1", total=10, matched=10)
    _seed_repro(isolated_db["db"], "p1", "https://github.com/a/b", success=False)
    html = _html_for([_scored_paper("p1", "재현 실패 논문", 1.0)])
    assert "<details open" in html


def test_html_leaves_clean_paper_collapsed(isolated_db):
    """(a-2) 정상 항목에는 open 속성이 없다."""
    _seed_verification(isolated_db["db"], "p1", total=10, matched=10)
    _seed_repro(isolated_db["db"], "p1", "https://github.com/a/b", success=True)
    html = _html_for([_scored_paper("p1", "정상 논문", 1.0)])
    assert "<details open" not in html
    assert "<details " in html


def test_html_has_no_external_resources(isolated_db):
    """(b) 외부 이미지·웹폰트·JS 없음 — 이메일 클라이언트가 차단하거나
    프라이버시 경고를 띄운다. arXiv 링크(<a href>)는 사용자가 누르는
    것이라 허용이고, 자동 로딩되는 리소스만 금지한다."""
    _seed_verification(isolated_db["db"], "p1", total=10, matched=10)
    html = _html_for([_scored_paper("p1", "논문", 1.0, abstract="초록")])
    for banned in ("<img", "<script", "<iframe", "@import", "background-image",
                    "url(http", "<link"):
        assert banned not in html, f"외부 리소스 발견: {banned}"


def test_html_stays_under_gmail_clipping_limit(isolated_db):
    """(c) Gmail은 HTML이 약 102KB를 넘으면 잘라낸다. 논문 8편(max_items
    기본값) 기준으로 상한 안에 들어와야 한다."""
    papers = []
    for i in range(8):
        aid = f"p{i}"
        _seed_verification(isolated_db["db"], aid, total=31, matched=28)  # 최악: 전부 flag
        papers.append(_scored_paper(aid, f"제법 긴 논문 제목 {i} " * 5, 1.5,
                                     core_hits=["agent"], domain_hits=["robot"],
                                     abstract="x" * 2000))
    html = _html_for(papers)
    assert len(html.encode("utf-8")) < 102_400


def test_html_escapes_special_characters(isolated_db):
    """제목·초록에 &, <, >가 실제로 들어온다(예: "R&D", "A < B") — 이스케이프
    안 하면 레이아웃이 깨지고 태그 주입도 가능해진다."""
    _seed_verification(isolated_db["db"], "p1", total=10, matched=10)
    html = _html_for([_scored_paper("p1", "A <b>bold</b> & R&D", 1.0)])
    assert "&lt;b&gt;bold&lt;/b&gt;" in html
    assert "&amp;" in html
    assert "<b>bold</b>" not in html


def test_html_reserves_retraction_slot_for_m5(isolated_db):
    """M5(철회 체크)가 채울 최상단 슬롯을 비워둔다."""
    html = _html_for([_scored_paper("p1", "논문", 1.0)])
    assert "<!-- retraction-warnings -->" in html


def test_html_and_text_versions_coexist(isolated_db):
    """(중요) 기존 텍스트판은 그대로 동작해야 한다 — multipart의 plain
    part로 계속 쓰이므로 삭제·변경하면 안 된다."""
    _seed_verification(isolated_db["db"], "p1", total=43, matched=43)
    paper = _scored_paper("p1", "논문", 1.0)
    text = _digest_for(paper)
    html = _html_for([paper])
    assert "[검증 43/43 통과]" in text      # 텍스트판은 대괄호 라벨 유지
    assert "검증 43/43 통과" in html        # HTML판은 chip 안에 들어감
    assert "<details" not in text           # 텍스트판에 태그가 새면 안 됨


def test_html_empty_papers_case(isolated_db):
    html = _html_for([], candidates=12)
    assert "새로 걸린 논문이 없습니다" in html
    assert "<details" not in html


# ---------------------------------------------------------------- M5: 철회 경고 표기


def _seed_retraction(db, arxiv_id, value):
    with sqlite3.connect(db) as con:
        con.execute("INSERT OR REPLACE INTO papers (arxiv_id, is_retracted) VALUES (?,?)",
                    (arxiv_id, value))


def test_retraction_warning_shown_for_confirmed(isolated_db):
    """(c) is_retracted=1 → 최상단에 철회 경고."""
    _seed_retraction(isolated_db["db"], "p1", 1)
    text = _digest_for(_scored_paper("p1", "철회 논문", 1.0))
    assert "[⚠ 철회된 논문]" in text


def test_suspect_warning_shown_for_unconfirmed(isolated_db):
    """(c-2) is_retracted=2 → 확정과 다른 문구로 구분한다."""
    _seed_retraction(isolated_db["db"], "p1", 2)
    text = _digest_for(_scored_paper("p1", "요주의 논문", 1.0))
    assert "[주의: 정정/우려 표명 이력]" in text
    assert "[⚠ 철회된 논문]" not in text


def test_no_marking_for_normal_paper(isolated_db):
    """(c-3) is_retracted=0은 아무 표기도 하지 않는다."""
    _seed_retraction(isolated_db["db"], "p1", 0)
    text = _digest_for(_scored_paper("p1", "정상 논문", 1.0))
    assert "철회" not in text


def test_no_marking_when_never_checked(isolated_db):
    """(c-4) NULL(미조회)에 "철회 아님"이라고 쓰면 조회조차 못 한 논문을
    검증된 정상으로 보이게 만든다 — 아무 말도 하지 않는다(CLAUDE.md 8)."""
    _seed_retraction(isolated_db["db"], "p1", None)
    text = _digest_for(_scored_paper("p1", "미조회 논문", 1.0))
    assert "철회" not in text
    assert "주의" not in text


def test_retraction_warning_appears_before_other_info(isolated_db):
    """경고는 제목 바로 밑, 다른 어떤 정보보다 먼저 나와야 한다."""
    _seed_retraction(isolated_db["db"], "p1", 1)
    text = _digest_for(_scored_paper("p1", "철회 논문", 1.0))
    assert text.index("[⚠ 철회된 논문]") < text.index("왜 걸렸나")


def test_html_retraction_chip_forces_open(isolated_db):
    """HTML판: 철회 항목은 무조건 펼쳐 보낸다 — 이 항목에서 가장 중요한 정보다."""
    _seed_verification(isolated_db["db"], "p1", total=10, matched=10)
    _seed_repro(isolated_db["db"], "p1", "https://github.com/a/b", success=True)
    _seed_retraction(isolated_db["db"], "p1", 1)
    html = _html_for([_scored_paper("p1", "철회 논문", 1.0)])
    assert "<details open" in html          # 검증·재현이 깨끗해도 펼친다
    assert "철회된 논문" in html


def test_html_no_retraction_chip_when_null(isolated_db):
    _seed_verification(isolated_db["db"], "p1", total=10, matched=10)
    html = _html_for([_scored_paper("p1", "미조회 논문", 1.0)])
    assert "철회" not in html


# ---------------------------------------------------------------- M6: S2 TLDR 발췌


def test_s2_tldr_replaces_abstract_for_failed_paper(isolated_db):
    """Deep 처리가 실패한 논문은 우리 요약이 없다 — S2 한줄요약이 있으면
    초록 발췌 대신 쓴다."""
    paper = _scored_paper("p1", "실패 논문", 1.0, abstract="긴 초록 " * 50,
                           deep_status="failed: Gemini·Groq 둘 다 실패")
    paper["s2_tldr"] = "A unified roadmap toward executable AI agent systems."
    text = _digest_for(paper)

    assert "S2 한줄요약 : A unified roadmap" in text
    assert "초록 발췌" not in text


def test_s2_tldr_label_distinguishes_from_verified_summary(isolated_db):
    """라벨을 정직하게 — S2 모델의 미검증 요약이지 우리 ⑤를 통과한 게 아니다."""
    paper = _scored_paper("p1", "실패 논문", 1.0, deep_status="failed: 오류")
    paper["s2_tldr"] = "one line summary"
    text = _digest_for(paper)

    assert "[미검증 · S2 TLDR]" in text
    assert "[검증" not in text


def test_falls_back_to_abstract_when_no_tldr(isolated_db):
    """S2에 없는 논문은 기존 초록 발췌 그대로."""
    paper = _scored_paper("p1", "실패 논문", 1.0, abstract="초록 내용",
                           deep_status="failed: 오류")
    text = _digest_for(paper)

    assert "초록 발췌 : 초록 내용" in text
    assert "[미검증 · 초록 기반]" in text


def test_tldr_not_used_for_successfully_processed_paper(isolated_db):
    """Deep 처리에 성공한 논문에는 tldr을 쓰지 않는다 — 검증된 우리 요약이
    있으므로 미검증 S2 요약으로 덮으면 안 된다."""
    _seed_verification(isolated_db["db"], "p1", total=10, matched=10)
    paper = _scored_paper("p1", "성공 논문", 1.0, abstract="초록", deep_status="ok")
    paper["s2_tldr"] = "S2가 만든 요약"
    text = _digest_for(paper)

    assert "S2가 만든 요약" not in text
    assert "[검증 10/10 통과]" in text


# ---------------------------------------------------------------- 재현 실패 사유 (2026-09-01)
#
# 예전엔 서로 완전히 다른 네 가지 사실이 [재현 ✗] 하나로 뭉개졌다. 아래 표의
# 핵심은 ✗ 와 – 의 구분이다 — ✗ 는 "코드를 돌렸는데 실패", – 는 "돌려보지도
# 못함"이라 후자는 저자 코드에 대한 판정이 아니다.


@pytest.mark.parametrize("stage,detail,expected", [
    ("run", "run_network_suspected", "[재현 ✗ 네트워크 차단 의심]"),
    ("run", "run_timeout", "[재현 ✗ 시간 초과]"),
    ("run", "run_nonzero_exit", "[재현 ✗ 실행 실패]"),
    ("build", "build_failed", "[재현 ✗ 설치 실패]"),
    ("install_only", "install_only_no_run_target", "[재현 ◐ 설치만 확인]"),
    ("no_target", "no_install_target", "[재현 – 실행 대상 없음]"),
    ("clone", "repo_not_found", "[재현 – 저장소 없음(404)]"),
    ("clone", "clone_timeout", "[재현 – 클론 시간 초과]"),
    ("clone", "clone_failed", "[재현 – 클론 실패]"),
])
def test_repro_label_reports_the_actual_reason(isolated_db, stage, detail, expected):
    _seed_repro(isolated_db["db"], "p1", "https://github.com/a/x", success=False,
                stage=stage, fail_detail=detail)
    assert expected in _digest_for(_scored_paper("p1", "논문", 1.0))


def test_missing_repo_is_not_reported_as_code_failure(isolated_db):
    """실측(2608.25176 / YOLOEZA): 저자가 논문에 적은 저장소가 404 였다.
    이건 "저자 코드가 안 돈다"가 아니라 "볼 수 있는 코드가 없다"이므로
    ✗ 를 붙이면 안 된다 — 이 변경이 겨냥한 바로 그 사례다."""
    _seed_repro(isolated_db["db"], "p1", "https://github.com/a/gone", success=False,
                stage="clone", fail_detail="repo_not_found")
    text = _digest_for(_scored_paper("p1", "논문", 1.0))
    assert "[재현 – 저장소 없음(404)]" in text
    assert "✗" not in text


def test_deepest_attempt_decides_the_label(isolated_db):
    """후보를 여러 개 시도하면 **가장 멀리 간** 시도가 정보량이 크다 —
    clone 도 못 한 후보보다 실제로 실행까지 간 후보가 그 논문을 더 말해준다."""
    _seed_repro(isolated_db["db"], "p1", "https://github.com/a/one", success=False,
                stage="clone", attempt=1, fail_detail="repo_not_found")
    _seed_repro(isolated_db["db"], "p1", "https://github.com/a/two", success=False,
                stage="run", attempt=2, fail_detail="run_nonzero_exit")
    assert "[재현 ✗ 실행 실패]" in _digest_for(_scored_paper("p1", "논문", 1.0))


def test_success_still_wins_over_any_failure_detail(isolated_db):
    _seed_repro(isolated_db["db"], "p1", "https://github.com/a/one", success=False,
                stage="run", attempt=1, fail_detail="run_network_suspected")
    _seed_repro(isolated_db["db"], "p1", "https://github.com/a/two", success=True,
                stage="run", attempt=2, fail_detail="")
    assert "[재현 ✓]" in _digest_for(_scored_paper("p1", "논문", 1.0))


def test_old_rows_without_fail_detail_still_render(isolated_db):
    """2026-09-01 이전 29건은 fail_detail 이 NULL 이다. stage 만으로도
    "돌려봤는가"는 알 수 있으므로 그만큼은 말해준다."""
    _seed_repro(isolated_db["db"], "p1", "https://github.com/a/x", success=False,
                stage="run", fail_detail=None)
    assert "[재현 ✗ 실행 실패]" in _digest_for(_scored_paper("p1", "논문", 1.0))


def test_old_rows_with_no_stage_fall_back_to_plain_failure(isolated_db):
    """stage 도 없는 아주 오래된 행은 예전처럼 [재현 ✗] — 데이터가 없는 것을
    아는 척하지 않는다."""
    _seed_repro(isolated_db["db"], "p1", "https://github.com/a/x", success=False,
                stage=None, fail_detail=None)
    assert "[재현 ✗]" in _digest_for(_scored_paper("p1", "논문", 1.0))


def test_network_suspected_label_is_flagged_in_html(isolated_db):
    """네트워크 차단 의심은 ✗ 라 HTML 에서 펼쳐진 채로 나가야 한다
    (기존 needs_attention 규칙이 유지되는지 확인)."""
    _seed_verification(isolated_db["db"], "p1", total=10, matched=10)
    _seed_repro(isolated_db["db"], "p1", "https://github.com/a/x", success=False,
                stage="run", fail_detail="run_network_suspected")
    html = _html_for([_scored_paper("p1", "논문", 1.0)])
    assert "네트워크 차단 의심" in html
    assert "<details open" in html


def test_not_run_labels_do_not_force_attention_in_html(isolated_db):
    """저장소가 404 인 건 저자 주장에 대한 경고가 아니다 — 펼칠 이유가 없다."""
    _seed_verification(isolated_db["db"], "p1", total=10, matched=10)
    _seed_repro(isolated_db["db"], "p1", "https://github.com/a/x", success=False,
                stage="clone", fail_detail="repo_not_found")
    html = _html_for([_scored_paper("p1", "논문", 1.0)])
    assert "저장소 없음(404)" in html
    assert "<details open" not in html


def test_install_only_is_not_collapsed_into_no_target(isolated_db):
    """실측(2026-09-02): requirements.txt 가 있는 저장소가 "실행 대상 없음"으로
    거부돼 "가중치 전용 저장소"와 구분이 안 됐다. ◐ 는 "설치되는 진짜 코드지만
    실행 대상이 없어 판정 불가"라는 뜻이고, – 와 다른 사실이다."""
    _seed_repro(isolated_db["db"], "p1", "https://github.com/a/x", success=False,
                stage="install_only", fail_detail="install_only_no_run_target")
    text = _digest_for(_scored_paper("p1", "논문", 1.0))
    assert "[재현 ◐ 설치만 확인]" in text
    assert "실행 대상 없음" not in text


def test_actually_running_beats_install_only_in_depth(isolated_db):
    """후보 둘 중 하나가 실제로 실행까지 갔으면 그쪽이 정보량이 크다."""
    _seed_repro(isolated_db["db"], "p1", "https://github.com/a/one", success=False,
                stage="install_only", attempt=1, fail_detail="install_only_no_run_target")
    _seed_repro(isolated_db["db"], "p1", "https://github.com/a/two", success=False,
                stage="run", attempt=2, fail_detail="run_nonzero_exit")
    assert "[재현 ✗ 실행 실패]" in _digest_for(_scored_paper("p1", "논문", 1.0))


def test_install_only_beats_build_failure_in_depth(isolated_db):
    """설치가 된 쪽이 안 된 쪽보다 멀리 간 것이다."""
    _seed_repro(isolated_db["db"], "p1", "https://github.com/a/one", success=False,
                stage="build", attempt=1, fail_detail="build_failed")
    _seed_repro(isolated_db["db"], "p1", "https://github.com/a/two", success=False,
                stage="install_only", attempt=2, fail_detail="install_only_no_run_target")
    assert "[재현 ◐ 설치만 확인]" in _digest_for(_scored_paper("p1", "논문", 1.0))


def test_old_install_only_row_without_detail_still_renders(isolated_db):
    _seed_repro(isolated_db["db"], "p1", "https://github.com/a/x", success=False,
                stage="install_only", fail_detail=None)
    assert "[재현 ◐ 설치만 확인]" in _digest_for(_scored_paper("p1", "논문", 1.0))


# ---------------------------------------------------------------- 본문 비공개 논문 (2026-09-03)
#
# S2 를 붙이자 팀 표적 논문이 실제로 걸렸는데(★★★ 2편) 그날 상위 6편 중 5편이
# arXiv ID 도 오픈액세스 PDF 도 없는 저널 논문이었다(실측 59편 중 35편, 59%).
# 링크는 `https://arxiv.org/abs/None` 으로 깨졌고 라벨은 "처리 실패"였다.

_TITLE_ONLY = {
    "title": "PhyHGNet: Physics guided micro defect detection",
    "doi": "10.1016/j.solener.2026.1",
    "venue": "Solar Energy",
    "_score": {"priority": 1.1, "core_hits": ["defect detection"],
               "domain_hits": [], "venue_hit": None, "top_core_weight": 1.0},
}


def test_paper_link_uses_doi_when_not_arxiv():
    """arXiv 밖 논문에 arxiv.org/abs/None 을 찍지 않는다."""
    assert digest.paper_link({"arxiv_id": "2608.1"}) == "https://arxiv.org/abs/2608.1"
    assert digest.paper_link({"doi": "10.1/x"}) == "https://doi.org/10.1/x"
    # 업로드 PDF 의 합성 ID(pdf-*)는 arXiv 링크가 아니다
    assert digest.paper_link({"arxiv_id": "pdf-a", "doi": "10.1/y"}) == "https://doi.org/10.1/y"
    assert digest.paper_link({"open_access_pdf": "http://x/y.pdf"}) == "http://x/y.pdf"
    assert digest.paper_link({}) == ""


def test_no_broken_arxiv_none_link_anywhere():
    """핵심 회귀: 어떤 렌더 경로에서도 /abs/None 이 나오면 안 된다."""
    scan = {"papers": [], "candidates_found": 9, "title_only_papers": [_TITLE_ONLY]}
    for text in (digest.generate_digest(scan, "t"), digest.generate_digest_html(scan, "t")):
        assert "abs/None" not in text
        assert "10.1016/j.solener.2026.1" in text


def test_out_of_rank_papers_are_listed_not_called_failures():
    """순위 밖 논문은 '처리 실패'가 아니다 — §8-24 와 같은 구분.

    2026-09-04: 절 이름이 "본문 비공개"에서 "그 밖에 걸린 논문"으로 바뀌었다.
    가르는 기준이 본문 확보 여부에서 관련도 순위로 옮겨갔기 때문이다.
    **주장("실패라 부르지 않는다")은 그대로다.**
    """
    scan = {"papers": [], "candidates_found": 9, "title_only_papers": [_TITLE_ONLY]}
    text = digest.generate_digest(scan, "t")
    assert "그 밖에 걸린 논문" in text
    assert "처리 실패" not in text
    assert "PhyHGNet" in text
    # 논문이 있는데 "없습니다"로 끝나면 안 된다
    assert "오늘은 새로 걸린 논문이 없습니다" not in text


def test_empty_scan_still_reports_nothing_found():
    """제목만 목록도 비면 종전대로 빈 다이제스트."""
    scan = {"papers": [], "candidates_found": 0}
    assert "오늘은 새로 걸린 논문이 없습니다" in digest.generate_digest(scan, "t")


# ---------------------------------------------------------------- 초록 기반 정리 (2026-09-04)
#
# "여전히 초록만 보고 있고 내용이 짧더라. 이걸 요약 정리라 할 수 있나?"
# 본문을 못 받으면 잘린 초록 한 토막에 오류 문자열을 붙여 내보내고 있었다.

_BRIEF = ("- 무엇을 하려 했는가 : 금속 표면의 미세 결함을 적은 데이터로 검출하려 했다.\n"
          "- 어떻게 했는가 : StyleGAN2-ADA 로 결함 이미지를 생성해 학습시켰다.\n"
          "- 무엇을 보였는가 : 초록에 없음.")


def _brief_paper():
    return {"arxiv_id": None, "doi": "10.1/x", "title": "Metal Surface Defect Detection",
            "deep_status": "abstract_only", "abstract_brief": _BRIEF,
            "_score": {"priority": 1.0, "core_hits": ["defect detection"],
                       "domain_hits": [], "venue_hit": None, "top_core_weight": 1.0}}


def test_abstract_brief_replaces_the_error_dump():
    entry = digest._paper_entry(1, _brief_paper())
    assert "StyleGAN2-ADA" in entry
    assert "처리 실패" not in entry          # 페이월은 우리 실패가 아니다
    assert "초록 발췌" not in entry          # 잘린 토막 대신 정리된 글


def test_abstract_brief_never_claims_verification():
    """본문 요약과 라벨을 같이 쓰면 안 된다 — ⑤ 를 통과한 게 아니다(규칙 8)."""
    entry = digest._paper_entry(1, _brief_paper())
    assert "[초록 기반 정리 · 본문 미확보 · 미검증]" in entry
    assert "검증 " not in entry.replace("미검증", "")


def test_abstract_brief_falls_back_when_empty():
    """정리를 못 만들었으면 예전 경로 그대로 — 빈 요약을 요약인 척하지 않는다."""
    p = _brief_paper()
    p["abstract_brief"] = ""
    p["deep_status"] = "failed: 오픈액세스 PDF 수집 실패"
    entry = digest._paper_entry(1, p)
    assert "초록 발췌" in entry


# ---------------------------------------------------------------- 오늘의 흐름 (2026-09-04)
#
# "동향을 알려줘야지 논문 제목에 별표만 친 게 왜 동향이야?"
# 동향 절이 키워드 빈도표 한 줄뿐이었다.

def _scan_with_story(ungrounded=None):
    return {"papers": [{"arxiv_id": "2609.1", "title": "A defect detection method",
                        "deep_status": "ok",
                        "_score": {"priority": 1.0, "core_hits": ["defect detection"],
                                   "domain_hits": [], "venue_hit": None, "top_core_weight": 1.0}}],
            "candidates_found": 321, "core_hit_counts": {"quantization": 22},
            "narrative": ("결함 검출은 합성 데이터로 메우는 흐름이 뚜렷하다.",
                          ungrounded or [])}


def test_daily_digest_carries_a_narrative_not_just_counts():
    text = digest.generate_digest(_scan_with_story(), "t")
    assert "결함 검출은 합성 데이터로 메우는 흐름이 뚜렷하다." in text
    assert "오늘의 흐름" in text
    # 빈도표는 "동향"이라 부르지 않는다 — 그건 셈이다
    assert "키워드별 적중 편수" in text


def test_narrative_is_labelled_unverified_and_separated_from_counts():
    text = digest.generate_digest(_scan_with_story(), "t")
    assert "검증되지 않았다" in text
    assert text.index("키워드별 적중 편수") < text.index("오늘의 흐름")


def test_narrative_warns_about_invented_numbers():
    text = digest.generate_digest(_scan_with_story(["17"]), "t")
    assert "원문에 없는 숫자" in text and "17" in text


def test_digest_renders_without_a_narrative():
    scan = _scan_with_story()
    del scan["narrative"]
    text = digest.generate_digest(scan, "t")
    assert "오늘의 흐름" not in text
    assert "키워드별 적중 편수" in text


def test_markdown_bold_does_not_leak_into_plain_email():
    """모델 회전(§8-38-1) 뒤 한 메일 안에서 형식이 섞였다 — flash-latest 는
    `**굵게**`, flash-lite 는 평문으로 답했다. 받는 쪽에서 지운다."""
    p = _brief_paper()
    p["abstract_brief"] = "- **무엇을 하려 했는가** : 결함을 검출한다."
    entry = digest._paper_entry(1, p)
    assert "**" not in entry
    assert "무엇을 하려 했는가 : 결함을 검출한다." in entry


def test_markdown_bold_stripped_from_narrative_too():
    scan = _scan_with_story()
    scan["narrative"] = ("**결함 검출**은 합성 데이터로 메우는 흐름이다.", [])
    text = digest.generate_digest(scan, "t")
    assert "**" not in text
    assert "결함 검출은 합성 데이터로 메우는 흐름이다." in text


# ---------------------------------------------------------------- 평문 메일 형식 (2026-09-04)
#
# 실제 논문 2편으로 시험하다 잡은 것들. 요약 내용은 정확한데 형식 때문에
# 메일에서 안 읽혔다.

def test_result_bullets_do_not_collapse_into_one_line():
    """핵심 회귀 — 실측(2608.28070)에서 `- - CF-YOLO는 … - 컴포넌트 … - 외부 …`
    가 한 줄로 찍혔다. '결과 절은 산문 문단'이라는 전제가 틀렸다."""
    got = digest._paragraphs("- 첫째 결과다.\n- 둘째 결과다.\n- 셋째 결과다.")
    assert got == ["첫째 결과다.", "둘째 결과다.", "셋째 결과다."]


def test_prose_paragraphs_still_work():
    """불릿을 받게 만들면서 기존 산문 경로가 깨지면 안 된다."""
    got = digest._paragraphs("산문 첫 줄\n이어지는 줄\n\n두 번째 문단")
    assert got == ["산문 첫 줄 이어지는 줄", "두 번째 문단"]


def test_bullet_with_continuation_line_stays_one_item():
    got = digest._paragraphs("- 불릿 하나\n  이어지는 설명\n- 불릿 둘")
    assert got == ["불릿 하나 이어지는 설명", "불릿 둘"]


def test_latex_is_made_readable_not_deleted():
    """메일에는 MathJax 가 없다. 지우면 정보를 버리므로 읽히게만 만든다."""
    got = digest._plain(r"입력은 표면 이미지( $I_i \in \mathbb{R}^{H \times W \times C}$ )이며")
    assert "\\" not in got and "$" not in got
    assert "∈" in got and "×" in got
    assert "I_i" in got            # 기호 이름은 살아 있다


def test_latex_loss_names_survive():
    got = digest._plain(r"회귀 손실( $\mathcal{L}_{CIoU}$ )과 분류 손실( $\mathcal{L}_{BCE}$ )")
    assert "L_CIoU" in got and "L_BCE" in got
    assert "mathcal" not in got


def test_plain_leaves_ordinary_text_alone():
    for s in ("정밀도 0.882, AP50 0.823 [S0170].", "640×640 해상도 · 배치 16"):
        assert digest._plain(s) == s


# ---------------------------------------------------------------- 재현 ✓ 의 실체 (2026-09-04)
#
# "재현을 진짜 해?" 라는 질문에 성공 3건을 열어보니:
#   2609.02212  `fudu --help`               → CLI 사용법 출력 (2.6초)
#   2110.15045  `python -c "import models"` → 출력 없음, exit 0 (1.3초)
# 결과가 재현된 게 아니라 설치·임포트가 됐다는 뜻인데 라벨은 `[재현 ✓]` 였다.

def _write_repro_log(tmp_path, arxiv_id, run_cmd, prefix=""):
    import json as _json
    body = _json.dumps({"arxiv_id": arxiv_id, "success": True,
                        "log": [{"success": True, "stage": "run",
                                 "plan": {"run_cmd": run_cmd}, "attempts": []}]})
    (tmp_path / f"{arxiv_id}.log").write_text(prefix + body, encoding="utf-8")


def test_import_only_success_is_not_called_bare_reproduction(tmp_path, monkeypatch):
    _write_repro_log(tmp_path, "p1", 'python -c "import models"')
    monkeypatch.setattr(digest.storage, "REPRO_DIR", tmp_path)
    assert digest._verified_kind("p1") == "설치·임포트 확인"


def test_entry_point_help_is_distinguished(tmp_path, monkeypatch):
    _write_repro_log(tmp_path, "p2", "fudu --help")
    monkeypatch.setattr(digest.storage, "REPRO_DIR", tmp_path)
    assert digest._verified_kind("p2") == "설치·진입점 확인"


def test_plain_text_prefix_before_json_is_tolerated(tmp_path, monkeypatch):
    """실측(2609.02212): 로그 앞에 후보 탐색 평문이 붙어 있어 통째로 파싱하면
    실패했고, 라벨이 조용히 `[재현 ✓]` 로 뭉뚱그려졌다."""
    _write_repro_log(tmp_path, "p3", "fudu --help",
                     prefix="  [계측] ⑦ 저장소 탐색: API 호출 1회\n  [후보 제외] ...\n")
    monkeypatch.setattr(digest.storage, "REPRO_DIR", tmp_path)
    assert digest._verified_kind("p3") == "설치·진입점 확인"


def test_missing_log_adds_nothing(tmp_path, monkeypatch):
    """모르면 덧붙이지 않는다 — 없는 근거를 만들어내지 않는다(규칙 8)."""
    monkeypatch.setattr(digest.storage, "REPRO_DIR", tmp_path)
    assert digest._verified_kind("없는논문") == ""
