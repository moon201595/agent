"""② 선별 — 주간 관리 에이전트. 브리프는 Python, 제안은 Claude, 판정은 Codex, 검증·적용은 Python.

왜 이 모양인가(2026-09-15, 사용자 결정 — PROGRESS §8-127):
- 사용자가 세션을 열지 않아도 키워드·가중치·검색어가 반응을 따라가야 한다. 그래서 cron(금 17:00 KST)이
  구독 CLI 를 헤드리스로 부른다(`claude -p` · `codex exec`). API 결제를 붙이지 않는다(규칙 6).
- 둘의 의견이 갈리면 **Codex 판정이 이긴다**(사용자 결정). Codex 가 돌려준 최종 목록만 적용 후보다 —
  Claude 가 낸 것이라도 Codex 목록에 없으면 적용하지 않는다.
- 주당 변경 개수 상한은 두지 않는다(사용자 결정). 대신 변경 하나하나를 Python 이 브리프 증거와 대조한다.
- 두 CLI 모두 **도구 없이** 돈다 — Claude `--tools ""`, Codex `--disable shell_tool`. 2026-09-15 실측: 도구를
  끄면 저장소 README 첫 줄을 못 읽었고(could_read=false) 켠 대조군은 읽었다. 브리프에는 논문 제목·초록(비신뢰
  입력)이 들어가므로, 모델이 거기 적힌 문장에 끌려가도 파일·네트워크에 손댈 수 없어야 한다(규칙 4·5).
  자식 프로세스 환경변수도 HOME·PATH·언어 설정만 넘긴다 — cron 셸에 시크릿이 올라와 있어도 모델 쪽엔 안 간다.
- 실패(인증·시간 초과·JSON 불량·검증 불통과)는 그 주를 건너뛰고 기록한다. 아침 메일과 무관하다(규칙 6).
  결과(바뀐 것·건너뛴 사유)는 그 프로필의 다음 아침 메일에 한 번만 실린다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedback_links
import profile_impact
import profile_scoring
import research_profile
import term_discovery
import term_hygiene

KST = timezone(timedelta(hours=9))
LOOKBACK_DAYS = 28
CLI_TIMEOUT_S = 600
WEIGHT_MIN, WEIGHT_MAX = 0.35, 2.0          # feedback_weights 의 clamp 와 같은 범위
ABSTRACT_CHARS = 700                        # 브리프 크기 상한 — 논문 한 편이 입력을 독점하지 않게
MAX_REACTED_PAPERS = 40                     # 입력 크기 상한(변경 개수 상한이 아니다)
MAX_TERM_CHARS = 80
MAX_REASON_CHARS = 200
PROMPTS = Path(__file__).resolve().parent / "prompts"
PROMPT_VERSION = "agent-v1"

OPS = ("add_keyword", "set_weight", "remove_keyword", "add_seed", "remove_seed", "add_exclude")
OP_LABELS = {"add_keyword": "키워드 추가", "set_weight": "가중치 조정", "remove_keyword": "키워드 삭제",
             "add_seed": "검색어 추가", "remove_seed": "검색어 삭제", "add_exclude": "제외어 추가"}
POSITIVE = ("more", "useful")

# OpenAI 구조화 출력(strict)은 maxLength·minimum 같은 제약을 받지 않는 경우가 있어 스키마에 넣지 않는다.
# 길이·범위는 validate() 가 본다.
_ACTION_ITEM = {
    "type": "object", "additionalProperties": False,
    "required": ["op", "term", "weight", "evidence", "reason"],
    "properties": {
        "op": {"type": "string", "enum": list(OPS)},
        "term": {"type": "string"},
        "weight": {"type": ["number", "null"]},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
    },
}
PROPOSAL_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["actions"],
                   "properties": {"actions": {"type": "array", "items": _ACTION_ITEM}}}
JUDGE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["reviews", "actions"],
    "properties": {
        "reviews": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["index", "verdict", "reason"],
            "properties": {"index": {"type": "integer"},
                           "verdict": {"type": "string", "enum": ["accept", "modify", "reject"]},
                           "reason": {"type": "string"}}}},
        "actions": {"type": "array", "items": _ACTION_ITEM},
    },
}

# 모델 출력에 시크릿처럼 생긴 문자열이 있으면 그 주 결과를 통째로 버린다. 도구를 껐으니 모델이 시크릿을 볼 길은
# 없지만, 출력은 DB·메일로 나가므로 한 겹 더 막는다(규칙 5). 원문은 저장하지 않고 해시만 남긴다.
_SECRET_RE = re.compile(
    r"(?i:\b(?:api[_-]?key|secret|passw(?:or)?d|bearer|token)\b\s*[:=])"
    r"|\b[A-Z0-9_]*(?:KEY|SECRET|TOKEN|PASSWORD|PASSWD)[A-Z0-9_]*\s*[:=]"        # AWS_SECRET_ACCESS_KEY= 같은 변수 이름
    r"|AIza[0-9A-Za-z_\-]{20,}|\bsk-[A-Za-z0-9_\-]{20,}|\bgsk_[A-Za-z0-9]{20,}|\bgh[pousr]_[A-Za-z0-9]{20,}"
    r"|\bAKIA[0-9A-Z]{16}\b|\bxox[abprs]-[A-Za-z0-9\-]{10,}"
    r"|\b[0-9a-f]{32,}\b"
    # 대소문자·숫자가 섞인 24자 이상 영숫자 덩어리. 하이픈으로 이은 소문자 용어(vision-language-action-v2-…)는
    # 덩어리가 짧고 대문자가 없어 걸리지 않는다(외부 검토 2026-09-15: 이전 규칙은 이런 용어를 오탐했다).
    r"|(?<![A-Za-z0-9])(?=[A-Za-z0-9]*[a-z])(?=[A-Za-z0-9]*[A-Z])(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{24,}")


class StepError(RuntimeError):
    """헤드리스 단계 실패. code 가 agent_runs.status 의 사유가 된다."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# ── 저장 ────────────────────────────────────────────────────────────────────
