"""② 주간 외부 정찰(External Scout) — 우리 검색 소스 **밖**의 중요한 연구를 찾아, 에이전트가 무엇을 왜 놓쳤는지 잰다(2026-09-30).

흐름(사용자 결정 2026-09-30, 무인):
  GPT 정찰(Codex, 웹검색 켬, 4개 프로필 한 번) → Python 정규화(신원·중복·링크·당시 스캔 대조) → Claude 검증(도구 없음, 한 번)
  → 검증된 사실만 주간 관리 브리프에 `E` 근거로 → 기존 Claude 제안·**GPT(Codex) 최종 판정** → `agent_maintenance.validate` → 적용.

역할을 나누는 이유:
- `term_discovery` 는 **검색 안쪽**의 사각지대(가져왔지만 핵심어에 안 걸린 논문)를 본다. 이 모듈은 **검색 바깥**(학회·저널·리더보드·HF·GitHub)을 본다.
- 정찰 모델의 **의견은 넘기지 않는다** — 읽을 우선순위·점수·추천 같은 필드는 스키마에 없고, 있어도 Python 이 버린다. 넘기는 것은 신원·출처·
  주장(검증 대상)뿐이다. 같은 계열 모델(GPT)이 최종 판정을 하므로, 제 의견을 제 근거로 다시 읽는 순환을 막는다.
- 주간 관리가 키워드를 검증할 때 쓰는 글은 **정찰이 쓴 문장이 아니라** Python 이 S2 에서 받은 공식 제목·초록이다 — 정찰이 용어를 끼워 넣지 못한다.
- "놓쳤다"는 **발견 시점 직전 스캔의 관측**으로 판정한다(시점 누수 방지). 그 뒤에 나온 논문은 잡을 기회가 없었으므로 `not_yet_evaluable`.

호출: 정찰 1회 + 검증 1회 / 주. Gemini 는 쓰지 않는다(일일 요약 몫). 무엇이 실패해도 주간 관리·일일 메일은 그대로 돈다(규칙 6).
"""
from __future__ import annotations

import lang_guard

import json
import re
import shutil
import sqlite3
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

SCOUT_TIMEOUT_S = 480            # 웹검색 4개 프로필 한 번. 실측 전 설정값 — 넘으면 그 주 외부 근거 0건
VERIFY_TIMEOUT_S = 300
PERF_BUDGET_S = 60.0             # 벤치마크 주장 표 검증(arXiv HTML) 전체 상한
MAX_PER_PROFILE = 5              # 입력 품질 상한(프로필 변경 개수 상한이 아니다)
MAX_VERIFY = 20                  # 논문 정찰 항목 검증 상한(4프로필 × 5) — 한 번의 Claude 호출
MAX_VERIFY_AXES = 24             # 연구축 근거로만 들어온 항목의 검증 상한(4프로필 × 연구축 2 × 근거 3) — 따로 한 번 더 부른다(2026-10-08)
MAX_VERIFY_CHARS = 120_000      # 직렬화한 검증 입력 상한이다. 300초 내 완료 보장은 실측 전이다.
MAX_AXES = 2                     # 프로필당 떠오르는 연구축 상한
AXIS_MIN_PAPERS = 2              # 연구축 하나를 받으려면 그 용어가 공식 제목·초록에 그대로 있는 서로 다른 근거 논문 2편 이상
AXIS_MAX_PAPERS = 3
AXIS_WINDOW_DAYS = 28            # "요즘 뜨는가" 를 우리 후보 안에서 셀 때 최근 창과 직전 창의 길이
CLIP = {"title": 300, "venue": 120, "contribution": 400, "change_from_prior": 400, "manufacturing_use": 300, "published": 20}
SOURCE_TYPES = ("conference", "journal", "arxiv", "openreview", "report", "leaderboard", "repository", "model_hub", "other")
MISSED_STAGES = ("not_retrieved", "no_core_hit", "ranked_out")
# not_delivered: 선정은 됐지만 발송 기록이 없다(본문 처리 탈락·발송 실패) — 프로필이 놓친 것이 아니므로 키워드 근거(MISSED_STAGES)가 아니다.
# before_monitoring: 그 프로필의 첫 스캔보다 먼저 나온 논문 — 잡을 기회가 없었으므로 놓친 것이 아니다(2026-10-08, 연구축 근거는 6개월 전까지 본다).
STAGES = ("not_yet_evaluable", "before_monitoring", "not_retrieved", "no_core_hit", "excluded", "ranked_out", "not_delivered", "already_captured")

# 정찰 출력에서 남기는 필드(화이트리스트). 이 밖(read_priority·score·recommendation·must_read …)은 전부 버린다.
_ITEM_FIELDS = ("title", "arxiv_id", "doi", "url", "source_type", "venue", "published", "contribution", "change_from_prior",
                "manufacturing_use", "code_url", "hf_url", "benchmark", "evidence_urls")
_ARXIV_RE = re.compile(r"(\d{4}\.\d{4,5})(?:v\d+)?")
_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")


# ── 저장 ────────────────────────────────────────────────────────────────────
def _ddl(con: sqlite3.Connection) -> None:
    """이 모듈의 스키마. schema_guard 를 통해서만 돈다. `api_usage` 는 메모리 계측이라 주간 호출 수·상태는 여기 영속 기록한다."""
    con.execute("CREATE TABLE IF NOT EXISTS external_scout_runs ("
                " run_id TEXT PRIMARY KEY, week TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,"
                " status TEXT NOT NULL, scout_calls INTEGER NOT NULL DEFAULT 0, verify_calls INTEGER NOT NULL DEFAULT 0,"
                " candidate_count INTEGER, identified_count INTEGER, verified_count INTEGER, capture_json TEXT, error TEXT)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_external_scout_runs_week ON external_scout_runs(week)")
    con.execute("CREATE TABLE IF NOT EXISTS external_observations ("
                " observation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, profile_id TEXT NOT NULL, paper_key TEXT,"
                " title TEXT NOT NULL, source_type TEXT, source_url TEXT, published_at TEXT, gap_stage TEXT NOT NULL,"
                " last_eligible_scan_id TEXT, evidence_json TEXT NOT NULL, verified_json TEXT, discovered_at TEXT NOT NULL)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_external_observations_profile ON external_observations(profile_id, discovered_at)")


def init_db(db: Path) -> None:
    import schema_guard
    schema_guard.ensure(db, _ddl, "external_scout")


