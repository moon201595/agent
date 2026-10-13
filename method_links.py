"""⑤⑨ 비교 방법의 원 논문과 인기 지표 — 살아 있는 리더보드가 없으니 "이 분야에서 많이 쓰이는 방법" 을 인용·별·HF 로 가리킨다(2026-10-13).

사용자 결정(2026-10-13): "인용·HF·별 수 같은 걸로 SOTA 리더보드에서 못 가져오는 것을 대체한다고 하지 않았나? 그 분야의 가장 인기 있는 모델의
성능은 얼마고, 이 논문은 어떤 모델로 어떤 성능이 나왔고, 가장 인기 있는 모델과 비교하면 어떻고". 10/07 에 Claude 가 "새 논문 말고 비교 대상
(기존 강자)에 인용·코드를 붙인다" 고 제안해 놓고 그 단계를 구현하지 않았다(§244 뒤 확인). 자료원 조사(2026-10-13 실측):
- Hugging Face `api/papers/<arXiv>` 는 논문 한 편의 upvotes·공식 GitHub 저장소와 **별 수**·연결 모델 수를 준다(OpenVLA: ★7,141 · 모델 33).
  모델 카드의 `model-index` 평가 결과는 비전·로봇 모델에 거의 없다 — SOTA 자료원으로 쓰지 않는다. `api/papers/search` 는 관련도 순이라
  분야의 인기 모델을 위로 올리지 않는다(PatchCore 가 안 나왔다).
- Connected Papers 는 이메일로 받는 API 키·사용량 상한·유료 요금제다 — 추가 결제 없이 운영한다는 규칙 6 에 걸린다. 같은 자료(S2)의
  추천 API 는 최근 논문 위주라 "인기 모델" 이 아니다.
- 그래서 **이 논문 표의 비교 방법** 을 원 논문에 잇고(Semantic Scholar 인용 수 + HF 별·모델 수) 그중 가장 많이 인용된 방법과 견준다.

잇는 방법(결정적, 이름 추측 없음): 논문 HTML 본문에서 방법 이름 바로 뒤의 인용 링크(`<cite>…#bib.bibN`)가 가리키는 참고문헌 하나 →
그 항목에 방법 이름이 그대로 있을 때만(저자가 그 방법의 원 논문으로 인용한 것) → 항목의 arXiv 주소, 없으면 S2 제목 일치(유사도 0.9 이상).
이름만 같은 다른 논문(10/07 실측: π0-FAST 를 FAST 토크나이저 논문으로)에 잇지 않는다. 못 이으면 인기 칸이 빈다.
"""
from __future__ import annotations

import difflib
import html as html_lib
import json
import re
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

CACHE_PATH = Path(__file__).parent / "data" / "cache" / "method_links.json"
CACHE_DAYS = 7                 # 인용·별은 하루 단위로 크게 안 변한다 — 같은 방법을 매일 다시 묻지 않는다(S2 초당 1회 한도)
RETRY_DAYS = 1                 # 못 찾음·일부 조회 실패는 다음 날 다시 묻는다 — 일시 실패를 7일 동안 빈칸으로 두지 않는다(Codex 검토 P2)
MAX_METHODS_PER_ROW = 15       # 한 결과(벤치마크·지표)에서 잇는 비교 방법 상한 — 성능 순 앞 몇 개로 자르면 유명한 하위 방법을 놓친다(Codex 검토 P2)
MAX_NAME_HITS = 200            # 이름 한 개가 본문에 나오는 자리 중 보는 수 — 비신뢰 HTML 의 반복 입력에서 시간 상한
TITLE_MATCH = 0.95

# 닫히지 않은 태그가 반복되면 비탐욕 `.*?` 가 매 시작점에서 끝까지 훑는다(제곱 시간, Codex 검토 P2) — 안쪽 길이를 묶는다.
_CITE_RE = re.compile(r"<cite\b[^>]{0,500}>(.{0,3000}?)</cite>", re.S)
_BIB_RE = re.compile(r'<li\b[^>]{0,300}\bid="(bib\.bib\d+)"[^>]{0,300}class="[^"]{0,200}ltx_bibitem[^"]{0,200}"[^>]{0,300}>(.{0,20000}?)</li>', re.S)
_BLOCK_RE = re.compile(r'<span\b[^>]{0,300}class="[^"]{0,200}ltx_bibblock[^"]{0,200}"[^>]{0,300}>(.{0,5000}?)</span>(?=\s*(?:<span\b[^>]{0,300}ltx_bibblock|$))', re.S)
_ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})|arXiv[:\s]+(\d{4}\.\d{4,5})", re.I)
_VERSION_RE = re.compile(r"^(?:v?\d+(?:\.\d+)?|[0-9]+[a-z]?)$")


