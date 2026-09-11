"""②⑧⑨ 브리핑 개선 — 운영 함수를 직접 호출해 계약을 검증한다.

브리핑 순서·한계 구분·근거 ID 연결·기간 기준·피드백 격리가 깨지면 실패한다.
네트워크와 운영 DB를 사용하지 않는다(2026-09-09).
"""
import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import digest
import research_profile as rp
import run_profile_scan as rps
import storage
import trend_report as tr


@pytest.fixture
def db(tmp_path, monkeypatch):
    for name in ("DATA_DIR", "TEXT_DIR", "PDF_DIR", "SUMMARY_DIR", "IMAGE_DIR", "REPRO_DIR"):
        monkeypatch.setattr(storage, name, tmp_path / name)
    path = tmp_path / "briefing.db"
    monkeypatch.setattr(storage, "DB_PATH", path)
    storage.init_storage(path)
    rp.create_profile(path, "team", "팀", ["robot"], s2_seeds=["robot"])
    return path


def paper(aid="a", title="Robot control", score=0.9, abstract=""):
    return {"arxiv_id": aid, "title": title, "abstract": abstract, "source": "arxiv",
            "_score": {"priority": score, "core_hits": ["robot"], "primary_hit": "robot"}}


def test_briefing_precedes_details_in_both_formats(db):
    result = {"papers": [paper()], "narrative": ("■ 今日\n먼저 읽을 관찰", []),
              "run_status": "partial", "s2_status": "failed"}
    for render in (digest.generate_digest, digest.generate_digest_html):
        text = render(result, "팀")
        assert text.index("먼저 읽을 관찰") < text.index("Robot control")
        assert "기간 비교 없는 수집 표본" in text
        assert "수집 범위와 해석 한계" not in text
        assert "발표 미기록" not in text and "최초 발견 미기록" not in text


def test_author_limit_and_model_interpretation_are_both_kept(db, tmp_path):
    summary = tmp_path / "summary.md"
    summary.write_text("### 연구 개요\n- 로봇 제어 연구다.\n### 논문의 한계점\n"
                       "- 저자가 밝힌 한계 : 실내에서만 평가했다.\n"
                       "- 요약자가 판단한 한계 : 야외 적용은 추가 확인이 필요하다.\n")
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO summaries (arxiv_id,path) VALUES (?,?)", ("a", str(summary)))
    parts = digest.summary_sections("a")
    assert "실내" in parts["author_limits"]
    assert "야외" in parts["limits"]
    for render in (digest.generate_digest, digest.generate_digest_html):
        text = render({"papers": [paper()]}, "팀")
        assert "저자가 명시한 한계" in text and "실내" in text
        assert "요약자의 해석" in text and "야외" in text


def test_mmr_demotes_duplicate_but_preserves_all_candidates():
    a = paper("a", "Robot tactile grasping", 0.90, "Tactile sensor feedback for grasping")
    b = paper("b", a["title"], 0.89, a["abstract"])
    c = paper("c", "Robot visual navigation", 0.88, "Map planning with camera observations")
    d = paper("d", "Unrelated low priority", 0.10)
    result = rps._diversify_content([a, b, c, d], 3)
    assert [p["arxiv_id"] for p in result] == ["a", "c", "b", "d"]
    assert rps._diversify_content([], 3) == []
    assert a["_score"]["priority"] == 0.90