def week_of(now: datetime) -> str:
    import agent_maintenance
    return agent_maintenance.week_of(now)


# ── ① 정찰(GPT = 헤드리스 Codex, 웹검색만) ─────────────────────────────────────
def scout_prompt(profiles: list[dict], now: datetime) -> str:
    """공개 정보만 넣는다 — 프로필 이름·핵심 키워드·대상 분야(관심 키워드는 LLM 입력 허용 목록, AGENTS.md). 반응 원문·수신자는 넣지 않는다."""
    since = (now - timedelta(days=60)).date().isoformat()
    axis_since = (now - timedelta(days=183)).date().isoformat()
    lines = []
    for p in profiles:
        # 핵심 키워드는 "이미 보고 있는 목록"이다 — 연구축 탐색은 이 목록 **밖**을 찾게 한다(2026-10-08 사용자 결정: 우리 키워드로만 물으면
        # 키워드 안의 논문을 다시 찾을 뿐 새 연구축을 못 찾는다).
        lines.append(f"- profile_id={p['id']} | name={p['name']} | already covered keywords: {', '.join(p['core'])}"
                     + (f" | target domain: {', '.join(p['domain'][:10])}" if p["domain"] else ""))
    template = (Path(__file__).resolve().parent / "prompts" / "external_scout_v1.md").read_text(encoding="utf-8")
    return template.format(max_per_profile=MAX_PER_PROFILE, since=since, source_types=", ".join(SOURCE_TYPES),
                           max_axes=MAX_AXES, axis_since=axis_since, axis_min=AXIS_MIN_PAPERS,
                           axis_max=AXIS_MAX_PAPERS) + "\n" + "\n".join(lines)


def _run_codex_scout(prompt: str, timeout: float) -> str:
    """동향 서술·외부 성능 비교와 같은 격리 — 셸·앱·브라우저·MCP 끄고 웹검색만."""
    import agent_maintenance as am
    exe = shutil.which("codex", path=am._cli_env()["PATH"])
    if not exe:
        raise am.StepError("codex_missing")
    with tempfile.TemporaryDirectory(prefix="scout-codex-") as cwd:
        last = Path(cwd) / "last.txt"
        argv = [exe, "exec", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config", "--ignore-rules", "-s", "read-only"]
        for feature in ("shell_tool", "apps", "browser_use", "computer_use", "in_app_browser", "image_generation", "tool_suggest"):
            argv += ["--disable", feature]
        argv += ["-c", "mcp_servers={}", "-c", 'web_search="live"', "-o", str(last), "-"]
        rc, _out, _err = am._run(argv, prompt, int(timeout), cwd)
        if rc != 0:
            raise am.StepError("codex_exit")
        return last.read_text(encoding="utf-8")


def _https(url: object) -> str | None:
    """정찰이 준 URL — 메일 링크와 같은 허용 목록 검사(`link_policy.safe_link`). 못 통과하면 버린다(항목 신원은 arXiv·DOI 로 남을 수 있다)."""
    import link_policy
    return link_policy.safe_link(url) if isinstance(url, str) else None


def sanitize(raw: str, profile_ids: list[str]) -> dict[str, list[dict]]:
    """정찰 출력 → 화이트리스트 필드만, 길이 자르고, 식별자 정규화. 묻지 않은 프로필·형식 틀린 항목은 버린다. JSON 이 아니면 ValueError."""
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        raise ValueError("JSON 없음")
    data = json.loads(m.group(0))
    profiles = data.get("profiles") if isinstance(data, dict) else None
    if not isinstance(profiles, dict):
        raise ValueError("profiles 없음")
    out: dict[str, list[dict]] = {}
    for pid in profile_ids:
        items = profiles.get(pid)
        if not isinstance(items, list):
            items = []
        out[pid] = [c for c in (_clean_item(it) for it in items[:MAX_PER_PROFILE]) if c]
    return out


def _clean_item(it: object) -> dict | None:
    """정찰 항목 하나 → 화이트리스트 필드만, 길이 자르고 식별자 정규화. 제목이 없으면 None."""
    if not isinstance(it, dict):
        return None
    c = {k: it.get(k) for k in _ITEM_FIELDS}                 # 의견 필드는 여기서 사라진다
    for k, n in CLIP.items():
        c[k] = " ".join(str(c[k]).split())[:n] if isinstance(c.get(k), str) else None
    if not c["title"]:
        return None
    aid_m = _ARXIV_RE.search(str(it.get("arxiv_id") or ""))
    if not aid_m and "arxiv.org/" in str(it.get("url") or ""):
        aid_m = _ARXIV_RE.search(str(it.get("url")))
    c["arxiv_id"] = aid_m.group(1) if aid_m else None
    doi = str(it.get("doi") or "").strip().lower().removeprefix("https://doi.org/").removeprefix("doi:")
    c["doi"] = doi if _DOI_RE.match(doi) else None
    c["source_type"] = c["source_type"] if c.get("source_type") in SOURCE_TYPES else "other"
    c["url"] = _https(it.get("url"))
    c["code_url"] = _https(it.get("code_url"))
    c["hf_url"] = _https(it.get("hf_url"))
    urls = it.get("evidence_urls")
    c["evidence_urls"] = [u for u in (_https(x) for x in (urls[:3] if isinstance(urls, list) else [])) if u]
    b = it.get("benchmark")
    c["benchmark"] = ({"name": str(b.get("name"))[:80], "metric": str(b.get("metric"))[:60], "value": str(b.get("value"))[:30]}
                      if isinstance(b, dict) and b.get("name") and b.get("metric") and b.get("value") is not None else None)
    return c


def _covered(term: str, keywords: list[str]) -> bool:
    """연구축 용어가 이미 보고 있는 키워드와 겹치는가 — 채점기와 같은 정규화(`agent_maintenance._norm_term`)로 어느 쪽이든 포함되면 겹친다."""
    import agent_maintenance as am
    t = f" {am._norm_term(term)} "
    return any((k := f" {am._norm_term(kw)} ").strip() and (k in t or t in k) for kw in keywords)


def sanitize_axes(raw: str, profiles: list[dict]) -> dict[str, list[dict]]:
    """정찰이 준 "떠오르는 연구축"(2026-10-08) → 프로필별 [{term, summary, papers}]. Python 이 먼저 거른다:
    이미 보는 키워드와 겹치거나, 우산어·상투 구절이거나, 너무 길거나, 근거 논문이 2편 미만이면 버린다. JSON 이 아니면 빈 dict —
    연구축이 실패해도 논문 정찰은 그대로 쓴다. 요약은 한국어만 통과한다(사람에게 보이는 LLM 출력)."""
    import agent_maintenance as am
    import term_hygiene
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        axes = json.loads(m.group(0)).get("axes") if m else None
    except (ValueError, AttributeError):
        return {}
    if not isinstance(axes, dict):
        return {}
    out: dict[str, list[dict]] = {}
    for p in profiles:
        clean: list[dict] = []
        for ax in (axes.get(p["id"]) or [])[:MAX_AXES] if isinstance(axes.get(p["id"]), list) else []:
            if not isinstance(ax, dict) or not isinstance(ax.get("term"), str):
                continue
            term = " ".join(ax["term"].split())
            words = term.lower().split()
            if (not (2 <= len(words) <= 5) or len(term) > am.MAX_TERM_CHARS or term_hygiene.is_umbrella(term)
                    or term_hygiene.reject_reason(words) or _covered(term, p["core"] + p.get("exclude", []))):
                continue
            summary = " ".join(str(ax.get("summary_ko") or "").split())[:80]
            try:
                lang_guard.require_korean(summary, term, "외부 정찰 연구축 요약")
                if not re.search(r"[가-힣]", summary):
                    raise lang_guard.NonKoreanOutput("한글 없음")   # 영어 요약은 한자 검사를 통과한다 — 한국어 칸이라 한글을 요구한다
            except lang_guard.NonKoreanOutput:
                summary = ""
            raw_papers = ax.get("papers")
            if not isinstance(raw_papers, list):
                continue
            papers = [c for c in (_clean_item(x) for x in raw_papers[:AXIS_MAX_PAPERS]) if c]
            if len(papers) >= AXIS_MIN_PAPERS:
                clean.append({"term": term, "summary": summary, "papers": papers})
        out[p["id"]] = clean
    return out


# ── ② 정규화: 신원(S2 공식 제목·초록), 중복, 당시 스캔 대조 ─────────────────────────
def _norm_title(t: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (t or "").lower()))


