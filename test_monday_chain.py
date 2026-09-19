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


def test_the_weekly_table_reaches_neither_the_mail_nor_the_model():
    """이 테스트가 잡는 것: 주간 운영 표를 메일 절로 되살리는 것 · **서술 입력으로 다시 넘기는 것**.

    2026-09-19 오후에 뺐다 — `build()` 출력 5,471자 중 60% 넘게가 검색 지문·S2 수율·프로필 건강 같은
    운영 진단이고 그 안에 검색 잡음 절이 있다(impedance spectroscopy 따위). 기간 비교는 `window_movement`
    가 매일 한다. `narrative(weekly=...)` 자리는 아직 코드에 남아 있지만 **부르는 쪽이 안 준다** —
    여기서 지키는 것은 그 사실이다."""
    body = (ROOT / "digest.py").read_text(encoding="utf-8")
    assert "lines += _weekly_review_lines(scan_result)" not in body
    assert "body += _weekly_review_html(scan_result)" not in body
    scan = (ROOT / "run_profile_scan.py").read_text(encoding="utf-8")
    call = scan[scan.index("story = await trend_report.narrative("):]
    assert "weekly=" not in call[:call.index(")\n")]


def test_weekly_diagnostics_run_after_every_mail_not_between_them(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 월요일 운영 진단을 프로필 루프 **안**에 두는 것.

    2026-09-20 실측 — `trend_report.build` 는 `client=None`(네트워크 없음)으로도 프로필당 175초다.
    루프 안에 있으면 첫 메일만 안 기다리고 **둘째부터는 앞 프로필 진단을 기다린다**(네 번째 메일이
    8분 45초를 더 기다렸다). 메일에 안 실리는 자료가 나가는 메일을 늦추면 안 된다(규칙 6).

    문자열 위치가 아니라 **실제 호출 순서**를 본다 — `메일1 → 진단1 → 메일2` 도 위치 비교는 통과한다."""
    import asyncio

    import research_profile as rp
    import run_profile_scan as rps

    db = tmp_path / "t.db"
    for pid in ("a", "b"):
        rp.create_profile(db, pid, pid.upper(), ["alpha"])
    events: list[str] = []

    async def scan(db_path, profile_id, client, max_pages=10):
        events.append(f"scan:{profile_id}")
        return ({"run_status": "done", "candidates_found": 0, "scored_count": 0}, "본문")

    async def diag(db_path, profile_id, result, now=None):
        events.append(f"diag:{profile_id}")

    monkeypatch.setattr(rps, "scan_and_digest", scan)
    monkeypatch.setattr(rps, "record_weekly_diagnostics", diag)
    monkeypatch.setattr(rps, "_deliver", lambda d, pid, r, t: events.append(f"mail:{pid}"))
    asyncio.run(rps.scan_all_profiles(db, None, send=True))

    assert events == ["scan:a", "mail:a", "scan:b", "mail:b", "diag:a", "diag:b"], events


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
