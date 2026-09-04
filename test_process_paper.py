"""④ 요약 경로의 본체 — 네트워크 없이 돈다 (2026-09-02).

왜 이 파일이 먼저인가: 영향 반경이 제일 크다. ⑦ 이 잘못되면 재현 라벨
하나가 비지만, ④ 가 잘못되면 **틀린 요약이 ⑤ 를 통과해 팀 메일로 나간다.**
그런데 이 함수는 커버리지 18% 였고, 2026-09-02 에 넣은 오픈액세스 분기는
한 줄도 검증되지 않은 상태였다.

여기서 고정하는 계약:
  · 어느 경로로 본문을 받든 그 뒤(요약·저장·철회·⑦ 트리거)는 한 줄기다
  · ⑦ 트리거는 이 함수가 소유한다(CLAUDE.md 5) — 실패 시엔 안 부른다
  · 실패는 예외가 아니라 status dict 로 나간다(호출부가 한 편의 실패로
    나머지를 멈추지 않게)
"""

import asyncio
import json

import pytest

import batch_summarize as bs


@pytest.fixture(autouse=True)
def stub_pipeline(monkeypatch):
    """네트워크·LLM·Docker 를 전부 가짜로. 각 단계가 실제로 불렸는지는
    calls 로 확인한다."""
    calls = {"fetch_arxiv": [], "fetch_pdf": [], "summarize": [], "save": [],
             "retraction": [], "repro": []}

    async def fake_fetch_paper(params):
        calls["fetch_arxiv"].append(params.arxiv_id)
        return json.dumps({"arxiv_id": params.arxiv_id, "title": "arXiv 논문"})

    async def fake_fetch_pdf(url, title="", source_note=""):
        calls["fetch_pdf"].append((url, source_note))
        return {"arxiv_id": "pdf-abc123", "title": title or "저널 논문",
                "text_chars": 4321}

    def fake_read_full_text(aid):
        return "본문 " * 100

    async def fake_summarize(client, text, template, on_progress=None):
        calls["summarize"].append(template)
        return "### 결론\n① 한 줄 요약 : 요약본", "gemini"

    async def fake_save(params):
        calls["save"].append((params.arxiv_id, params.engine))
        return json.dumps({"saved_path": "x",
                           "verification": {"pass_ratio": 1.0, "matched": 3,
                                            "total_numbers": 3}})

    async def fake_retraction(aid):
        calls["retraction"].append(aid)
        return 0

    def fake_launch(aid):
        calls["repro"].append(aid)
        return "코드 재현을 백그라운드에서 시작함"

    monkeypatch.setattr(bs.server, "fetch_paper", fake_fetch_paper)
    monkeypatch.setattr(bs.server, "fetch_pdf_from_url", fake_fetch_pdf)
    monkeypatch.setattr(bs.server, "read_full_text", fake_read_full_text)
    monkeypatch.setattr(bs.server, "save_summary", fake_save)
    monkeypatch.setattr(bs.server, "refresh_retraction_status", fake_retraction)
    monkeypatch.setattr(bs.engine, "summarize", fake_summarize)
    monkeypatch.setattr(bs.docker_runner, "launch_background", fake_launch)
    return calls


def _run(arxiv_id="", paper=None):
    return asyncio.run(bs._process_paper(None, arxiv_id, paper=paper))


# ---------------------------------------------------------------- arXiv 경로


def test_arxiv_paper_runs_the_whole_chain(stub_pipeline):
    out = _run("2608.29955")
    assert out["status"] == "done"
    assert out["arxiv_id"] == "2608.29955"
    assert stub_pipeline["fetch_arxiv"] == ["2608.29955"]
    assert stub_pipeline["fetch_pdf"] == []          # PDF 경로는 안 탄다
    assert stub_pipeline["save"] == [("2608.29955", "gemini")]
    assert stub_pipeline["retraction"] == ["2608.29955"]
    assert stub_pipeline["repro"] == ["2608.29955"]


def test_fetch_failure_stops_before_burning_llm_quota(stub_pipeline, monkeypatch):
    """fetch 가 실패하면 요약을 시도하면 안 된다 — 무료 한도를 그냥 태운다."""
    async def failing(params):
        return json.dumps({"error": "없는 논문"})

    monkeypatch.setattr(bs.server, "fetch_paper", failing)
    out = _run("2608.99999")
    assert out["status"] == "fetch_failed"
    assert stub_pipeline["summarize"] == []
    assert stub_pipeline["repro"] == []              # ⑦ 도 안 띄운다


def test_engine_is_recorded_with_the_summary(stub_pipeline):
    """§8-25: 커버리지 계산이 engine 을 필요로 한다 — 안 넘기면 NULL 이 되고
    "원문 몇 % 를 봤나"를 영영 모른다."""
    _run("2608.1")
    assert stub_pipeline["save"][0][1] == "gemini"


# ---------------------------------------------------------------- 오픈액세스 경로


def test_journal_paper_is_fetched_through_open_access_pdf(stub_pipeline):
    """2026-09-02 에 넣은 분기. S2 로 찾은 저널 논문은 arxiv_id 가 없다."""
    out = _run("", paper={"open_access_pdf": "http://x/y.pdf",
                          "doi": "10.1109/x", "title": "Metal Surface Defect Detection"})
    assert out["status"] == "done"
    assert out["arxiv_id"] == "pdf-abc123"           # 합성 ID 로 이어진다
    assert stub_pipeline["fetch_arxiv"] == []        # arXiv 경로는 안 탄다
    assert stub_pipeline["fetch_pdf"][0][0] == "http://x/y.pdf"