def title_match(a: str, b: str) -> bool:
    """정찰이 적은 제목과 S2 의 공식 제목이 같은 논문인가 — 낱말 겹침(Jaccard) 0.8 이상. 같은 ID 에 엉뚱한 제목을 붙인 환각을 거른다."""
    x, y = set(_norm_title(a).split()), set(_norm_title(b).split())
    return bool(x and y) and len(x & y) / len(x | y) >= 0.8


def _official_from(row: dict, it: dict, source: str) -> dict:
    ext = row.get("externalIds") or {}
    return {"title": str(row["title"])[:300], "abstract": str(row.get("abstract") or "")[:3000],
            "published": (row.get("publicationDate") or row.get("published") or "")[:10], "venue": str(row.get("venue") or "")[:120],
            "arxiv_id": ext.get("ArXiv") or row.get("arxiv_id") or it.get("arxiv_id"),
            "doi": (ext.get("DOI") or it.get("doi") or "").lower() or None, "source": source}


def identify(items: list[dict], s2_batch: Callable[[list[str]], list],
             arxiv_batch: Callable[[list[str]], dict] | None = None) -> list[str]:
    """신원 확인. S2 batch 한 번 → 거기서 못 맞춘 arXiv 논문은 **arXiv API 로 원 출처에서** 다시 본다. 공식 제목이 정찰 제목과 맞으면
    `official`(제목·초록·날짜·venue)을 붙이고 `identity: verified`. 못 맞추면 unverified — 주간 관리 근거로 쓰지 않는다
    (신원이 확인 안 된 논문으로 키워드를 못 바꾼다). 돌려주는 값은 조회 실패 사유 목록(비면 정상).

    실측(2026-09-30): 첫 실전에서 S2 batch 가 429 로 떨어져 17편 전부 unverified 가 됐다 — 한 번 기다려 다시 부르고, 그래도 안 되면 arXiv 로."""
    errors: list[str] = []
    for it in items:
        it["identity"] = "unverified"
    ids = list(dict.fromkeys(f"ARXIV:{it['arxiv_id']}" if it.get("arxiv_id") else f"DOI:{it['doi']}"
                             for it in items if it.get("arxiv_id") or it.get("doi")))
    got: dict = {}
    if ids:
        for attempt in range(2):
            try:
                rows = s2_batch(ids)
                got = dict(zip(ids, rows if isinstance(rows, list) else []))
                break
            except Exception as error:  # noqa: BLE001
                if attempt == 0 and "429" in str(error):
                    time.sleep(5)
                    continue
                errors.append(f"s2:{type(error).__name__}")
                break
    for it in items:
        key = f"ARXIV:{it['arxiv_id']}" if it.get("arxiv_id") else (f"DOI:{it['doi']}" if it.get("doi") else None)
        row = got.get(key)
        if isinstance(row, dict) and row.get("title") and title_match(it["title"], row["title"]):
            it["official"], it["identity"] = _official_from(row, it, "s2"), "verified"
    rest = [it["arxiv_id"] for it in items if it["identity"] != "verified" and it.get("arxiv_id")]
    if rest and arxiv_batch is not None:
        try:
            meta = arxiv_batch(list(dict.fromkeys(rest)))
        except Exception as error:  # noqa: BLE001
            errors.append(f"arxiv:{type(error).__name__}")
            meta = {}
        for it in items:
            row = meta.get(it.get("arxiv_id") or "")
            if it["identity"] != "verified" and row and row.get("title") and title_match(it["title"], row["title"]):
                it["official"], it["identity"] = _official_from(row, it, "arxiv"), "verified"
    return errors


def _arxiv_batch(ids: list[str]) -> dict[str, dict]:
    """arXiv API(원 출처) — {id: {title, abstract, published}}. 버전 없는 id 로 키를 맞춘다."""
    import httpx
    import http_client
    resp = httpx.get(http_client.ARXIV_API, params={"id_list": ",".join(ids), "max_results": len(ids)}, timeout=30,
                     headers={"User-Agent": "paper-harness/1.0 (external scout identity check)"})
    resp.raise_for_status()
    return {_ARXIV_RE.search(p["arxiv_id"]).group(1): {"title": p["title"], "abstract": p["abstract"], "published": p["published"]}
            for p in http_client.parse_arxiv_feed(resp.text) if _ARXIV_RE.search(p.get("arxiv_id") or "")}


