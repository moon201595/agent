"""⑦ 코드 재현 — 저장소를 못 찾아도 거기서 끝내지 않는다: 공식 → 저자 연관 → 제3자 → 유사 구현 → 없음 사다리(2026-09-16).

사용자 피드백(2026-09-14): "코드 재현 저장소를 못 찾는다고 그냥 포기하지 말고 응용 소스라도, 비슷한 거라도 반드시 찾도록".
단, **비슷한 코드를 이 논문의 공식 구현인 것처럼 표시하지 않는다**(CLAUDE.md 규칙 7). 그래서 결과는 단계(tier)로 갈라 적고
메일에도 단계 이름을 그대로 쓴다.

단계 판정은 이미 있는 사실에서만 나온다 — LLM 이 아니다:
- `official`      저자가 논문 본문에 적은 저장소(`repro_results.source='in_text'`).
- `author`        GitHub 검색 결과인데 저장소 소유자 이름에 저자 성이 들어 있다.
- `third_party`   GitHub 검색으로 이름이 맞은 저장소 — 이 논문의 구현인지 **미확인**(이름 충돌일 수 있다).
- `analogous`     위가 없거나 전부 실패했을 때, 이 논문이 걸린 핵심 키워드로 GitHub 를 검색한 최다 별점 저장소.
                  같은 **과제**의 구현이지 이 논문의 구현이 아니다.
- `none`          그것도 못 찾음(검색어를 남긴다).
유사 구현은 **찾아서 보여 주기만** 한다 — 격리 실행(⑦)에 태우지 않는다. 남의 과제 코드가 돌아도 이 논문을 재현한 것이 아니다.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import storage

TIERS = ("official", "author", "third_party", "analogous", "none")
_STAGE_DEPTH = {"clone": 0, "no_target": 1, "build": 2, "install_only": 3, "run": 4}     # docker_runner 의 단계 순서
TIER_LABELS = {"official": "공식 코드", "author": "저자 연관 저장소(공식 확인 안 됨)", "third_party": "제3자 저장소(이 논문 구현인지 미확인)",
               "analogous": "유사 구현(같은 과제, 이 논문 아님)", "none": "코드 없음"}
REFRESH_DAYS = 30           # 유사 구현 검색은 한 달에 한 번이면 된다 — GitHub 검색 한도(분당 30)를 매일 쓰지 않는다
MAX_KEYWORDS = 2            # 검색어 수 상한 = GitHub 호출 상한(논문당)


def _ddl(con: sqlite3.Connection) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS code_ladder ("
        " arxiv_id    TEXT PRIMARY KEY,"
        " tier        TEXT NOT NULL,"
        " url         TEXT,"
        " full_name   TEXT,"
        " stars       INTEGER,"
        " query       TEXT,"          # analogous·none 일 때 쓴 검색어(JSON 목록)
        " description TEXT,"
        " searched_at TEXT NOT NULL)"
    )


def init_db(db: Path) -> None:
    import schema_guard
    schema_guard.ensure(db, _ddl, "code_ladder")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get(arxiv_id: str, db: Path | None = None) -> dict | None:
    init_db(db or storage.DB_PATH)
    with sqlite3.connect(db or storage.DB_PATH) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM code_ladder WHERE arxiv_id=?", (arxiv_id,)).fetchone()
    if not row:
        return None
    out = dict(row)
    try:
        out["query"] = json.loads(out["query"] or "[]")
    except ValueError:
        out["query"] = []
    return out


def _record(db: Path, arxiv_id: str, tier: str, cand: dict | None, queries: list[str]) -> dict:
    init_db(db)
    cand = cand or {}
    with sqlite3.connect(db) as con:
        con.execute("INSERT OR REPLACE INTO code_ladder (arxiv_id, tier, url, full_name, stars, query, description, searched_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (arxiv_id, tier, cand.get("url"), cand.get("full_name"), cand.get("stars"),
                     json.dumps(queries, ensure_ascii=False), (cand.get("description") or "")[:300], _now().isoformat()))
    return get(arxiv_id, db)


def classify_repro_rows(rows: list[dict], authors_json: str | None) -> tuple[str, dict | None]:
    """repro_results 행(이미 시도한 저장소)에서 가장 높은 단계. 성공한 행이 있으면 그 행을 우선한다."""
    import code_finder
    surnames = code_finder._author_surnames(authors_json)

    def tier_of(r: dict) -> str:
        if r.get("source") == "in_text":
            return "official"
        owner = (r.get("repo_url") or "").rstrip("/").split("/")[-2] if (r.get("repo_url") or "").count("/") >= 4 else ""
        if surnames and surnames & code_finder._tokens(owner):
            return "author"
        return "third_party"
    # 이름만 맞은 제3자 저장소가 clone 도 안 되거나 설치 대상이 없었다면 참고 가치가 없다 — 그건 이름 충돌이다(2026-09-16 실측:
    # 'Rastaman4e/-1' 이 제3자 저장소로 나왔다). 그런 행은 없는 셈 치고 유사 구현 검색으로 내려간다. 공식·저자 연관은 실패해도 사실이다.
    usable = [r for r in rows if tier_of(r) != "third_party" or r.get("success")
              or _STAGE_DEPTH.get(r.get("stage") or "", 0) >= _STAGE_DEPTH["build"]]
    if not usable:
        return "none", None
    ranked = sorted(usable, key=lambda r: (not r.get("success"), TIERS.index(tier_of(r)), -_STAGE_DEPTH.get(r.get("stage") or "", 0)))
    best = ranked[0]
    return tier_of(best), {"url": best.get("repo_url"), "full_name": "/".join((best.get("repo_url") or "").rstrip("/").split("/")[-2:]),
                           "stars": None, "description": None, "success": bool(best.get("success"))}


# 참고 구현이 아닌 것 — 링크 모음(awesome-*)·논문 목록·튜토리얼·벤치마크 데이터 저장소. 키워드 검색은 이런 저장소가 별점이 가장 높다.
_LIST_REPO_RE = re.compile(r"(?:^|[-_/])(?:awesome|papers?|paper[-_]list|reading[-_]list|survey|curated|tutorials?|notes|collection)(?:$|[-_])",
                           re.IGNORECASE)


def looks_like_list_repo(url: str, description: str | None) -> bool:
    name = url.rstrip("/").rsplit("/", 1)[-1]
    return bool(_LIST_REPO_RE.search(name)) or bool(re.search(r"\b(?:curated list|awesome list|list of papers|paper list)\b",
                                                                description or "", re.IGNORECASE))


def find_analogous(keywords: list[str], exclude_urls: set[str] = frozenset()) -> tuple[dict | None, list[str]]:
    """핵심 키워드로 GitHub 검색 — 최다 별점 **코드** 저장소 하나. returns (후보 | None, 쓴 검색어)."""
    import code_finder
    queries: list[str] = []
    best: dict | None = None
    for kw in [k for k in keywords if k and k.strip()][:MAX_KEYWORDS]:
        queries.append(kw)
        for c in code_finder.github_search(kw, limit=5):
            if c.url in exclude_urls or code_finder.is_non_code_repo(c.url) or code_finder._is_tool_repo(c.url) \
                    or looks_like_list_repo(c.url, c.description):
                continue
            if best is None or (c.stars or 0) > (best.get("stars") or 0):
                best = {"url": c.url, "full_name": c.full_name, "stars": c.stars, "description": c.description, "query": kw}
        if best:
            break          # 첫 검색어에서 찾았으면 둘째 검색어는 쓰지 않는다
    return best, queries


def resolve(arxiv_id: str, keywords: list[str], db: Path | None = None, now: datetime | None = None) -> dict:
    """이 논문의 코드 단계를 정하고 기록한다. ⑦ 재현이 끝난 뒤(repro_results 가 있는 상태) 부르는 것이 맞다.
    official·author·third_party 가 있으면 검색하지 않는다. 유사 구현은 REFRESH_DAYS 안에 찾은 게 있으면 다시 찾지 않는다."""
    db = db or storage.DB_PATH
    now = now or _now()
    cached = get(arxiv_id, db)
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute("SELECT repo_url, source, confidence, success, stage FROM repro_results WHERE arxiv_id=?",
                                             (arxiv_id,))]
        prow = con.execute("SELECT authors FROM papers WHERE arxiv_id=?", (arxiv_id,)).fetchone()
    tier, cand = classify_repro_rows(rows, prow["authors"] if prow else None)
    if tier != "none":
        return _record(db, arxiv_id, tier, cand, [])
    if cached and cached["tier"] in ("analogous", "none"):
        try:
            age = now - datetime.fromisoformat(cached["searched_at"])
        except ValueError:
            age = timedelta(days=REFRESH_DAYS + 1)
        wanted = [k for k in keywords if k and k.strip()][:MAX_KEYWORDS]
        # 유사 구현은 첫 검색어에서 찾았으면 그 검색어 하나만 기록돼 있다 — 주 키워드가 그대로면 캐시, 바뀌었으면 다시 찾는다
        # (외부 검토 2026-09-16: 그전엔 analogous 면 키워드가 바뀌어도 옛 저장소를 계속 보였다).
        same_query = (cached["query"][:1] == wanted[:1]) if cached["tier"] == "analogous" else (cached["query"] == wanted)
        if age < timedelta(days=REFRESH_DAYS) and same_query:
            return cached
    found, queries = find_analogous(keywords, {r["repo_url"] for r in rows if r.get("repo_url")})
    return _record(db, arxiv_id, "analogous" if found else "none", found, queries)


def mail_line(arxiv_id: str, db: Path | None = None) -> str:
    """메일 한 줄. 공식 코드가 있으면 재현 라벨이 이미 말하므로 비운다. 나머지는 단계 이름을 그대로 쓴다."""
    row = get(arxiv_id, db)
    if not row or row["tier"] == "official":
        return ""
    name = row.get("full_name") or row.get("url") or ""
    if row["tier"] == "author":
        return f"코드: 저자 연관으로 보이는 저장소 {name} — 공식 여부는 확인되지 않았다"
    if row["tier"] == "third_party":
        return f"코드: 이름이 맞는 제3자 저장소 {name} — 이 논문의 구현인지 확인되지 않았다"
    if row["tier"] == "analogous":
        stars = f" ★{row['stars']:,}" if row.get("stars") else ""
        q = (row.get("query") or [""])[0]
        return f"코드: 이 논문의 저장소는 없음 · 같은 과제('{q}')의 참고 구현 {name}{stars} — 이 논문을 재현한 것이 아니다"
    q = ", ".join(row.get("query") or [])
    return f"코드: 이 논문의 저장소도, 같은 과제의 참고 구현도 찾지 못했다" + (f" (검색어: {q})" if q else "")
