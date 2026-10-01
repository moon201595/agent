"""⑧⑨ 외부 신호 — 인용(S2)·공식 저장소(GitHub)·HF 모델. **성능과 섞지 않는 관측값**이다(2026-09-30).

왜 따로인가: 인기 ≠ 성능. 별·인용·다운로드를 성능 판정에 더하면 3년 전 유명 모델이 막 나온 실제 1위를 먹는다. 그래서 성능 동향(`research_frontier`)과
다른 줄에, **판정어 없이** 관측값만 싣는다 — "높음·인기·우수" 같은 말을 붙이지 않는다(사용자 결정 2026-09-30).
GitHub 별은 ⑦ 사다리가 **공식·저자 연관**으로 가른 저장소일 때만 쓴다 — 제3자 구현의 별을 그 논문의 영향력처럼 붙이지 않는다.
HF 연결 모델은 그 논문 arXiv 주소를 README 에 적은 모델이라 "인용한"으로만 쓴다(공식이라는 보장이 없다).
외부 조회는 무료 API 이고, 실패하면 그 값만 "조회 실패"다 — 없음·0 으로 바꾸지 않는다(Codex 검토 2026-09-30).
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

HTTP_TIMEOUT_S = 10.0
_DEADLINE: float | None = None   # research_frontier 가 조회 예산의 절대 마감(monotonic)을 넣는다 — 요청마다 남은 시간만 쓴다

S2_MIN_INTERVAL_S = 1.1        # S2 한도는 "초당 1회, 엔드포인트 합산"이다(http_client 와 같은 값)
_last_s2 = 0.0


def _get_json(url: str, headers: dict | None = None) -> object:
    import time
    import httpx
    import api_usage
    global _last_s2
    host = re.sub(r"^https://([^/]+)/.*$", r"\1", url)
    is_s2 = host == "api.semanticscholar.org"
    for attempt in range(2):
        if is_s2:
            # 2026-09-30 실측: 주장 논문 두 편을 연달아 조회하자 두 번째가 HTTPStatusError(429)로 떨어졌다.
            wait = S2_MIN_INTERVAL_S - (time.monotonic() - _last_s2)
            if wait > 0:
                time.sleep(wait)
            _last_s2 = time.monotonic()
        timeout = HTTP_TIMEOUT_S
        if _DEADLINE is not None:
            timeout = min(timeout, _DEADLINE - time.monotonic())
            if timeout <= 0:
                raise TimeoutError("조회 예산 초과")
        try:
            resp = httpx.get(url, headers=headers or {}, timeout=timeout, follow_redirects=False)
        except httpx.HTTPError:
            api_usage.record(host, "error")
            raise
        api_usage.record(host, "ok" if resp.status_code == 200 else str(resp.status_code))
        if resp.status_code == 429 and attempt == 0:
            time.sleep(3.0)                                  # 한 번만 다시 — 그래도 안 되면 그 축만 "조회 실패"
            continue
        break
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def scholarly(arxiv_id: str, get: Callable[..., object] = None) -> dict:
    """S2 인용. {citations, influential, publication_date} 또는 {error}."""
    get = get or _get_json
    import http_client
    try:
        data = get(f"https://api.semanticscholar.org/graph/v1/paper/ARXIV:{arxiv_id}"
                   "?fields=citationCount,influentialCitationCount,publicationDate", http_client.s2_headers())
    except Exception as error:  # noqa: BLE001 — 부가 정보다
        return {"error": type(error).__name__}
    if not isinstance(data, dict):
        return {"missing": True}
    return {"citations": data.get("citationCount"), "influential": data.get("influentialCitationCount"),
            "publication_date": data.get("publicationDate")}


def hub(arxiv_id: str, get: Callable[..., object] = None) -> dict:
    """HF 논문 페이지와 연결 모델. 논문 페이지가 없으면(새 논문은 대부분 없다) 모델 검색을 하지 않는다."""
    get = get or _get_json
    try:
        page = get(f"https://huggingface.co/api/papers/{arxiv_id}")
    except Exception as error:  # noqa: BLE001
        return {"error": type(error).__name__}
    if not isinstance(page, dict) or page.get("error"):
        return {"page": False}
    out = {"page": True, "upvotes": page.get("upvotes"), "github_repo": page.get("githubRepo"),
           "github_stars": page.get("githubStars"), "n_models": page.get("numTotalModels") or 0,
           "n_datasets": page.get("numTotalDatasets") or 0, "n_spaces": page.get("numTotalSpaces") or 0, "models": []}
    if out["n_models"]:
        try:
            models = get(f"https://huggingface.co/api/models?filter=arxiv:{arxiv_id}&sort=downloads&direction=-1&limit=3"
                         "&expand%5B%5D=downloads&expand%5B%5D=likes")
        except Exception as error:  # noqa: BLE001 — 실패를 "없음"으로 바꾸지 않는다(Codex 검토 2026-09-30)
            out["models_error"] = type(error).__name__
            models = None
        out["models"] = [{"id": m.get("id"), "downloads_30d": m.get("downloads"), "likes": m.get("likes")}
                         for m in (models or []) if isinstance(m, dict)]
    return out


_GH_REPO_RE = re.compile(r"^https://github\.com/([\w.-]+/[\w.-]+?)(?:\.git)?/?$")


def github(repo_url: str, get: Callable[..., object] = None) -> dict:
    """공식 저장소의 별·포크·마지막 push. 비인증 REST(시간당 60회) — 하루 주장 논문이 1~2편이라 넉넉하다."""
    get = get or _get_json
    m = _GH_REPO_RE.match(repo_url or "")
    if not m:
        return {}
    try:
        data = get(f"https://api.github.com/repos/{m.group(1)}", {"Accept": "application/vnd.github+json"})
    except Exception as error:  # noqa: BLE001
        return {"repo": m.group(1), "error": type(error).__name__}
    if not isinstance(data, dict):
        return {"repo": m.group(1), "missing": True}
    return {"repo": m.group(1), "stars": data.get("stargazers_count"), "forks": data.get("forks_count"),
            "pushed_at": data.get("pushed_at")}




def _sum_known(values) -> int | None:
    """관측된 값만 더한다. 하나도 없으면 None — 미관측을 0 으로 적지 않는다."""
    known = [v for v in values if isinstance(v, int)]
    return sum(known) if known else None


# ---------------------------------------------------------------- 날짜별 스냅숏 — 별·인용·다운로드는 계속 바뀐다

def _ddl(con: sqlite3.Connection) -> None:
    """이 모듈의 스키마. schema_guard 를 통해서만 돈다."""
    con.execute("CREATE TABLE IF NOT EXISTS adoption_snapshots ("
                " arxiv_id TEXT NOT NULL, collected_on TEXT NOT NULL, collected_at TEXT NOT NULL,"
                " citation_count INTEGER, influential_citations INTEGER, publication_date TEXT,"
                " github_repo TEXT, github_tier TEXT, github_stars INTEGER, github_forks INTEGER,"
                " hf_models INTEGER, hf_downloads_30d INTEGER, hf_likes INTEGER, hf_upvotes INTEGER,"
                " signals_json TEXT NOT NULL, PRIMARY KEY (arxiv_id, collected_on))")


def init_db(db: Path) -> None:
    import schema_guard
    schema_guard.ensure(db, _ddl, "adoption_signals")


def _cached(db: Path, arxiv_id: str, day: str) -> dict | None:
    try:
        with sqlite3.connect(db) as con:
            row = con.execute("SELECT signals_json FROM adoption_snapshots WHERE arxiv_id=? AND collected_on=?", (arxiv_id, day)).fetchone()
    except sqlite3.Error:
        return None
    return json.loads(row[0]) if row else None


def _repo_of(db: Path, arxiv_id: str) -> tuple[str, str]:
    """(저장소 URL, 단계) — 공식·저자 연관일 때만. 나머지 단계는 빈 값."""
    try:
        import code_ladder
        row = code_ladder.get(arxiv_id, db)
    except Exception:  # noqa: BLE001 — 사다리가 없는 DB 에서도 나머지 신호는 모은다
        return "", ""
    if row and row.get("tier") in ("official", "author") and row.get("url"):
        return row["url"], row["tier"]
    return "", ""


def _post_json(url: str, payload: dict, headers: dict | None = None) -> object:
    import httpx
    import api_usage
    resp = httpx.post(url, json=payload, headers=headers or {}, timeout=HTTP_TIMEOUT_S * 3, follow_redirects=False)
    api_usage.record("api.semanticscholar.org", "ok" if resp.status_code == 200 else str(resp.status_code))
    resp.raise_for_status()
    return resp.json()


def scholarly_batch(arxiv_ids: list[str], post: Callable[..., object] = None) -> dict[str, dict]:
    """S2 /paper/batch 한 번으로 여러 편의 인용. 실패하면 빈 dict — 그러면 편마다 조회로 돌아간다."""
    import http_client
    post = post or _post_json
    ids = [a for a in dict.fromkeys(arxiv_ids) if a and not a.startswith("pdf-")][:500]
    if not ids:
        return {}
    data = post("https://api.semanticscholar.org/graph/v1/paper/batch?fields=citationCount,influentialCitationCount,publicationDate",
                {"ids": [f"ARXIV:{a}" for a in ids]}, http_client.s2_headers())
    out: dict[str, dict] = {}
    for aid, item in zip(ids, data if isinstance(data, list) else []):
        out[aid] = ({"citations": item.get("citationCount"), "influential": item.get("influentialCitationCount"),
                     "publication_date": item.get("publicationDate")} if isinstance(item, dict) else {"missing": True})
    return out


def collect(db: Path, arxiv_id: str, *, get: Callable[..., object] = None, now: datetime | None = None,
            scholarly_data: dict | None = None) -> dict:
    """논문 하나의 외부 신호. 같은 날 같은 논문은 한 번만 조회한다(여러 프로필 메일에 실린다)."""
    now = now or datetime.now(timezone.utc)
    day = now.date().isoformat()
    cached = _cached(db, arxiv_id, day)
    if cached:
        return cached
    repo, tier = _repo_of(db, arxiv_id)
    sig = {"arxiv_id": arxiv_id, "collected_on": day, "scholarly": scholarly_data or scholarly(arxiv_id, get), "hub": hub(arxiv_id, get),
           "github": github(repo, get) if repo else {}, "github_tier": tier}
    try:
        init_db(db)
        s, h, g = sig["scholarly"], sig["hub"], sig["github"]
        models = h.get("models") or []
        with sqlite3.connect(db) as con:
            con.execute("INSERT OR REPLACE INTO adoption_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                arxiv_id, day, now.isoformat(timespec="seconds"), s.get("citations"), s.get("influential"), s.get("publication_date"),
                g.get("repo"), tier or None, g.get("stars"), g.get("forks"), h.get("n_models"),
                _sum_known(m.get("downloads_30d") for m in models), _sum_known(m.get("likes") for m in models), h.get("upvotes"),
                json.dumps(sig, ensure_ascii=False)))
    except Exception as error:  # noqa: BLE001 — 스냅숏을 못 남겨도 메일에는 싣는다(migrate 전 운영 DB 등)
        print(f"  [외부 신호] 스냅숏 저장 생략: {type(error).__name__}")
    return sig


# ---------------------------------------------------------------- 메일 한 줄 — 관측값만, 판정어 없음

def _days_since(pub: str | None, today: date) -> int | None:
    try:
        return (today - date.fromisoformat((pub or "")[:10])).days
    except ValueError:
        return None


def _k(n: int) -> str:
    return f"{n / 1000:.1f}k".replace(".0k", "k") if n >= 1000 else f"{n:,}"


def signals_line(sig: dict, today: date | None = None) -> str:
    """`인용 42회 · 공식 GitHub ★1.3k · HF 모델 30일 다운로드 27k`. 아무것도 관측 못 했으면 그 사실을 적는다."""
    today = today or date.today()
    s, h, g = sig.get("scholarly") or {}, sig.get("hub") or {}, sig.get("github") or {}
    parts: list[str] = []
    if s.get("error"):
        parts.append("인용 조회 실패")
    elif s.get("citations") is not None:
        parts.append(f"인용 {s['citations']:,}회")
    if g.get("repo"):
        who = "공식" if sig.get("github_tier") == "official" else "저자 연관"
        parts.append(f"{who} GitHub ★{_k(g['stars'])}" if isinstance(g.get("stars"), int) else f"{who} GitHub(별 조회 실패)")
    if h.get("error"):
        parts.append("HF 조회 실패")
    elif h.get("page"):
        models = h.get("models") or []
        dl = _sum_known(m.get("downloads_30d") for m in models)
        if h.get("models_error"):
            parts.append(f"HF 연결 모델 {h['n_models']}개(다운로드 조회 실패)")
        elif dl is not None:
            parts.append(f"HF 모델 30일 다운로드 {_k(dl)}")
        if h.get("upvotes"):
            parts.append(f"HF 추천 {h['upvotes']}")
    days = _days_since(s.get("publication_date"), today)
    observed = [p for p in parts if "실패" not in p and not p.startswith("인용 0회")]
    failed = [p for p in parts if "실패" in p]
    if not observed and not failed:
        return f"신규 논문으로 아직 관측 없음(공개 {days}일)" if days is not None and days < 90 else " · ".join(parts) or "관측 없음"
    # 조회 실패 표시는 "관측 없음" 설명으로 덮지 않는다(Codex 최종 검토: 신규 논문 분기가 HF 다운로드 조회 실패를 지웠다).
    return " · ".join(parts)
