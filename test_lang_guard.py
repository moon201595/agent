"""사용자에게 보이는 LLM 출력은 한국어만 — `lang_guard` 와 적용 지점 회귀(2026-10-06 사용자 결정, PROGRESS §217).

실측: 주간 판정 사유 15건 중 2건이 중국어, Gemini 한국어 제목·초록 정리에 `显微镜`, 저장 요약 353편 중 6편에 한자·가나.
"""
import asyncio
import json

import pytest

import agent_maintenance as am
import lang_guard
import research_profile as rp
import summarize_engine as se
import title_ko
import trend_report
from test_agent_maintenance import FakeRunner, _act, _rid, world  # noqa: F401 — world 는 픽스처

ZH = "视觉语言模型与现有兴趣有关"


# ── 판정 함수 ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,found", [
    ("원자력显微镜을 통한", ["显微镜"]),
    ("駆動 방식", ["駆動"]),                        # 일본어 한자
    ("カメラ 기반", ["カメラ"]),                     # 가나
    ("VLA 와 LLM-as-a-judge, related_direction, 0.7 → 1.2, $\\alpha$", []),
    ("한국어만 있는 문장이다.", []),
])
def test_foreign_script_detects_han_and_kana_only(text, found):
    """범위에서 가나를 빼거나 한자 범위를 줄이면 검출이 빠져 실패하고, 라틴·숫자·기호를 잡게 넓히면 정상 문장이 걸려 실패한다."""
    assert lang_guard.foreign_script(text) == found


def test_foreign_script_allows_chunks_copied_from_source():
    """원문 예외(allowed_source)를 무시하면 원문의 중국어 용어를 옮겨 적은 정상 출력이 걸려 실패한다. 예외를 넓혀 원문에 없는 한자까지
    통과시키면 두 번째 단언이 실패한다."""
    assert lang_guard.foreign_script("기관 重点实验室 소속", allowed_source="Lab (重点实验室)") == []
    assert lang_guard.foreign_script("기관 重点实验室 소속", allowed_source="Lab") == ["重点实验室"]


def test_require_korean_raises_dedicated_error():
    """전용 예외를 일반 ValueError 로 바꾸면 잡는 쪽이 다른 결함까지 언어 문제로 분류한다 — isinstance 단언이 실패한다."""
    with pytest.raises(lang_guard.NonKoreanOutput) as e:
        lang_guard.require_korean(ZH, "", "시험")
    assert isinstance(e.value, ValueError)
    lang_guard.require_korean("한국어 문장", "", "시험")


# ── ① 주간 관리: 제안·판정 사유 ───────────────────────────────────────────
class CorrectableRunner(FakeRunner):
    """실제 HeadlessRunner 처럼 correction 인자를 받는 가짜."""

    def __init__(self, judges):
        super().__init__(None, None)
        self.judges, self.corrections = list(judges), []

    def judge(self, payload_json, correction=""):
        self.calls.append(("judge", json.loads(payload_json)))
        self.corrections.append(correction)
        return self.judges.pop(0)


def test_judge_with_chinese_reason_is_retried_once_then_applied(world):
    """재시도를 없애면 한 번의 중국어 사유로 그 주가 실패해 status 가 applied 가 아니다. 보정 문구를 안 넘기면 corrections 단언이,
    두 번 넘게 부르면 호출 수 단언이 실패한다."""
    b = am.build_brief(world, "p")
    action = _act("add_keyword", "tactile skin", [_rid(b, "tactile")], 0.7, "반복 용어")
    bad = {"reviews": [{"index": 0, "verdict": "accept", "reason": ZH}], "actions": [action]}
    good = {"reviews": [{"index": 0, "verdict": "accept", "reason": "좋아요 반응과 이어진다"}], "actions": [action]}
    runner = CorrectableRunner([bad, good])
    res = am.run_profile(world, "p", runner)
    assert res["status"] == "applied"
    assert runner.corrections[0] == "" and "한국어" in runner.corrections[1] and "视觉语言" in runner.corrections[1]
    assert [c[0] for c in runner.calls] == ["propose", "judge", "judge"]


