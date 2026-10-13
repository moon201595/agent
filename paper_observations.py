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


# ── 카드의 성능 비교(2026-10-13 사용자 지적) ─────────────────────────────────────────────────
# "논문 내 결과도 이 논문 표도 그렇고 저걸 어떻게 보고 사용자가 이해를 해? 표 형태로 주던가, 어느 논문의 어느 방법이 어느 수치였는데 그거 대비
# 이건 어떻다 이런 식으로". 예전 카드는 "SWE-Bench · success rates / Average 53.8 — 논문 표 … 행 (S3.T1#0:r10c3)" 처럼 내부 위치 표기와 값을
# 줄로 나열했고 비교 문장이 없었다. 이제 벤치마크·지표마다 **한 상자 = 비교 표 + 한 문장**이다.
# 표 행 문법(평문 저장 → 형식 수정본 역파싱 → 같은 HTML 표): "<구분> · <방법> · <값>[ · <출처·조건>]". 문장 행은 "→ " 로 시작한다.
# 문장은 계산이 아니라 **같은 표·같은 열·같은 구획**의 두 값의 차이와 지표 방향만 말한다(규칙 7 — 조건이 다른 값끼리는 차이를 말하지 않는다).
ROW_KINDS = ("이 논문", "같은 표", "외부 논문", "기존 관측")
COMPARISON_PREFIX = "성능 비교 · "
CODE_HEADING = "코드 저장소"
DIRECTION_LABEL = {"higher": "높을수록 좋음", "lower": "낮을수록 좋음"}


def table_label(r: dict) -> str:
    """"표 1" 처럼 논문에 적힌 번호. 캡션에 번호가 없으면 "논문 표"."""
    import re
    m = re.match(r"\s*(?:Table|Tab\.?)\s*(\d+)", r.get("caption") or "", re.I)
    return f"표 {m[1]}" if m else "논문 표"


def _decimals(text: str) -> int | None:
    """값 문자열의 소수 자릿수. 지수 표기(1.2e-3)는 None — 자릿수로 차이를 말할 수 없다(Codex 검토 P2: ".85" 를 정수로 읽어 0.05 차이를 "같다" 고 했다)."""
    import re
    text = text or ""
    if re.search(r"\d\s*[eE][-+]?\d|×\s*10", text):
        return None
    m = re.search(r"\d*\.(\d+)|\d+", text)
    return len(m[1]) if m and m[1] else 0


def conditions_verified(c: dict) -> bool:
    """외부 결과의 평가 조건 4가지가 출처 인용으로 모두 대조됐는가 — `comparison_rows` 와 같은 기준(Codex 검토 P2: 카드 표가 더 느슨했다)."""
    import external_evidence
    conditions = c.get("matched_conditions")
    if not isinstance(conditions, list) or any(not isinstance(x, str) or not x.strip() for x in conditions):
        return False
    return (c.get("same_conditions") is True and not c.get("differences") and c.get("unverified_differences") == 0
            and not c.get("conditions_not_checked") and len(set(conditions)) >= len(external_evidence.REQUIRED_CONDITIONS))


def difference_sentence(main: dict, other: dict, where: str, *, same_column: bool) -> str:
    """같은 조건이 확인된 두 값의 차이 한 문장. 방향을 모르거나 척도가 다르면 빈 문자열 — 대소를 말하지 않는다.
    같은 표·같은 열이면 척도가 같다 — 그대로 뺀다. 다른 표(외부)는 두 값의 척도(0~1·%)가 같을 때만 견준다: `comparable_values` 의 ×100 은
    "0.8%" 같은 명시 % 값까지 80 으로 바꿔 우열을 뒤집는다(Codex 검토 P1)."""
    direction = main.get("direction")
    a, b = main.get("value"), other.get("value")
    if direction not in DIRECTION_LABEL or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (a, b)):
        return ""
    a_text, b_text = main.get("text") or "", other.get("text") or ""
    if not same_column and (("%" in a_text) != ("%" in b_text) or (abs(a) <= 1.0) != (abs(b) <= 1.0)):
        return ""
    found = [_decimals(a_text), _decimals(b_text)]
    if None in found:
        return ""
    places = max(found)
    diff = a - b
    if diff == 0:
        verdict = "같다"
    else:
        while round(abs(diff), places) == 0 and places < 12:   # 표시 자릿수 때문에 차이가 사라지면 늘린다 — 다른 값을 같다고 하지 않는다
            places += 1
        better = diff > 0 if direction == "higher" else diff < 0
        verdict = f"{abs(diff):.{places}f} {'높다' if diff > 0 else '낮다'}({'더 좋음' if better else '더 나쁨'})"
    return (f"→ {where} {main['model']} {main['text']}은(는) {other['model']} {other['text']}보다 {verdict}. "
            f"지표는 {DIRECTION_LABEL[direction]}.")


def _field(text: object) -> str:
    """표 행 문법의 구분자(" · ")가 칸 안에 들어가면 방법 이름이 값 칸으로 밀린다(Codex 검토 P2) — 칸 안의 것은 붙여 쓴다."""
    return str(text or "").replace(" · ", "·").strip()


def _external_row(c: dict) -> tuple[str, str]:
    """외부 결과 한 행. 출처 표의 실제 열 이름과 "같은 과제" 가 에이전트 판독이라는 것을 그대로 적는다(2026-10-07 설계 — 의미 판단의 책임을 드러낸다)."""
    if conditions_verified(c):
        conditions = "평가 조건 4가지 대조함"
    elif c.get("differences"):
        conditions = "조건 차이: " + ", ".join(c["differences"])
    else:
        conditions = "평가 조건 대조 못 함"
    where = f'{c.get("table_label") or c.get("table_id") or "출처 표"} 열 "{" / ".join(c.get("column_path") or [])}"'
    cited = " · 다른 논문 표의 재인용" if c.get("third_party") else ""
    judged = f" · 같은 과제: 에이전트 판독({c['task_note']})" if c.get("task_note") else " · 같은 과제: 에이전트 판독"
    return (f"외부 논문 · {_field(c.get('display_model') or c['model'])} · {_field(c['text'])} · {where}{cited}{judged} · {conditions}",
            c.get("source_url") or "")


