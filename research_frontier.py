"""④⑤⑧⑨ 성능 동향 — 최종 후보 논문의 결과를 관측 DB 와 외부 근거까지 추적해 "어디까지 믿을 수 있는지"를 보인다(2026-09-30).

"SOTA 를 판정하는 에이전트"가 아니라 **"성능 주장을 외부 증거와 평가 조건까지 추적하는 에이전트"**다(사용자 방향). 흐름:
1. 모든 최종 후보(arXiv)에서 표 결과를 뽑아(`performance_results`) 관측 DB 에 쌓는다(`frontier_store`) — SOTA 라는 말이 없어도.
2. 이 논문 결과를 **다른 논문들의** 관측값과 견준다. 넘으면 외부 조사의 강한 트리거.
3. 프로필·날짜마다 최대 5편을 한 호출로 외부 조사(`external_evidence`, 전체 300초). 같은 (벤치마크, 지표)는 7일 캐시. 실패하면 "외부 비교 미완료"로 메일은 나간다.
4. 외부 신호(인용·공식 GitHub·HF)는 따로 모은다(`adoption_signals`) — 성능과 섞지 않는다.

외부 조사 우선순위: 기존 관측 최고를 넘은 결과 · 명시적 SOTA 주장 → 프로필 순위(메일 순서). 인용·별은 우선순위에 안 쓴다 — 새 논문은 당연히 적다.
메일 문구는 이 모듈이 고정한다. "현재 SOTA" 라고 쓰지 않는다. 조건이 다르면 수치·출처·다른 조건을 보이되 **우열을 계산하지 않는다**.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

MAX_EXTERNAL_PER_DAY = 5
CACHE_DAYS = 7
# DB 기록은 값 캐시일 뿐이다. 호출 예산은 DB·프로필·날짜 키로 별도로 센다(2026-10-07 사용자 결정).
_CALLED_ON: set[tuple[str, str, str]] = set()
_RESULTS: dict[tuple[str, str, str], dict] = {}     # (DB, 날, 논문) → 오늘 외부 조사 결과 — DB 표가 없어도 다음 프로필이 재사용
_SIGNALS: dict[tuple[str, str], dict] = {}          # (날, 논문) → 외부 신호 — 스냅숏 표가 없어도 프로필마다 다시 조회하지 않는다
# 외부 조사(300초)와 **별도로**, 표 HTML 받기·외부 신호 조회에 한 번 부를 때 쓰는 시간 상한. 넘으면 남은 논문은 그 조회를 건너뛴다
# (Codex 검토 2026-09-30: 논문 수 × 프로필 4개만큼 순차 조회가 쌓여 설정값 합산으로 16분 규모가 나왔다).
LOOKUP_BUDGET_S = 60.0
_GH_MENTION_RE = re.compile(r"github\.com/([\w.-]+/[\w.-]+)", re.I)

_HTML_CACHE: dict[str, list[dict] | None] = {}


# 성능 결과를 못 낸 사유 기록. 테스트는 conftest 가 이 값을 임시 경로로 돌린다 — 기본값으로 두면 테스트가 운영 logs/ 에 쓴다
# (2026-10-07 실측: 테스트 실행만으로 196줄이 쌓였다).
STATUS_PATH = Path(__file__).parent / "logs" / "frontier_status.jsonl"


def _status(path: Path | None, day: str, profile: str, aid: str, stage: str, reason: str) -> None:
    """부가 기록 실패가 배달을 막지 않게 별도로 처리한다."""
    import json
    try:
        path = path or STATUS_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"date": day, "profile": profile, "paper_id": aid,
                                     "stage": stage, "reason": reason}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _baselines(results: list[dict], main: dict, method: str = "") -> list[dict]:
    """같은 표·열의 명확한 다른 방법만 남겨 자기 변형과 섞이지 않게 한다.

    방법 이름이 `Foo` 면 `Foo-S`·`Foo-B` 같은 크기 변형은 추출 단계에서 own=False 가 될 수 있다 — 이름이 방법 이름으로 시작하면
    자기 변형으로 보고 뺀다(2026-10-07 Codex 독립 검토 P2-2). 애매하면 싣지 않는 쪽이다."""
    own_key = re.sub(r"[^0-9a-z]", "", method.casefold())
    table, _, cell = main["locator"].partition(":r")
    col = cell.partition("c")[2]
    rows = [r for r in results if not r["own"] and r["locator"].partition(":r")[0] == table
            and r["locator"].partition(":r")[2].partition("c")[2] == col
            and not re.search(r"(?i)\bours\b|\bproposed\b|\bw/|\bwithout\b", r["model"])
            and not (len(own_key) >= 2 and re.sub(r"[^0-9a-z]", "", r["model"].casefold()).startswith(own_key))]
    if main.get("direction") in ("higher", "lower"):
        rows = sorted(rows, key=lambda r: r["value"], reverse=main["direction"] == "higher")
    return rows[:3]


def _arxiv_tables(arxiv_id: str) -> list[dict] | None:
    """이 논문의 arXiv HTML 표. 프로세스 안에서 한 번만 받는다(여러 프로필에 같은 논문이 실린다).
    **받기 실패는 None, 받았는데 표가 없으면 빈 목록** — 둘을 가르지 않으면 정상 재추출의 빈 결과로 옛 관측을 못 지운다(Codex 최종 검토)."""
    if arxiv_id not in _HTML_CACHE:
        try:
            import arxiv_tables
            import external_evidence
            _HTML_CACHE[arxiv_id] = arxiv_tables.parse_tables(
                external_evidence._fetch(f"https://arxiv.org/html/{arxiv_id}", external_evidence.FETCH_TIMEOUT_S))
        except Exception as error:  # noqa: BLE001 — HTML 판이 없는 논문이 많다(PDF 만 있는 경우)
            print(f"  [성능 동향] {arxiv_id} 표 받기 실패: {type(error).__name__}")
            _HTML_CACHE[arxiv_id] = None
    return _HTML_CACHE[arxiv_id]


def _paper_row(db: Path, arxiv_id: str) -> tuple[str, str, str, str]:
    """(원문/초록, 제목, 공개일, 출처). 짧은 원문도 원문이고 긴 초록도 초록이다."""
    try:
        with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as con:
            con.execute("PRAGMA query_only=ON")
            row = con.execute("SELECT text_path, abstract, title, published FROM papers WHERE arxiv_id=?", (arxiv_id,)).fetchone()
    except sqlite3.Error:
        return "", "", "", "abstract"
    if not row:
        return "", "", "", "abstract"
    text = ""
    if row[0]:
        try:
            text = Path(row[0]).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = ""
    return text or (row[1] or ""), row[2] or "", (row[3] or "")[:10], "text" if text else "abstract"


def _main_results(own: list[dict], claims: list[dict]) -> list[dict]:
    """메일에 보일 이 논문 결과 — 주장한 벤치마크를 먼저, 벤치마크마다 하나(평균 열 우선), 최대 2개."""
    import performance_results as pr
    claimed = list(dict.fromkeys(pr.norm_key(b) for c in claims for b in c.get("benchmarks") or []))

    def order(r: dict) -> tuple:
        # 주장 문장에 나온 벤치마크 순서 → 평균 열 → 표 순서·열 순서(왼쪽이 주 지표인 경우가 많다). 실측(2026-09-30): 문자열 정렬이던 때
        # FIVE-VLA 의 주 결과(Bench2Drive DS)를 두고 Fail2Drive 일반화 열(67.0)을 골랐다.
        tbl, _, cell = r["locator"].partition(":r")
        row, _, col = cell.partition("c")
        # 평균 열을 앞세우지 않는다 — 범주별 열은 추출 단계에서 이미 평균만 남고, 앞세우면 뒤쪽 보조 표(능력별 평균)가 주 결과를 밀어낸다
        # (2026-09-30 실측: FIVE-VLA 가 Bench2Drive DS 90.95 대신 "DS / Ability / Mean 78.75" 를 골랐다).
        tno = int(tbl.rsplit("#", 1)[1]) if "#" in tbl else 0
        return (claimed.index(r["bench_key"]) if r["bench_key"] in claimed else len(claimed), tno, int(col or 0), bool(re.search(r"(?i)\bw/|\bw/o|\bwithout|\bablation|\bvariant", r.get("model", ""))), int(row or 0))
    seen: set[str] = set()
    ranked = sorted(own, key=order)
    out = []
    for r in ranked:
        if r["bench_key"] in seen:
            continue
        seen.add(r["bench_key"])
        out.append(r)
        if len(out) >= 2:
            break
    return out


def analyze(db: Path, papers: list[dict], *, tables_of: Callable[[str], list[dict]] | None = None,
            run_external: Callable[[list[dict]], dict] | None = None, signals_of: Callable[[str], dict] | None = None,
            profile_id: str = "", status_path: Path | None = None,
            now: datetime | None = None, clock: Callable[[], float] | None = None) -> None:
    """papers 에 `_frontier`·`_signals` 를 붙인다. 어떤 실패도 예외로 올리지 않는다(메일은 나가야 한다)."""
    import time
    import frontier_store
    import performance_results as pr
    clock = clock or time.monotonic
    lookup_deadline = clock() + LOOKUP_BUDGET_S
    given = tables_of
    now = now or datetime.now(timezone.utc)
    day = now.date().isoformat()
    status_path = status_path or STATUS_PATH

    def status(aid: str, stage: str, reason: str) -> None:
        _status(status_path, day, profile_id, aid, stage, reason)

    def get_tables(aid: str) -> list[dict] | None:
        if given is not None:
            return given(aid)
        if aid not in _HTML_CACHE and clock() > lookup_deadline:
            status(aid, "lookup", "예산 소진")
            return None                                             # 조회 예산이 끝났다 — 확인 못 함(빈 결과와 다르다)
        return _arxiv_tables(aid)
    candidates: list[tuple[tuple, dict, dict]] = []
    texts: dict[str, str] = {}
    for order, paper in enumerate(papers):
        aid = paper.get("arxiv_id") or ""
        if not aid or aid.startswith("pdf-") or paper.get("source") in ("crossref", "doi"):
            status(aid, "extract", "비arXiv")
            # arXiv 밖 논문(DOI·합성 ID)은 HTML 표가 없어 표 분석을 **지원하지 않는다** — 조용히 빼지 않고 그 상태를 적는다(Codex 최종 검토).
            # 원문·초록이 있으면 문장 근거(원문 주장 표시)만 본다.
            try:
                text, title, _pub, src = _paper_row(db, aid) if aid else ("", "", "", "abstract")
                text = text or paper.get("abstract") or ""
                claims = paper.get("_sota_claims") or []
                paper["_frontier"] = {"main": [], "claims": claims, "external": None, "unsupported": "non_arxiv",
                                      "sentences": pr.performance_evidence(text, claims, src, pr.method_name(title or paper.get("title")))}
            except Exception as error:  # noqa: BLE001
                print(f"  [성능 동향] {aid or 'DOI 논문'} 생략: {type(error).__name__}")
            continue
        try:
            text, title, published, src = _paper_row(db, aid)
            texts[aid] = text
            claims = paper.get("_sota_claims") or []
            method = pr.method_name(title or paper.get("title"))
            benches = pr.benchmark_candidates(text, claims)
            try:
                tables = get_tables(aid) if benches else []
            except Exception:
                tables = None
            results = pr.table_results(tables, method=method, benchmarks=benches) if tables else []
            try:
                if tables is not None:                          # 받았다면(빈 결과 포함) 그 논문 관측을 이번 추출로 바꾼다. 받기 실패면 그대로 둔다
                    frontier_store.record(db, aid, results, published, now)
            except Exception as error:  # noqa: BLE001 — migrate 전 운영 DB 등. 비교만 못 한다
                print(f"  [성능 동향] 관측 저장 생략: {type(error).__name__}")
            # 대표 결과는 방향(높을수록·낮을수록)을 아는 열만이다 — 방향 모르는 열(파라미터 수·시간 등)이 카드의 대표 결과가 되면 안 된다.
            main = _main_results([r for r in results if r["own"] and r["direction"]], claims)
            reason = ("벤치마크 후보 없음" if not benches else "HTML 확보 실패" if tables is None else
                      "표 0" if not tables else "추출 0" if not results else
                      "자기 행 인식 0" if not any(r["own"] for r in results) else "방향 미상" if not main else "")
            if reason:
                status(aid, "extract", reason)
            for r in main:
                r["baselines"] = _baselines(results, r, method)
            for r in main:
                r["compare"] = frontier_store.compare(db, aid, r)
            fr = {"main": main, "claims": claims, "external": None,
                  "comparison_methods": [r for r in results if not r["own"] and not re.search(r"(?i)\bours\b|\bproposed\b|\bw/|\bwithout\b", r["model"])],
                  "sentences": [] if main else pr.performance_evidence(text, claims, src, method)}
            paper["_frontier"] = fr
            if main:
                trigger = any(r["compare"]["status"] == "above_observed" for r in main) or bool(claims)
                candidates.append(((not trigger, order), paper, main[0]))
        except Exception as error:  # noqa: BLE001
            status(aid, "extract", type(error).__name__)
            print(f"  [성능 동향] {aid} 생략: {type(error).__name__}")
    # 외부 조사(호출당 300초 상한)는 조회 예산에서 뺀다 — 둘은 별개 상한이다(Codex 최종 검토: 외부 조사가 조회 예산을 다 먹은 뒤
    # S2 batch 를 새로 불렀다). 외부 조사 전 남은 조회 시간을 그 뒤로 옮긴다.
    left = lookup_deadline - clock()
    try:
        _external(db, candidates, day, run_external, texts, profile_id=profile_id, status_path=status_path)
    except Exception as error:
        for _, paper, _ in candidates:
            paper["_frontier"]["external"] = {"status": "incomplete", "reason": type(error).__name__}
            status(paper["arxiv_id"], "external", "에이전트 실패: " + type(error).__name__)
    lookup_deadline = clock() + left                     # 이미 다 썼으면(음수) 마감은 지난 것이다
    ids = [p.get("arxiv_id") for p in papers if p.get("arxiv_id") and not p["arxiv_id"].startswith("pdf-")]
    todo = [a for a in ids if (day, a) not in _SIGNALS]
    prefetched: dict = {}
    if signals_of is None and todo and clock() < lookup_deadline:
        try:
            import adoption_signals
            # 캐시(프로세스·당일 스냅숏)에 없는 것만 S2 batch 로 한 번에 — 편마다 부르면 초당 1회 한도에 걸린다(실측 2026-09-30)
            todo = [a for a in todo if not adoption_signals._cached(db, a, day)]
            prefetched = adoption_signals.scholarly_batch(todo) if todo else {}
        except Exception as error:  # noqa: BLE001
            print(f"  [외부 신호] 인용 일괄 조회 실패: {type(error).__name__}")
    for aid in ids:
        paper = next(p for p in papers if p.get("arxiv_id") == aid)
        if (day, aid) in _SIGNALS:
            paper["_signals"] = _SIGNALS[(day, aid)]
            continue
        if signals_of is None and clock() >= lookup_deadline:
            continue                                                # 조회 예산이 끝났다 — 외부 신호 없이 간다
        try:
            import adoption_signals
            if signals_of is None:
                adoption_signals._DEADLINE = time.monotonic() + max(0.0, lookup_deadline - clock())   # 요청마다 남은 시간만 준다
            sig = (signals_of or (lambda a: adoption_signals.collect(db, a, now=now, scholarly_data=prefetched.get(a))))(aid)
            paper["_signals"] = sig
            if signals_of is None:
                _SIGNALS[(day, aid)] = sig
        except Exception as error:  # noqa: BLE001
            print(f"  [외부 신호] {aid} 생략: {type(error).__name__}")
        finally:
            try:
                import adoption_signals
                adoption_signals._DEADLINE = None
            except Exception:  # noqa: BLE001
                pass


def _external(db: Path, candidates: list, day: str, run_external: Callable | None, texts: dict[str, str] | None = None, *,
              profile_id: str = "", status_path: Path | None = None) -> None:
    """프로필·날짜마다 최대 5편을 한 에이전트 호출로 조사한다. 오늘 이미 본 논문·7일 안에 본 (벤치마크, 지표)는 재사용한다."""
    import external_evidence
    import frontier_store
    done_today = {e["paper_id"]: e for e in frontier_store.external_today(db, day)}
    done_today.update({aid: res for (database, d, aid), res in _RESULTS.items() if d == day and database == str(db.resolve())})
    since = (date.fromisoformat(day) - timedelta(days=CACHE_DAYS)).isoformat()
    fresh: list[tuple[dict, dict]] = []
    for _key, paper, main in sorted(candidates, key=lambda c: c[0]):
        aid = paper["arxiv_id"]
        if aid in done_today:
            paper["_frontier"]["external"] = done_today[aid]
            continue
        cached = frontier_store.external_recent(db, main["bench_key"], main["metric_key"], since)
        if cached:
            # 다른 논문 기준으로 한 조사다 — **출처·셀 값만** 재사용하고 조건 판정(같다·다르다)은 버린다. 조건은 대상 논문마다 다르다
            # (Codex 재현: A 의 "동일 조건"이 B 로 복사돼 B 가 "관측 범위 내 최고"가 됐다).
            comps = [{**c, "same_conditions": False, "differences": [], "unverified_differences": 0, "matched_conditions": [],
                      "same_task": False, "task_note": "", "task_not_checked": True, "conditions_not_checked": True} for c in cached.get("competitors") or []]
            paper["_frontier"]["external"] = {**cached, "competitors": comps, "cached_from": cached.get("paper_id")}
            continue
        fresh.append((paper, main))
    if not fresh:
        return
    call_key = (str(db.resolve()), profile_id, day)
    if call_key in _CALLED_ON:
        slots = 0                                                 # 이 프로필의 오늘 호출은 이미 했다 — 호출당 300초
    else:
        slots = MAX_EXTERNAL_PER_DAY
    chosen, skipped = fresh[:slots], fresh[slots:]
    for paper, _main in skipped:
        paper["_frontier"]["external"] = {"status": "not_selected"}
        _status(status_path, day, profile_id, paper["arxiv_id"], "external", "외부 미선정·예산 소진")
    if not chosen:
        return
    _CALLED_ON.add(call_key)
    texts = texts or {}
    # 대상 원문은 에이전트에 주지 않는다(프롬프트는 제목·벤치마크·값만). Python 검증이 쓴다 — 조건 인용 대조, 원문이 가리킨 GitHub 저장소.
    targets = [{"paper_id": p["arxiv_id"], "title": p.get("title") or "", "method": "", "benchmark": m["benchmark"],
                "metric": m["metric"], "metric_key": m["metric_key"], "value": m["text"], "direction": m["direction"],
                "existing_methods": list(dict.fromkeys(r["model"] for r in p["_frontier"].get("comparison_methods", m.get("baselines", [])))),
                "source_text": texts.get(p["arxiv_id"], ""),
                "repo_mentions": {x.lower().rstrip(".").removesuffix(".git") for x in _GH_MENTION_RE.findall(texts.get(p["arxiv_id"], ""))}}
               for p, m in chosen]
    try:
        results = (run_external or external_evidence.check)(targets)
    except Exception as error:  # noqa: BLE001
        results = {t["paper_id"]: {"status": "incomplete", "reason": type(error).__name__, "competitors": []} for t in targets}
    for paper, main in chosen:
        res = results.get(paper["arxiv_id"]) or {"status": "incomplete", "reason": "응답 없음", "competitors": []}
        paper["_frontier"]["external"] = res
        _RESULTS[(str(db.resolve()), day, paper["arxiv_id"])] = res
        if res.get("status") == "incomplete":
            _status(status_path, day, profile_id, paper["arxiv_id"], "external", "외부 비교 미완료: " + str(res.get("reason") or "에이전트 실패"))
            for competitor in res.get("competitors") or []:
                if competitor.get("status") == "table_failed":
                    _status(status_path, day, profile_id, paper["arxiv_id"], "external", competitor["reason"])
        elif not any(c.get("status") == "verified" for c in res.get("competitors") or []):
            reasons = [str(c.get("reason") or c.get("status") or "미확인") for c in res.get("competitors") or []]
            _status(status_path, day, profile_id, paper["arxiv_id"], "external",
                    "외부 셀 확인 0: " + (" / ".join(reasons) if reasons else "비교 후보 없음"))
        try:
            frontier_store.save_external(db, paper["arxiv_id"], day, main["bench_key"], main["metric_key"], res["status"], res)
        except Exception as error:  # noqa: BLE001
            print(f"  [성능 동향] 외부 조사 저장 생략: {type(error).__name__}")


# ---------------------------------------------------------------- 메일 문구 — 판정이 아니라 근거 수준

def _better(a: float, b: float, direction: str, metric: str = "") -> bool | None:
    """척도를 맞춘 뒤 견준다. 비율 지표만 0~1 ↔ % 로 맞추고(실측 Group3AD 0.751 vs 87.2), 맞출 수 없으면 None — 견주지 않는다."""
    import external_evidence
    if direction not in ("higher", "lower"):
        return None
    pair = external_evidence.comparable_values(a, b, metric)
    if pair is None or pair[0] == pair[1]:
        return None
    return pair[0] > pair[1] if direction == "higher" else pair[0] < pair[1]


def _verified(fr: dict) -> list[dict]:
    ext = fr.get("external") or {}
    import paper_observations
    return [c for c in ext.get("competitors") or [] if paper_observations.external_visible(c)]


def _same(c: dict) -> bool:
    return bool(c.get("same_conditions")) and not c.get("differences") and not c.get("unverified_differences")


def status_label(fr: dict) -> str:
    main = (fr.get("main") or [None])[0]
    prefix = "성능 선도 주장 · " if fr.get("claims") else ""
    if not main:
        return (prefix or "") + "원문 문장의 성능 주장" if fr.get("sentences") else ""
    ext = fr.get("external") or {}
    verified = _verified(fr)
    same = [c for c in verified if _same(c)]
    if same:
        return prefix + "동일 조건 외부 비교 가능"
    if verified:
        return prefix + "외부 경쟁 결과 확인 · 조건 차이로 직접 비교 안 함"
    if ext.get("status") == "incomplete":
        return prefix + "외부 비교 미완료"
    if ext.get("status") == "done":
        return prefix + "외부 경쟁 결과를 원문 표에서 확인하지 못함"
    if main["compare"]["status"] == "above_observed":
        return prefix + "성능 결과 보고 · 외부 검증 대상"
    return prefix + "성능 결과 보고"


def _host(url: str) -> str:
    from urllib.parse import urlparse
    return (urlparse(url).hostname or "").removeprefix("www.")


def block_lines(fr: dict) -> list[tuple[str, str]]:
    """[(글, 링크 URL 또는 "")]. 첫 줄은 상태 라벨. 비어 있으면 성능 동향을 싣지 않는다."""
    label = status_label(fr)
    if not label:
        return []
    lines: list[tuple[str, str]] = [(label, "")]
    main = (fr.get("main") or [None])[0]
    if not main:
        s = fr["sentences"][0]
        where = f" [S{s['index']:04d}]" if s.get("index") else " (초록)"
        lines.append((f"원문 주장: “{s['sentence']}”{where} — 표 구조가 없어 수치 비교는 하지 않음", ""))
        if fr.get("unsupported") == "non_arxiv":
            lines.append(("arXiv 밖 논문 — 표 분석·외부 비교는 지원하지 않음", ""))
        return lines
    for r in fr["main"]:
        lines.append((f"이 논문: {r['benchmark']} · {r['metric']} {r['text']} (원문 표 {r['model']} 행)", ""))
    verified = _verified(fr)
    for c in verified:
        import paper_observations
        lines.append(paper_observations.external_line(c))
    if verified and not all(_same(c) for c in verified):
        lines.append(("→ 평가 조건이 달라 직접적인 우열 판정은 하지 않음", ""))
    ext = fr.get("external") or {}
    unchecked = [c for c in ext.get("competitors") or [] if c.get("status") in ("no_structure", "not_found", "fetch_failed", "table_failed", "unofficial_repo")]
    if unchecked:
        hosts = ", ".join(dict.fromkeys(_host(c.get("source_url") or "") for c in unchecked))
        lines.append((f"표에서 수치를 확인하지 못한 경쟁 후보 {len(unchecked)}건({hosts}) — 수치는 싣지 않음", ""))
    if ext.get("unverified_sources"):
        lines.append((f"미확인 외부 출처 {ext['unverified_sources']}건 — 판정에 쓰지 않음", ""))
    if ext.get("status") == "incomplete":
        lines.append(("외부 비교 미완료 — 논문 자체 결과만 표시", ""))
    if ext.get("cached_from"):
        lines.append((f"외부 경쟁 결과는 {ext.get('checked_on')} 같은 벤치마크 조사 재사용", ""))
    return lines


def reading_point(paper: dict) -> str:
    """`읽을 포인트` — 관심축 연관 + 확인 범위만. 칭찬·추천·점수 금지(사용자 결정 2026-09-30). Python 템플릿이라 LLM 이 쓰지 않는다.
    이 문장은 반응 가중치 학습 입력이 아니다 — `feedback_weights` 는 사용자 클릭만 읽는다."""
    kws = ((paper.get("_score") or {}).get("core_hits") or [])
    parts: list[str] = []
    if kws:
        parts.append(f"`{kws[0]}` 관심축과 직접 연관.")
    fr = paper.get("_frontier") or {}
    main = (fr.get("main") or [None])[0]
    if main:
        head = f"{main['benchmark']}에서 {main['metric']} {main['text']}를 보고"
        verified = _verified(fr)
        if verified and not all(_same(c) for c in verified):
            parts.append(head + "했으나, 확인한 외부 경쟁 결과와 평가 조건이 달라 직접 우열 비교는 어려움.")
        elif verified:
            parts.append(head + "했고, 외부 논문 표와 일부 조건 대조 기록이 있음. 전체 평가 조건의 일치는 별도 확인이 필요함.")
        elif main["compare"]["status"] == "above_observed":
            parts.append(head + "했고, 외부 검증 대상으로 선택됨.")
        elif (fr.get("external") or {}).get("status") == "incomplete":
            parts.append(head + "함. 외부 비교는 이번에 마치지 못함.")
        else:
            parts.append(head + "함.")
    elif fr.get("sentences"):
        parts.append("논문 내 성능 결과를 문장으로 보고하나, 표 구조를 확인하지 못해 논문 간 수치 비교는 하지 않음.")
    elif paper.get("deep_status") in ("abstract_only", "fetch_failed"):
        parts.append("본문을 확보하지 못해 결과 수치는 확인하지 않음.")
    return " ".join(parts)
