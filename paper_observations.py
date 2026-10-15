"""⑤⑨ 카드의 관측 근거 — 논문 보고·논문 간 관측·외부 이용 신호를 구분한다."""
from __future__ import annotations

from datetime import date
import math
import re
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
    return [(latex_display(f"{main['benchmark']} · {main['metric']} — {main['model']} {main['text']} ({main['locator']})"), ""), *competitors]


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
REFERENCE_KIND = "다른 구획 참고"
LEAD_PREFIX = "요약: "                 # 성능 비교 상자의 첫 줄 — 표 위에 그린다(digest._comparison_table_html)
REFERENCE_NOTE = "평가 조건 미검증 · 직접 우열 비교 불가"
CODE_HEADING = "코드 저장소"
DIRECTION_LABEL = {"higher": "높을수록 좋음", "lower": "낮을수록 좋음"}


def table_label(r: dict) -> str:
    """"표 1" 처럼 논문에 적힌 번호. 캡션에 번호가 없으면 "논문 표"."""
    import re
    m = re.match(r"\s*(?:Table|Tab\.?)\s*(\d+|[IVXLC]+\b)", r.get("caption") or "", re.I)
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


def difference_sentence(main: dict, other: dict, where: str, *, same_column: bool, direction_note: bool = True) -> str:
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
    show = latex_display
    # 백분율이 표에서 확인된 값(`unit`)이면 값에 %, 차이에 %p — 같은 열이라 단위가 같다. 외부 값은 단위를 붙이지 않는다(원 글 그대로).
    pct = same_column and main.get("unit") == "%" and other.get("unit") == "%"     # 두 값 모두 표에서 % 가 확인될 때만(Codex 검토 P2)
    def value(x: dict) -> str:
        v = _sentence_value(x)
        if not pct or "%" in v:
            return v
        head, _, tail = v.partition(" ")                  # "51.2 ± 1.0" → "51.2% ± 1.0" — 오차 뒤에 붙이지 않는다
        return head + "%" + (f" {tail}" if tail else "")
    if pct and diff != 0:
        verdict = verdict.replace(" 높다", "%p 높다").replace(" 낮다", "%p 낮다")
    return (f"→ {show(where)} {show(main['model'])} {value(main)}은(는) {show(other['model'])} {value(other)}보다 {verdict}."
            # "높을수록 좋음" 은 당연해서 쓰지 않는다(2026-10-15 사용자). 낮을수록 좋은 지표만 밝힌다.
            + (f" 지표는 {DIRECTION_LABEL[direction]}." if direction_note and direction == "lower" else ""))


# arXiv HTML 표의 수식 칸은 LaTeX 원문(alttext)으로 읽힌다 — 2026-10-13 재생에서 "80.3 \pm 1.1"·"Claude Opus 5 \leftrightarrow GPT-5.6-sol" 이
# 카드에 그대로 찍힐 뻔했다. 값 비교는 원문으로 하고 **보이는 글자만** 바꾼다. 모르는 명령은 그대로 둔다(없는 기호를 지어내지 않는다).
_LATEX_SYMBOLS = {"pm": "±", "mp": "∓", "leftrightarrow": "↔", "rightarrow": "→", "to": "→", "leftarrow": "←", "times": "×",
                  "geq": "≥", "leq": "≤", "approx": "≈", "sim": "~", "tau": "τ", "sigma": "σ", "mu": "μ", "pi": "π", "bullet": "•"}
_SUPERSCRIPT = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


_ARROWS = {"→", "←", "↔"}


def _latex_command(m: "re.Match") -> str:
    """알려진 명령만 기호로. 뒤에 붙은 대문자 낱말은 떼어 읽는다 — 표 글에서 띄어쓰기가 사라진 "SFT\\toDAPO" 를 2026-10-15 카드가
    그대로 찍었다. "\\top"(다른 명령)처럼 소문자로 이어지면 모르는 명령으로 남긴다(없는 기호를 지어내지 않는다)."""
    name = m[1]
    head, rest = name, ""
    if name not in _LATEX_SYMBOLS:
        head = next((k for k in sorted(_LATEX_SYMBOLS, key=len, reverse=True)
                     if name.startswith(k) and name[len(k)].isupper()), "")
        if not head:
            return m[0]
        rest = name[len(head):]
    symbol = _LATEX_SYMBOLS[head]
    return (f" {symbol} " if symbol in _ARROWS else symbol + (" " if rest else "")) + rest