def test_judge_still_chinese_after_retry_fails_the_week(world):
    """두 번째도 위반인데 적용하면 revision 이 바뀌어 실패하고, 실패 사유가 non_korean_output 이 아니면(다음 근무일 따라잡기가 사유를
    구별하지 않더라도 운영 화면이 원인을 못 보인다) 실패한다. 선택 인자가 없는 가짜 러너(FakeRunner)도 같은 정책을 탄다."""
    before = rp.current_revision(world, "p")
    b = am.build_brief(world, "p")
    action = _act("add_keyword", "tactile skin", [_rid(b, "tactile")], 0.7, ZH)
    runner = FakeRunner(None, {"reviews": [], "actions": [action]})
    res = am.run_profile(world, "p", runner)
    assert res["status"] == "failed" and res["error"] == "non_korean_output"
    assert rp.current_revision(world, "p") == before
    assert [c[0] for c in runner.calls] == ["propose", "judge", "judge"]


def test_proposal_reason_is_checked_too(world):
    """제안(Claude) 사유 검사를 빼면 중국어 제안이 판정 단계로 넘어가 judge 호출이 생기고 status 가 failed 가 아니다."""
    b = am.build_brief(world, "p")
    claude = {"actions": [_act("add_keyword", "tactile skin", [_rid(b, "tactile")], 0.7, ZH)]}
    runner = FakeRunner(claude, {"reviews": [], "actions": []})
    res = am.run_profile(world, "p", runner)
    assert res["status"] == "failed" and res["error"] == "non_korean_output"
    assert [c[0] for c in runner.calls] == ["propose", "propose"]


# ── ② 제목 번역 ───────────────────────────────────────────────────────────
def test_title_with_chinese_is_dropped_and_requested_again(monkeypatch):
    """`_parse` 의 검사를 빼면 `显微镜` 제목이 그대로 실리고, 누락분 재요청이 안 돌면 두 번째(정상) 번역을 못 받아 실패한다."""
    title = "Sub-4 nm Device Diagnostics via Atomic Force Microscopy"
    replies = iter([f"1. 원자력显微镜을 통한 4 nm 미만 소자 진단", "1. 원자간력 현미경을 통한 4 nm 미만 소자 진단"])

    async def complete(client, prompt):
        return next(replies)
    monkeypatch.setattr(se, "complete", complete)
    got = asyncio.run(title_ko.translate(object(), [title]))
    assert got == {title: "원자간력 현미경을 통한 4 nm 미만 소자 진단"}


# ── ③ 초록 정리 · 본문 요약 · 화면 번역 ────────────────────────────────────
def test_abstract_brief_falls_back_to_next_engine(monkeypatch):
    """검사를 빼면 Gemini 의 중국어 정리가 그대로 돌아오고, 다음 엔진으로 안 가면 빈 문자열이 돌아와 실패한다."""
    outs = {"Gemini(초록 정리)": "- 연구 목적: 원자간력显微镜(AFM) 기반 검사", "Groq(초록 정리)": "- 연구 목적: 원자간력 현미경(AFM) 기반 검사"}

    async def call(fn, label, max_wait=None):
        return outs[label]
    monkeypatch.setattr(se, "_call_with_rate_limit_retry", call)
    assert asyncio.run(se.summarize_abstract(None, "AFM paper", "We use AFM.")) == outs["Groq(초록 정리)"]


def test_abstract_brief_all_engines_foreign_returns_empty(monkeypatch):
    """마지막 엔진의 위반까지 받아들이면 빈 문자열이 아니어서 실패한다 — 초록 정리는 없는 것으로 둔다(규칙 6: 메일은 나간다)."""
    async def call(fn, label, max_wait=None):
        return "- 연구 목적: 显微镜"
    monkeypatch.setattr(se, "_call_with_rate_limit_retry", call)
    assert asyncio.run(se.summarize_abstract(None, "AFM paper", "We use AFM.")) == ""


def test_full_summary_with_foreign_text_goes_to_groq(monkeypatch):
    """본문 요약의 Gemini 결과 검사를 빼면 engine 이 gemini 로 남아 실패한다. 원문에 있는 한자는 허용되어야 한다(Groq 결과)."""
    paper = "We thank the 重点实验室 for support. " * 3
    outs = iter([("## 개요\n- 实例化 방식을 쓴다", 1.0), ("## 개요\n- 소속: 重点实验室", 1.0)])

    async def chunked(*args, **kwargs):
        return next(outs)
    monkeypatch.setattr(se, "_summarize_chunked", chunked)
    summary, engine, _cov = asyncio.run(se.summarize(None, paper, "템플릿"))
    assert engine == "groq" and "重点实验室" in summary


