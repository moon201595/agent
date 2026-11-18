"""⑨ 기술 용어 표기 고정(ko_terms) — 2026-11-18 사용자: memory 를 '기억'으로, grasp·gripper 를 '잡기'·'집게'로 옮긴다."""
from __future__ import annotations

import ast
import asyncio
import json
import smtplib
import sqlite3
from contextlib import closing
from email import policy
from email.parser import BytesParser
from pathlib import Path

import pytest

import ko_terms

# 사용자가 정한 표기(2026-11-18) — TERMS 에서 지워도 이 표가 남아 실패한다(Codex 검토: TERMS 만 돌면 삭제를 못 잡는다).
PINNED = {"memory": "메모리", "context": "컨텍스트", "world model": "월드 모델", "foundation model": "파운데이션 모델",
          "prompt": "프롬프트", "fine-tuning": "파인튜닝", "grasp / grasping": "파지", "gripper": "그리퍼"}


@pytest.mark.parametrize("src,want", [
    # 조사까지 맞춘다 — 받침 있는 '기억' 뒤 조사를 받침 없는 '메모리' 꼴로
    ("기억 시스템", "메모리 시스템"), ("기억을 줄여도", "메모리를 줄여도"), ("기억이 어디서", "메모리가 어디서"),
    ("기억과 판단", "메모리와 판단"), ("기억으로 남긴다", "메모리로 남긴다"), ("기억이다.", "메모리다."), ("기억은", "메모리는"),
    ("기억이었다", "메모리였다"), ("기억이라는", "메모리라는"), ("기억에서도", "메모리에서도"), ("장기 기억", "장기 메모리"),
    ("기억입니다. 기억인가? 기억인 경우. 기억임을 확인했다. 기억일 때.", "메모리입니다. 메모리인가? 메모리인 경우. 메모리임을 확인했다. 메모리일 때."),
    ("기억이지만 맥락이므로", "메모리지만 컨텍스트므로"), ("운전 맥락을", "운전 컨텍스트를"), ("맥락이", "컨텍스트가"),
    ("세계 모델은", "월드 모델은"), ("세계모델로", "월드 모델로"),
    ("행동 모델을 미세 조정하여", "행동 모델을 파인튜닝하여"), ("LLM 을 미세조정(fine-tuning)으로", "LLM 을 파인튜닝(fine-tuning)으로"),
    ("LLM 에 주는 지시문과 도구 호출", "LLM 에 주는 프롬프트와 도구 호출"),
    ("물체 잡기·밀기", "물체 파지·밀기"), ("뻗기·잡기·놓기", "뻗기·파지·놓기"), ("로봇의 잡기 위치나", "로봇의 파지 위치나"),
    ("휴대용 집게로 시연을", "휴대용 그리퍼로 시연을"), ("로봇 집게·손", "로봇 그리퍼·손"),
    # 바꾸면 안 되는 것 — 동사·합성어·식별자·다른 뜻(실측 문맥, Codex 검토 재현)
    ("기억해야 할", "기억해야 할"), ("기억하는", "기억하는"), ("기억력", "기억력"), ("기억나는", "기억나는"), ("기억된", "기억된"),
    ("기준을 잡기 어렵다", "기준을 잡기 어렵다"), ("바로잡기 위해", "바로잡기 위해"), ("균형 잡기 동작을 학습한다", "균형 잡기 동작을 학습한다"),
    ("LLM 기반 모델", "LLM 기반 모델"), ("집게나물의 생장", "집게나물의 생장"), ("맥락의존적 추론", "맥락의존적 추론"),
    ("memory_기억", "memory_기억"), ("기억2", "기억2"),
    ("안전 지시문을 읽고 주방 집게로 음식을 옮긴다.", "안전 지시문을 읽고 주방 집게로 음식을 옮긴다."),
    ("그림의 위치를 미세 조정했다.", "그림의 위치를 미세 조정했다."),
])
def test_fix(src, want):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 조사·서술격 변환(을→를·이→가·이지만→지만·입니다)이 틀리거나, 동사(기억하다)·합성어(기억력·집게나물·
    맥락의존적)·식별자(memory_기억)·다른 뜻의 '잡기'·'X 기반 모델'·일상어(안전 지시문·주방 집게·위치 미세 조정)를 바꾸거나, 용어를 목록에서 빼면 실패한다."""
    assert ko_terms.fix(src) == want


def test_fix_keeps_tags_and_urls():
    """태그 속성(따옴표 속 '>' 포함)·URL(대문자 스킴·//·www.) 속 한글을 바꿔 링크를 깨면 실패한다."""
    assert ko_terms.fix('<a title="1 > 0 기억" href="https://x/기억">기억을</a>') == '<a title="1 > 0 기억" href="https://x/기억">메모리를</a>'
    for url in ("HTTPS://example.org/기억", "//example.org/맥락", "www.example.org/지시문", "WWW.EXAMPLE.ORG/기억"):
        assert ko_terms.fix(f"LLM 자료 {url} 의 기억을") == f"LLM 자료 {url} 의 메모리를"
    assert ko_terms.fix("") == "" and ko_terms.fix(None) is None


def test_prompt_rule_has_the_pinned_terms():
    """사용자가 정한 용어가 목록·규칙에서 빠지거나 다른 표기로 바뀌면 실패한다."""
    assert {t.en: t.ko for t in ko_terms.TERMS} == PINNED
    for en, ko in PINNED.items():
        assert f"{en} → {ko}" in ko_terms.PROMPT_RULE
    for trap in ("기억", "맥락", "세계 모델", "기반 모델", "지시문", "미세 조정", "잡기", "집게"):
        assert f"'{trap}' 아님" in ko_terms.PROMPT_RULE


def test_every_korean_prompt_carries_the_rule(monkeypatch):
    """한국어를 만드는 프롬프트 하나라도 규칙을 안 실으면 실패한다 — 한 곳만 고치면 다른 곳에서 다시 터진다(사용자 지적)."""
    import external_evidence
    import plain_ko
    import quote_ko
    import summarize_engine as se
    import title_ko
    rule = ko_terms.PROMPT_RULE
    assert rule in plain_ko._prompt([{"title": "T", "abstract": "A"}])
    assert rule in title_ko._prompt(["T"]) and rule in quote_ko._prompt(["Q 1.0"])
    assert se.select_template("x").startswith(rule) and se.select_template("x", force="survey").startswith(rule)
    target = {"paper_id": "p", "title": "T", "method": "m", "benchmark": "b", "metric": "acc", "value": 1.0}
    assert external_evidence._prompt([target]).startswith(rule)

    seen = []

    async def fake_post(_client, prompt):
        seen.append(prompt)
        return "요약"

    async def passthrough(call, _label):
        return await call()

    monkeypatch.setattr(se, "_post_gemini", fake_post)
    monkeypatch.setattr(se, "_post_groq", fake_post)
    monkeypatch.setattr(se, "_call_with_rate_limit_retry", passthrough)
    asyncio.run(se.summarize_abstract(None, "T", "A"))
    import narrative_engine
    asyncio.run(narrative_engine.generate(None, "본문", order=("gemini",)))
    for addendum in (se.call_gemini_addendum, se.call_groq_addendum):
        try:
            asyncio.run(addendum(None, "[S0001] 발췌"))
        except Exception:  # noqa: BLE001 — 응답 파싱은 여기 관심이 아니다. 보낸 프롬프트만 본다
            pass
    assert len(seen) == 4 and all(p.startswith(rule) for p in seen)


@pytest.mark.parametrize("path,needle", [
    ("research_assets_collect.py", 'ko_terms.PROMPT_RULE + "\\n" + _prompt("research_assets_select_v1.md")'),
    ("agent_maintenance.py", '_ko_rule() + _prompt("agent_propose_v1.md")'),
    ("agent_maintenance.py", '_ko_rule() + _prompt("agent_judge_v1.md")'),
    ("external_scout.py", "ko_terms.PROMPT_RULE + template.format("),
])
def test_cli_prompts_carry_the_rule_in_code_not_comments(path, needle):
    """CLI 로 넘기는 프롬프트에서 규칙이 빠지면 실패한다. 주석이 아니라 코드(AST)에서 찾는다 — 주석에만 남기고 인자에서 빼면 잡는다(Codex 검토)."""
    code = ast.unparse(ast.parse(Path(path).read_text(encoding="utf-8")))
    assert needle.replace('"', "'") in code


def test_send_function_fixes_both_parts_for_new_and_resent_mail(monkeypatch):
    """발송 함수의 교정이 빠지거나 평문·HTML 중 한쪽만 고치면 실패한다. 새 메일(_deliver)과 저장 메일 재발송(mail_reformat)이 모두 이 함수를 지난다."""
    import email_delivery
    import summarize_engine as engine
    monkeypatch.setattr(engine, "ENV", {"SMTP_USER": "me@gmail.com", "SMTP_PASSWORD": "app-password"})
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def starttls(self, context=None): pass
        def login(self, user, password): pass
        def send_message(self, msg): sent.append(msg)

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    email_delivery.send_digest_email("에이전트 기억을 줄인다", "제목", ["a@x.example"], "<p>로봇 휴대용 집게로 시연</p>")
    msg = BytesParser(policy=policy.default).parsebytes(sent[0].as_bytes())
    text = msg.get_body(("plain",)).get_content()
    html = msg.get_body(("html",)).get_content()
    assert "메모리를" in text and "기억" not in text
    assert "그리퍼로" in html and "집게" not in html
    import inspect
    import mail_reformat
    assert "email_delivery.send_digest_email(" in inspect.getsource(mail_reformat)   # 재발송이 발송 함수를 우회하면 교정도 빠진다


def test_saved_narrative_fixes_body_and_story(tmp_path):
    """서술 저장에서 본문이나 구조 자료(audit.story) 교정이 빠지면 실패한다 — 다음 날 trend_history 가 구조 자료를 먼저 읽어
    '기억'을 모델에 다시 넣는다(Codex 검토 재현). 근거 ID·영어는 그대로여야 한다."""
    import narrative_store
    db = tmp_path / "n.db"
    audit = {"story": {"version": "story-v3", "threads": [{"title": "기억과 맥락", "body": "기억을 줄인다 [P1:A]", "papers": ["P1"]}]}}
    narrative_store.save(db, "p", narrative_store.KINDS[0], "에이전트 기억과 세계 모델", audit=audit)
    with closing(sqlite3.connect(db)) as con:
        (body, raw), = con.execute("SELECT body, audit_json FROM profile_narratives").fetchall()
    assert body == "에이전트 메모리와 월드 모델"
    thread = json.loads(raw)["story"]["threads"][0]
    assert thread == {"title": "메모리와 컨텍스트", "body": "메모리를 줄인다 [P1:A]", "papers": ["P1"]}