def latex_display(text: object) -> str:
    """표 칸의 흔한 LaTeX 꼴을 읽을 수 있는 글로. "\\tau^{2}" → "τ²", "SFT\\toDAPO" → "SFT → DAPO", "TSRL {}_{\\text{DAPO}}" → "TSRL (DAPO)"."""
    import re
    out = str(text or "")
    # 글자 아래첨자 중 **방법 변형 표기만** 괄호로 — "{}_{\\text{DAPO}}"(빈 묶음 뒤)·"_{\\text{DAPO}}"(글자 묶음). "x_{t}"·"F_{max}" 같은 수식은 그대로(Codex 검토 P2).
    out = re.sub(r"\s*\{\}\s*_\{\\(?:text|textrm|mathrm)\{([^{}]{1,24})\}\}|\s*(?:\{\})?\s*_\{\\(?:text|textrm)\{([^{}]{1,24})\}\}",
                 lambda m: f" ({m[1] or m[2]})", out)
    out = re.sub(r"\s*\{\}\s*_\{([A-Za-z][^{}]{0,23})\}", r" (\1)", out)
    out = re.sub(r"\\(?:text|textbf|textit|mathrm|mathbf|mathit|operatorname)\{([^{}]{0,40})\}", r"\1", out)
    out = re.sub(r"\\([A-Za-z]+)", _latex_command, out)
    out = re.sub(r"\^\{(\d{1,3})\}", lambda m: m[1].translate(_SUPERSCRIPT), out)
    out = re.sub(r"_\{([0-9.]{1,6})\}", r"\1", out)          # "\pi_{0.5}" → "π0.5"
    return re.sub(r"\s{2,}", " ", out).strip() if out != str(text or "") else out


def row_value(x: dict) -> str:
    """표시용 값. 증감이 **확인된** 칸(`performance_results._mark_gains`)만 "66.5 (+25.2)" 로 가르고, 나머지는 원 글 그대로(LaTeX 기호만 바꿈).
    2026-10-14 사용자가 "66.5 +25.2" 를 합으로 읽었다. 확인 안 된 꼬리를 괄호로 바꾸면 하한·둘째 지표를 증감이라고 단정한다(Codex 검토 P2)."""
    text = latex_display(x.get("text")).strip()
    if x.get("gain"):
        return f"{text.split()[0]} ({x['gain']})"
    return text


def _sentence_value(x: dict) -> str:
    """문장 안의 값 — 확인된 증감은 뺀다("π0.5 + Ours 78.3은(는) …"). 그 밖의 꼬리(오차 "± 1.0" 등)는 원 글 그대로."""
    return latex_display(x.get("text")).strip().split()[0] if x.get("gain") else latex_display(x.get("text")).strip()


def _field(text: object) -> str:
    """표 행 문법의 구분자(" · ")가 칸 안에 들어가면 방법 이름이 값 칸으로 밀린다(Codex 검토 P2) — 칸 안의 것은 붙여 쓴다."""
    return latex_display(text).replace(" · ", "·").strip()


def _external_row(c: dict) -> tuple[str, str]:
    """외부 결과 한 행 — 출처 표 이름과 조건 차이 두 가지까지만(2026-10-15 사용자: 카드를 봐도 무슨 값인지 모르겠다 — 열 경로·판독 근거·차이 전부를
    한 칸에 넣어 표가 무너졌다). "같은 과제" 가 에이전트 판독이라는 것은 표 아래 한 문장으로 한 번 밝힌다(2026-10-07 설계의 책임 표시는 유지)."""
    if conditions_verified(c):
        conditions = "평가 조건 4가지 대조함"
    elif c.get("differences"):
        diffs = [_field(d) for d in c["differences"]]
        conditions = "조건 다름: " + ", ".join(diffs[:2]) + (f" 외 {len(diffs) - 2}가지" if len(diffs) > 2 else "")
    else:
        conditions = "평가 조건 대조 못 함"
    # 출처 열 이름은 남긴다 — 값의 척도(0~1·%)를 읽는 데 필요하다(Codex 검토 P2). 열 경로가 길면 마지막 두 칸만.
    column = " / ".join((c.get("column_path") or [])[-2:])
    where = _field(c.get("table_label") or c.get("table_id") or "출처 표") + (f' 열 "{_field(column)}"' if column else "")
    cited = " · 다른 논문 표의 재인용" if c.get("third_party") else ""
    return (f"외부 논문 · {_field(c.get('display_model') or c['model'])} · {_field(c['text'])} · {where}{cited} · {conditions}",
            c.get("source_url") or "")


