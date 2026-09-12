"""⑧ 축적 — 제한된 LLM 프로필 개선기 (D단계, 2026-09-11).

docs/ASTRA_PLAN_2026-09-10.md §8·§9, 설계 판정은 PROGRESS §8-89.

**모델은 제안만 한다.** 운영 정책을 바꾸지도, 자기 제안에 적용 판정을 내리지도
않는다. 제안은 C(profile_impact)로 넘어가 같은 스냅샷에서 전후 재채점되고,
게이트가 판정한다. 게이트 임계값이 미설정인 동안 어떤 제안도 `eligible_for_apply`
로 가지 않는다. 그 위에 운영 모드 기본값 `proposal_only` 가 한 번 더 막는다 —
임계값이 설정돼도 이 모드가 열리기 전에는 실제 적용이 0건이다.

**규칙 4 경계(PROGRESS §8-89).** 밖에 보내는 것은 현재 관심사(core·계층·씨앗)와
대표 논문의 제목·초록·키다 — 공개 텍스트와 "무엇에 관심 있나". 편수·증감·수율·
탈락 사유·이전 제안 이력은 **내부 관측 결과**라 안 보낸다. 모델이 논문 텍스트에서
스스로 용어를 뽑고, 코드가 전체 스냅샷에서 출현·guard·영향을 잰다.

**예산.** 배치당 실제 HTTP 요청 최대 2회. 키 회전·모델 전환·형식 재시도가
**전부 같은 2회**를 쓴다 — 그래서 `summarize_engine._post_gemini`(키 개수만큼
회전)를 안 쓰고 `_post_gemini_once` 를 직접 부른다. 전송 직전에 영속 장부
(`advisor_budget`)에 예약하므로 재시작·동시 실행이 상한을 우회하지 못한다.
전송 여부가 불명확한 중단은 소비로 친다.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

import api_usage
import profile_impact
import profile_scoring
import research_profile
import summarize_engine as engine

PROMPT_PATH = Path(__file__).parent / "prompts" / "profile_advisor_v2.md"
PROMPT_VERSION = "profile_advisor_v2"

# 초기 제한값(적정성 미실측 — PROGRESS §8-89). 무료 제공량이 아니라 D 자체의 상한이다.
MAX_REQUESTS_PER_BATCH = 2
MAX_PROPOSALS = 3
MAX_PAPERS = 8
# 탐색 차선(②, §8-97): 대표 논문은 5편으로 줄이고 탐색 용어 3개 × 증거 2편을 더한다.
# 증거 초록은 짧게 — 프롬프트 상한(MAX_PROMPT_CHARS) 안에서 11편이 들어가야 한다.
DELIVERY_PAPERS = 5
EXPLORATION_ABSTRACT_CHARS = 500
MAX_TITLE_CHARS = 240
MAX_ABSTRACT_CHARS = 1200
MAX_PROMPT_CHARS = 16000
REQUEST_TIMEOUT_S = 60
BATCH_DEADLINE_S = 120

ALLOWED_ACTIONS = {"add_core_term", "change_core_tier", "add_s2_seed", "no_change"}
DEFERRED_ACTIONS = {"replace_s2_seed": "unsupported_in_v1",
                    "propose_term_guard": "unsupported_guard_application"}

MODE_PROPOSAL_ONLY = "proposal_only"
MODE_AUTO_APPLY = "auto_apply"


# ── 저장 구조 ────────────────────────────────────────────────────────────
def init_db(db: Path) -> None:
    with sqlite3.connect(db) as con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS advisor_runs ("
            " run_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, created_at TEXT NOT NULL,"
            " base_revision INTEGER NOT NULL, week TEXT NOT NULL,"
            " window_start TEXT NOT NULL, window_end TEXT NOT NULL,"
            " snapshot_id TEXT, snapshot_sha256 TEXT,"           # 전체 분석 스냅샷 (로컬)
            " sent_input_json TEXT, sent_input_sha256 TEXT,"     # 모델이 실제로 본 부분집합
            " prompt_version TEXT, prompt_sha256 TEXT, prompt_text TEXT,"
            " status TEXT NOT NULL, status_reason TEXT)")
        con.execute(
            "CREATE TABLE IF NOT EXISTS advisor_attempts ("
            " attempt_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, attempt INTEGER NOT NULL,"
            " purpose TEXT NOT NULL, requested_model TEXT, response_model TEXT,"
            " key_name TEXT, started_at TEXT NOT NULL, finished_at TEXT,"
            " outcome TEXT NOT NULL, http_status INTEGER, raw_response TEXT,"
            " usage_json TEXT, usage_kind TEXT)")   # usage_kind: provider_reported | estimated | unknown
        con.execute(
            "CREATE TABLE IF NOT EXISTS advisor_proposals ("
            " proposal_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, ordinal INTEGER NOT NULL,"
            " action TEXT NOT NULL, term TEXT, proposed_tier REAL,"
            " evidence_json TEXT, reason TEXT, risks_json TEXT,"
            " validation_json TEXT NOT NULL, status TEXT NOT NULL,"
            " analysis_id TEXT, bundle_analysis_id TEXT)")
        con.execute(
            "CREATE TABLE IF NOT EXISTS advisor_events ("
            " event_id TEXT PRIMARY KEY, at TEXT NOT NULL, profile_id TEXT NOT NULL,"
            " kind TEXT NOT NULL, ref_id TEXT, detail_json TEXT)")
        # 영속 예산 장부 — (profile_id, week) 하나에 요청 수. 전송 직전에 올린다.
        con.execute(
            "CREATE TABLE IF NOT EXISTS advisor_budget ("
            " profile_id TEXT NOT NULL, week TEXT NOT NULL, requests INTEGER NOT NULL DEFAULT 0,"
            " runs INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (profile_id, week))")
        con.execute(
            "CREATE TABLE IF NOT EXISTS advisor_settings ("
            " profile_id TEXT PRIMARY KEY, mode TEXT NOT NULL, rules_json TEXT)")


def _event(con: sqlite3.Connection, profile_id: str, kind: str, ref_id: str | None, detail: dict | None) -> None:
    con.execute("INSERT INTO advisor_events (event_id, at, profile_id, kind, ref_id, detail_json) VALUES (?,?,?,?,?,?)",
                (uuid.uuid4().hex[:12], _now(), profile_id, kind, ref_id,
                 json.dumps(detail, ensure_ascii=False) if detail is not None else None))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def week_of(now: datetime | None = None) -> str:
    """KST 기준 ISO 주. 요일 검사가 아니라 (profile_id, week) 예약으로 주 1회를 지킨다 —
    수동 스캔과 cron 이 같은 진입점을 쓰기 때문이다."""
    from datetime import timedelta
    kst = (now or datetime.now(timezone.utc)).astimezone(timezone(timedelta(hours=9)))
    y, w, _ = kst.isocalendar()
    return f"{y}-W{w:02d}"


def get_mode(db: Path, profile_id: str) -> tuple[str, dict | None]:
    init_db(db)
    with sqlite3.connect(db) as con:
        row = con.execute("SELECT mode, rules_json FROM advisor_settings WHERE profile_id=?", (profile_id,)).fetchone()
    if not row:
        return MODE_PROPOSAL_ONLY, None
    return row[0], (json.loads(row[1]) if row[1] else None)


# ── 입력 (규칙 4 whitelist) ──────────────────────────────────────────────
SENT_FIELDS = {"key", "title", "abstract"}   # 대표 논문에서 밖으로 나가는 필드 전부


def select_papers(snap: dict, profile: dict, limit: int = MAX_PAPERS) -> list[dict]:
    """대표 논문을 **로컬에서** 고른다 — 선정 사유·빈도·수율은 밖으로 안 나간다.
    기준: 현재 프로필 순위 계약 상위(관련성 계층 우선·최신 우선). 초록 없는 논문은
    뒤로 — 모델이 볼 텍스트가 없다."""
    ranked = profile_scoring.score_and_rank(snap["papers"], profile)["papers"]
    with_abs = [p for p in ranked if p.get("abstract")] + [p for p in ranked if not p.get("abstract")]
    return with_abs[:limit]


def build_input(snap: dict, profile: dict, papers: list[dict],
                exploration: list[dict] | None = None) -> dict:
    """모델에 보낼 것만. 편수·수율·탈락 사유·이력은 **넣지 않는다**(PROGRESS §8-89).
    `sent_paper_keys` 는 근거 키 검증용 — 전체 스냅샷이 아니라 실제 전송 부분집합에
    있어야 근거로 인정한다. `exploration`(term_discovery.discover 결과)은 용어와 증거
    논문만 보낸다 — support·도메인 편수는 내부 집계라 뺀다(규칙 4·R5)."""
    weights = profile.get("core_weights") or {}
    by_tier: dict[float, list[str]] = {}
    for kw in profile.get("core_topics") or []:
        by_tier.setdefault(float(weights.get(kw, 1.0)), []).append(kw)
    sent_papers = [{"key": p["_paper_key"] if "_paper_key" in p else research_profile.paper_key(p),
                    "title": (p.get("title") or "")[:MAX_TITLE_CHARS],
                    "abstract": (p.get("abstract") or "")[:MAX_ABSTRACT_CHARS]} for p in papers]
    sent_terms = [{"term": t["term"],
                   "papers": [{"key": e["key"], "title": (e.get("title") or "")[:MAX_TITLE_CHARS],
                               "abstract": (e.get("abstract") or "")[:EXPLORATION_ABSTRACT_CHARS]}
                              for e in t.get("evidence") or []]} for t in (exploration or [])]
    keys = [p["key"] for p in sent_papers]
    keys += [e["key"] for t in sent_terms for e in t["papers"] if e["key"] not in keys]
    return {
        "core_by_tier": {str(t): sorted(v) for t, v in sorted(by_tier.items(), reverse=True)},
        "allowed_tiers": sorted({float(w) for w in by_tier}, reverse=True),
        "s2_seeds": sorted(profile.get("s2_seeds") or []),
        "papers": sent_papers,
        "exploration": sent_terms,
        "sent_paper_keys": keys,
    }


def render_prompt(sent: dict) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    core = "\n".join(f"- 계층 {t}: " + ", ".join(v) for t, v in sent["core_by_tier"].items())
    papers = "\n\n".join(f"[{p['key']}] {p['title']}\n{p['abstract'] or '(초록 없음)'}" for p in sent["papers"])
    explo = "\n\n".join(
        f"- 용어: {t['term']}\n" + "\n".join(f"  [{e['key']}] {e['title']}\n  {e['abstract'] or '(초록 없음)'}" for e in t["papers"])
        for t in sent.get("exploration") or []) or "(이번 기간에는 없음)"
    text = (template.replace("{{CORE_BY_TIER}}", core)
                    .replace("{{EXPLORATION}}", explo)
                    .replace("{{ALLOWED_TIERS}}", ", ".join(str(t) for t in sent["allowed_tiers"]))
                    .replace("{{SEEDS}}", ", ".join(sent["s2_seeds"]) or "(없음)")
                    .replace("{{PAPERS}}", papers))
    if len(text) > MAX_PROMPT_CHARS:
        raise ValueError(f"prompt too long: {len(text)} > {MAX_PROMPT_CHARS}")
    return text


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── 응답 검증 ────────────────────────────────────────────────────────────
_JSON_RE = re.compile(r"\{.*\}", re.S)


def parse_response(raw: str) -> dict | None:
    """허용 JSON 만 파싱한다. 코드펜스는 벗기되 그 밖의 것은 해석하지 않는다."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def validate_proposals(data: dict, sent: dict, profile: dict) -> list[dict]:
    """각 제안에 검증 결과를 붙인다. **모델 출력은 명령이 아니다** — 허용 액션·기존
    계층·실제 전송된 근거 키·문자열 실재만 인정하고, 나머지는 사유와 함께 무효다.
    이 검사가 통과해도 의미상 타당성이 입증된 것은 아니다."""
    out: list[dict] = []
    if data.get("decision") not in ("propose", "no_change"):
        return [{"action": "invalid", "errors": ["bad_decision"]}]
    proposals = data.get("proposals") or []
    if data["decision"] == "no_change":
        return []          # 정상 — "이번 입력에서 변경안을 내지 않았다"이지 최적 판정이 아니다
    if not isinstance(proposals, list):
        return [{"action": "invalid", "errors": ["proposals_not_list"]}]
    if len(proposals) > MAX_PROPOSALS:
        proposals = proposals[:MAX_PROPOSALS]
        overflow = True
    else:
        overflow = False
    core = set(profile.get("core_topics") or [])
    tiers = set(sent["allowed_tiers"])
    sent_keys = set(sent["sent_paper_keys"])
    corpus = {p["key"]: (p["title"] + " " + p["abstract"]).lower() for p in sent["papers"]}
    # 탐색 차선의 증거 논문도 실제로 전송된 텍스트다 — R7(문자열 실재)을 같은 corpus 로 본다.
    for t in sent.get("exploration") or []:
        for p in t.get("papers") or []:
            corpus.setdefault(p["key"], (p["title"] + " " + p["abstract"]).lower())
    seen_terms: set[str] = set()
    for i, p in enumerate(proposals):
        errors: list[str] = []
        if not isinstance(p, dict):
            out.append({"action": "invalid", "errors": ["not_object"]}); continue
        action = p.get("action")
        term = (p.get("term") or "").strip()
        if action in DEFERRED_ACTIONS:
            out.append({**p, "action": action, "errors": [DEFERRED_ACTIONS[action]], "deferred": True}); continue
        if action not in ALLOWED_ACTIONS or action == "no_change":
            errors.append("unknown_action")
        if not term:
            errors.append("empty_term")
        if term.lower() in seen_terms:
            errors.append("duplicate_term")
        seen_terms.add(term.lower())
        tier = p.get("proposed_tier")
        if action in ("add_core_term", "change_core_tier"):
            try:
                tier_f = float(tier)
            except (TypeError, ValueError):
                tier_f = None
            if tier_f is None or tier_f != tier_f or tier_f not in tiers:
                errors.append("tier_not_allowed")   # 새 계층을 모델이 만들 수 없다
        if action == "add_core_term" and term in core:
            errors.append("already_core")
        if action in ("change_core_tier", "add_s2_seed") and term not in core and action != "add_s2_seed":
            errors.append("term_not_in_core")
        ev = p.get("evidence_paper_keys") or []
        if not isinstance(ev, list) or (action == "add_core_term" and len(ev) < 2):
            errors.append("insufficient_evidence_keys")
        missing = [k for k in ev if k not in sent_keys]   # 전체 스냅샷이 아니라 **전송 부분집합**
        if missing:
            errors.append(f"evidence_not_sent:{','.join(missing[:3])}")
        if term and action == "add_core_term":
            pat = profile_scoring._keyword_pattern(term)
            present = [k for k in ev if k in corpus and pat.search(corpus[k])]
            if len(present) < min(2, len(ev)):
                errors.append("term_absent_in_evidence")   # R7 — 문자열 그대로 있어야 한다
        if overflow and i == MAX_PROPOSALS - 1:
            errors.append("proposals_truncated")
        out.append({"action": action, "term": term, "proposed_tier": tier,
                    "evidence_paper_keys": ev, "reason": p.get("reason"),
                    "ambiguity_risks": p.get("ambiguity_risks") or [], "errors": errors})
    return out