def _s2_batch(ids: list[str]) -> list:
    import adoption_signals
    import http_client
    return adoption_signals._post_json(
        "https://api.semanticscholar.org/graph/v1/paper/batch?fields=title,abstract,publicationDate,venue,externalIds",
        {"ids": ids}, http_client.s2_headers())


def paper_keys(it: dict) -> list[str]:
    o = it.get("official") or {}
    keys = []
    if o.get("arxiv_id") or it.get("arxiv_id"):
        keys.append(str(o.get("arxiv_id") or it.get("arxiv_id")))
    if o.get("doi") or it.get("doi"):
        keys.append(f"doi:{o.get('doi') or it.get('doi')}")
    return keys


def _identity_aliases(it: dict) -> set[str]:
    """공식 신원의 별칭을 함께 보아 arXiv·DOI 를 서로 다른 근거로 세지 않는다."""
    official = it.get("official") or it
    aliases = set(paper_keys(it))
    title = _norm_title(official.get("title") or "")
    if title:
        aliases.add("title:" + title)
    return aliases


def _merge_identified(items: list[dict]) -> list[dict]:
    """조회 전에는 모르는 별칭을 조회 뒤 합치고 정찰 출처·연구축은 보존한다."""
    out: list[dict] = []
    for it in items:
        if it["identity"] != "verified":
            out.append(it)
            continue
        aliases = _identity_aliases(it)
        matches = [x for x in out if x["profile_id"] == it["profile_id"] and x["identity"] == "verified"
                   and _identity_aliases(x) & aliases]
        for old in matches:
            if old["from_scout"]:
                # 축 근거의 빈 링크·other 출처로 기존 논문 정찰의 서지·주장을 덮지 않는다.
                for name in _ITEM_FIELDS:
                    it[name] = old.get(name)
            it["from_scout"] = it["from_scout"] or old["from_scout"]
            it["axes"] = list(dict.fromkeys(old["axes"] + it["axes"]))
            it["axis_summaries"] = old["axis_summaries"] | it["axis_summaries"]
            for name in ("arxiv_id", "doi"):
                it["official"][name] = it["official"].get(name) or old["official"].get(name)
            out.remove(old)
        out.append(it)
    return out


def gap_stage(db: Path, profile_id: str, it: dict, discovered_at: datetime) -> tuple[str, str | None]:
    """(단계, 판정에 쓴 스캔). **발견 시각 이전의 관측만** 본다 — 지금 DB 를 보면 뒤늦게 잡힌 논문이 "찾았다"가 된다(시점 누수).

    not_yet_evaluable: 마지막 스캔 뒤에 나온 논문(잡을 기회가 없었다) · not_retrieved: 검색 소스가 가져오지 않음 ·
    no_core_hit: 가져왔으나 핵심어에 안 걸림 · excluded: 제외어에 걸림 · ranked_out: 관련인데 자리에서 밀림 · already_captured: 메일까지 감."""
    cutoff = discovered_at.isoformat()
    with sqlite3.connect(db) as con:
        scan = con.execute("SELECT scan_id, started_at FROM scan_runs WHERE profile_id=? AND started_at<=? "
                           "ORDER BY started_at DESC LIMIT 1", (profile_id, cutoff)).fetchone()
        if not scan:
            return "not_yet_evaluable", None
        pub = ((it.get("official") or {}).get("published") or "")[:10]
        if len(pub) == 10 and pub > scan[1][:10]:
            return "not_yet_evaluable", scan[0]
        keys = paper_keys(it)
        rows = []
        if keys:
            marks = ",".join("?" * len(keys))
            # **가장 최근 관측**이 단계를 정한다(Codex 검토 2026-09-30: 예전 reserve 가 직전 스캔의 exclude_hit 을 이겼다).
            # 증분 검색이라 한 논문은 보통 처음 들어온 스캔에서 한 번만 관측된다 — 그래서 "직전 스캔 것만"이 아니라 "발견 전 마지막 관측"이다.
            rows = con.execute(f"SELECT outcome, filter_reason, scan_id FROM candidate_observations WHERE profile_id=? AND observed_at<=? "
                               f"AND paper_key IN ({marks}) ORDER BY observed_at DESC", (profile_id, cutoff, *keys)).fetchall()
            delivered = con.execute(
                f"SELECT 1 FROM mail_issue_items i JOIN mail_issues m ON m.issue_id=i.issue_id WHERE m.profile_id=? AND m.sent_at<=? "
                f"AND m.status IN ('sent','partial') AND i.paper_key IN ({marks}) LIMIT 1", (profile_id, cutoff, *keys)).fetchone() \
                if con.execute("SELECT 1 FROM sqlite_master WHERE name='mail_issue_items'").fetchone() else None
        if not rows:
            # ID 로 못 찾으면 제목으로(합성 ID `pdf-…` 로 들어온 논문 등). 가장 긴 낱말로 좁힌 뒤 **정규화 제목이 같을 때만**.
            title = _norm_title((it.get("official") or {}).get("title") or it["title"])
            longest = max(title.split(), key=len) if title else ""
            if len(longest) >= 5:
                rows = [(o, f, sid) for o, f, t, sid in con.execute(
                    "SELECT outcome, filter_reason, title, scan_id FROM candidate_observations WHERE profile_id=? AND observed_at<=? "
                    "AND title LIKE ? ORDER BY observed_at DESC", (profile_id, cutoff, f"%{longest}%")).fetchall() if _norm_title(t) == title]
    if not keys:
        delivered = None
    # 배달은 관측이 아니라 **발송 기록**으로 본다 — 관측은 발송 전에 저장되므로 선정(content)이 곧 배달이 아니다(Codex 검토 2026-09-30).
    if delivered:
        return "already_captured", (rows[0][2] if rows else scan[0])
    if not rows:
        # 관측이 없고, 그 프로필의 가장 이른 검색 창보다 먼저 나온 논문이면 잡을 기회가 없었다 — 놓침이 아니다(2026-10-08).
        # 검색 창(window_from)으로 본다: 첫 스캔도 며칠 앞까지 검색하므로 스캔 시각으로 자르면 실제로 잡은 논문까지 빠진다.
        try:
            with sqlite3.connect(db) as con:
                start = con.execute("SELECT MIN(NULLIF(window_from, '')) FROM search_runs WHERE profile_id=? "
                                    "AND julianday(started_at)<=julianday(?)",
                                    (profile_id, cutoff)).fetchone()[0]
        except sqlite3.Error:
            start = None
        if start and ((len(pub) == 10 and pub < str(start)[:10])
                      or (len(pub) == 7 and pub < str(start)[:7])):
            return "before_monitoring", scan[0]
        if not start or len(pub) != 10:
            return "not_yet_evaluable", scan[0]
        return "not_retrieved", scan[0]
    outcome, reason, sid = rows[0]
    if outcome in ("content", "title_only") or (outcome == "filtered" and reason == "already_shown"):
        return "not_delivered", sid
    if outcome == "reserve" or (outcome == "dropped" and reason is None):
        return "ranked_out", sid
    if reason == "exclude_hit":
        return "excluded", sid
    return "no_core_hit", sid