def _comparison_section(r: dict, ext: dict, first: bool) -> tuple[str, list[tuple[str, str]]]:
    where = table_label(r) + (f" {latex_display(r['block'])} 구획" if r.get("block") else "")
    direction = DIRECTION_LABEL.get(r.get("direction") or "", "방향 미상")
    def own_row(x: dict, place: str) -> tuple[str, str]:
        gain = f"괄호는 {_field(x['gain_base_model'])} {_field(latex_display(x['gain_base_text']))} 대비 증감" if x.get("gain") else ""
        note = " · ".join(n for n in (place, gain) if n)
        return (f"이 논문 · {_field(x['model'])} · {_field(row_value(x))}" + (f" · {note}" if note else ""), "")
    # 위치(표·구획)는 대표 행에만 — 같은 표·구획의 다른 자기 행에 되풀이하면 비고 칸이 위치로 찬다(2026-10-15 카드).
    rows: list[tuple[str, str]] = [own_row(r, where)] + [own_row(x, "") for x in r.get("own_rows") or []]
    baselines = r.get("baselines") or []
    # 비교 방법의 원 논문 인기 지표(2026-10-13 사용자 결정 — 리더보드 대신 "이 분야에서 많이 쓰이는 방법"). 가장 많이 인용된 방법이
    # 성능 상위 3개 밖이면 그 행도 싣는다. 이어진 방법은 이름에 원 논문 링크를 단다.
    import method_links
    pop = r.get("popularity") or {}
    # 원 논문 인용 수를 확인한 방법만 견준다 — 일부만 확인했는데 "이 표에서 가장 많이 인용된" 이라고 하면 과장이다(Codex 검토 P2).
    checked, total = r.get("popularity_checked") or 0, r.get("popularity_total") or len(r.get("peers") or [])
    # 확인한 방법이 하나뿐이면 "가장 많이 인용된" 은 비교가 아니다(2026-10-15 카드: 7개 중 1개 확인으로 그렇게 썼다) — 둘 이상일 때만.
    cited = method_links.most_cited(r) if checked >= 2 else None
    scope = (f"이 표의 비교 방법 {total}개 중 원 논문 인용 수를 확인한 {checked}개에서" if checked < total
             else f"이 표의 비교 방법 {total}개 중")
    shown = list(baselines) + ([cited[0]] if cited and all(b["model"] != cited[0]["model"] for b in baselines) else [])
    for b in shown:
        p = pop.get(b["model"]) or {}
        note = method_links.label(p)
        rows.append((f"같은 표 · {_field(b['model'])} · {_field(row_value(b))}" + (f" · {note}" if note else ""),
                     f"https://arxiv.org/abs/{p['arxiv']}" if p.get("arxiv") else ""))
    # 같은 표의 다른 구획(이 논문 행이 없는 구획) — 실행 예산·구성 등 평가 조건이 다를 수 있어 차이·우열 문장을 쓰지 않는다(2026-10-13).
    for ref in r.get("references") or []:
        rows.append((f"{REFERENCE_KIND} · {_field(ref['model'])} · {_field(row_value(ref))} · {table_label(r)} {latex_display(ref.get('block') or '')} 구획"
                     f" · {REFERENCE_NOTE}", ""))
    shown_ext = [c for c in (ext.get("competitors") or []) if external_visible(c)] if first else []
    rows += [_external_row(c) for c in shown_ext]
    best = (r.get("compare") or {}).get("best")
    if best:
        rows.append((f"기존 관측 · {_field(best['model'])} · {best['value']:g} · {best['reported_in']} 표 · 평가 조건 미확인", ""))
    # 요약 — 같은 표·같은 열·같은 구획의 가장 좋은 비교 방법 대비 한 문장을 **표 위에** 둔다(2026-10-15 사용자: 표를 먼저 보면 무엇과 무엇을
    # 견주는지 모른다). `_baselines` 가 방향대로 정렬해 둔다.
    if baselines:
        # 지표 방향은 상자 머리에 있다 — 요약 줄에서는 뺀다.
        line = difference_sentence(r, baselines[0], f"{where}에서", same_column=True, direction_note=False)
        if line and cited and cited[0]["model"] == baselines[0]["model"]:
            line += f" {latex_display(cited[0]['model'])}은(는) {scope} 가장 많이 인용된 방법이기도 하다(인용 {cited[1]['citations']:,}회)."
        if line:
            rows.insert(0, (LEAD_PREFIX + line.removeprefix("→ "), ""))
    # 가장 많이 인용된 비교 방법 대비 — 같은 표·같은 열·같은 구획이라 척도가 같다. "많이 인용됨" 은 많이 쓰인다는 뜻이지 성능 순위가 아니다.
    if cited and (not baselines or cited[0]["model"] != baselines[0]["model"]):
        line = difference_sentence(r, {**cited[0], "model": f"{latex_display(cited[0]['model'])}(인용 {cited[1]['citations']:,}회)"},
                                   f"{scope} 가장 많이 인용된 방법과 견주면", same_column=True)
        if line:
            rows.append((line, ""))                          # 원 논문 링크는 표의 그 방법 행에 있다 — 문장 전체를 링크로 만들면 읽기 어렵다
    unchecked = []
    for c in shown_ext:
        if conditions_verified(c):
            line = difference_sentence(r, {**c, "model": c.get("display_model") or c["model"]}, "평가 조건이 같다고 대조한 외부 결과와 견주면",
                                       same_column=False)
            if line:
                rows.append((line, c.get("source_url") or ""))
        else:
            unchecked.append(_field(c.get("display_model") or c["model"]))
    if unchecked:
        rows.append((f"→ 외부 논문 결과({', '.join(unchecked)})는 평가 조건이 같은지 확인되지 않아 차이를 말하지 않는다.", ""))
    if shown_ext:
        rows.append(("→ 외부 논문 행의 같은 과제·지표 여부는 웹 검색 에이전트가 판독했다 — 이름의 링크로 원문 표를 확인할 수 있다.", ""))
    missed = sum(c.get("status") in ("fetch_failed", "table_failed") for c in ext.get("competitors") or []) if first else 0
    if first and ext.get("status") == "incomplete" and shown_ext and missed:
        # 확인된 외부 결과가 있는데 뭉뚱그린 "미완료" 만 붙이면 그 결과까지 못 믿게 읽힌다(2026-10-07 실측) — 못 본 후보 수만 센다.
        rows.append((f"→ 웹 검색 후보 중 {missed}건은 출처 표를 확인하지 못해 싣지 않았다.", ""))
    elif first and ext.get("status") == "incomplete" and not shown_ext:
        rows.append(("→ 저자가 비교하지 않은 더 최근 결과(웹 검색)는 이번에 확인하지 못했다.", ""))
    elif first and ext.get("status") == "done" and not shown_ext and ext.get("cached_from"):
        # 다른 논문 기준으로 한 검색 기록을 재사용했다 — 이 논문의 "저자가 비교하지 않은" 목록으로 찾은 것이 아니다(Codex 검토 P2).
        when = f"{ext['checked_on']} " if ext.get("checked_on") else ""
        rows.append((f"→ 같은 벤치마크·지표의 최근 웹 검색 기록({when}다른 논문 기준)을 다시 썼고, 거기서 이 카드에 실을 결과는 없었다.", ""))
    elif first and ext.get("status") == "done" and not shown_ext and not ext.get("competitors"):
        # 웹 검색이 후보를 하나도 내지 않은 경우 — "찾았지만 확인 못 함" 과 다르다(2026-10-14 WARP-VLA: 응답이 빈 목록이었다).
        rows.append(("→ 저자가 비교하지 않은 같은 벤치마크·지표의 다른 결과를 웹 검색에서 찾지 못했다.", ""))
    elif first and ext.get("status") == "done" and not shown_ext:
        rows.append(("→ 저자가 비교하지 않은 더 최근 결과를 웹에서 찾았지만 출처 표에서 확인된 것이 없다.", ""))
    unit = " (%)" if r.get("unit") == "%" and "%" not in r.get("metric", "") else ""
    # 반복 횟수는 이 논문 표의 것이다 — 외부 행은 출처 열 이름을 따로 단다(Codex 검토 P2: 머리가 외부 값까지 설명하는 것처럼 읽혔다).
    runs = f" · 이 논문 표는 {r['averaged_over']}회 평균" if isinstance(r.get("averaged_over"), int) and r["averaged_over"] > 1 else ""
    # 머리의 방향도 "낮을수록 좋음"·"방향 미상" 일 때만(2026-10-15 사용자: "높을수록 좋음은 당연하니 빼자").
    tail = "" if r.get("direction") == "higher" else f" ({direction})"
    return (f"{COMPARISON_PREFIX}{_field(r['benchmark'])} · {_field(r['metric'])}{unit}{runs}{tail}", rows)


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
