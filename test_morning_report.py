"""scripts/morning_report.py 진입점 스모크 — `run_daily_scan.sh` 가 매일 새벽 끝에 부르는데(63행) 어느 테스트도 이 모듈을 import 하지
않았다(Codex 사후 검토 2026-09-17 #6, 우선순위 높음 — `data/codex_research_2026-09-16/refactor_review.md`). 화면 모듈이 사흘 동안
IndentationError 였던 것(§8-154)과 같은 구멍이다. 네트워크·메일 없음, 임시 로그·DB 만 쓴다.

망가뜨리면 실패하는 것: 모듈이 import 수준에서 깨지는 것 · 로그와 DB 가 있는 정상 경로에서 main() 이 예외를 내거나 0 이 아닌 코드를 주는 것 ·
`logs/morning_report.txt` 를 안 남기는 것 · 다이제스트 제목 줄이 보고서에 안 실리는 것 · 로그가 없을 때 1 을 돌려주지 않는 것."""
import sqlite3

import research_profile
import scripts.morning_report as mr

_LOG = """=== 2026-09-17T05:00:01Z 시작 (pid 4242) ===
[요약] 2509.00001 gemini 로 생성됨
"delivery": {"sent": 1}
=== 2026-09-17T05:31:40Z 종료 (exit 0) ===
"""


def _setup(tmp_path, monkeypatch, *, with_log: bool = True):
    (tmp_path / "logs").mkdir()
    log = tmp_path / "logs" / "daily_scan.log"
    if with_log:
        log.write_text(_LOG, encoding="utf-8")
    db = tmp_path / "papers.db"
    research_profile.init_db(db)
    research_profile.create_profile(db, "p1", "테스트 프로필", core_topics=["x"])
    with sqlite3.connect(db) as con:
        con.execute("UPDATE profiles SET last_digest=?, last_digest_at=? WHERE profile_id='p1'",
                    ("■ 오늘의 논문\n1. 첫 논문 제목\n   핵심 키워드: x\n", "2026-09-17T05:30:00Z"))
    monkeypatch.setattr(mr, "ROOT", tmp_path)
    monkeypatch.setattr(mr, "LOG", log)
    monkeypatch.setattr(mr, "DB", db)
    return tmp_path / "logs" / "morning_report.txt"


def test_main_writes_report_from_log_and_db(tmp_path, monkeypatch, capsys):
    report = _setup(tmp_path, monkeypatch)
    assert mr.main() == 0
    text = report.read_text(encoding="utf-8")
    assert "■ 일일 스캔 보고" in text and "종료코드 0" in text
    assert "Gemini 1편" in text                   # ① 요약 엔진 집계가 로그 줄을 읽었다
    assert "[테스트 프로필]" in text and "1. 첫 논문 제목" in text   # ⑤ 다이제스트 제목 줄
    assert capsys.readouterr().out.strip() == text.strip()   # 화면 출력과 파일이 같다


def test_main_returns_1_without_log(tmp_path, monkeypatch):
    report = _setup(tmp_path, monkeypatch, with_log=False)
    assert mr.main() == 1
    assert not report.exists()
