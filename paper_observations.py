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
        if (not external_visible(other) or other.get("same_conditions") is not True
                or other.get("differences") or other.get("unverified_differences") != 0
                or other.get("conditions_not_checked") or len(checked) < len(external_evidence.REQUIRED_CONDITIONS)
                or isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number)
                or not all(other.get(k) for k in ("text", "model", "locator", "source_url"))
                or not other["source_url"].startswith("https://")
                or external_evidence.comparable_values(value, number, main["metric"], main["text"], other["text"]) is None):
            continue
        competitors.append(external_line(other))
    if not competitors:
        return []
    return [(f"{main['benchmark']} · {main['metric']} — {main['model']} {main['text']} ({main['locator']})", ""), *competitors]


def has_signals(sig: dict) -> bool:
    """조회 실패와 0은 보존하되 표시할 양수 근거가 있을 때만 상자를 연다."""
    def positive(value: object) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
    return bool((sig.get("github_tier") in ("official", "author") and (sig.get("github") or {}).get("repo"))
                or positive((sig.get("scholarly") or {}).get("citations"))
                or positive((sig.get("hub") or {}).get("n_models"))
                or positive((sig.get("hub") or {}).get("upvotes")))


def external_visible(candidate: dict) -> bool:
    """과제 판독이 없거나 다른 대상에서 재사용한 캐시는 숫자가 있어도 싣지 않는다."""
    return (candidate.get("status") == "verified" and candidate.get("same_task") is True
            and not candidate.get("task_not_checked") and bool(candidate.get("column_path")))


def external_line(c: dict) -> tuple[str, str]:
    """실제 출처 헤더와 에이전트 판단을 나란히 보여 의미 판단의 책임을 드러낸다."""
    conditions = ("조건 차이: " + ", ".join(c['differences']) if c.get("differences") else
                  "대조한 조건: " + ", ".join(c['matched_conditions']) if c.get("matched_conditions")
                  and not c.get("conditions_not_checked") else "조건 대조 미확인")
    note = f" ({c['task_note']})" if c.get("task_note") else ""
    header = " / ".join(c["column_path"])
    cited = " · 다른 논문 표의 재인용" if c.get("third_party") else ""
    return (f"외부 논문 표: {c.get('display_model') or c['model']} {c['text']} — 출처 표 열 \"{header}\" "
            f"({c.get('table_label') or c.get('table_id')}){cited} · 같은 과제 판단: 에이전트 판독{note} · {conditions}",
            c.get("source_url") or "")


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
        baselines = []
        for r in fr.get("main") or []:
            rows = r.get("baselines") or []
            if rows:
                values = " · ".join(f"{b['model']} {b['text']}" for b in rows)
                baselines.append((f"{r['benchmark']} · {r['metric']} — {values} ({r['locator'].partition(':r')[0]})", ""))
        if baselines:
            out.append(("비교 대상 · 이 논문 표(저자 보고값)", baselines))
        other: list[tuple[str, str]] = []
        for r in fr.get("main") or []:
            best = (r.get("compare") or {}).get("best")
            if best:
                other.append((f"기존 관측: {r['benchmark']} · {r['metric']} {best['value']:g} — {best['model']} "
                              f"({best['reported_in']} 표). 같은 평가 조건인지 미확인.", ""))
        ext = fr.get("external") or {}
        for c in ext.get("competitors") or []:
            if external_visible(c):
                other.append(external_line(c))
        if other:
            other.append(("벤치마크·지표 외에 데이터 버전·split·backbone·추론 조건까지 같아야 비교 가능함. 여기서는 우열을 판정하지 않음.", ""))
        else:
            other.append(("동일 조건으로 확인한 논문 간 비교 결과 없음.", ""))
        if ext.get("status") == "incomplete":
            # 확인된 외부 결과가 있는데 "미완료"만 붙이면 그 결과까지 못 믿을 것처럼 읽힌다(2026-10-07 실측: 1건 확인·1건 표 확보 실패).
            # 그때는 무엇이 안 됐는지만 센다. 확인된 것이 없으면 예전처럼 미완료 한 줄이다.
            shown = any(external_visible(c) for c in ext.get("competitors") or [])
            missed = sum(c.get("status") in ("fetch_failed", "table_failed") for c in ext.get("competitors") or [])
            if shown and missed:
                other.append((f"외부 조사 일부 미완료 — 출처 표를 확인하지 못한 후보 {missed}건.", ""))
            else:
                other.append(("외부 조사 미완료.", ""))
        out.append(("논문 간 관측", other))
    claims = paper.get("_sota_claims") or fr.get("claims") or []
    if claims:
        import sota_claims
        out.append(("논문 자체 주장 · 미검증", [(sota_claims.mail_line(claims), "")]))
    sig = paper.get("_signals")
    if sig and has_signals(sig):
        import adoption_signals
        rows = [(f"{label} · {text}", "") for label, text in adoption_signals.signal_rows(sig, today)]
        out.append(("외부 관측 신호", rows))
    return out