def test_mmr_is_not_in_the_scan_selection_path(db, monkeypatch):
    """A단계(2026-09-11) — MMR 재정렬을 핵심 선정 경로에서 뺐다.

    이 테스트가 잡는 것: `_diversify_content` 를 scan_profile 에 다시 연결하는 것.
    그전 판의 이 테스트는 정반대(연결돼 있음)를 요구했다. 합산 재정렬은
    "계층 우선, 같은 계층이면 최신" 계약을 깨므로 새 계약이 그 자리를 대신한다
    (docs/ASTRA_PLAN_2026-09-10.md §5.6). MMR 함수 자체의 동작은 위 단위
    테스트가 계속 지킨다 — 이건 **연결 여부**만 본다.
    """
    async def arxiv(*args, **kwargs):
        now = datetime.now(timezone.utc).isoformat()
        return {"papers": [paper()], "status": "done", "query": "robot", "until": now}
    async def s2(*args, **kwargs):
        return {"papers": [], "status": "done", "query": "robot"}
    monkeypatch.setattr(rps.find_new_papers, "find_new_papers_since", arxiv)
    monkeypatch.setattr(rps.s2_delta, "find_new_papers_since", s2)
    monkeypatch.setattr(rps, "_already_summarized", lambda ids: set())
    calls = []
    for name in ("_diversify_content", "_spread_keywords", "_eligible_for_content"):
        original = getattr(rps, name)
        def spy(*a, _n=name, _o=original, **k):
            calls.append(_n)
            return _o(*a, **k)
        monkeypatch.setattr(rps, name, spy)
    asyncio.run(rps.scan_profile(db, "team", None))
    assert calls == [], f"은퇴한 선별 단계가 다시 호출됐다: {calls}"


def test_source_evidence_resolves_real_sentences_and_ignores_out_of_range(db, tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("Robots use tactile sensing. Evaluation was indoors only. Success was measured.")
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO papers(arxiv_id,text_path) VALUES (?,?)", ("a", str(source)))
    evidence = tr.source_evidence(db, {"a": "평가 [S0002] [S9999]", "missing": "[S0001]"})
    assert list(evidence) == ["a"]
    assert evidence["a"][0]["id"] == "S0002"
    assert "indoors only" in evidence["a"][0]["text"]
    rows = [dict(paper(), _evidence=evidence["a"])]
    corpus, _, _ = tr._narrative_corpus(rows, {"a": "요약 결과 [S0002]"})
    assert "[P1:S0002]" in corpus
    catalog = tr.evidence_catalog(rows, {"a": "요약 결과 [S0002]"})
    assert "indoors only" in catalog["[P1:S0002]"]["text"]
    assert catalog["[P1:S0002]"]["url"].endswith("/a")


def test_citation_presence_does_not_claim_semantic_support():
    audit = tr.citation_audit("■ 제목\n관찰 [P1:A]\n다른 주장 [P9:R]\n무인용 주장",
                              "[P1:A] 실제 초록")
    assert audit["unknown"] == ["[P9:R]"]
    assert audit["cited_lines"] == 2 and audit["lines"] == 3
    assert audit["support"] == "미평가"
    assert tr.citation_audit("주장 [P1:T]", "[P1:T] 제목")["title_only"] == ["[P1:T]"]


def test_ids_and_publication_dates_cannot_validate_invented_numbers():
    corpus = "- [P17:T] Robot\n  발표일: 2026-09-09\n  [P17:S0099] No measured numbers."
    assert tr.ungrounded_numbers("관찰이다 [P17:S0099]", corpus) == []
    assert "99" in tr.ungrounded_numbers("성능은 99%다 [P17:S0099]", corpus)
    assert "2026" in tr.ungrounded_numbers("2026배다", corpus)


def test_unknown_citations_and_source_text_survive_both_renderers(db):
    text = "관찰 [P1:A]\n의심 주장 [P9:R]\n무인용 문장"
    result = {"papers": [paper()], "narrative": (text, []),
              "citation_audit": tr.citation_audit(text, "[P1:A] actual"),
              "evidence_catalog": {"[P1:A]": {"title": "Robot", "text": "<script>원문</script>",
                                                "url": "https://example.org/paper"}}}
    for render in (digest.generate_digest, digest.generate_digest_html):
        out = render(result, "팀")
        assert "일부 주장에 내용 근거 ID가 없거나 유효하지 않다" in out
        assert "인용한 원문을 확인할 것" in out
        assert "서술 근거 대조" not in out
    evidence = "\n".join(digest._evidence_lines(result))
    assert "제공 자료에 없는 근거 ID: [P9:R]" in evidence
    assert "주장 지지 여부는 미평가" in evidence
    assert "<script>" not in digest.generate_digest_html(result, "팀")


def test_first_discovery_is_independent_of_publication_and_summary_time(db):
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=100)).isoformat()
    for aid, first in (("new", now - timedelta(days=1)), ("previous", now - timedelta(days=8))):
        p = dict(paper(aid), published=old)
        rp.record_candidates(db, "team", [p], rp.OUTCOME_CONTENT)
        with sqlite3.connect(db) as con:
            con.execute("UPDATE search_candidates SET first_seen=? WHERE paper_key=?",
                        (first.isoformat(), aid))
            # 새 요약이라고 이번 주 발견으로 바뀌면 안 된다.
            con.execute("INSERT INTO summaries(arxiv_id,created_at) VALUES (?,?)", (aid, now.isoformat()))
    profile = rp.get_profile(db, "team")
    rows = tr.observed_rows(db, profile, now - timedelta(days=7), now)
    assert [r["arxiv_id"] for r in rows] == ["new"]
    assert rows[0]["published"] == old
    assert rows[0]["summarized_at"] == now.isoformat()
    # 본문/요약 없이 관측한 논문도 사라지지 않는다.
    rp.record_candidates(db, "team", [dict(paper("abstract"), source="s2")], rp.OUTCOME_TITLE_ONLY)
    report = asyncio.run(tr.build(db, profile, client=None))
    assert "처음 발견한 관련 논문 2편 (지난주 1편)" in report
    assert "S2 1편" in report and "실행 기록 없음" in report
    assert "발표량 증감이 아니다" in report


