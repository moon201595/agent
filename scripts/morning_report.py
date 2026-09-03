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
# 이 스크립트는 scripts/ 아래에 있어서 sys.path 에 레포 루트가 안 들어간다.
# 2026-09-02 첫 실전에서 digest 임포트가 ModuleNotFoundError 로 죽어 재현
# 집계가 통째로 빠졌다 — 보고서가 조용히 한 절을 잃는 형태라 더 나쁘다.
sys.path.insert(0, str(ROOT))

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


def _work_window(started: str | None) -> str | None:
    """이번 실행이 요약을 저장한 첫 시각~마지막 시각. 벽시계와 다르면
    그 차이가 곧 "멈춰 있던 시간"이다."""
    if not (DB.exists() and started):
        return None
    try:
        con = sqlite3.connect(DB)
        row = con.execute(
            "SELECT MIN(created_at), MAX(created_at), COUNT(*) FROM summaries "
            "WHERE created_at >= ?", (started.replace("Z", ""),)
        ).fetchone()
        con.close()
    except sqlite3.Error:
        return None
    lo, hi, n = row
    if not (lo and hi and n):
        return None
    try:
        a = datetime.fromisoformat(lo.replace("Z", "+00:00"))
        b = datetime.fromisoformat(hi.replace("Z", "+00:00"))
    except ValueError:
        return None
    s = int((b - a).total_seconds())
    return (f"{_kst(lo)} ~ {_kst(hi)} KST "
            f"({s // 60}분 {s % 60}초 동안 {n}편)")


def _section(title: str) -> str:
    return f"\n{'─' * 66}\n{title}\n{'─' * 66}"


def main() -> int:
    out: list[str] = []
    started, ended, exit_code, block = _last_run_block()
    if started is None:
        print("일일 스캔 로그가 없다 — 아직 한 번도 안 돌았거나 로그가 지워졌다.")
        return 1

    out.append(f"■ 일일 스캔 보고 — 시작 {_kst(started)} KST")
    out.append(f"  벽시계 {_elapsed(started, ended)}"
               + (f" · 종료코드 {exit_code}" if exit_code is not None else " · **아직 실행 중**"))

    # 벽시계만 보면 오해한다 — PC 가 자면 프로세스도 같이 멈춰 있다가 깨어난 뒤
    # 이어서 돈다. 2026-09-02 실전이 그랬다: 벽시계 4시간 45분인데 요약 6편은
    # 마지막 4분에 다 나왔다. 실제 작업 구간을 같이 보여줘야 "느렸다"와
    # "자고 있었다"를 구분할 수 있다.
    work = _work_window(started)
    if work:
        out.append(f"  실제 작업 {work}")

    delivery = [l.strip().strip(',') for l in block if '"delivery"' in l]
    if delivery:
        out.append("  " + delivery[-1].replace('"delivery":', "메일:").replace('"', ""))

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
            digest_lines = (prof["last_digest"] or "").splitlines()
            for i, line in enumerate(digest_lines):
                if re.match(r"^\d+\. \[", line) or line.startswith("■"):
                    out.append("  " + line)
                    # "동향 신호" 절은 제목 다음 줄에 들여쓰기로 온다 —
                    # 제목만 찍고 내용을 빠뜨리면 절이 있으나 마나다.
                    if "동향 신호" in line and i + 1 < len(digest_lines):
                        out.append("     " + digest_lines[i + 1].strip())
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

        # ── §8-16 종결 조건 추적
        #
        # 이 항목은 "근거가 쌓이면 판단하자"로 한 달을 보냈는데 실제로는
        # 아무것도 안 쌓이고 있었다(network_suspected 가 계산만 되고 저장은
        # 안 됐다). 이제 fail_detail 로 기록되므로, 사람이 기억해서 확인하는
        # 대신 **보고서가 매일 진척을 말하고 조건이 차면 알려준다**.
        try:
            target = 10   # 실행 단계(run/build) 누적 시도 — §8-16 종결 조건
            done = con.execute(
                "SELECT COUNT(*) FROM repro_results "
                "WHERE stage IN ('run','build','install_only') AND fail_detail IS NOT NULL").fetchone()[0]
            success = con.execute(
                "SELECT COUNT(*) FROM repro_results "
                "WHERE stage='run' AND success=1").fetchone()[0]
            suspected = con.execute(
                "SELECT COUNT(*) FROM repro_results "
                "WHERE fail_detail='run_network_suspected'").fetchone()[0]
            counted = done + success
            out.append(_section("⑦ §8-16 egress allowlist 판단 근거"))
            out.append(f"  실행 단계 누적 {counted}/{target}건 · 네트워크 차단 의심 {suspected}건")
            if counted < target:
                out.append(f"  근거 수집 중 — {target - counted}건 더 필요하다.")
            elif suspected == 0:
                out.append("  ✅ 종결 조건 충족 — 차단이 아무 대가도 안 치르고 있다.")
                out.append('     §8-16 을 "필요 없음"으로 닫아도 된다.')
            else:
                out.append(f"  ⚠ 종결 조건 충족, 다만 의심 {suspected}건 — 차단이 실제로 "
                           "재현을 막고 있다.")
                out.append("     egress allowlist(pypi·github·huggingface 만 허용) 구현 근거가 "
                           "생겼다.")
        except sqlite3.Error as e:
            out.append(f"  (§8-16 집계 실패: {e})")

        con.close()

    out.append("")
    text = "\n".join(out)
    print(text)
    report = ROOT / "logs" / "morning_report.txt"
    report.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
