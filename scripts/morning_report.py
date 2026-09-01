"""아침 보고서 — 마지막 일일 스캔이 어떻게 됐는지 한 장으로 정리한다.

run_daily_scan.sh 끝에서 자동으로 불린다. 사람이 나중에 직접 돌려도 같은 걸
본다: `.venv/bin/python scripts/morning_report.py`

왜 스크립트로 두나: 보고가 "그때 누가 붙어 있었는가"에 의존하면 안 된다.
실행 직후에 스스로 남겨두면 몇 시간 뒤에 열어봐도 그대로 있다.

아무것도 판정하지 않고 기록된 사실만 읽는다(DB + 로그). 네트워크를 안 쓴다.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "logs" / "daily_scan.log"
DB = ROOT / "data" / "papers.db"

_START_RE = re.compile(r"^=== (\S+) 시작 \(pid (\d+)\) ===")
_END_RE = re.compile(r"^=== (\S+) 종료 \(exit (\d+)\) ===")


def _last_run_block() -> tuple[str | None, str | None, int | None, list[str]]:
    """마지막 '시작' 이후의 로그 줄들. 종료 표시가 없으면 아직 도는 중이다."""
    if not LOG.exists():
        return None, None, None, []
    lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    start_idx = None
    started = None
    for i, line in enumerate(lines):
        m = _START_RE.match(line)
        if m:
            start_idx, started = i, m.group(1)
    if start_idx is None:
        return None, None, None, []
    block = lines[start_idx + 1:]
    ended, exit_code = None, None
    for line in block:
        m = _END_RE.match(line)
        if m:
            ended, exit_code = m.group(1), int(m.group(2))
    return started, ended, exit_code, block


def _elapsed(started: str | None, ended: str | None) -> str:
    if not (started and ended):
        return "진행 중"
    try:
        a = datetime.fromisoformat(started.replace("Z", "+00:00"))
        b = datetime.fromisoformat(ended.replace("Z", "+00:00"))
    except ValueError:
        return "?"
    s = int((b - a).total_seconds())
    return f"{s // 3600}시간 {s % 3600 // 60}분 {s % 60}초" if s >= 3600 else f"{s // 60}분 {s % 60}초"


def _kst(ts: str | None) -> str:
    if not ts:
        return "?"
    try:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts
    return d.astimezone().strftime("%m-%d %H:%M")


def _section(title: str) -> str:
    return f"\n{'─' * 66}\n{title}\n{'─' * 66}"


def main() -> int:
    out: list[str] = []
    started, ended, exit_code, block = _last_run_block()
    if started is None:
        print("일일 스캔 로그가 없다 — 아직 한 번도 안 돌았거나 로그가 지워졌다.")
        return 1

    out.append(f"■ 일일 스캔 보고 — 시작 {_kst(started)} KST")
    out.append(f"  소요 {_elapsed(started, ended)}"
               + (f" · 종료코드 {exit_code}" if exit_code is not None else " · **아직 실행 중**"))

    # ── 엔진 (키 회전이 먹었는지가 여기서 보인다)
    gemini_n = sum(1 for l in block if "gemini 로 생성됨" in l)
    groq_n = sum(1 for l in block if "groq 로 생성됨" in l)
    rotated = sum(1 for l in block if "다음 키로 전환" in l)
    exhausted = sum(1 for l in block if "모두 429" in l)
    fellback = sum(1 for l in block if "Groq로 전환" in l)
    out.append(_section("① 요약 엔진"))
    out.append(f"  Gemini {gemini_n}편 · Groq {groq_n}편")
    out.append(f"  키 회전 {rotated}회 · 전체 키 소진 {exhausted}회 · Groq 폴백 {fellback}회")
    if groq_n and not gemini_n:
        out.append("  ⚠ 전부 Groq 다 — 요약이 긴 논문의 절반만 봤을 수 있다(§8-25).")
    elif gemini_n and not groq_n:
        out.append("  ✓ 전부 Gemini — 원문 전체를 봤고 호출도 편당 1회다.")

    # ── 계측
    out.append(_section("② API 호출 (계측)"))
    meas = [l.strip() for l in block if "[계측]" in l]
    out += ["  " + m for m in meas] or ["  (계측 줄 없음 — 구버전으로 돌았을 수 있다)"]

    # ── 경고
    warns = Counter(re.sub(r"\d+", "N", l.strip())[:90] for l in block if "[경고]" in l)
    out.append(_section("③ 경고"))
    out += [f"  ×{n:<3} {w}" for w, n in warns.most_common(8)] or ["  없음"]

    # ── DB: 검색·선정·발송
    if DB.exists():
        con = sqlite3.connect(DB)
        con.row_factory = sqlite3.Row
        run = con.execute("SELECT * FROM search_runs ORDER BY started_at DESC LIMIT 1").fetchone()
        out.append(_section("④ 검색"))
        if run:
            out.append(f"  창   : {run['window_from'][:16]} → {run['window_to'][:16]} (UTC)")
            out.append(f"  결과 : {run['retrieved_count']}편 · status={run['status']}")
            if run["error_detail"]:
                out.append(f"  오류 : {str(run['error_detail'])[:140]}")

        prof = con.execute(
            "SELECT name, last_digest, last_digest_at FROM profiles LIMIT 1").fetchone()
        out.append(_section("⑤ 다이제스트"))
        if prof and prof["last_digest"]:
            out.append(f"  저장 시각: {_kst(prof['last_digest_at'])} KST")
            for line in (prof["last_digest"] or "").splitlines():
                if re.match(r"^\d+\. \[", line) or line.startswith("■"):
                    out.append("  " + line)
                elif line.strip().startswith(("왜 걸렸나", "[검증", "[재현")):
                    out.append("     " + line.strip())
        else:
            out.append("  (저장된 다이제스트 없음)")

        # ── 재현 라벨 분포
        try:
            import digest as digest_mod
            ids = [r[0] for r in con.execute("SELECT DISTINCT arxiv_id FROM repro_results")]
            c = Counter()
            for aid in ids:
                lab = digest_mod.repro_label(aid)
                c["성공 ✓" if "✓" in lab else
                  ("돌렸는데 실패 ✗" if "✗" in lab else "돌려보지도 못함 –")] += 1
            out.append(_section("⑥ ⑦ 재현 누적"))
            out += [f"  {k:18} {v}편" for k, v in c.items()] or ["  기록 없음"]
            recent = con.execute(
                "SELECT arxiv_id, stage, fail_detail FROM repro_results "
                "WHERE fail_detail IS NOT NULL ORDER BY created_at DESC LIMIT 6").fetchall()
            if recent:
                out.append("  최근 실패 사유:")
                out += [f"    {r['arxiv_id']:12} {r['stage']:9} {r['fail_detail']}" for r in recent]
        except Exception as e:  # noqa: BLE001 — 보고서가 절대 죽으면 안 된다
            out.append(f"  (재현 집계 실패: {type(e).__name__})")
        con.close()

    out.append("")
    text = "\n".join(out)
    print(text)
    report = ROOT / "logs" / "morning_report.txt"
    report.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