def test_screen_translation_refuses_foreign(monkeypatch):
    """화면 번역 검사를 빼면 중국어가 섞인 번역이 그대로 돌아와 예외가 안 난다."""
    async def complete(client, prompt):
        return "원자력显微镜"
    monkeypatch.setattr(se, "complete", complete)
    with pytest.raises(lang_guard.NonKoreanOutput):
        asyncio.run(se.translate_ko(None, "atomic force microscope"))


# ── ④ 동향 서술 ───────────────────────────────────────────────────────────
def test_narrative_with_chinese_moves_to_next_engine(monkeypatch):
    """동향 서술 검사를 빼면 첫 엔진(gemini)의 중국어 글이 실려 engine 이 groq 가 아니고 본문에 한자가 남는다."""
    import narrative_engine as ne
    rows = [{"arxiv_id": f"p{i}", "title": f"Paper number {i}", "abstract": f"abstract {i}", "published": "2026-09-15"}
            for i in (1, 2, 3)]

    def story(body):
        return json.dumps({"headline": "한 줄 [P1:A]", "threads": [{"name": "흐름", "papers": ["P1", "P2"], "body": body}],
                           "relation": "", "implications": [], "side_signals": []}, ensure_ascii=False)
    replies = iter([story("共同 문제를 다룬다 [P1:A][P2:A]"), story("공통 문제를 다룬다 [P1:A][P2:A]")])

    async def call(fn, label, max_wait=None):
        return next(replies)
    monkeypatch.setattr(ne, "_codex", lambda *a, **k: None)
    monkeypatch.setattr(se, "_call_with_rate_limit_retry", call)
    text, _u, _e, engine = asyncio.run(trend_report.narrative(None, rows, {"core_topics": ["x"]}, anchors=2))
    assert engine == "groq" and "共同" not in text and "공통 문제" in text


# ── ⑤ 외부 정찰 검증 ──────────────────────────────────────────────────────
def test_scout_note_in_chinese_marks_only_that_item_unverified():
    """예외를 밖으로 올리면 정찰 전체가 멈춰 함수가 예외로 끝나고, 검사를 빼면 첫 항목이 중국어 note 로 판정된다.
    다른 항목까지 버리면 두 번째 단언이 실패한다."""
    import external_scout as es
    items = [{"id": f"E{i}", "official": {"title": "Tactile skin sensing", "abstract": "tactile skin for robots"}} for i in (1, 2)]
    base = {"verdict": "verified", "relevant": True, "manufacturing_relation": "direct", "claim_supported": True,
            "candidate_terms": ["tactile skin"]}
    verdict = {"items": [{**base, "id": "E1", "note": "与现有兴趣有关"}, {**base, "id": "E2", "note": "촉각 센서 관련 연구다"}]}
    es.apply_verdicts(items, verdict)
    assert items[0]["verified"] == {"verdict": "missing"}
    assert items[1]["verified"]["note"] == "촉각 센서 관련 연구다"


# ── ⑥ 외부 성능 비교 설명 ─────────────────────────────────────────────────
def test_external_comparison_with_chinese_description_is_dropped_not_crashed():
    """비교 설명 검사를 빼면 중국어 차이 설명이 검증 결과로 실리고(status done·verified), 예외를 `check` 밖으로 올리면 이 호출이
    예외로 끝난다. 사유를 영어 코드로 남기면 화면에 그대로 보여 reason 단언이 실패한다."""
    import external_evidence as ee
    from test_frontier import TARGET, _comp, _fetch_fixture
    comp = _comp(differences=[{"what": "多视角 대 단일 시점", "quote": "M: Multi-view, S: Single view, L: LiDAR."}])
    reply = json.dumps({"papers": [{"paper_id": TARGET["paper_id"], "competitors": [comp]}]})
    res = ee.check([TARGET], run_agent=lambda p, t: reply, fetch=_fetch_fixture)[TARGET["paper_id"]]
    assert res["status"] == "incomplete" and not res.get("competitors")
    assert res["reason"] == "비교 설명이 한국어가 아니어서 버림"