def _comparison_section(r: dict, ext: dict, first: bool) -> tuple[str, list[tuple[str, str]]]:
    where = table_label(r) + (f" {r['block']} 구획" if r.get("block") else "")
    direction = DIRECTION_LABEL.get(r.get("direction") or "", "방향 미상")
    rows: list[tuple[str, str]] = [(f"이 논문 · {_field(r['model'])} · {_field(r['text'])} · {where}", "")]
    baselines = r.get("baselines") or []
    rows += [(f"같은 표 · {_field(b['model'])} · {_field(b['text'])}", "") for b in baselines]
    shown_ext = [c for c in (ext.get("competitors") or []) if external_visible(c)] if first else []
    rows += [_external_row(c) for c in shown_ext]
    best = (r.get("compare") or {}).get("best")
    if best:
        rows.append((f"기존 관측 · {_field(best['model'])} · {best['value']:g} · {best['reported_in']} 표 · 평가 조건 미확인", ""))
    # 한 문장 — 같은 표·같은 열·같은 구획의 가장 좋은 비교 방법 대비(`_baselines` 가 방향대로 정렬해 둔다).
    if baselines:
        line = difference_sentence(r, baselines[0], f"{where}에서", same_column=True)
        if line:
            rows.append((line, ""))
    for c in shown_ext:
        if conditions_verified(c):
            line = difference_sentence(r, {**c, "model": c.get("display_model") or c["model"]}, "평가 조건이 같다고 대조한 외부 결과와 견주면",
                                       same_column=False)
            if line:
                rows.append((line, c.get("source_url") or ""))
        else:
            rows.append((f"→ 외부 결과 {c.get('display_model') or c['model']} {c['text']}는 평가 조건이 같은지 확인되지 않아 차이를 말하지 않는다.", ""))
    missed = sum(c.get("status") in ("fetch_failed", "table_failed") for c in ext.get("competitors") or []) if first else 0
    if first and ext.get("status") == "incomplete" and shown_ext and missed:
        # 확인된 외부 결과가 있는데 뭉뚱그린 "미완료" 만 붙이면 그 결과까지 못 믿게 읽힌다(2026-10-07 실측) — 못 본 후보 수만 센다.
        rows.append((f"→ 웹 검색 후보 중 {missed}건은 출처 표를 확인하지 못해 싣지 않았다.", ""))
    elif first and ext.get("status") == "incomplete" and not shown_ext:
        rows.append(("→ 저자가 비교하지 않은 더 최근 결과(웹 검색)는 이번에 확인하지 못했다.", ""))
    elif first and ext.get("status") == "done" and not shown_ext:
        rows.append(("→ 저자가 비교하지 않은 더 최근 결과를 웹에서 찾았지만 출처 표에서 확인된 것이 없다.", ""))
    return (f"{COMPARISON_PREFIX}{r['benchmark']} · {r['metric']} ({direction})", rows)


def code_rows(sig: dict | None) -> list[tuple[str, str]]:
    """이 논문의 공식·저자 저장소. 새 논문의 인용 수·HF 내려받기 수는 싣지 않는다(2026-10-13 사용자: "왜 최신 논문에 대한 인용수 이런 게
    아직도 있어") — 공개 며칠 된 논문에서는 거의 늘 0이라 읽을거리가 아니다. 수집은 그대로 하고 운영 화면이 쓴다."""
    g = (sig or {}).get("github") or {}
    if (sig or {}).get("github_tier") not in ("official", "author") or not g.get("repo"):
        return []
    tier = "공식 저장소" if sig["github_tier"] == "official" else "저자 연관 저장소(공식 여부 미확인)"
    return [(f"{tier} · {g['repo']}", f"https://github.com/{g['repo']}")]


def sections(paper: dict, today: date | None = None) -> list[tuple[str, list[tuple[str, str]]]]:
    """숫자는 저장 근거 그대로 쓴다. 차이 문장은 같은 표·같은 열·같은 구획이나 조건 대조가 끝난 외부 값과만 만든다."""
    fr = paper.get("_frontier") or {}
    out: list[tuple[str, list[tuple[str, str]]]] = []
    mains = fr.get("main") or []
    ext = fr.get("external") or {}
    for i, r in enumerate(mains):
        out.append(_comparison_section(r, ext, first=i == 0))   # 외부 조사는 analyze 가 고른 첫 관측 하나가 대상이다
    if not mains:
        own: list[tuple[str, str]] = []
        for s in (fr.get("sentences") or [])[:2]:
            where = f"[S{s['index']:04d}]" if s.get("source") == "text" and s.get("index") else "(초록)"
            own.append((f"“{s['sentence']}” {where}", ""))
        if own:
            own.append(("저자 자신의 실험 보고를 인용함. 표 구조를 확인하지 못해 논문 간 수치 비교에는 쓰지 않음.", ""))
            out.append(("성능 관측 · 논문 내 결과", own))
    claims = paper.get("_sota_claims") or fr.get("claims") or []
    if claims:
        import sota_claims
        out.append(("논문 자체 주장 · 미검증", [(sota_claims.mail_line(claims), "")]))
    code = code_rows(paper.get("_signals"))
    if code:
        out.append((CODE_HEADING, code))
    return out