def check_performance(it: dict, fetch: Callable[..., str] | None, deadline: float) -> bool | None:
    """정찰이 적은 벤치마크 수치가 그 논문 arXiv 표의 **자기 행**에 있는가 — 성능 동향과 같은 표 검증(`performance_results`)을 쓴다.
    arXiv 가 아니거나 시간이 없으면 None(확인 안 함). 새 검증 규칙을 만들지 않는다."""
    b, aid = it.get("benchmark"), (it.get("official") or {}).get("arxiv_id")
    if not b or not aid or time.monotonic() > deadline:
        return None
    try:
        import arxiv_tables
        import external_evidence
        import performance_results as pr
        page = (fetch or external_evidence._fetch)(f"https://arxiv.org/html/{aid}", min(15.0, max(1.0, deadline - time.monotonic())))
        rows = pr.table_results(arxiv_tables.parse_tables(page), method=pr.method_name(it["official"]["title"]), benchmarks=[b["name"]])
        claimed = arxiv_tables.cell_number(b["value"])
        want = pr.metric_key(b["metric"])
        if claimed is None or not want:
            return False
        # 벤치마크·**지표**·자기 행이 맞는 셀만 대조한다(Codex 검토 2026-09-30: AUROC 주장이 F1 셀 값으로 확인됐다).
        return any(r["own"] and (r["metric_key"] == want or r["metric_key"].startswith(want) or want.startswith(r["metric_key"]))
                   and (abs(r["value"] - claimed) <= 0.051 or abs(r["value"] * 100 - claimed) <= 0.051) for r in rows)
    except Exception:  # noqa: BLE001 — HTML 판이 없는 논문이 많다
        return None


# ── ③ Claude 검증(도구 없음) ───────────────────────────────────────────────────
VERIFY_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["items"],
    "properties": {"items": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["id", "verdict", "relevant", "manufacturing_relation", "claim_supported", "candidate_terms", "note"],
        "properties": {
            "id": {"type": "string"},
            "verdict": {"type": "string", "enum": ["verified", "rejected", "uncertain"]},
            "relevant": {"type": "boolean"},
            "manufacturing_relation": {"type": "string", "enum": ["direct", "indirect", "none"]},
            "claim_supported": {"type": "boolean"},
            "candidate_terms": {"type": "array", "items": {"type": "string"}},
            "note": {"type": "string"}}}}},
}
VERIFY_PROMPT_FILE = "external_verify_v1.md"     # prompts/ — 버전 관리 대상 자산(AGENTS.md)


def _run_claude_verify(payload_json: str, timeout: float) -> dict:
    """주간 제안과 같은 격리 — 도구 없음, MCP·설정 파일 없음, 세션 저장 없음."""
    import agent_maintenance as am
    exe = shutil.which("claude", path=am._cli_env()["PATH"])
    if not exe:
        raise am.StepError("claude_missing")
    with tempfile.TemporaryDirectory(prefix="scout-claude-") as cwd:
        rc, out, _err = am._run([exe, "-p", am._prompt(VERIFY_PROMPT_FILE), "--output-format", "json", "--json-schema", json.dumps(VERIFY_SCHEMA),
                                 "--tools", "", "--strict-mcp-config", "--setting-sources", "", "--no-session-persistence"],
                                payload_json, int(timeout), cwd)
    if rc != 0:
        raise am.StepError("claude_exit", f"rc={rc}")
    data = json.loads(out)
    if data.get("is_error") or not isinstance(data.get("structured_output"), dict):
        raise am.StepError("claude_error")
    return data["structured_output"]


def apply_verdicts(items: list[dict], verdict: dict) -> None:
    """Claude 판정을 붙이되 Python 이 한 번 더 조인다: 후보 용어는 공식 제목·초록에 실제로 있어야 하고 우산어가 아니어야 한다."""
    import agent_maintenance as am
    import jsonschema
    import term_hygiene
    jsonschema.validate(verdict, VERIFY_SCHEMA)
    by_id = {v["id"]: v for v in verdict["items"]}
    for it in items:
        v = by_id.get(it["id"])
        if not v:
            it["verified"] = {"verdict": "missing"}
            continue
        text = f"{it['official']['title']}. {it['official']['abstract']}"
        try:
            lang_guard.require_korean(v["note"], text, "Claude(외부 정찰 검증)")
        except lang_guard.NonKoreanOutput:
            it["verified"] = {"verdict": "missing"}      # 이 항목만 판정 없음으로 — 정찰 전체를 멈추지 않는다
            continue
        terms = [" ".join(t.split())[:am.MAX_TERM_CHARS] for t in v["candidate_terms"][:3]]
        terms = [t for t in terms if t and am._in_text(t, text) and not term_hygiene.is_umbrella(t)]
        it["verified"] = {"verdict": v["verdict"], "relevant": v["relevant"], "manufacturing_relation": v["manufacturing_relation"],
                          "claim_supported": v["claim_supported"], "candidate_terms": terms, "note": v["note"][:200]}


# ── 한 주 실행 ─────────────────────────────────────────────────────────────────
def _profiles(db: Path) -> list[dict]:
    import research_profile
    out = []
    for pid in research_profile.list_profiles(db):
        p = research_profile.get_profile(db, pid)
        if p:
            out.append({"id": pid, "name": p["name"], "core": sorted(p["core_topics"], key=str.lower),
                        "domain": list(p["target_domain"]), "exclude": list(p.get("exclude") or [])})
    return out


def _round_robin(group: list[dict], ids: list[str], cap: int) -> list[dict]:
    """상한을 **프로필마다 돌아가며** 채운다 — 앞에서부터 자르면 뒤쪽 프로필이 매주 외부 근거를 못 받는다(Codex 검토 2026-09-30)."""
    by_profile: dict[str, list[dict]] = {}
    for it in group:
        by_profile.setdefault(it["profile_id"], []).append(it)
    out, rank = [], 0
    while len(out) < cap and any(rank < len(v) for v in by_profile.values()):
        for pid in ids:
            lst = by_profile.get(pid) or []
            if rank < len(lst) and len(out) < cap:
                out.append(lst[rank])
        rank += 1
    return out


