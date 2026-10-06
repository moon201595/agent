"""선택적 시간 연결은 실제 과거 기록과 오늘 근거를 동시에 지켜야 한다."""
import asyncio
import json

import pytest

import trend_history as history
import trend_report as tr

CORPUS = "- [P1:T] One\n  [P1:A] 검색 선택\n- [P2:T] Two\n  [P2:A] 탐색 경로\n- [P3:T] Other\n  [P3:A] 외부 논문\n"
PAST = [{"reader_date": "2026-10-01", "narrative_id": "old-observation", "body": "■ 오늘의 한 줄\n검색 연구\n■ 1. 도구 선택\n도구 선택을 검색기로 옮긴다 [P99:A].\n- Prior paper [P99:A]"}]


def plan(connection=None):
    return {"headline": "검색 문제 [P1:A]", "threads": [{"name": "검색 역할", "papers": ["P1", "P2"],
            "body": "선택과 탐색 [P1:A][P2:A]", "history": connection}], "relation": "", "implications": [], "side_signals": []}


def test_catalog_uses_saved_structure_and_old_text_without_reusing_p_ids():
    """audit 구조를 버리거나 옛 P번호를 오늘 근거로 넘기면 실패한다. 날짜 없는 기록도 후보가 아니다."""
    rows = PAST + [{"reader_date": "2026-10-02", "body": "틀린 복원", "audit_json": json.dumps({"story": {
        "version": "story-v3", "threads": [{"name": "궤적 학습", "body": "성공 경로 [P2:A]", "titles": ["Actual title"]}]}})},
        {"reader_date": "bad", "body": PAST[0]["body"]}]
    got = history.catalog(rows)
    assert got["H1"]["name"] == "궤적 학습" and got["H1"]["titles"] == ["Actual title"]
    assert got["H2"]["narrative_id"] == "old-observation" and got["H2"]["body"] == "도구 선택을 검색기로 옮긴다."
    assert len(got) == 2 and "[P" not in history.context(got)
    assert "[H1] 2026-10-02" in history.context(got) and "[H2] 2026-10-01" in history.context(got)


def test_verified_connection_has_original_date_and_stays_an_interpretation():
    """연결 표시·원본 날짜·해석 표시를 지우거나 본문에 섞으면 실패한다. 날짜는 모델 숫자 대조에 넣지 않는다."""
    story = tr.repair_story(plan({"ref": "H1", "relation": "expanding", "note": "선택 문제에서 경로 탐색으로 범위를 넓힌다 [P2:A]."}), CORPUS, history=history.catalog(PAST))
    text = tr.render_story(story)
    assert "↳ 지난 관측 2026-10-01 · 도구 선택 · 확장 — 선택 문제에서 경로 탐색으로 범위를 넓힌다 [P2:A]. (해석)" in text
    assert "2026-10-01" not in tr.model_text(story)
    parsed = tr.parse_rendered_story(text)
    assert parsed["threads"][0]["history_note"].startswith("↳ 지난 관측 2026-10-01")
    assert "지난 관측" not in parsed["threads"][0]["body"]
    assert "선택 문제에서 경로 탐색으로 범위를 넓힌다" in tr.model_text(story)


@pytest.mark.parametrize("connection", [None, {"ref": "H99", "relation": "continuing", "note": "연결 [P1:A]"},
    {"ref": "H1", "relation": "continuing", "note": "연결 [P99:A]"},
    {"ref": "H1", "relation": "continuing", "note": "연결 [P3:A]"},
    {"ref": "H1", "relation": "new", "note": "연결 [P1:A]"},
    {"ref": "H1", "relation": "continuing", "note": "급증했다 [P1:A]"},
    {"ref": "H1", "relation": "continuing", "note": "두 배인 20%로 변했다 [P1:A]"}])
def test_no_connection_is_invented_for_missing_or_unverified_history(connection):
    """없는 H번호·오늘 흐름 밖 근거·정량 증감을 허용하거나 null에 기본 연결을 붙이면 실패한다."""
    story = tr.repair_story(plan(connection), CORPUS, history=history.catalog(PAST))
    assert "history" not in story["threads"][0]
    text = tr.render_story(story)
    assert "지난 관측" not in text and "새 흐름" not in text
    assert "선택과 탐색" in text


def test_generation_keeps_tuple_contract_and_exposes_verified_structure(monkeypatch):
    """생성 경로에 H후보를 빠뜨리거나 저장할 구조를 잃으면 실패한다. 구형 v2의 연결 칸 생략도 null로만 보완한다."""
    import narrative_engine
    seen = {}

    async def generate(client, prompt, **kwargs):
        seen.update(prompt=prompt, schema=kwargs["schema"])
        value = plan({"ref": "H1", "relation": "branching", "note": "같은 도구 문제를 다른 탐색 방식으로 다룬다 [P2:A]."})
        return kwargs["accept"](json.dumps(value, ensure_ascii=False)), "fake"

    monkeypatch.setattr(narrative_engine, "generate", generate)
    rows = [{"title": title, "abstract": "검색", "arxiv_id": f"p{i}"} for i, title in enumerate(["One", "Two", "Other"], 1)]
    result = asyncio.run(tr.narrative(None, rows, past=PAST, anchors=2))
    assert len(result) == 4 and result[3] == "fake"
    assert result.structured["version"] == "story-v3"
    assert result.structured["threads"][0]["history"]["source"]["narrative_id"] == "old-observation"
    assert "[H1] 2026-10-01" in seen["prompt"] and seen["schema"] is tr.STORY_SCHEMA
    assert "이전 기간 비교 근거는 제공되지 않았다" not in seen["prompt"]
    legacy = plan(); del legacy["threads"][0]["history"]
    assert tr.parse_story(json.dumps(legacy))["threads"][0]["history"] is None


def test_ui_escapes_history_and_shows_it_separately_from_the_body():
    """과거 연결을 숨기거나 HTML로 실행하거나 본문과 합치면 실패한다."""
    import review_app
    text = "■ 오늘의 한 줄\n요지\n■ 1. 흐름\n↳ 지난 관측 2026-10-01 · <script>bad</script> · 이어짐 — 관찰 (해석)\n오늘 본문 [P1:A][P2:A]"
    parsed = tr.parse_rendered_story(text)
    html = review_app._story_html(parsed)
    assert "rm-history" in html and "&lt;script&gt;bad&lt;/script&gt;" in html and "<script>" not in html
    assert html.index("지난 관측") < html.index("오늘 본문")
    assert "지난 관측" not in parsed["threads"][0]["body"]