def apply_actions(profile: dict, proposals: list[dict]) -> dict:
    """유효한 제안들을 **하나의** after 프로필로 합친다(§9.3 묶음). 순수 함수."""
    after = json.loads(json.dumps({k: profile.get(k) for k in
                                   ("core_topics", "core_weights", "target_domain", "exclude", "s2_seeds", "max_items")}))
    after["core_topics"] = list(after.get("core_topics") or [])
    after["core_weights"] = dict(after.get("core_weights") or {})
    after["s2_seeds"] = list(after.get("s2_seeds") or [])
    for p in proposals:
        if p.get("errors") or p.get("deferred"):
            continue
        if p["action"] == "add_core_term":
            after["core_topics"].append(p["term"]); after["core_weights"][p["term"]] = float(p["proposed_tier"])
        elif p["action"] == "change_core_tier":
            after["core_weights"][p["term"]] = float(p["proposed_tier"])
        elif p["action"] == "add_s2_seed" and p["term"] not in after["s2_seeds"]:
            after["s2_seeds"].append(p["term"])
    return after


# ── 예산 장부 ────────────────────────────────────────────────────────────
def reserve_request(db: Path, profile_id: str, week: str) -> bool:
    """전송 **직전**에 한 칸 예약. 상한이면 False. 원자적(UPDATE ... WHERE requests < 상한)."""
    with sqlite3.connect(db) as con:
        con.execute("INSERT OR IGNORE INTO advisor_budget (profile_id, week, requests, runs) VALUES (?,?,0,0)",
                    (profile_id, week))
        cur = con.execute("UPDATE advisor_budget SET requests = requests + 1 WHERE profile_id=? AND week=? AND requests < ?",
                          (profile_id, week, MAX_REQUESTS_PER_BATCH))
        return cur.rowcount == 1


