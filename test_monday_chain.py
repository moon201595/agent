"""월요일 배치 — 주간 관리를 먼저 돌리고, 주간 리뷰는 별도 절이 아니라 **동향 서술의 입력**이 된다.

2026-09-19 사용자 결정. 그전 구조의 문제: ① 주간 관리가 금요일 17:00 이라 바꾼 키워드가 주말(arXiv 신규가
거의 없는 이틀)을 헛돌았다 ② 주간 리뷰가 월요일 메일에 별도 절로 붙어, 같은 주 자료를 동향 서술과 주간 리뷰가
따로 읽고 따로 말했다."""
import re
from pathlib import Path

import digest
import trend_report

ROOT = Path(__file__).resolve().parent


def test_monday_runs_weekly_maintenance_before_the_scan():
    """이 테스트가 잡는 것: 월요일 체인을 빼거나 일일 스캔 **뒤로** 옮기는 것
    (그러면 바뀐 키워드가 그날 스캔에 반영되지 않아 하루 늦는다) · 금요일 cron 을 되살리는 것."""
    sh = (ROOT / "run_daily_scan.sh").read_text(encoding="utf-8")
    assert "date +%u" in sh and '= "1" ]' in sh          # 월요일 판정(KST)
    # 주석에도 같은 문자열이 있어 **실제 명령줄**로 찾는다(파일 첫머리 설명이 먼저 걸렸다)
    chain = sh.index(".venv/bin/python agent_maintenance.py")
    scan = sh.index(".venv/bin/python run_profile_scan.py --all")
    assert chain < scan, "주간 관리는 일일 스캔보다 먼저여야 한다"
    assert "|| echo" in sh[chain - 200:scan], "주간 관리가 실패해도 스캔은 이어져야 한다(규칙 6)"


def test_weekly_numbers_go_into_the_narrative_not_a_separate_mail_block():
    """이 테스트가 잡는 것: 주간 리뷰를 메일에 별도 절로 되살리는 것 · 서술 입력에서 빼는 것."""
    body = (ROOT / "digest.py").read_text(encoding="utf-8")
    assert "lines += _weekly_review_lines(scan_result)" not in body
    assert "body += _weekly_review_html(scan_result)" not in body
    ctx = trend_report._weekly_context("키워드 A 12편 (지난주 8)")
    assert "12편" in ctx
    assert "새 수치를 만들지 않는다" in ctx          # 모델이 수치를 지어내지 못하게
    assert trend_report._weekly_context(None) == ""


def test_label_names_every_input_the_narrative_actually_had():
    """이 테스트가 잡는 것: 입력이 늘었는데 라벨이 옛 문장 그대로 나가는 것(규칙 8 — 라벨이 거짓이 된다) ·
    2026-09-19 오후에 서술 입력에서 뺀 주간 운영 표를 라벨이 계속 봤다고 적는 것."""
    full = digest.narrative_source_label({"narrative_summaries": 2, "narrative_past_days": 6})
    assert "2편" in full and "지난 6일" in full
    plain = digest.narrative_source_label({"narrative_summaries": 2})
    assert "지난" not in plain
    assert "지난 한 주" not in digest.narrative_source_label(
        {"narrative_summaries": 2, "weekly_diagnostics": "표"})
    assert digest.narrative_source_label({}) == "LLM 이 제목·초록만 보고 쓴 것."


def test_monday_prompt_carries_all_three_inputs_without_leaking_counts():
    """이 테스트가 잡는 것: 프롬프트에서 입력(오늘 논문·지난 서술·늘어난 말) 중 하나가 빠지는 것 ·
    창 집계의 편수가 모델에 새어 들어가 모델이 우리 수치를 자기 문장으로 옮기는 것.
    주간 운영 표는 2026-09-19 오후에 입력에서 뺐다 — 여기서는 빈 자리로 둔다."""
    prompt = trend_report._NARRATIVE_PROMPT.format(
        papers="- [P1:T] 오늘 논문\n  [P1:A] 초록",
        topics="defect detection",
        movement=trend_report._movement_context(
            {"comparable": True, "days": 7, "terms": [("agentic rl", 9, 3)]}),
        history=trend_report._history_context([{"reader_date": "2026-09-18", "body": "어제 흐름"}]),
        weekly=trend_report._weekly_context("키워드 A 12편 (지난주 8)"))
    assert "오늘 논문" in prompt and "어제 흐름" in prompt and "키워드 A 12편" in prompt
    assert "agentic rl" in prompt                      # 늘어난 말은 이름만
    movement_part = prompt.split("agentic rl")[1][:80]
    assert " 9" not in movement_part and " 3" not in movement_part   # 그 말의 편수는 안 간다