def _ddl(con: sqlite3.Connection) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS agent_runs ("
        " profile_id    TEXT NOT NULL,"
        " week          TEXT NOT NULL,"          # KST ISO 주 — 주 1회 예약 키
        " started_at    TEXT NOT NULL,"
        " finished_at   TEXT,"
        " status        TEXT NOT NULL,"          # running | applying | applied | no_change | skipped_no_signal | failed
        " error         TEXT,"                   # failed 사유 코드
        " run_tag       TEXT,"                   # 이 실행이 만든 revision 의 note — 기록 실패 뒤에도 적용 여부를 찾는다
        " base_revision INTEGER,"
        " new_revision  INTEGER,"
        " prompt_version TEXT,"
        " brief_sha     TEXT,"
        " proposal_json TEXT,"                   # Claude 구조화 출력(시크릿 의심이면 NULL)
        " judge_json    TEXT,"                   # Codex 구조화 출력
        " applied_json  TEXT,"                   # 적용된 변경
        " rejected_json TEXT,"                   # Python 검증에서 버린 변경과 사유
        " reported_at   TEXT,"                   # 아침 메일에 실린 시각 — 한 번만 싣는다
        " impact_json   TEXT,"                   # 적용 직전 반사실 재채점 요약(profile_impact, 기록만 — 2026-09-17)
        " PRIMARY KEY (profile_id, week))"
    )
    # 2026-09-17 추가 컬럼 — 표가 이미 있는 DB 에는 migrate 가 아니라 여기서 보탠다(schema_guard 가 이 DDL 을 부를 때만)
    cols = {r[1] for r in con.execute("PRAGMA table_info(agent_runs)")}
    if "impact_json" not in cols:
        con.execute("ALTER TABLE agent_runs ADD COLUMN impact_json TEXT")


def init_db(db: Path) -> None:
    import schema_guard
    schema_guard.ensure(db, _ddl, "agent_maintenance")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def week_of(now: datetime) -> str:
    y, w, _ = now.astimezone(KST).isocalendar()
    return f"{y}-W{w:02d}"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ── 브리프 ──────────────────────────────────────────────────────────────────
@dataclass
class Brief:
    data: dict                      # 모델에 보내는 JSON
    texts: dict[str, str]           # 증거 id → 제목+초록(검증용)
    reactions: dict[str, Counter]   # R id → 반응 수
    keyword_ids: dict[str, str]     # K id → 핵심 키워드
    base_revision: int
    liked_texts: list[str] = field(default_factory=list)   # 좋다고 반응한 **모든** 논문(창·상한 무관) — 제외어 보호용, 모델엔 안 간다

    @property
    def evidence_ids(self) -> set[str]:
        return set(self.texts) | set(self.keyword_ids)

    def has_signal(self) -> bool:
        """모델을 부를 거리가 있는가 — 반응이 있거나, 적중 0편인 자동 키워드가 있을 때만(구독 한도를 빈 브리프에 쓰지 않는다).
        탈락 논문 반복어만으로는 부르지 않는다: 키워드 추가는 좋다고 반응한 논문 근거가 있어야 통과하므로(validate),
        반응 없는 주에 부르면 한도만 쓴다. 메일을 받지 않는 manual 프로필은 그래서 자연히 건너뛴다."""
        prof = self.data["profile"]
        stale_auto = any(k["origin"] in AUTO_ORIGINS and k["hits_28d"] == 0 for k in prof["core"]) and prof["scans_28d"] >= 7
        return bool(self.data["reactions"] or stale_auto)


def _latest_reactions(rows: list[dict], since: str) -> dict[str, Counter]:
    """회차·수신자·논문마다 마지막 반응만 센다(되돌리기·마음 바꿈 반영)."""
    last: dict[tuple, dict] = {}
    for r in rows:
        k = (r["issue_id"], r["recipient_hash"], r["paper_key"])
        if k not in last or str(r["received_at"]) >= str(last[k]["received_at"]):
            last[k] = r
    out: dict[str, Counter] = {}
    for r in last.values():
        if str(r["received_at"]) >= since and r["action"] in ("more", "useful", "out"):
            out.setdefault(r["paper_key"], Counter())[r["action"]] += 1
    return out


def _latest_observation(con: sqlite3.Connection, profile_id: str, key: str) -> dict | None:
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM candidate_observations WHERE profile_id=? AND paper_key=? "
                      "ORDER BY observed_at DESC, scan_id DESC LIMIT 1", (profile_id, key)).fetchone()
    if not row:
        return None
    r = dict(row)
    abstract, _ = profile_impact._restore_abstract(con, row)
    r["abstract"] = abstract or ""
    return r


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _schedule(db: Path, profile_id: str) -> tuple[str, str]:
    with sqlite3.connect(db) as con:
        row = con.execute("SELECT COALESCE(schedule_frequency,'daily'), COALESCE(schedule_time,'05:00') "
                          "FROM profiles WHERE profile_id=?", (profile_id,)).fetchone()
    return (row[0], row[1]) if row else ("daily", "05:00")


AUTO_ORIGINS = ("agent", "advisor", "rule")


def _origin(value: object) -> str:
    """자동 출처로 **확실히** 적힌 것만 자동이다. NULL·대소문자 다른 값·모르는 값은 전부 'user'(보호, 규칙 1 — 외부 검토 2026-09-15)."""
    v = str(value or "").strip()
    return v if v in AUTO_ORIGINS else "user"


def _origins(db: Path, profile_id: str) -> dict[tuple[str, str], str]:
    """(소문자 용어, kind) → provenance. 키워드 이벤트가 없는 옛 프로필은 전부 'user' 로 본다 — revision 단위 출처를
    키워드마다 나눠 추정하면 사용자 키워드를 자동으로 오판할 수 있다. 모르면 보호 쪽이다."""
    gens = research_profile.active_generations(db, profile_id)
    return {(kw.lower(), kind): _origin(v["provenance_origin"]) for (kw, kind), v in gens.items()}


