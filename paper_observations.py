"""⑤⑨ 카드의 관측 근거 — 논문 보고·논문 간 관측·외부 이용 신호를 구분한다."""
from __future__ import annotations

from datetime import date
import math
import json
from pathlib import Path
import sqlite3


def stored_comparison_rows(db: Path, paper_id: str) -> list[tuple[str, str]]:
    """화면은 같은 날의 저장 관측만 읽는다. 자신의 셀이 하나로 정해지지 않으면 비교를 표시하지 않는다."""
    import time_policy
    try:
        with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as con:
            con.execute("PRAGMA query_only=ON")
            con.row_factory = sqlite3.Row
            ext = con.execute("SELECT checked_on,bench_key,metric_key,result_json FROM frontier_external "
                              "WHERE paper_id=? ORDER BY checked_on DESC LIMIT 1", (paper_id,)).fetchone()
            if ext is None:
                return []
            own = con.execute("SELECT * FROM observed_results WHERE reported_in=? AND own=1 AND bench_key=? AND metric_key=?",
                              (paper_id, ext["bench_key"], ext["metric_key"])).fetchall()
        if len(own) != 1 or time_policy.kst_day(own[0]["observed_at"]) != ext["checked_on"]:
            return []
        row = dict(own[0])
        main = {**row, "text": row["cell_text"]}
        return comparison_rows({"_frontier": {"main": [main], "external": json.loads(ext["result_json"])}})
    except (sqlite3.Error, KeyError, TypeError, ValueError, AttributeError):
        return []


def comparison_rows(paper: dict) -> list[tuple[str, str]]:
    """Python 검증으로 전체 조건이 확인된 기존 관측만 별도 비교에 쓴다. 새 값·우열은 만들지 않는다."""
    import external_evidence
    fr = paper.get("_frontier") or {}
    main = (fr.get("main") or [None])[0]  # 외부 조사의 대상은 analyze가 고른 첫 관측 하나다.
    if not main or not all(main.get(k) for k in ("benchmark", "metric", "text", "model", "locator")):
        return []
    value = main.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return []
    competitors: list[tuple[str, str]] = []
    for other in (fr.get("external") or {}).get("competitors") or []:
        conditions = other.get("matched_conditions") or []
        if not isinstance(conditions, list) or any(not isinstance(c, str) or not c.strip() for c in conditions):
            continue
        checked = {c for c in conditions if isinstance(c, str) and c.strip()}
        number = other.get("value")
        if (other.get("status") != "verified" or other.get("same_conditions") is not True
                or other.get("differences") or other.get("unverified_differences") != 0
                or other.get("conditions_not_checked") or len(checked) < len(external_evidence.REQUIRED_CONDITIONS)
                or isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number)
                or not all(other.get(k) for k in ("text", "model", "locator", "source_url"))
                or not other["source_url"].startswith("https://")
                or external_evidence.comparable_values(value, number, main["metric"], main["text"], other["text"]) is None):
            continue
        competitors.append((f"{other['model']} {other['text']} ({other['locator']}) — 대조 조건: " + ', '.join(conditions),
                            other["source_url"]))
    if not competitors:
        return []
    return [(f"{main['benchmark']} · {main['metric']} — {main['model']} {main['text']} ({main['locator']})", ""), *competitors]


def sections(paper: dict, today: date | None = None) -> list[tuple[str, list[tuple[str, str]]]]:
    """숫자는 저장 근거 그대로 쓴다. 벤치마크·지표만 같은 값으로 우열을 표시하지 않는다."""
    fr = paper.get("_frontier") or {}
    out: list[tuple[str, list[tuple[str, str]]]] = []
    own: list[tuple[str, str]] = []
    for r in fr.get("main") or []:
        own.append((f"{r['benchmark']} · {r['metric']} {r['text']} — 논문 표 {r['model']} 행 ({r['locator']})", ""))
    if not own:
        for s in (fr.get("sentences") or [])[:2]:
            where = f"[S{s['index']:04d}]" if s.get("source") == "text" and s.get("index") else "(초록)"
            own.append((f"“{s['sentence']}” {where}", ""))
        if own:
            own.append(("저자 자신의 실험 보고를 인용함. 표 구조를 확인하지 못해 논문 간 수치 비교에는 쓰지 않음.", ""))
    if own:
        out.append(("성능 관측 · 논문 내 결과", own))
        other: list[tuple[str, str]] = []
        for r in fr.get("main") or []:
            best = (r.get("compare") or {}).get("best")
            if best:
                other.append((f"기존 관측: {r['benchmark']} · {r['metric']} {best['value']:g} — {best['model']} "
                              f"({best['reported_in']} 표). 같은 평가 조건인지 미확인.", ""))
        ext = fr.get("external") or {}
        for c in ext.get("competitors") or []:
            if c.get("status") != "verified":
                continue
            conditions = ("조건 차이: " + ", ".join(c['differences']) if c.get("differences") else
                          "대조한 조건: " + ", ".join(c['matched_conditions']) if c.get("matched_conditions")
                          and not c.get("conditions_not_checked") else "이 논문과 조건 대조 미확인")
            other.append((f"외부 논문 표: {c['model']} · {c['text']} — {conditions}", c.get("source_url") or ""))
        if other:
            other.append(("벤치마크·지표 외에 데이터 버전·split·backbone·추론 조건까지 같아야 비교 가능함. 여기서는 우열을 판정하지 않음.", ""))
        else:
            other.append(("동일 조건으로 확인한 논문 간 비교 결과 없음.", ""))
        if ext.get("status") == "incomplete":
            other.append(("외부 조사 미완료.", ""))
        out.append(("논문 간 관측", other))
    claims = paper.get("_sota_claims") or fr.get("claims") or []
    if claims:
        import sota_claims
        out.append(("논문 자체 주장 · 미검증", [(sota_claims.mail_line(claims), "")]))
    sig = paper.get("_signals")
    if sig:
        import adoption_signals
        rows = [(f"{label} · {text}", "") for label, text in adoption_signals.signal_rows(sig, today)]
        out.append(("외부 관측 신호", rows))
    return out