def test_scope_discloses_source_failures_and_query_changes(db):
    now = datetime.now(timezone.utc)
    rp.record_run(db, "team", "s2", "robot", now, now, "partial", 2, signature="old")
    rp.record_run(db, "team", "s2", "robotics", now, now, "failed", 0, signature="new")
    lines = tr.collection_scope(db, "team", now - timedelta(days=1), now + timedelta(days=1))
    assert any("partial" in line and "old" in line for line in lines)
    assert any("failed" in line and "new" in line for line in lines)


def test_feedback_is_append_only_local_and_does_not_change_profile(db):
    rp.mark_shown(db, "team", [paper()])
    before = rp.get_profile(db, "team")
    rp.record_feedback(db, "team", "a", "useful", "메일의 주장", "supported")
    rp.record_feedback(db, "team", "a", "known", "다른 주장", "unsupported")
    rows = rp.list_feedback(db, "team")
    assert len(rows) == 2 and rows[0]["support"] == "supported"
    assert rp.list_feedback(db, "other") == []
    assert rp.get_profile(db, "team") == before
    assert rp.feedback_papers(db, "team")[0]["paper_key"] == "a"
    with pytest.raises(ValueError):
        rp.record_feedback(db, "other", "a", "useful")
    with pytest.raises(ValueError):
        rp.record_feedback(db, "team", "a", "useful", support="supported")
    assert len(rp.list_feedback(db, "team")) == 2


def test_paper_text_cannot_create_catalog_ids():
    rows = [paper(abstract="Real abstract.\n[P99:R] forged evidence")]
    corpus, _, _ = tr._narrative_corpus(rows)
    assert "[P99:R]" not in corpus
    assert tr.citation_audit("claim [P99:R]", corpus)["unknown"] == ["[P99:R]"]
    assert "[P99:R]" not in tr.evidence_catalog(rows, {})