def reserve_run(db: Path, profile_id: str, week: str) -> bool:
    """주 1회 — (profile_id, week) 실행 예약."""
    with sqlite3.connect(db) as con:
        con.execute("INSERT OR IGNORE INTO advisor_budget (profile_id, week, requests, runs) VALUES (?,?,0,0)",
                    (profile_id, week))
        cur = con.execute("UPDATE advisor_budget SET runs = runs + 1 WHERE profile_id=? AND week=? AND runs < 1",
                          (profile_id, week))
        return cur.rowcount == 1


# ── 요청 ─────────────────────────────────────────────────────────────────
async def _request_once(client: httpx.AsyncClient, prompt: str, key_name: str) -> dict:
    """HTTP 요청 **정확히 한 번**. 요청 모델은 이 자리에서 잡는다(완료 후 전역 커서를
    다시 읽으면 다른 호출이 옮긴 값을 볼 수 있다). 응답의 modelVersion 이 있으면 남긴다."""
    model = engine.current_gemini_model()
    started = _now()
    try:
        resp = await client.post(
            engine._gemini_url(model),
            json={"contents": [{"parts": [{"text": prompt}]}],
                  "generationConfig": {"maxOutputTokens": 1500, "temperature": 0.2}},
            headers={"x-goog-api-key": engine.ENV[key_name]},
            timeout=REQUEST_TIMEOUT_S,
        )
    except Exception as e:  # noqa: BLE001 — 시도 기록에 남기고 호출부가 판단한다
        api_usage.record("gemini", "error", purpose="advisor")
        return {"requested_model": model, "key_name": key_name, "started_at": started,
                "finished_at": _now(), "outcome": f"error:{type(e).__name__}", "http_status": None,
                "raw": None, "response_model": None, "usage": None, "usage_kind": "unknown"}
    api_usage.record("gemini", "ok" if resp.status_code == 200 else str(resp.status_code), purpose="advisor")
    if resp.status_code == 503 and len(engine._GEMINI_MODELS) > 1:
        engine._gemini_model_cursor = (engine._gemini_model_cursor + 1) % len(engine._GEMINI_MODELS)
    text, rmodel, usage = None, None, None
    if resp.status_code == 200:
        try:
            data = resp.json()
            text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
            rmodel = data.get("modelVersion")
            usage = data.get("usageMetadata")
        except (KeyError, IndexError, TypeError, ValueError):
            text = None
    return {"requested_model": model, "key_name": key_name, "started_at": started,
            "finished_at": _now(), "outcome": "ok" if text is not None else f"http_{resp.status_code}",
            "http_status": resp.status_code, "raw": resp.text[:20000] if resp.text else None,
            "text": text, "response_model": rmodel, "usage": usage,
            "usage_kind": "provider_reported" if usage else ("estimated" if text is not None else "unknown")}