def capture_summary(stages: list[str]) -> dict:
    """세 가지 capture(사용자 설계): 검색 소스가 가져왔나(retrieval) · 프로필이 관련으로 잡았나(profile) · 메일까지 갔나(delivery).
    분모는 판정 가능한 항목(`not_yet_evaluable` 제외). 외부 정찰은 완전한 정답 집합이 아니다 — 독립 관측 집합일 뿐이다."""
    ev = [s for s in stages if s not in ("not_yet_evaluable", "before_monitoring")]
    retrieved = [s for s in ev if s != "not_retrieved"]
    core = [s for s in retrieved if s in ("ranked_out", "not_delivered", "already_captured")]
    return {"evaluable": len(ev), "not_yet_evaluable": len(stages) - len(ev), "retrieved": len(retrieved),
            "core_hit": len(core), "delivered": sum(1 for s in ev if s == "already_captured")}


def run_weekly(db: Path, now: datetime | None = None, *, scout: Callable[[str, float], str] | None = None,
               verify: Callable[[str, float], dict] | None = None, s2_batch: Callable[[list[str]], list] | None = None,
               fetch: Callable[..., str] | None = None, force: bool = False,
               arxiv_batch: Callable[[list[str]], dict] | None = None) -> dict:
    """주 1회. 어떤 실패도 예외로 올리지 않는다 — 실패하면 그 주 외부 근거가 0건일 뿐, 주간 관리는 내부 근거로 돈다."""
    now = now or datetime.now(timezone.utc)
    week = week_of(now)
    run_id = uuid.uuid4().hex[:12]
    summary = {"run_id": run_id, "week": week, "status": "failed"}
    try:
        init_db(db)
        with sqlite3.connect(db) as con:
            done = con.execute("SELECT run_id, status FROM external_scout_runs WHERE week=? AND status IN ('done','partial')",
                               (week,)).fetchone()
            if done and not force:
                return {"run_id": done[0], "week": week, "status": "already_ran"}
            con.execute("INSERT INTO external_scout_runs (run_id, week, started_at, status) VALUES (?,?,?,'running')",
                        (run_id, week, now.isoformat()))
    except Exception as error:  # noqa: BLE001 — 표가 없으면(migrate 전) 이번 주는 건너뛴다
        return {**summary, "status": "skipped", "error": type(error).__name__}

    def finish(status: str, **cols) -> dict:
        cols = {"status": status, "finished_at": datetime.now(timezone.utc).isoformat(), **cols}
        try:
            with sqlite3.connect(db) as con:
                con.execute(f"UPDATE external_scout_runs SET {', '.join(f'{k}=?' for k in cols)} WHERE run_id=?", [*cols.values(), run_id])
        except sqlite3.Error:
            pass
        return {**summary, **cols}

    scout_calls = verify_calls = 0
    try:
        profiles = _profiles(db)
        ids = [p["id"] for p in profiles]
        try:
            scout_calls = 1
            raw = (scout or _run_codex_scout)(scout_prompt(profiles, now), SCOUT_TIMEOUT_S)
            found = sanitize(raw, ids)
        except Exception as error:  # noqa: BLE001
            return finish("failed", scout_calls=1, error=f"scout:{getattr(error, 'code', None) or type(error).__name__}")
        axes = sanitize_axes(raw, profiles)
        items: list[dict] = []
        for pid, lst in found.items():
            seen: dict[str, dict] = {}
            for it in lst:
                key = it.get("arxiv_id") or it.get("doi") or _norm_title(it["title"])
                if key in seen:
                    continue                                        # 프로필 안 중복
                seen[key] = {**it, "profile_id": pid, "from_scout": True, "axes": [], "axis_summaries": {}}
                items.append(seen[key])
            # 떠오르는 연구축의 근거 논문도 같은 신원·놓침·검증 경로를 탄다. 이미 정찰 항목으로 있는 논문이면 그 항목에 연구축만 붙인다.
            # 근거 논문만으로 들어온 항목(from_scout=False)은 "외부 정찰 대비 수집률"에서 뺀다 — 정의가 바뀌면 주별 추이를 못 견준다.
            for ax in axes.get(pid, []):
                for paper in ax["papers"]:
                    key = paper.get("arxiv_id") or paper.get("doi") or _norm_title(paper["title"])
                    if key not in seen:
                        seen[key] = {**paper, "profile_id": pid, "from_scout": False, "axes": [], "axis_summaries": {}}
                        items.append(seen[key])
                    if ax["term"] not in seen[key]["axes"]:
                        seen[key]["axes"].append(ax["term"])
                        seen[key]["axis_summaries"][ax["term"]] = ax["summary"]
        identity_errors = identify(items, s2_batch or _s2_batch, arxiv_batch if arxiv_batch is not None else _arxiv_batch)
        items = _merge_identified(items)
        if identity_errors:
            print(f"  [외부 정찰] 신원 확인 일부 실패: {', '.join(identity_errors)}")
        deadline = time.monotonic() + PERF_BUDGET_S
        for i, it in enumerate(items, start=1):
            it["id"] = f"E{i}"
            if it["identity"] == "verified":
                it["gap_stage"], it["last_scan"] = gap_stage(db, it["profile_id"], it, now)
                it["performance_verified"] = check_performance(it, fetch, deadline)
            else:
                it["gap_stage"], it["last_scan"] = "not_yet_evaluable", None
        # 검증은 두 번 부른다(2026-10-08 외부 검토 "근거 검증 예산과 기존 정찰 예산의 충돌"): 논문 정찰 항목과, 연구축 근거로만 들어온 항목.
        # 한 번에 보내면 최대 44항목이 300초 한 호출을 넘어 그 주 검증이 통째로 사라질 수 있다 — 나누면 연구축 쪽이 실패해도 논문 정찰은 남는다.
        verified_items = [it for it in items if it["identity"] == "verified"]
        unresolved = any(it["identity"] != "verified" and (it.get("arxiv_id") or it.get("doi")) for it in items)
        # 신원 조회가 실패해 확인 못 한 항목이 있으면 "done" 이 아니다 — 사실대로 partial(실측: S2 429 인데 done 으로 적혔다).
        problems = ["identity:" + ",".join(identity_errors)] if identity_errors and unresolved else []
        by_pid = {p["id"]: p for p in profiles}
        verify_calls = 0
        sent_any = False
        for label, group, cap in (("verify", [it for it in verified_items if it.get("from_scout", True)], MAX_VERIFY),
                                  ("axis_verify", [it for it in verified_items if not it.get("from_scout", True)], MAX_VERIFY_AXES)):
            batch = _round_robin(group, ids, cap)
            payload = {"items": [{"id": it["id"], "profile": {"name": by_pid[it["profile_id"]]["name"],
                                                                "core": by_pid[it["profile_id"]]["core"]},
                                  "official": {k: it["official"][k] for k in ("title", "abstract", "venue", "published")},
                                  "scout_claims": {k: it.get(k) for k in ("contribution", "change_from_prior", "manufacturing_use")},
                                  "axes": it.get("axes") or [],
                                  "python_checks": {"gap_stage": it["gap_stage"], "performance_verified": it["performance_verified"]}}
                                 for it in batch]}
            # 프로필별로 돌아가며 채운 순서를 유지하며 입력 크기 초과분은 검증 안 함으로 남긴다.
            while payload["items"] and len(json.dumps(payload, ensure_ascii=False)) > MAX_VERIFY_CHARS:
                payload["items"].pop()
                batch.pop()
            if len(batch) < len(group):
                problems.append(f"{label}_cap:{len(group) - len(batch)}" if label != "verify" else f"verify_cap:{len(group) - len(batch)}")
            if not batch:
                continue
            sent_any = True
            try:
                verify_calls += 1
                apply_verdicts(batch, (verify or _run_claude_verify)(json.dumps(payload, ensure_ascii=False), VERIFY_TIMEOUT_S))
                if any(it.get("verified", {}).get("verdict") == "missing" for it in batch):
                    problems.append(f"{label}:missing")
            except Exception as error_:  # noqa: BLE001 — 검증이 없으면 그 묶음의 외부 근거를 쓰지 않는다
                problems.append(f"{label}:{getattr(error_, 'code', None) or type(error_).__name__}")
                for it in batch:
                    it["verified"] = {"verdict": "missing"}
        status, error = ("partial", ";".join(problems)) if problems else ("done", None)
        verified_n = 0
        try:
            with sqlite3.connect(db) as con:
                for it in items:
                    v = it.get("verified") or {"verdict": "not_sent"}
                    verified_n += v.get("verdict") == "verified" and bool(v.get("relevant"))
                    evidence = {k: it.get(k) for k in _ITEM_FIELDS} | {"identity": it["identity"], "official": it.get("official"),
                                                                         "performance_verified": it.get("performance_verified"),
                                                                         "from_scout": it.get("from_scout", True), "axes": it.get("axes") or [],
                                                                         "axis_summaries": it.get("axis_summaries") or {}}
                    con.execute("INSERT INTO external_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                        f"{run_id}:{it['id']}", run_id, it["profile_id"], (paper_keys(it) or [None])[0],
                        (it.get("official") or {}).get("title") or it["title"], it.get("source_type"), it.get("url"),
                        (it.get("official") or {}).get("published") or it.get("published"), it["gap_stage"], it.get("last_scan"),
                        json.dumps(evidence, ensure_ascii=False), json.dumps(v, ensure_ascii=False), now.isoformat()))
        except sqlite3.Error as error_:
            return finish("failed", scout_calls=1, verify_calls=verify_calls, error=f"store:{type(error_).__name__}")
        capture = {pid: capture_summary([it["gap_stage"] for it in items if it["profile_id"] == pid and it["identity"] == "verified"
                                          and it.get("from_scout", True)])
                   for pid in ids}
        return finish(status, scout_calls=1, verify_calls=verify_calls, candidate_count=len(items),
                      identified_count=sum(it["identity"] == "verified" for it in items), verified_count=verified_n,
                      capture_json=json.dumps(capture, ensure_ascii=False), error=error)
    except Exception as error:  # 선택적인 외부 정찰 때문에 주간 관리가 멈추면 안 된다.
        return finish("failed", scout_calls=scout_calls, verify_calls=verify_calls,
                      error=f"scout_processing:{type(error).__name__}")