def build_brief(db: Path, profile_id: str, now: datetime | None = None) -> Brief | None:
    profile = research_profile.get_profile(db, profile_id)
    if not profile:
        return None
    now = now or _now()
    start, end = now - timedelta(days=LOOKBACK_DAYS), now
    since = start.isoformat()
    origins = _origins(db, profile_id)
    texts: dict[str, str] = {}

    with sqlite3.connect(db) as con:
        obs = con.execute("SELECT paper_key, scan_id, core_hits FROM candidate_observations "
                          "WHERE profile_id=? AND observed_at>=? AND observed_at<?",
                          (profile_id, since, end.isoformat())).fetchall()
        hits: dict[str, set] = {}
        for key, _scan, raw in obs:
            for h in json.loads(raw or "[]"):
                hits.setdefault(str(h).lower(), set()).add(key)
        scans = len({s for _k, s, _r in obs})

        core, keyword_ids = [], {}
        for i, kw in enumerate(sorted(profile["core_topics"], key=str.lower), start=1):
            kid = f"K{i}"
            keyword_ids[kid] = kw
            core.append({"id": kid, "term": kw, "weight": round(profile["core_weights"].get(kw, 1.0), 3),
                         "origin": origins.get((kw.lower(), "core"), "user"), "hits_28d": len(hits.get(kw.lower(), ()))})

        all_rows = feedback_links.valid_reactions(db, profile_id)
        counts = _latest_reactions(all_rows, since)
        liked_texts = []
        for key, c in _latest_reactions(all_rows, "").items():
            if sum(c[a] for a in POSITIVE) > 0 and (held := _latest_observation(con, profile_id, key)) is not None:
                liked_texts.append(f"{held['title'] or ''}. {held['abstract']}")
        # 최근 반응 순이 아니라 반응 수 → 키 순으로 자른다(결정적). 상한은 입력 크기 보호용이다.
        chosen = sorted(counts, key=lambda k: (-sum(counts[k].values()), k))[:MAX_REACTED_PAPERS]
        reactions, reaction_ids, liked_pool = [], {}, []
        for i, key in enumerate(chosen, start=1):
            row = _latest_observation(con, profile_id, key)
            if row is None:
                continue
            rid = f"R{i}"
            reaction_ids[rid] = counts[key]
            texts[rid] = f"{row['title'] or ''}. {row['abstract']}"
            c = counts[key]
            reactions.append({"id": rid, "title": row["title"] or "", "abstract": _clip(row["abstract"], ABSTRACT_CHARS),
                              "more": c["more"], "useful": c["useful"], "out": c["out"],
                              "core_hits": json.loads(row.get("core_hits") or "[]")})
            if sum(c[a] for a in POSITIVE) > c["out"]:
                day, _p = profile_scoring.publication_day(row["published"])
                liked_pool.append({"_paper_key": rid, "title": row["title"] or "", "abstract": row["abstract"],
                                   "published": row["published"], "day": day,
                                   "domain_hits": json.loads(row.get("domain_hits") or "[]"),
                                   "s2_seeds": json.loads(row.get("s2_seeds") or "[]")})

    liked_terms = [{"term": t["term"], "support": t["support"], "evidence": [e["key"] for e in t["evidence"]]}
                   for t in term_discovery.discover(liked_pool, profile,
                                                    rules={"min_papers": 2, "top_terms": 8, "evidence_per_term": 3})]

    missed_terms, missed_papers, missed_ids = [], [], {}
    pool = term_discovery.exploration_pool(db, profile_id, start, end)
    for t in term_discovery.discover(pool, profile, rules={"top_terms": 6}):
        ev = []
        for e in t["evidence"]:
            xid = missed_ids.get(e["key"])
            if xid is None:
                xid = missed_ids[e["key"]] = f"X{len(missed_ids) + 1}"
                full = next((p for p in pool if p["_paper_key"] == e["key"]), None)
                texts[xid] = f"{e['title']}. {(full or {}).get('abstract') or e['abstract']}"
                missed_papers.append({"id": xid, "title": e["title"], "abstract": _clip(e["abstract"], ABSTRACT_CHARS)})
            ev.append(xid)
        missed_terms.append({"term": t["term"], "support": t["support"], "domain_papers": t["domain_papers"],
                             "evidence": ev})

    data = {
        "profile": {"id": profile_id, "name": profile["name"], "scans_28d": scans,
                    "papers_28d": len({k for k, _s, _r in obs}), "core": core,
                    "seeds": [{"term": s, "origin": origins.get((s.lower(), "s2_seed"), "user")} for s in profile["s2_seeds"]],
                    "target_domain": list(profile["target_domain"]), "exclude": list(profile["exclude"])},
        "reactions": reactions, "liked_terms": liked_terms,
        "missed_terms": missed_terms, "missed_papers": missed_papers,
    }
    return Brief(data=data, texts=texts, reactions=reaction_ids, keyword_ids=keyword_ids,
                 base_revision=research_profile.current_revision(db, profile_id), liked_texts=liked_texts)


# ── 검증 ────────────────────────────────────────────────────────────────────
def _in_text(term: str, text: str) -> bool:
    """채점기와 **같은** 매처(match-v2)로 근거 논문 본문에서 찾는다 — 규칙이 둘이면 "근거 있음"과 "적중" 이 어긋난다."""
    return bool(profile_scoring.keyword_pattern(term).search(text or ""))


def _norm_term(term: str) -> str:
    """채점기(profile_scoring match-v2)와 같은 구분자 — 공백·하이픈류·언더스코어·슬래시 — 로 나눈 소문자 낱말열, 마지막 낱말 단수.
    term_discovery.canonical 은 하이픈만 풀어서 `robot_manipulation` 과 `robot manipulation` 을 다른 말로 봤다(외부 검토 2026-09-15)."""
    toks = [t for t in profile_scoring.TOKEN_SPLIT_RE.split(str(term).lower().replace("(", " ").replace(")", " ")) if t]
    if toks:
        toks[-1] = term_hygiene.singular(toks[-1])
    return " ".join(toks)


def _same(a: str, b: str) -> bool:
    return _norm_term(a) == _norm_term(b)


def _liked(brief: Brief, evidence_id: str) -> bool:
    c = brief.reactions.get(evidence_id)
    return bool(c) and sum(c[x] for x in POSITIVE) > c["out"]