def _text(fragment: str) -> str:
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def _norm(text: str) -> str:
    """대소문자·하이픈·공백·위첨자 차이를 지운 비교용 글자열 — "MAS<sup>2</sup>" 와 "MAS2", "Mini-SWE" 와 "mini swe"."""
    import unicodedata
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", text or "").lower())     # NFKC: 참고문헌의 "MAS²" 를 "MAS2" 로


def _title_tokens(title: str) -> list[str]:
    """제목 낱말 — 하이픈으로 붙은 것은 한 낱말("GPT-4" → gpt4, "Skill-MAS" → skillmas), 공백으로 떨어진 것은 따로("MAS 2" → mas, 2)."""
    import unicodedata
    plain = unicodedata.normalize("NFKC", title or "").lower()
    return [re.sub(r"[\W_]+", "", t) for t in re.findall(r"[^\W_]+(?:[-‐–][^\W_]+)*", plain)]


def name_in_title(name: str, title: str) -> bool:
    """방법 이름이 참고문헌 **제목**에 낱말 경계로 있는가. 이어진 낱말 몇 개를 붙인 것이 이름과 같아야 하고, 그 바로 뒤가 숫자 낱말이면 다른 판이다
    ("SAM" 과 "SAM 2", "GPT" 와 "GPT-4" 는 다른 모델). 부분 문자열("base"↔"baseline", "DINO"↔"DINOv2")은 받지 않는다(Codex 검토 P2)."""
    want, toks = _norm(name), _title_tokens(title)
    if len(want) < 2:
        return False
    for i in range(len(toks)):
        joined = ""
        for j in range(i, min(len(toks), i + 4)):
            joined += toks[j]
            if joined == want:
                return not (j + 1 < len(toks) and _VERSION_RE.match(toks[j + 1]))
            if len(joined) >= len(want):
                break
    return False


def bibliography(html: str) -> dict[str, dict]:
    """{bib id: {text, title, arxiv}}. 제목은 LaTeXML 의 둘째 bibblock(첫째는 저자)."""
    out: dict[str, dict] = {}
    for m in _BIB_RE.finditer(html or ""):
        inner = m.group(2)
        blocks = [_text(b) for b in _BLOCK_RE.findall(inner)]
        text = _text(inner)
        ax = _ARXIV_RE.search(inner) or _ARXIV_RE.search(text)
        title = blocks[1].strip(" .") if len(blocks) > 1 else ""
        out[m.group(1)] = {"text": text, "title": title, "arxiv": (ax.group(1) or ax.group(2)) if ax else ""}
    return out


def _marked_body(html: str) -> str:
    """인용을 ⟦bib.bibN,…⟧ 표시로 바꾸고 나머지 태그를 지운 본문."""
    body = _CITE_RE.sub(lambda m: " ⟦" + ",".join(re.findall(r"#(bib\.bib\d+)", m.group(1))) + "⟧ ", html or "")
    return " ".join(html_lib.unescape(re.sub(r"<[^>]{0,2000}>", " ", body)).split())


def _name_pattern(name: str) -> re.Pattern | None:
    parts = [p for p in re.findall(r"[^\W\d_]+|\d+", name or "") if p]
    if not parts or len(_norm(name)) < 2:
        return None
    return re.compile(r"(?<![\w-])" + r"[\s\-_]*".join(re.escape(p) for p in parts) + r"(?![\w-])")


def cited_bib(body: str, name: str) -> str:
    """이름 **바로 뒤**(공백·여는 괄호만 사이에 허용)의 단독 인용이 본문 전체에서 한 참고문헌으로만 정해질 때 그 id. 여럿이면 빈 문자열.
    사이에 다른 낱말이 끼면 받지 않는다 — "Base and FooNet (cite)" 에서 Base 가 FooNet 의 인용을 집었다(Codex 검토 P2)."""
    pat = _name_pattern(name)
    if pat is None:
        return ""
    ids: set[str] = set()
    for n, m in enumerate(pat.finditer(body)):
        if n >= MAX_NAME_HITS:
            break
        tail = re.match(r"[\s(\[~]{0,4}⟦([^⟧]{0,500})⟧", body[m.end():m.end() + 520])   # 짧은 구간만 자른다(제곱 시간 방지)
        if tail:
            found = [x for x in tail.group(1).split(",") if x]
            if len(found) == 1:
                ids.add(found[0])
    return next(iter(ids)) if len(ids) == 1 else ""