# ── ④ 주간 관리 브리프로 ─────────────────────────────────────────────────────────
def evidence_for_brief(db: Path, profile_id: str, now: datetime, days: int = 7) -> list[dict]:
    """이번 주(최근 7일) 이 프로필의 **검증된** 외부 근거. 신원 확인 + Claude verified + relevant 만. 정찰의 주장 문장은 넘기지 않는다 —
    공식 제목·초록, 놓친 단계, 후보 용어(공식 글에 있는 것), 출처 종류만."""
    since = (now - timedelta(days=days)).isoformat()
    try:
        with sqlite3.connect(db) as con:
            rows = con.execute("SELECT observation_id, paper_key, title, source_type, published_at, gap_stage, evidence_json, verified_json, run_id "
                               "FROM external_observations WHERE profile_id=? AND julianday(discovered_at)>julianday(?) AND julianday(discovered_at)<=julianday(?) "
                               "ORDER BY julianday(discovered_at) DESC, rowid DESC", (profile_id, since, now.isoformat())).fetchall()
    except sqlite3.Error:
        return []
    out, seen = [], set()
    latest_run = rows[0][-1] if rows else None
    try:
        with sqlite3.connect(db) as con:
            run = con.execute("SELECT run_id FROM external_scout_runs WHERE julianday(started_at)>julianday(?) "
                              "AND julianday(started_at)<=julianday(?) ORDER BY julianday(started_at) DESC, rowid DESC LIMIT 1",
                              (since, now.isoformat())).fetchone()
        if run:
            latest_run = run[0]  # 최신 회차가 빈 결과여도 지난 회차 연구축을 되살리지 않는다.
    except sqlite3.Error:
        pass
    for oid, key, title, stype, pub, stage, ev_json, v_json, run_id in rows:
        try:
            v = json.loads(v_json or "{}")
            ev = json.loads(ev_json or "{}")
        except (ValueError, TypeError):
            continue
        if not isinstance(v, dict) or not isinstance(ev, dict):
            continue
        official = ev.get("official") or {}
        if not isinstance(official, dict):
            continue
        aliases = _identity_aliases(ev) | {key or "title:" + _norm_title(title)}
        duplicate = bool(aliases & seen)
        seen.update(aliases)
        if duplicate or ev.get("identity") != "verified" or v.get("verdict") != "verified" or not v.get("relevant"):
            continue
        out.append({"paper_key": key, "title": official.get("title") or title, "abstract": official.get("abstract") or "",
                    "venue": official.get("venue") or "", "source_type": stype, "published": official.get("published") or "", "gap_stage": stage,
                    "manufacturing_relation": v.get("manufacturing_relation"), "candidate_terms": v.get("candidate_terms") or [],
                    "performance_verified": ev.get("performance_verified"), "from_scout": ev.get("from_scout", True),
                    "axes": (ev.get("axes") or []) if run_id == latest_run else [],
                    "axis_summaries": ev.get("axis_summaries") or {}})
    return out