# 한글 조사는 유니코드 단어 문자라 \b 가 "X4의" 사이에서 안 끊긴다 — 경계를 영숫자 기준 lookaround 로 직접 건다.
_ID = r"(?<![A-Za-z0-9])[RXK]\d+(?![0-9A-Za-z])"
_EVIDENCE_ID_RE = re.compile(rf"\(?{_ID}(?:\s*[·,/]\s*{_ID})*\)?(?:\s?(?:에서|의|에|는|은|이|가)(?![가-힣]))?")


def reader_reason(reason: str) -> str:
    """메일용 사유 — 브리프 내부 id(R1·X3·K2)는 받는 사람에게 뜻이 없다(2026-09-15 실측: 'X3·X4의 제목·초록에…')."""
    return " ".join(_EVIDENCE_ID_RE.sub("", reason or "").split()).strip(" ·,")


def secret_like(value: object) -> bool:
    if isinstance(value, str):
        return bool(_SECRET_RE.search(value))
    if isinstance(value, dict):
        return any(secret_like(v) for v in value.values())
    if isinstance(value, list):
        return any(secret_like(v) for v in value)
    return False


def validate(actions: list, brief: Brief) -> tuple[list[dict], list[dict]]:
    """Codex 최종 목록을 순서대로 현재 상태에 대어 본다. 앞의 변경이 뒤의 판단에 반영된다
    (add_keyword 뒤의 add_seed). 통과한 것과 (변경, 사유) 를 돌려준다."""
    prof = brief.data["profile"]
    core = {k["term"]: {"weight": k["weight"], "origin": k["origin"]} for k in prof["core"]}
    seeds = {s["term"]: s["origin"] for s in prof["seeds"]}
    exclude = list(prof["exclude"])
    target = list(prof["target_domain"])
    ok: list[dict] = []
    bad: list[dict] = []

    def find(pool, term):
        return next((t for t in pool if _same(t, term)), None)

    def reject(a: dict, why: str) -> None:
        bad.append({"action": a, "reason": why})

    for raw in actions if isinstance(actions, list) else []:
        if not isinstance(raw, dict):
            reject({"raw": str(raw)[:120]}, "not_object")
            continue
        a = {"op": raw.get("op"), "term": " ".join(str(raw.get("term") or "").split()),
             "weight": raw.get("weight"), "evidence": raw.get("evidence"),
             "reason": _clip(str(raw.get("reason") or ""), MAX_REASON_CHARS)}
        op, term, ev = a["op"], a["term"], a["evidence"]
        if op not in OPS:
            reject(a, "unknown_op"); continue
        if not term or len(term) > MAX_TERM_CHARS:
            reject(a, "bad_term"); continue
        if not isinstance(ev, list) or not ev or not all(isinstance(e, str) for e in ev):
            reject(a, "no_evidence"); continue
        if any(e not in brief.evidence_ids for e in ev):
            reject(a, "unknown_evidence"); continue
        if op in ("add_keyword", "set_weight"):
            w = a["weight"]
            if isinstance(w, bool) or not isinstance(w, (int, float)) or not (WEIGHT_MIN <= float(w) <= WEIGHT_MAX):
                reject(a, "bad_weight"); continue
            a["weight"] = round(float(w), 3)
        else:
            a["weight"] = None
        known_core = find(core, term)

        if op == "add_keyword":
            if known_core or find(exclude, term) or find(target, term):
                reject(a, "already_known"); continue
            if term_hygiene.is_umbrella(term):
                reject(a, "umbrella_term"); continue
            if any(term_hygiene.token_sequence_contains(_norm_term(term), _norm_term(k)) for k in core):
                reject(a, "narrower_than_core"); continue   # 이미 그 키워드가 잡는 논문이다
            if term_hygiene.overlaps_known(_norm_term(term), {_norm_term(x) for x in exclude}):
                reject(a, "overlaps_exclude"); continue
            if not any(e in brief.texts and _in_text(term, brief.texts[e]) for e in ev):
                reject(a, "term_not_in_evidence"); continue
            # 규칙 1 — 새 키워드는 **좋다고 반응한 논문**에 그 용어가 있을 때만. 2026-09-15 실측(운영 DB 복사본): 반응 0건인
            # 주에 두 모델이 모두 탈락 논문 반복어 'communication overhead' 를 받아들였다. 탈락 논문(X)은 보조 근거일 뿐이다.
            if not any(_liked(brief, e) and _in_text(term, brief.texts[e]) for e in ev):
                reject(a, "no_liked_evidence"); continue
            core[term] = {"weight": a["weight"], "origin": "agent"}
        elif op == "set_weight":
            if not known_core:
                reject(a, "not_core"); continue
            if abs(core[known_core]["weight"] - a["weight"]) < 1e-9:
                reject(a, "no_op"); continue
            if a["weight"] > core[known_core]["weight"] and not any(
                    _liked(brief, e) and _in_text(known_core, brief.texts[e]) for e in ev):
                reject(a, "no_liked_evidence"); continue    # 올리는 것도 반응을 따른다. 내리는 것은 적중 0편(K)으로도 된다
            a["term"] = known_core
            core[known_core]["weight"] = a["weight"]
        elif op == "remove_keyword":
            if not known_core:
                reject(a, "not_core"); continue
            if core[known_core]["origin"] not in AUTO_ORIGINS:
                reject(a, "user_keyword_protected"); continue   # 규칙 1 — 하향만 된다
            if len(core) <= 1:
                reject(a, "last_keyword"); continue
            if find(seeds, known_core):
                reject(a, "still_seed"); continue    # 검색어로 남은 채 채점에서 빠지면 잡음만 모은다
            a["term"] = known_core
            del core[known_core]
        elif op == "add_seed":
            if find(seeds, term):
                reject(a, "already_seed"); continue
            if not known_core:
                reject(a, "seed_not_core"); continue
            a["term"] = known_core
            seeds[known_core] = "agent"
        elif op == "remove_seed":
            known = find(seeds, term)
            if not known:
                reject(a, "not_seed"); continue
            if seeds[known] not in AUTO_ORIGINS:
                reject(a, "user_seed_protected"); continue
            if len(seeds) <= 1:
                reject(a, "last_seed"); continue
            a["term"] = known
            del seeds[known]
        elif op == "add_exclude":
            if find(exclude, term) or known_core:
                reject(a, "already_known"); continue
            if term_hygiene.overlaps_known(_norm_term(term), {_norm_term(k) for k in [*core, *target]}):
                reject(a, "overlaps_core"); continue
            out_ev = [e for e in ev if e in brief.reactions and brief.reactions[e]["out"] > 0 and _in_text(term, brief.texts[e])]
            if not out_ev:
                reject(a, "no_out_evidence"); continue
            # 제외어는 **서로 다른 관심 밖 논문 2편 이상**에 있어야 한다. 제외된 논문은 메일에 다시 안 나오므로 사용자가 반응으로
            # 되돌릴 기회가 없다 — 한 편짜리 근거로 막으면 그 방향의 피드백이 영영 끊긴다. 2026-09-15 실측(합성 반응): Codex 가
            # 근거를 한 편으로 줄인 채 'Chain-of-thought' 를 제외어로 확정했다. 변경 개수 상한이 아니라 근거 강도 조건이다.
            out_papers = [r for r, c in brief.reactions.items() if c["out"] > 0 and _in_text(term, brief.texts[r])]
            if len(out_papers) < 2:
                reject(a, "single_out_evidence"); continue
            # 브리프 상한(40편)·창(28일) 밖의 좋아요 논문도 지킨다 — 모델에 보낸 목록만 보면 41번째 논문이 제외된다(외부 검토 2026-09-15).
            if any(_in_text(term, t) for t in brief.liked_texts):
                reject(a, "hits_liked_paper"); continue
            exclude.append(term)
        ok.append(a)
    return ok, bad