# ── 실행 ─────────────────────────────────────────────────────────────────
async def run_weekly(db: Path, profile_id: str, client: httpx.AsyncClient | None,
                     start: datetime, end: datetime, now: datetime | None = None,
                     k: int | None = None) -> dict:
    """주간 제안 배치 하나. 반환값은 사람이 읽을 상태 dict. **적용하지 않는다.**
    실패·예산 소진·관측 없음은 전부 정상 종료이고 일일 전달과 무관하다."""
    init_db(db)
    profile = research_profile.get_profile(db, profile_id)
    if not profile:
        return {"status": "skipped", "reason": "no_profile"}
    week = week_of(now)
    if not reserve_run(db, profile_id, week):
        return {"status": "skipped", "reason": "already_ran_this_week", "week": week}
    base_rev = research_profile.current_revision(db, profile_id)
    run_id = uuid.uuid4().hex[:12]
    snap = profile_impact.snapshot(db, profile_id, start, end)
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO advisor_runs (run_id, profile_id, created_at, base_revision, week, window_start,"
                    " window_end, snapshot_id, snapshot_sha256, status) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (run_id, profile_id, _now(), base_rev, week, start.isoformat(), end.isoformat(),
                     snap["snapshot_id"], snap["content_sha256"], "started"))
        _event(con, profile_id, "run_started", run_id, {"week": week, "base_revision": base_rev})

    def finish(status: str, reason: str | None = None, **extra) -> dict:
        with sqlite3.connect(db) as con:
            con.execute("UPDATE advisor_runs SET status=?, status_reason=? WHERE run_id=?", (status, reason, run_id))
            _event(con, profile_id, f"run_{status}", run_id, {"reason": reason, **extra})
        return {"status": status, "reason": reason, "run_id": run_id, **extra}

    if snap["paper_count"] == 0:
        return finish("skipped", "no_observations")
    if client is None or not engine.gemini_key_names():
        return finish("skipped", "budget_unknown")   # provider 잔여를 모르면 보내지 않는다(§8.5)

    papers = select_papers(snap, profile, limit=DELIVERY_PAPERS)
    # 탐색 차선(②): 키워드에 안 걸려 탈락한 논문에서 로컬로 찾은 용어 + 증거 논문.
    # 실패해도 제안기는 대표 논문만으로 간다 — 탐색이 주간 제안을 막으면 안 된다.
    try:
        import term_discovery
        exploration = term_discovery.discover(
            term_discovery.exploration_pool(db, profile_id, start, end), profile)
    except Exception as e:  # noqa: BLE001
        print(f"  [탐색] 실패(무시) {type(e).__name__}: {str(e)[:80]}", flush=True)
        exploration = []
    sent = build_input(snap, profile, papers, exploration)
    try:
        prompt = render_prompt(sent)
    except ValueError as e:
        return finish("skipped", str(e))
    sent_json = json.dumps(sent, ensure_ascii=False, sort_keys=True)
    with sqlite3.connect(db) as con:
        con.execute("UPDATE advisor_runs SET sent_input_json=?, sent_input_sha256=?, prompt_version=?,"
                    " prompt_sha256=?, prompt_text=? WHERE run_id=?",
                    (sent_json, _sha(sent_json), PROMPT_VERSION, _sha(prompt), prompt, run_id))

    import time
    deadline = time.monotonic() + BATCH_DEADLINE_S
    keys = engine.gemini_key_names()
    key_idx = engine._gemini_key_cursor % len(keys)
    parsed, attempts = None, 0
    while attempts < MAX_REQUESTS_PER_BATCH and time.monotonic() < deadline:
        if not reserve_request(db, profile_id, week):
            return finish("skipped", "request_budget_exhausted", attempts=attempts)
        attempts += 1
        res = await _request_once(client, prompt, keys[key_idx % len(keys)])
        with sqlite3.connect(db) as con:
            con.execute("INSERT INTO advisor_attempts (attempt_id, run_id, attempt, purpose, requested_model,"
                        " response_model, key_name, started_at, finished_at, outcome, http_status, raw_response,"
                        " usage_json, usage_kind) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (uuid.uuid4().hex[:12], run_id, attempts, "advisor", res["requested_model"],
                         res["response_model"], res["key_name"], res["started_at"], res["finished_at"],
                         res["outcome"], res["http_status"], res["raw"],
                         json.dumps(res["usage"]) if res["usage"] else None, res["usage_kind"]))
        if res["outcome"] == "ok":
            parsed = parse_response(res["text"] or "")
            if parsed is not None:
                break
            # 형식 오류 — 같은 예산 안에서 한 번 더
        elif res["http_status"] == 429:
            key_idx += 1        # 다음 키. 이것도 같은 2회 예산이다.
    if parsed is None:
        return finish("failed", "no_valid_response", attempts=attempts)

    validated = validate_proposals(parsed, sent, profile)
    after = apply_actions(profile, validated)
    mode, rules = get_mode(db, profile_id)
    consumed = research_profile.already_shown(db, profile_id)
    analysis = None
    if any(not p.get("errors") and not p.get("deferred") for p in validated):
        analysis = profile_impact.analyze_and_store(db, snap, profile, after, k or int(profile.get("max_items") or 6),
                                                    rules=rules, consumed_keys=consumed)
    with sqlite3.connect(db) as con:
        for i, p in enumerate(validated, start=1):
            status = ("deferred" if p.get("deferred") else "invalid" if p.get("errors") else "analyzed")
            con.execute("INSERT INTO advisor_proposals (proposal_id, run_id, ordinal, action, term, proposed_tier,"
                        " evidence_json, reason, risks_json, validation_json, status, analysis_id, bundle_analysis_id)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (uuid.uuid4().hex[:12], run_id, i, p.get("action") or "invalid", p.get("term"),
                         float(p["proposed_tier"]) if isinstance(p.get("proposed_tier"), (int, float)) else None,
                         json.dumps(p.get("evidence_paper_keys") or []), p.get("reason"),
                         json.dumps(p.get("ambiguity_risks") or [], ensure_ascii=False),
                         json.dumps({"errors": p.get("errors") or []}, ensure_ascii=False), status,
                         None, analysis["analysis_id"] if analysis else None))
    return finish("proposed" if validated else "no_change", None, attempts=attempts,
                  proposals=len(validated), valid=sum(1 for p in validated if not p.get("errors") and not p.get("deferred")),
                  gate=analysis["gate_status"] if analysis else None,
                  gate_reasons=json.loads(analysis["reasons_json"]) if analysis else None,
                  mode=mode, applied=False)