def test_doi_is_recorded_in_the_source_note(stub_pipeline):
    """나중에 이 논문이 어디서 왔는지 추적할 수 있어야 한다."""
    _run("", paper={"open_access_pdf": "http://x/y.pdf", "doi": "10.1109/x"})
    assert "10.1109/x" in stub_pipeline["fetch_pdf"][0][1]


def test_synthetic_id_flows_into_save_retraction_and_repro(stub_pipeline):
    """합성 ID 가 뒷단으로 안 흐르면 다이제스트가 검증·재현 결과를 못 찾아
    "데이터 없음"으로 나간다."""
    _run("", paper={"open_access_pdf": "http://x/y.pdf", "title": "T"})
    assert stub_pipeline["save"][0][0] == "pdf-abc123"
    assert stub_pipeline["retraction"] == ["pdf-abc123"]
    assert stub_pipeline["repro"] == ["pdf-abc123"]


def test_no_id_and_no_pdf_link_fails_cleanly(stub_pipeline):
    out = _run("", paper={"title": "링크 없음"})
    assert out["status"] == "fetch_failed"
    assert stub_pipeline["summarize"] == []
    assert stub_pipeline["repro"] == []


def test_broken_pdf_link_does_not_raise(stub_pipeline, monkeypatch):
    """실측(server.fetch_pdf_from_url docstring): "*.pdf" URL 이 실제로는
    HTML 로그인 페이지인 경우가 있다. 그때 예외가 위로 새면 스캔 한 편이
    아니라 그날 전체가 죽는다."""
    async def boom(url, title="", source_note=""):
        raise ValueError("PDF가 아닌 응답")

    monkeypatch.setattr(bs.server, "fetch_pdf_from_url", boom)
    out = _run("", paper={"open_access_pdf": "http://x/login.html"})
    assert out["status"] == "fetch_failed"
    assert "PDF가 아닌 응답" in out["detail"]
    assert stub_pipeline["repro"] == []


# ---------------------------------------------------------------- ⑦ 트리거 소유


def test_repro_is_triggered_only_after_a_successful_save(stub_pipeline):
    """CLAUDE.md 5: 전이 지점은 요약 저장 직후 한 곳이다. 저장 전에 띄우면
    재현이 요약 없는 논문에 붙는다."""
    _run("2608.1")
    assert stub_pipeline["save"] and stub_pipeline["repro"]


def test_survey_template_is_chosen_by_code_not_llm(stub_pipeline, monkeypatch):
    """판단은 LLM 이 아니라 코드가 한다(engine.select_template)."""
    monkeypatch.setattr(bs.engine, "select_template", lambda title: f"템플릿:{title}")

    async def fake_fetch(params):
        return json.dumps({"arxiv_id": params.arxiv_id, "title": "A Survey of X"})

    monkeypatch.setattr(bs.server, "fetch_paper", fake_fetch)
    _run("2608.1")
    assert stub_pipeline["summarize"] == ["템플릿:A Survey of X"]


# ---------------------------------------------------------------- 초록 보강 (2026-09-04)
#
# 09-04 아침 메일 상위 6편 중 **3편이 "(초록 없음)"** 으로 나갔다. 본문도
# 못 받고 초록도 없으면 제목 말고 실을 게 없다. 그런데 OpenAlex 에는 있었다
# (실측 7편 중 5편, 1,457~1,879자).

def test_openalex_fills_a_missing_abstract(monkeypatch):
    """S2 가 초록을 안 줘도 DOI 로 보강해 정리까지 간다."""
    asked = []

    async def fake_fetch(*a, **k):
        raise ValueError("PDF가 아닌 응답")

    async def fake_unpaywall(doi):
        return None

    async def fake_openalex(doi):
        asked.append(doi)
        return "Metal surfaces sustain scratches and pits during manufacturing."

    async def fake_brief(client, title, abstract):
        assert abstract, "보강된 초록이 정리 함수까지 와야 한다"
        return "- 무엇을 하려 했는가 : 금속 표면 결함을 검사한다."

    monkeypatch.setattr(bs.server, "fetch_pdf_from_url", fake_fetch)
    monkeypatch.setattr(bs.server, "resolve_unpaywall_pdf", fake_unpaywall)
    monkeypatch.setattr(bs.server, "resolve_openalex_abstract", fake_openalex)
    monkeypatch.setattr(bs.engine, "summarize_abstract", fake_brief)

    out = asyncio.run(bs._process_paper(
        None, "", paper={"open_access_pdf": "http://x", "doi": "10.1/x",
                         "title": "T", "abstract": ""}))
    assert asked == ["10.1/x"]
    assert out["status"] == "abstract_only"
    assert "금속 표면 결함" in out["brief"]


def test_openalex_not_called_when_abstract_already_present(monkeypatch):
    """이미 있으면 호출을 아낀다 — 무료라도 공짜는 아니다."""
    asked = []

    async def fake_fetch(*a, **k):
        raise ValueError("PDF가 아닌 응답")

    async def fake_openalex(doi):
        asked.append(doi)
        return "should not be used"

    monkeypatch.setattr(bs.server, "fetch_pdf_from_url", fake_fetch)
    monkeypatch.setattr(bs.server, "resolve_unpaywall_pdf",
                        lambda doi: _none())
    monkeypatch.setattr(bs.server, "resolve_openalex_abstract", fake_openalex)
    monkeypatch.setattr(bs.engine, "summarize_abstract",
                        lambda c, t, a: _text("- 정리"))

    asyncio.run(bs._process_paper(
        None, "", paper={"open_access_pdf": "http://x", "doi": "10.1/x",
                         "title": "T", "abstract": "이미 있는 초록"}))
    assert asked == []


async def _none():
    return None


async def _text(s):
    return s