def emerging_axes(db: Path, profile_id: str, items: list[dict], now: datetime) -> list[dict]:
    """검증된 근거 논문으로 받을 수 있는 연구축만. 받는 조건(결정적): 신원 확인·Claude verified·관련 있음인 서로 다른 근거 논문 중
    **그 용어가 공식 제목·초록에 그대로 있는** 것이 2편 이상. 떠오르는 연구축인지의 판단은 에이전트 몫이다.
    Python 편수는 최근 28일과 그 전 28일에 우리 검색에 처음 들어온 후보 중 해당 용어가 있는 수이며 분야 전체의 인기도가 아니다."""
    import agent_maintenance as am
    import collections
    papers: dict[str, set[str]] = {}
    summaries: dict[str, str] = {}
    misses: dict[str, collections.Counter] = {}
    for e in items:
        text = f"{e['title']}. {e.get('abstract') or ''}"
        try:
            published = datetime.fromisoformat(e.get("published") or "").date()
        except (ValueError, TypeError):
            continue
        if not (now.date() - timedelta(days=183) <= published <= now.date()):
            continue
        for term in e.get("axes") or []:
            if am._in_text(term, text):
                key = e["paper_key"] or e["title"]
                if key not in papers.setdefault(term, set()):
                    papers[term].add(key)
                    if e.get("gap_stage") in MISSED_STAGES:
                        misses.setdefault(term, collections.Counter())[e["gap_stage"]] += 1
                # 정찰 설명은 한 줄 정의만 싣는다 — 판정어(추천·유망·주목 …)가 섞이면 버린다(2026-10-08 Codex 검토: 자유 글에 점수·추천이 들어갈 수 있다).
                text_ko = (e.get("axis_summaries") or {}).get(term) or ""
                summaries.setdefault(term, "" if _JUDGMENT_RE.search(text_ko) else text_ko)
    out = []
    for term, keys in papers.items():
        if len(keys) < AXIS_MIN_PAPERS:
            continue
        recent = previous = recent_total = previous_total = None
        try:
            with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as con:
                con.execute("PRAGMA query_only=ON")
                rows = con.execute("SELECT title, abstract, julianday(first_seen)>=julianday(?) FROM search_candidates "
                                   "WHERE profile_id=? AND julianday(first_seen)>=julianday(?) AND julianday(first_seen)<=julianday(?)",
                                   ((now - timedelta(days=AXIS_WINDOW_DAYS)).isoformat(), profile_id,
                                    (now - timedelta(days=2 * AXIS_WINDOW_DAYS)).isoformat(), now.isoformat())).fetchall()
            hits = [recent for title, abstract, recent in rows if am._in_text(term, f"{title}. {abstract or ''}")]
            recent, previous = sum(hits), len(hits) - sum(hits)
            # 분모: 같은 창에 우리 검색에 처음 들어온 후보 전체. 주간 관리가 키워드를 바꾸면 두 창의 검색 조건이 다르므로
            # 이 비율은 "우리 검색 안의 비중"이지 분야 성장세가 아니다(2026-10-08 외부 검토).
            recent_total = sum(1 for *_x, r in rows if r)
            previous_total = len(rows) - recent_total
        except sqlite3.Error:
            pass
        out.append({"term": term, "summary": summaries.get(term, ""), "papers": len(keys), "recent": recent, "previous": previous,
                    "recent_total": recent_total, "previous_total": previous_total,
                    "missed": {k: misses.get(term, {}).get(k, 0) for k in MISSED_STAGES if misses.get(term, {}).get(k)}})
    return sorted(out, key=lambda a: (-a["papers"], a["term"]))


_JUDGMENT_RE = re.compile(r"추천|유망|주목|중요|최고|최신 동향|핵심|필수|반드시|혁신|뛰어|우수|각광|떠오르")


# ── 월요일 메일 — 에이전트가 무엇을 놓쳤나 ──────────────────────────────────────────
STAGE_LABELS = {"not_retrieved": "검색 소스가 못 가져옴", "no_core_hit": "가져왔으나 핵심어에 안 걸림", "ranked_out": "관련인데 자리에서 밀림",
                "excluded": "제외어에 걸림", "not_delivered": "선정됐으나 발송 기록 없음", "already_captured": "이미 메일로 보냄",
                "not_yet_evaluable": "마지막 스캔 뒤에 나와 판정 전", "before_monitoring": "관측 시작 전에 나온 논문"}


def mail_summary(db: Path, profile_id: str, now: datetime, days: int = 7) -> dict | None:
    """이번 주 이 프로필의 외부 정찰 요약 — capture 세 수치와 **놓친 검증 연구** 목록(최대 5편, 공식 제목·링크). 없으면 None.
    정찰의 평가·순위는 싣지 않는다. 링크는 발송 직전 감사(`link_policy.audit_mail`)를 한 번 더 거친다."""
    items = evidence_for_brief(db, profile_id, now, days)
    if not items:
        return None
    scouted = [e for e in items if e.get("from_scout", True)]
    missed = [e for e in scouted if e["gap_stage"] in MISSED_STAGES]
    link = lambda e: (f"https://arxiv.org/abs/{e['paper_key']}" if e["paper_key"] and not e["paper_key"].startswith("doi:")
                      else f"https://doi.org/{e['paper_key'][4:]}" if e["paper_key"] else "")
    return {"capture": capture_summary([e["gap_stage"] for e in scouted]), "verified": len(scouted),
            "axes": emerging_axes(db, profile_id, items, now),
            "missed": [{"title": e["title"], "venue": e["venue"], "stage": STAGE_LABELS.get(e["gap_stage"], e["gap_stage"]),
                        "link": link(e)} for e in missed[:5]]}