# ── 적용 (기본 닫힘) ─────────────────────────────────────────────────────
def apply_analysis(db: Path, profile_id: str, analysis_id: str, *, origin: str = "advisor") -> dict:
    """저장된 분석 하나를 프로필에 적용한다. **호출자의 말을 믿지 않는다** — 저장된
    게이트 상태·기준 revision·현재 revision·운영 모드를 여기서 다시 확인한다.
    하나의 트랜잭션: revision 재확인 → profile_keywords 갱신 → 새 revision → 이벤트.
    같은 분석의 재실행은 두 번 적용하지 않는다(이벤트로 잡는다)."""
    init_db(db)
    mode, _ = get_mode(db, profile_id)
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        an = con.execute("SELECT * FROM impact_analyses WHERE analysis_id=? AND profile_id=?",
                         (analysis_id, profile_id)).fetchone()
        if not an:
            return {"applied": False, "reason": "analysis_not_found"}
        if an["gate_status"] != profile_impact.ELIGIBLE:
            _event(con, profile_id, "apply_refused", analysis_id, {"reason": f"gate:{an['gate_status']}"})
            return {"applied": False, "reason": f"gate:{an['gate_status']}"}
        if mode != MODE_AUTO_APPLY:
            _event(con, profile_id, "apply_refused", analysis_id, {"reason": f"mode:{mode}"})
            return {"applied": False, "reason": f"mode:{mode}"}
        if con.execute("SELECT 1 FROM advisor_events WHERE kind='applied' AND ref_id=?", (analysis_id,)).fetchone():
            return {"applied": False, "reason": "already_applied"}
        # 기준: 분석의 before 가 **지금** 프로필과 같아야 한다(stale 검사). revision 도 같이 본다.
        current = research_profile.get_profile(db, profile_id)
        if profile_impact.profile_hash(current) != an["before_hash"]:
            _event(con, profile_id, "apply_refused", analysis_id, {"reason": "stale_before"})
            return {"applied": False, "reason": "stale_before"}
        after = json.loads(an["after_json"])
    rev = research_profile.create_profile(
        db, profile_id, current["name"], core_topics=after["core_topics"], core_weights=after["core_weights"],
        target_domain=after["target_domain"], exclude=after["exclude"], venues=current.get("venues") or [],
        max_items=after["max_items"] or current["max_items"], s2_seeds=after["s2_seeds"],
        origin=origin, note=f"analysis:{analysis_id}")
    with sqlite3.connect(db) as con:
        _event(con, profile_id, "applied", analysis_id, {"revision": rev})
    return {"applied": True, "revision": rev}