# ── 적용 ────────────────────────────────────────────────────────────────────
def projected_profile(profile: dict, actions: list[dict]) -> dict:
    """변경을 적용한 **뒤**의 프로필(dict) — 쓰지 않고 계산만. `apply` 와 `impact_of` 가 같은 함수를 써야 재채점이 실제 적용과 같은 상태를 본다."""
    core = list(profile["core_topics"])
    weights = dict(profile["core_weights"])
    seeds = list(profile["s2_seeds"])
    exclude = list(profile["exclude"])
    for a in actions:
        t = a["term"]
        if a["op"] == "add_keyword":
            core.append(t); weights[t] = a["weight"]
        elif a["op"] == "set_weight":
            weights[t] = a["weight"]
        elif a["op"] == "remove_keyword":
            core.remove(t); weights.pop(t, None)
        elif a["op"] == "add_seed":
            seeds.append(t)
        elif a["op"] == "remove_seed":
            seeds.remove(t)
        elif a["op"] == "add_exclude":
            exclude.append(t)
    return {**profile, "core_topics": core, "core_weights": weights, "s2_seeds": seeds, "exclude": exclude}


IMPACT_WINDOW = timedelta(days=28)      # 반사실 재채점이 보는 관측 기간 — 브리프와 같은 4주


def impact_of(db: Path, profile_id: str, before: dict, after: dict, now: datetime) -> dict | None:
    """적용 직전 **반사실 재채점**(profile_impact) — 지난 4주 관측 스냅숏에 전·후 프로필을 적용해 무엇이 바뀌는지 잰다.
    2026-09-17: 옛 주간 개선기(profile_advisor)만 부르던 것을 새 에이전트 경로에 연결했다. **기록만 한다** — 차단 임계는 두지 않는다
    (임계값은 기준선 실측 뒤 정한다, 계획 v2 §9). `impact_analyses` 에 전체가, `agent_runs.impact_json` 에 요약이 남는다.
    실패는 None — 재채점이 안 된다고 적용을 막지 않는다(기록만 하는 단계가 적용을 막으면 규칙 6 을 어긴다)."""
    import profile_impact
    try:
        snap = profile_impact.snapshot(db, profile_id, now - IMPACT_WINDOW, now)
        if not snap["papers"]:
            return {"status": "no_observations"}
        consumed = research_profile.already_shown(db, profile_id)
        k = int(after.get("max_items") or before.get("max_items") or 5)
        row = profile_impact.analyze_and_store(db, snap, before, after, k, consumed_keys=consumed)
        imp = json.loads(row.get("impact_json") or "{}") if isinstance(row.get("impact_json"), str) else (row.get("impact_json") or {})
        delivered = imp.get("delivery_view") or {}
        return {"status": "ok", "analysis_id": row["analysis_id"], "snapshot_papers": snap["paper_count"],
                "gained": len(imp.get("eligible_gained") or []), "lost": len(imp.get("eligible_lost") or {}),
                "topk_changed": len(imp.get("topk_entered") or []) + len(imp.get("topk_left") or []),
                "exclude_risk": (imp.get("exclude_risk") or {}).get("rate"), "gate_status": row.get("gate_status"),
                "delivered_gained": len(delivered.get("gained") or []) if delivered else None}
    except Exception as e:  # noqa: BLE001 — 기록 단계의 예외는 적용을 막지 않는다
        return {"status": f"error:{type(e).__name__}"}


def shadow_of(db: Path, profile_id: str, before: dict, after: dict, analysis_id: str | None, now: datetime) -> dict | None:
    """검색어(S2 시드·arXiv 질의)가 바뀌는 변경만 **격리 검색**(shadow_search)으로 손실을 잰다 — 재채점은 저장된 관측만 보므로
    "시드를 빼면 앞으로 못 보게 되는 논문"을 알 수 없다(2026-09-12 실측: world model 시드 제거 294→40). 2026-09-17 새 에이전트 경로에 연결.
    **기록만** 한다(`shadow_runs`). 검색어 변경이 없으면 None. 실패는 상태로 남기고 적용을 막지 않는다. 주 1회·시드 몇 개라 S2 예산 안이다."""
    import shadow_search
    try:
        arms = shadow_search.arms_for(before, after)
        if not (arms["seed_changed"] or arms["arxiv_query_changed"]):
            return None
        import asyncio
        import httpx

        async def _go():
            async with httpx.AsyncClient() as client:
                return await shadow_search.run_shadow(db, profile_id, before, after, client, analysis_id=analysis_id, now=now)
        out = asyncio.run(_go())
        d = (out.get("metrics") or {}).get("diff") or {}
        return {"status": out.get("status"), "shadow_id": out.get("shadow_id"), "seeds_removed": arms["seeds_removed"],
                "seeds_added": arms["seeds_added"], "eligible_before": d.get("eligible_before"), "eligible_after": d.get("eligible_after"),
                "eligible_lost": len(d.get("eligible_lost") or []), "topk_overlap": d.get("topk_overlap"), "api_calls": out.get("api_calls")}
    except Exception as e:  # noqa: BLE001
        return {"status": f"error:{type(e).__name__}"}


