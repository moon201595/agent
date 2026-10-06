"""② 비연구 제목이 연구 카드 자리를 먹는 회귀를 막는다(2026-10-06)."""
import pytest
import profile_scoring as ps
from digest import _filtered_line

BAD = [
    "The Third Workshop on Agentic and Generative AI for E-Commerce",
    "Intent Formalization: Assessing the Quality of AI-Generated Formal Program Specifications (Keynote)",
    "Tutorial on Human-centric Embodied Multimodal Interaction (HEMI) in conjunction with ICMI’26",
    "Composing Agents, Compounding Risks: A Tutorial on Robustness and Alignment in Multi-Agent Recommender Systems",
]
GOOD = [
    "Collaboratively Designing Curriculum-Aligned Bee-Bot Learning Resources: Insights from a Qualitative Case Study of a Professional Development Workshop for Primary Teachers",
    "Experts’ and Community Insights on Measuring Comfort and Perceived Safety in Automated Driving: Synthesis of a Participatory Workshop",
    "VidTutorAssistant: Automating Responses to Programming Tutorial Questions",
    "Ergodic Control and Controlled Diffusion for Robot Learning: Review and Tutorial",
]
PROFILE = {"core_topics": ["research"], "exclude": [], "target_domain": []}

@pytest.mark.parametrize("title", BAD + ["Keynote: Research", "Panel: Research", "Proceedings of Research", "Research (Workshop)"])
def test_행사소개는_생산선별에서_빠진다(title):
    """규칙 호출이나 행사 구문을 제거하면 제외 사유·순위 제외·집계 검사가 실패한다."""
    p = {"title": title, "abstract": "research", "arxiv_id": "event"}
    score = ps.score_paper(p, PROFILE)
    assert score["excluded"] is True
    assert score["filter_reason"] == "non_research_title"
    result = ps.score_and_rank([p], PROFILE)
    assert result["papers"] == []
    assert result["excluded_count"] == 1
    assert _filtered_line(result) == "제외 규칙 1건(제외어·비연구 제목)"

@pytest.mark.parametrize("title", GOOD)
def test_정상연구의_행사낱말은_보존한다(title):
    """구문 대신 workshop·tutorial 낱말 전체를 막으면 운영 DB의 정상 연구 보존 검사가 실패한다."""
    p = {"title": title, "abstract": "research", "arxiv_id": "study"}
    result = ps.score_and_rank([p], PROFILE)
    assert [p["arxiv_id"] for p in result["papers"]] == ["study"]
    assert result["excluded_count"] == 0

def test_초록의_행사소개는_제목규칙으로_막지않는다():
    """제목 규칙 입력을 초록까지 넓히면 연구의 행사 언급 보존 검사가 실패한다."""
    p = {"title": "Research evaluation", "abstract": "A Tutorial on research", "arxiv_id": "study"}
    assert ps.score_paper(p, PROFILE)["excluded"] is False

def test_실행관측에_비연구사유를_저장한다(tmp_path, monkeypatch):
    """관측 사유를 제외어로 덮거나 규칙 호출을 빼면 dropped·사유·메일 집계 검사가 실패한다."""
    import asyncio
    import sqlite3
    from datetime import datetime, timezone
    import research_profile as rp
    import scan_search
    import http_client
    db = tmp_path / "test.db"
    rp.create_profile(db, "test", "테스트", ["research"], s2_seeds=["research"])
    papers = [{"title": title, "abstract": "research", "arxiv_id": f"event{i}",
               "published": datetime.now(timezone.utc).isoformat()} for i, title in enumerate(BAD)]

    async def fake_get(client, params):
        class Response:
            text = "fake"
        return Response()

    async def fake_s2(*args, **kwargs):
        return {"papers": [], "status": "skipped", "query": "test", "keywords_failed": 0}

    monkeypatch.setattr(http_client, "throttled_arxiv_get", fake_get)
    monkeypatch.setattr(http_client, "parse_arxiv_feed", lambda text: papers)
    monkeypatch.setattr(scan_search.s2_delta, "find_new_papers_since", fake_s2)
    result = asyncio.run(scan_search.scan_profile(db, "test", None, max_pages=1))
    assert result["papers"] == []
    assert result["reserve"] == []
    assert result["excluded_count"] == 4
    with sqlite3.connect(db) as con:
        rows = con.execute("SELECT outcome, filter_reason, rank_pos FROM candidate_observations").fetchall()
    assert rows == [("dropped", "non_research_title", None)] * 4
    assert _filtered_line(result) == "제외 규칙 4건(제외어·비연구 제목)"