def rollback(db: Path, profile_id: str, to_revision: int, reason: str) -> dict:
    """직전 유효 revision 의 내용을 **새 revision 으로** 복원한다. 이력을 지우지 않는다."""
    init_db(db)
    with sqlite3.connect(db) as con:
        row = con.execute("SELECT snapshot FROM profile_revisions WHERE profile_id=? AND revision=?",
                          (profile_id, to_revision)).fetchone()
        if not row:
            return {"rolled_back": False, "reason": "revision_not_found"}
        snap = json.loads(row[0])
        current = research_profile.get_profile(db, profile_id)
    kws = snap["keywords"]
    core = [k for k, kind, w in kws if kind == "core"]
    weights = {k: float(w if w is not None else 1.0) for k, kind, w in kws if kind == "core"}
    rev = research_profile.create_profile(
        db, profile_id, current["name"], core_topics=core, core_weights=weights,
        target_domain=[k for k, kind, w in kws if kind == "target"],
        exclude=[k for k, kind, w in kws if kind == "exclude"], venues=current.get("venues") or [],
        max_items=snap.get("max_items") or current["max_items"],
        s2_seeds=[k for k, kind, w in kws if kind == "s2_seed"], origin="rollback", note=reason)
    with sqlite3.connect(db) as con:
        _event(con, profile_id, "rolled_back", str(to_revision), {"new_revision": rev, "reason": reason})
    return {"rolled_back": True, "revision": rev}