def link_methods(html: str, names: list[str]) -> dict[str, dict]:
    """{방법 이름: 참고문헌 항목}. 참고문헌 **제목**에 그 이름이 낱말 경계로 있어야 한다 — 저자가 그 방법의 원 논문으로 인용했다는 근거."""
    bibs = bibliography(html)
    if not bibs:
        return {}
    body = _marked_body(html)
    out: dict[str, dict] = {}
    for name in dict.fromkeys(n for n in names if n):
        bid = cited_bib(body, name)
        entry = bibs.get(bid)
        if entry and entry["title"] and name_in_title(name, entry["title"]):
            out[name] = {**entry, "bib": bid}
    return out


# ---------------------------------------------------------------- 인기 지표(캐시)

def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(path: Path, updates: dict) -> None:
    """디스크의 최신 캐시에 이번 갱신분만 합쳐 고유 임시 파일로 바꿔 쓴다 — 같은 `.tmp` 경쟁·갱신 유실을 줄인다(Codex 검토 P3)."""
    import os
    import tempfile
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        merged = {**_load(path), **updates}
        fd, tmp = tempfile.mkstemp(prefix=".method_links.", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(merged, stream, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except OSError:
        pass


def _versions(title: str) -> set[str]:
    return {t for t in _title_tokens(title) if _VERSION_RE.match(t)}


def _resolve(entry: dict, get: Callable[..., object]) -> dict:
    """참고문헌 항목 → {arxiv, citations, s2_title, found}. 받지 않는 경우(Codex 검토 P2):
    arXiv 직접 조회는 돌려받은 arXiv id 가 같고 제목이 크게 다르지 않을 때만, 제목 검색은 정규화 제목이 **정확히 같은 후보 하나**
    또는 유사도 0.95 이상 후보가 하나뿐이고 판 표시(v1·2 …)가 같을 때만 — 여럿이면 고르지 않는다."""
    import http_client
    fields = "title,citationCount,externalIds"
    bib_title = entry.get("title") or ""
    item = None
    if entry.get("arxiv"):
        data = get(f"https://api.semanticscholar.org/graph/v1/paper/ARXIV:{entry['arxiv']}?fields={fields}", http_client.s2_headers())
        if isinstance(data, dict) and (data.get("externalIds") or {}).get("ArXiv") == entry["arxiv"] and (
                not bib_title or difflib.SequenceMatcher(None, _norm(data.get("title")), _norm(bib_title)).ratio() >= 0.8):
            item = data
    elif bib_title:
        data = get("https://api.semanticscholar.org/graph/v1/paper/search/match?query=" + urllib.parse.quote(bib_title[:300])
                   + f"&fields={fields}", http_client.s2_headers())
        cands = [x for x in ((data or {}).get("data") or []) if isinstance(x, dict)] if isinstance(data, dict) else []
        exact = [x for x in cands if _norm(x.get("title")) == _norm(bib_title)]
        close = [x for x in cands if difflib.SequenceMatcher(None, _norm(x.get("title")), _norm(bib_title)).ratio() >= TITLE_MATCH
                 and _versions(x.get("title") or "") == _versions(bib_title)]
        item = exact[0] if len(exact) == 1 else close[0] if not exact and len(close) == 1 else None
    if item is None:
        return {"arxiv": entry.get("arxiv") or "", "citations": None, "found": False}
    return {"arxiv": entry.get("arxiv") or (item.get("externalIds") or {}).get("ArXiv") or "", "citations": item.get("citationCount"),
            "s2_title": item.get("title") or "", "found": True}


def popularity(entry: dict, *, get: Callable[..., object] | None = None) -> dict:
    """{arxiv, title, citations, github_repo, github_stars, hf_models, complete}. 조회 실패한 칸은 None(0 으로 적지 않는다).
    complete=False(못 찾음·HF 실패)면 캐시를 하루만 믿는다."""
    import adoption_signals
    get = get or adoption_signals._get_json
    found = _resolve(entry, get)
    hub = adoption_signals.hub(found["arxiv"], get, models=False) if found.get("arxiv") else {}
    return {"arxiv": found.get("arxiv") or "", "title": found.get("s2_title") or entry.get("title") or "",
            "citations": found.get("citations"), "github_repo": hub.get("github_repo"), "github_stars": hub.get("github_stars"),
            "hf_models": hub.get("n_models"), "complete": bool(found.get("found")) and "error" not in hub}


def _key(entry: dict) -> str:
    """캐시 키 — arXiv 가 있으면 그것, 없으면 참고문헌 전체 글(저자·연도 포함, 낱말 경계 보존). 제목만 붙여 쓰면 "AB: C" 와 "A: BC" 가 겹친다(Codex 검토 P3)."""
    if entry.get("arxiv"):
        return "arxiv:" + entry["arxiv"]
    return "bib:" + " ".join(_title_tokens(entry.get("text") or ""))[:300]


def annotate(html: str, rows: list[dict], *, get: Callable[..., object] | None = None, now: datetime | None = None,
             deadline: float | None = None, clock: Callable[[], float] = time.monotonic, cache_path: Path | None = None) -> None:
    """결과 행마다 `peers`(같은 표·열·구획의 비교 방법 전체)에 원 논문 인기 지표를 붙여 `r["popularity"] = {방법: {...}}`,
    `r["popularity_checked"] = 인용 수를 확인한 방법 수` 로 둔다. 예외를 올리지 않는다 — 부가 정보다. deadline(monotonic)을 넘기면 남은 방법은 잇지 않는다."""
    import adoption_signals
    now = now or datetime.now(timezone.utc)
    path = cache_path or CACHE_PATH
    cache = _load(path)
    updates: dict[str, dict] = {}
    saved_deadline = adoption_signals._DEADLINE
    try:
        if deadline is not None and clock() >= deadline:
            return
        names = [p["model"] for r in rows for p in (r.get("peers") or [])[:MAX_METHODS_PER_ROW]]
        links = link_methods(html, names)
        for r in rows:
            pop: dict[str, dict] = {}
            for peer in (r.get("peers") or [])[:MAX_METHODS_PER_ROW]:
                entry = links.get(peer["model"])
                if not entry:
                    continue
                key = _key(entry)
                hit = updates.get(key) or cache.get(key)
                days = CACHE_DAYS if (hit or {}).get("complete") else RETRY_DAYS
                fresh = hit and hit.get("checked_on", "") > (now.date() - timedelta(days=days)).isoformat()
                if not fresh:
                    if deadline is not None and clock() >= deadline:
                        if hit:
                            pop[peer["model"]] = hit                 # 오래된 값이라도 있으면 쓴다 — 빈칸보다 낫다
                        continue
                    adoption_signals._DEADLINE = time.monotonic() + max(0.0, deadline - clock()) if deadline is not None else saved_deadline
                    try:
                        new = {**popularity(entry, get=get), "checked_on": now.date().isoformat()}
                    except Exception:  # noqa: BLE001 — 한 방법의 조회 실패가 나머지를 막지 않는다
                        if hit:
                            pop[peer["model"]] = hit
                        continue
                    finally:
                        adoption_signals._DEADLINE = saved_deadline
                    # 이번에 못 가져온 칸은 예전 정상 값으로 채운다 — 일시 실패가 좋은 값을 빈칸으로 덮지 않게.
                    for field in ("arxiv", "citations", "github_repo", "github_stars", "hf_models"):
                        if new.get(field) in (None, "") and hit and hit.get(field) not in (None, ""):
                            new[field] = hit[field]
                    hit = updates[key] = new
                pop[peer["model"]] = hit
            if pop:
                r["popularity"] = pop
                r["popularity_checked"] = sum(isinstance(p.get("citations"), int) for p in pop.values())
                r["popularity_total"] = len((r.get("peers") or [])[:MAX_METHODS_PER_ROW])
    except Exception:  # noqa: BLE001
        pass
    finally:
        adoption_signals._DEADLINE = saved_deadline
        if updates:
            _save(path, updates)


def most_cited(r: dict) -> tuple[dict, dict] | None:
    """(비교 방법 행, 인기 지표) — 인용 수가 확인된 것 중 가장 많은 것. 동률이면 표 순서대로 앞의 것."""
    pop = r.get("popularity") or {}
    best = None
    for peer in r.get("peers") or []:
        p = pop.get(peer["model"])
        c = (p or {}).get("citations")
        if isinstance(c, int) and not isinstance(c, bool) and (best is None or c > best[1]["citations"]):
            best = (peer, p)
    return best


def label(p: dict | None) -> str:
    """표의 출처·조건 칸 — "인용 12,411회 · ★7.1k · HF 모델 33". 모르는 칸은 빼고, 아무것도 모르면 빈 문자열."""
    if not p:
        return ""
    parts = []
    if isinstance(p.get("citations"), int):
        parts.append(f"인용 {p['citations']:,}회")
    stars = p.get("github_stars")
    if isinstance(stars, int) and stars > 0:
        parts.append(f"★{stars / 1000:.1f}k" if stars >= 1000 else f"★{stars}")
    if isinstance(p.get("hf_models"), int) and p["hf_models"] > 0:
        parts.append(f"HF 모델 {p['hf_models']}")
    return " · ".join(parts)