def apply(db: Path, profile_id: str, actions: list[dict], base_revision: int, run_tag: str) -> int | None:
    """검증을 통과한 변경을 revision 하나로 쓴다(origin='agent'). 되돌리기는 research_profile.rollback_to_revision."""
    if not actions:
        return None
    current = research_profile.current_revision(db, profile_id)
    if current != base_revision:     # 검증은 base 상태 기준이었다 — 그 사이 바뀌었으면 이번 주는 버린다
        raise ValueError(f"expected_revision_mismatch:{base_revision}!={current}")
    profile = research_profile.get_profile(db, profile_id)
    after = projected_profile(profile, actions)
    freq, at = _schedule(db, profile_id)
    return research_profile.create_profile(
        db, profile_id, profile["name"], after["core_topics"], target_domain=profile["target_domain"], exclude=after["exclude"],
        venues=profile["venues"], max_items=profile["max_items"], schedule_frequency=freq, schedule_time=at,
        core_weights=after["core_weights"], s2_seeds=after["s2_seeds"], origin="agent", note=run_tag,
        expected_revision=base_revision)


# ── 헤드리스 실행 ───────────────────────────────────────────────────────────
def _cli_env() -> dict[str, str]:
    """자식에게 넘기는 환경. 시크릿이 들어 있을 수 있는 나머지 변수는 버린다(규칙 5)."""
    keep = ("HOME", "PATH", "LANG", "LC_ALL", "USER", "CODEX_HOME", "XDG_CONFIG_HOME")
    env = {k: os.environ[k] for k in keep if os.environ.get(k)}
    local_bin = str(Path.home() / ".local" / "bin")
    if local_bin not in env.get("PATH", "").split(":"):
        env["PATH"] = f"{local_bin}:{env.get('PATH', '/usr/bin:/bin')}"
    return env