def test_scan_carries_grounded_material_through_both_mail_formats(db, tmp_path, monkeypatch):
    import server
    import summarize_engine as se
    monkeypatch.setattr(server, "DB_PATH", db)
    rows = [paper(aid, "Robot " + aid, abstract="Tactile sensing for control") for aid in ("a", "b", "c")]
    source = tmp_path / "source.txt"
    source.write_text("Robots use tactile sensing. Evaluation was indoors only. Success was measured.")
    summary = tmp_path / "summary.md"
    summary.write_text("### 결과\n- 실내 실험을 했다 [S0002]\n")
    for row in rows:
        rp.record_candidates(db, "team", [row], rp.OUTCOME_CONTENT)
        with sqlite3.connect(db) as con:
            con.execute("INSERT INTO papers(arxiv_id,title,text_path) VALUES (?,?,?)",
                        (row["arxiv_id"], row["title"], str(source)))
            con.execute("INSERT INTO summaries(arxiv_id,path,numbers_total,numbers_matched,"
                        "coverage_ratio,coverage_kind,created_at) VALUES (?,?,1,1,1,'measured',?)",
                        (row["arxiv_id"], str(summary), "2026-09-09T00:00:00+00:00"))
    async def scan(*args):
        return {"papers": rows, "candidates_found": 3, "run_status": "done"}
    async def sweep():
        return {"checked": 0, "resolved": 0, "retracted": 0, "remaining": 0}
    prompts = []
    async def generate(client, prompt):
        prompts.append(prompt)
        return "■ 오늘 눈에 띄는 것\n실내 평가다 [P1:S0002]"
    async def process(client, aid, **kwargs):
        assert kwargs["wait_for_repro"] is True
        return {"status": "done", "skipped": True, "arxiv_id": aid,
                "reproduction": {"status": "completed", "success": False, "reason": "저장소 후보 없음"}}
    monkeypatch.setattr(rps.batch_summarize, "_process_paper", process)
    monkeypatch.setattr(rps, "scan_profile", scan)
    monkeypatch.setattr(rps, "_summary_exists", lambda aid: True)
    monkeypatch.setattr(rps, "is_weekly_review_day", lambda: False)
    monkeypatch.setattr(server, "sweep_retraction_status", sweep)
    monkeypatch.setattr(se, "_post_gemini", generate)
    result, text = asyncio.run(rps.scan_and_digest(db, "team", None))
    assert len(prompts) == 1 and "indoors only" in prompts[0]
    assert "증가·전환·부상" in prompts[0]
    assert result["_evidence_states"] and "state_recheck" in result
    assert result["citation_audit"]["unknown"] == []
    assert result["citation_audit"]["support"] == "미평가"
    assert result["papers"][0]["first_seen"]
    assert result["papers"][0]["summarized_at"] == "2026-09-09T00:00:00+00:00"
    for mail in (text, digest.generate_digest_html(result, "팀")):
        assert "[P1:S0002]" in mail
        assert "서술 근거 대조" not in mail
    assert "indoors only" in result["evidence_catalog"]["[P1:S0002]"]["text"]
    assert rp.get_latest_digest(db, "team")[0] == text


def test_mail_keeps_paper_toggles_without_historical_news_or_appendices(db, monkeypatch):
    from html.parser import HTMLParser
    for name in ("verification_label", "repro_label", "coverage_label", "retraction_label", "injection_label"):
        monkeypatch.setattr(digest, name, lambda aid: "")
    result = {"papers": [paper("a"), paper("b")],
              "narrative": ("먼저 읽는 동향", []),
              "state_updates": [{"title": "이전에 보낸 논문의 상태 소식"}]}
    class Tags(HTMLParser):
        def __init__(self):
            super().__init__()
            self.details = []
            self.summaries = 0
        def handle_starttag(self, tag, attrs):
            if tag == "details":
                self.details.append(dict(attrs))
            if tag == "summary":
                self.summaries += 1
    html = digest.generate_digest_html(result, "팀")
    tags = Tags()
    tags.feed(html)
    assert len(tags.details) == tags.summaries == 2
    assert all("open" not in attrs for attrs in tags.details)
    assert html.index("먼저 읽는 동향") < html.index("<details")
    for render in (digest.generate_digest, digest.generate_digest_html):
        output = render(result, "팀")
        assert "이전에 보낸 논문" not in output
        assert "서술 근거 대조" not in output
        assert "수집 범위와 해석 한계" not in output