def _run(argv: list[str], stdin_text: str, timeout: int, cwd: str) -> tuple[int, str, str]:
    """프로세스 그룹째로 띄우고, 시간이 넘으면 그룹째 죽인다 — CLI 가 띄운 손자 프로세스가 cron 뒤에 남지 않게."""
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, cwd=cwd, env=_cli_env(), start_new_session=True)
    try:
        out, err = proc.communicate(stdin_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        raise StepError("timeout", f"{Path(argv[0]).name} {timeout}s")
    return proc.returncode, out, err


def _prompt(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8") + "\n\n" + (PROMPTS / "agent_ops_v1.md").read_text(encoding="utf-8")


class HeadlessRunner:
    """구독 CLI 두 개. 테스트는 같은 두 메서드를 가진 가짜를 넘긴다."""

    def __init__(self, timeout: int = CLI_TIMEOUT_S) -> None:
        self.timeout = timeout

    def propose(self, brief_json: str) -> dict:
        exe = shutil.which("claude", path=_cli_env()["PATH"])
        if not exe:
            raise StepError("claude_missing")
        with tempfile.TemporaryDirectory(prefix="agent-claude-") as cwd:
            rc, out, err = _run([exe, "-p", _prompt("agent_propose_v1.md"), "--output-format", "json",
                                 "--json-schema", json.dumps(PROPOSAL_SCHEMA), "--tools", "",
                                 "--strict-mcp-config", "--setting-sources", "", "--no-session-persistence"],
                                brief_json, self.timeout, cwd)
        if rc != 0:
            raise StepError("claude_exit", f"rc={rc}")
        try:
            data = json.loads(out)
        except json.JSONDecodeError as e:
            raise StepError("claude_bad_json", str(e)[:80])
        if data.get("is_error") or not isinstance(data.get("structured_output"), dict):
            raise StepError("claude_error", str(data.get("subtype"))[:40])
        return data["structured_output"]

    def judge(self, payload_json: str) -> dict:
        exe = shutil.which("codex", path=_cli_env()["PATH"])
        if not exe:
            raise StepError("codex_missing")
        with tempfile.TemporaryDirectory(prefix="agent-codex-") as cwd:
            schema, last = Path(cwd) / "schema.json", Path(cwd) / "last.json"
            schema.write_text(json.dumps(JUDGE_SCHEMA), encoding="utf-8")
            # 사용자 설정(config.toml)·실행 규칙(.rules)을 읽지 않는다 — 인증만 CODEX_HOME 에서 쓴다(외부 검토 2026-09-15, 실측 동작 확인).
            argv = [exe, "exec", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config", "--ignore-rules", "-s", "read-only"]
            for feature in ("shell_tool", "apps", "browser_use", "computer_use", "in_app_browser",
                            "image_generation", "tool_suggest"):
                argv += ["--disable", feature]
            argv += ["-c", "mcp_servers={}", "-c", 'web_search="disabled"', "--output-schema", str(schema),
                     "-o", str(last), _prompt("agent_judge_v1.md")]
            rc, _out, _err = _run(argv, payload_json, self.timeout, cwd)
            if rc != 0:
                raise StepError("codex_exit", f"rc={rc}")
            try:
                return json.loads(last.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                raise StepError("codex_bad_json", type(e).__name__)


# ── 한 주 실행 ──────────────────────────────────────────────────────────────
STALE_RUNNING = timedelta(hours=3)     # 헤드리스 두 단계 상한(600초×2)보다 넉넉히 — 이보다 오래된 running 은 죽은 실행이다


def _reserve(db: Path, profile_id: str, week: str, now: datetime, force: bool) -> bool:
    """(프로필, 주) 예약. 이미 끝난 주는 건너뛴다. 'running' 인 채 3시간이 지난 행은 프로세스가 죽은 것으로 보고 다시 잡는다 —
    그대로 두면 그 주 재시도가 --force 없이는 영영 막힌다(외부 검토 2026-09-15). 'applying' 은 revision 을 이미 썼을 수
    있으므로 자동으로 다시 잡지 않는다(pending_report 가 run_tag 로 적용 여부를 가린다)."""
    with sqlite3.connect(db) as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT status, started_at FROM agent_runs WHERE profile_id=? AND week=?",
                          (profile_id, week)).fetchone()
        if row and not force:
            started = datetime.fromisoformat(row[1]) if row[1] else None
            if not (row[0] == "running" and started is not None and now - started > STALE_RUNNING):
                return False
        con.execute("INSERT INTO agent_runs (profile_id, week, started_at, status, prompt_version) VALUES (?,?,?,'running',?) "
                    "ON CONFLICT(profile_id, week) DO UPDATE SET started_at=excluded.started_at, status='running', "
                    "finished_at=NULL, error=NULL, run_tag=NULL, base_revision=NULL, new_revision=NULL, brief_sha=NULL, "
                    "proposal_json=NULL, judge_json=NULL, applied_json=NULL, rejected_json=NULL, reported_at=NULL, "
                    "prompt_version=excluded.prompt_version",
                    (profile_id, week, now.isoformat(), PROMPT_VERSION))
    return True


def _finish(db: Path, profile_id: str, week: str, **cols) -> None:
    cols["finished_at"] = _now().isoformat()
    keys = sorted(cols)
    with sqlite3.connect(db) as con:
        con.execute(f"UPDATE agent_runs SET {', '.join(f'{k}=?' for k in keys)} WHERE profile_id=? AND week=?",
                    [cols[k] for k in keys] + [profile_id, week])


def _tagged_revision(db: Path, profile_id: str, run_tag: str | None) -> int | None:
    if not run_tag:
        return None
    with sqlite3.connect(db) as con:
        row = con.execute("SELECT MAX(revision) FROM profile_revisions WHERE profile_id=? AND origin='agent' AND note=?",
                          (profile_id, run_tag)).fetchone()
    return row[0] if row and row[0] is not None else None


def _fail(db: Path, profile_id: str, week: str, code: str, run_tag: str | None, **extra) -> dict:
    """실패 기록. revision 을 이미 썼는데 그 뒤(기록 등)에서 실패했으면 사실대로 applied 로 남긴다 — 그러지 않으면 프로필은
    바뀌었는데 메일엔 "키워드는 그대로"가 나간다(외부 검토 2026-09-15). 기록 자체가 또 실패해도 예외를 올리지 않는다 —
    다음 프로필이 돌아야 한다."""
    out = {"profile_id": profile_id, "week": week, "status": "failed", "error": code}
    try:
        rev = _tagged_revision(db, profile_id, run_tag)
        if rev is not None:
            _finish(db, profile_id, week, status="applied", new_revision=rev, error=f"after_apply:{code}")
            return {**out, "status": "applied", "revision": rev}
        _finish(db, profile_id, week, status="failed", error=code, **extra)
    except Exception as e:  # noqa: BLE001
        print(f"  [에이전트] {profile_id} 실패 기록도 실패: {type(e).__name__}")
    return out


def run_profile(db: Path, profile_id: str, runner, now: datetime | None = None, force: bool = False) -> dict:
    """한 프로필의 한 주. 어떤 실패도 예외로 올리지 않고 상태로 남긴다."""
    init_db(db)
    now = now or _now()
    week = week_of(now)
    if not _reserve(db, profile_id, week, now, force):
        return {"profile_id": profile_id, "week": week, "status": "already_ran"}
    run_tag = f"weekly-agent {week} {now.isoformat()}"     # 같은 주 --force 재실행과도 구별된다
    try:
        brief = build_brief(db, profile_id, now)
        if brief is None:
            raise StepError("no_profile")
        brief_json = json.dumps(brief.data, ensure_ascii=False, sort_keys=True)
        common = {"base_revision": brief.base_revision, "brief_sha": _sha(brief_json)}
        if not brief.has_signal():
            _finish(db, profile_id, week, status="skipped_no_signal", **common)
            return {"profile_id": profile_id, "week": week, "status": "skipped_no_signal"}
        proposal = runner.propose(brief_json)
        if secret_like(proposal):
            raise StepError("secret_like_output", "claude")
        _finish(db, profile_id, week, status="running", proposal_json=json.dumps(proposal, ensure_ascii=False), **common)
        verdict = runner.judge(json.dumps({"brief": brief.data, "proposal": proposal}, ensure_ascii=False, sort_keys=True))
        if secret_like(verdict):
            raise StepError("secret_like_output", "codex")
        accepted, rejected = validate(verdict.get("actions") or [], brief)
        decided = {"judge_json": json.dumps(verdict, ensure_ascii=False),
                   "applied_json": json.dumps(accepted, ensure_ascii=False),
                   "rejected_json": json.dumps(rejected, ensure_ascii=False)}
        if accepted:
            # 적용 **전에** 반사실 재채점을 기록한다(기록만, 차단 없음 — 2026-09-17). 그다음 무엇을 적용하려는지와 run_tag 를 남긴다.
            before_profile = research_profile.get_profile(db, profile_id)
            after_profile = projected_profile(before_profile, accepted)
            imp = impact_of(db, profile_id, before_profile, after_profile, now)
            shadow = shadow_of(db, profile_id, before_profile, after_profile, (imp or {}).get("analysis_id"), now)
            decided["impact_json"] = json.dumps({**(imp or {}), "shadow": shadow}, ensure_ascii=False)
            # 적용 뒤 기록이 실패해도 revision 과 짝을 찾을 수 있다.
            _finish(db, profile_id, week, status="applying", run_tag=run_tag, **decided, **common)
        new_rev = apply(db, profile_id, accepted, brief.base_revision, run_tag)
        status = "applied" if new_rev else "no_change"
        _finish(db, profile_id, week, status=status, new_revision=new_rev, **decided, **common)
        return {"profile_id": profile_id, "week": week, "status": status, "applied": len(accepted),
                "rejected": len(rejected), "revision": new_rev}
    except StepError as e:
        extra = {"proposal_json": None} if e.code == "secret_like_output" else {}
        return _fail(db, profile_id, week, e.code, run_tag, **extra)
    except ValueError as e:
        return _fail(db, profile_id, week, "revision_changed" if "expected_revision_mismatch" in str(e) else "value_error", run_tag)
    except Exception as e:  # noqa: BLE001 — 한 프로필의 예외가 나머지 프로필을 막으면 안 된다
        return _fail(db, profile_id, week, f"crash:{type(e).__name__}", run_tag)


def run_week(db: Path, runner=None, now: datetime | None = None, force: bool = False,
             profile_ids: list[str] | None = None) -> list[dict]:
    runner = runner or HeadlessRunner()
    ids = profile_ids or research_profile.list_profiles(db)
    return [run_profile(db, pid, runner, now=now, force=force) for pid in ids]


# ── 아침 메일 ───────────────────────────────────────────────────────────────
FAIL_LABELS = {"timeout": "시간 초과", "claude_exit": "Claude 실행 실패", "claude_error": "Claude 응답 오류",
               "claude_bad_json": "Claude 응답 형식 오류", "claude_missing": "Claude CLI 없음",
               "codex_exit": "Codex 실행 실패", "codex_bad_json": "Codex 응답 형식 오류", "codex_missing": "Codex CLI 없음",
               "secret_like_output": "출력에 시크릿 의심 문자열", "revision_changed": "실행 중 프로필이 바뀜",
               "interrupted": "적용 중 중단"}


REPORT_TTL = timedelta(days=14)   # 이보다 오래된 보고는 싣지 않는다 — 한 수신자 주소가 계속 거절돼도 영원히 반복되지 않게


def pending_report(db: Path, profile_id: str, now: datetime | None = None) -> tuple[list[str], list[tuple[str, str]]]:
    """다음 메일에 실을 줄과 (profile_id, week) 키. 바꾼 것과 실패만 싣는다 — '바꿀 것 없음'·'기각만 있음'은 소음이다(기각 사유는
    agent_runs.rejected_json 에 남는다). 'applying' 으로 멈춘 행은 run_tag 로 revision 을 찾아 적용됐으면 변경으로, 아니면 실패로 싣는다."""
    init_db(db)
    cutoff = ((now or _now()) - REPORT_TTL).isoformat()
    with sqlite3.connect(db) as con:
        rows = con.execute("SELECT week, status, error, base_revision, new_revision, applied_json, run_tag, impact_json FROM agent_runs "
                           "WHERE profile_id=? AND reported_at IS NULL AND status IN ('applied','failed','applying') "
                           "AND COALESCE(finished_at, started_at) >= ? ORDER BY week", (profile_id, cutoff)).fetchall()
    lines: list[str] = []
    keys: list[tuple[str, str]] = []
    for week, status, error, base, new, applied, run_tag, impact_raw in rows:
        if status == "applying":
            new = _tagged_revision(db, profile_id, run_tag)
            status, error = ("applied", error) if new is not None else ("failed", "interrupted")
        keys.append((profile_id, week))
        if status == "failed":
            label = FAIL_LABELS.get(error or "", error or "알 수 없음")
            lines.append(f"· {week} 자동 관리는 건너뛰었습니다 — {label}. 키워드는 그대로입니다.")
            continue
        lines.append(f"· {week} 변경 (revision {base} → {new}, 되돌릴 수 있음)")
        for a in json.loads(applied or "[]"):
            w = f" {a['weight']:g}" if a.get("weight") is not None else ""
            why = reader_reason(a.get("reason") or "")
            lines.append(f"   - {OP_LABELS.get(a['op'], a['op'])}: {a['term']}{w}" + (f" — {why}" if why else ""))
        imp = json.loads(impact_raw) if impact_raw else None
        if imp and imp.get("status") == "ok":
            lines.append(f"   - 지난 4주 관측 {imp['snapshot_papers']}편에 이 변경을 대 보면: 새로 걸리는 논문 {imp['gained']}편 · "
                         f"빠지는 논문 {imp['lost']}편 · 상위 자리 바뀜 {imp['topk_changed']}편 (재채점 기록, 차단 없음)")
        sh = (imp or {}).get("shadow")
        if sh and sh.get("status") in ("done", "partial"):
            lines.append(f"   - 검색어 변경을 격리 검색으로 재 보면: 적격 {sh['eligible_before']} → {sh['eligible_after']}편"
                         f" (잃음 {sh['eligible_lost']}편, 상위 겹침 {sh['topk_overlap'] if sh['topk_overlap'] is not None else '미측정'}) — 기록만")
    return lines, keys


def mark_reported(db: Path, keys: list[tuple[str, str]], when: datetime | None = None) -> None:
    if not keys:
        return
    stamp = (when or _now()).isoformat()
    with sqlite3.connect(db) as con:
        con.executemany("UPDATE agent_runs SET reported_at=? WHERE profile_id=? AND week=? AND reported_at IS NULL",
                        [(stamp, p, w) for p, w in keys])


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="주간 관리 에이전트(금 17:00 KST cron)")
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent / "data" / "papers.db"))
    ap.add_argument("--profile", action="append", help="이 프로필만(여러 번 가능). 생략하면 전체")
    ap.add_argument("--force", action="store_true", help="이번 주에 이미 돌았어도 다시")
    ap.add_argument("--brief-only", action="store_true", help="모델을 부르지 않고 브리프만 출력")
    args = ap.parse_args(argv)
    db = Path(args.db)
    if args.brief_only:
        for pid in args.profile or research_profile.list_profiles(db):
            b = build_brief(db, pid)
            print(json.dumps(b.data if b else None, ensure_ascii=False, indent=1))
        return 0
    results = run_week(db, force=args.force, profile_ids=args.profile)
    for r in results:
        print(json.dumps(r, ensure_ascii=False))
    return 1 if any(r["status"] == "failed" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
