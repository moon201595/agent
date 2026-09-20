"""⑨ 운영 화면 자료 — 프로필별 현황(키워드·가중치·발송·반응·변경 이력·에이전트)을 DB 에서 조립한다. Streamlit 없이 테스트한다.

2026-09-16 사용자 요청: "들어가면 프로필별로 어떤 키워드가 있고 가중치는 어떻고 총 몇 편의 메일이 보내졌고 어떤 피드백이 왔고
그래서 어떤 키워드가 추가되거나 그랬고 논문은 어떤 것들이 지금까지 나갔고… 프로필별로". 화면(review_app)은 여기 결과를 그리기만
한다 — 숫자를 화면 코드에서 다시 세지 않는다.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import agent_maintenance
import feedback_links
import mail_ledger
import research_profile

from time_policy import KST, kst_hm, kst_day   # 시각 정책은 time_policy 하나(2026-09-17)
HIT_WINDOW_DAYS = 28
ACTION_LABELS = {"more": "더 보고 싶음", "useful": "유용함", "out": "관심 밖"}
STATUS_LABELS = {feedback_links.STATUS_VALID: "유효", "quarantined_prefetch": "선열람 격리(보안 스캐너)",
                 "quarantined_burst": "연속 클릭 격리", "cancelled": "취소됨", "undo": "취소 요청",
                 "expired": "만료", "bad_signature": "서명 불일치", "unknown_tid": "알 수 없는 링크"}
ORIGIN_LABELS = {"user": "사용자", "feedback": "반응", "agent": "에이전트", "advisor": "제안기", "rule": "규칙",
                 "rollback": "되돌림", "bootstrap": "초기"}


_TERM_RULES = (("씨앗으로", "시드로"), ("씨앗은", "시드는"), ("씨앗을", "시드를"), ("씨앗과", "시드와"),
               ("씨앗이던", "시드였던"), ("씨앗이면", "시드면"))
_TERM_SUBJECT_RE = re.compile(r"씨앗이(?![가-힣])")


def display_text(value: str | None) -> str:
    """화면에 보이는 저장 문구의 옛 용어를 지금 용어로. 2026-09-16 사용자 결정: "씨앗" 대신 "시드". 코드·문서는 바꿨지만 그전에 DB 에
    남은 revision 메모("씨앗 교체: …")·로그는 **기록이라 고치지 않고** 보여 줄 때만 바꾼다(조사까지 맞춘다)."""
    text = "" if value is None else str(value)
    if "씨앗" not in text:
        return text
    for old, new in _TERM_RULES:
        text = text.replace(old, new)
    return _TERM_SUBJECT_RE.sub("시드가", text).replace("씨앗", "시드")


def _kst(value: str | None) -> str:
    return kst_hm(value)            # 이름은 화면 코드 호환용 — 구현은 time_policy


def _kst_day(value: str | None) -> str:
    return kst_day(value)


# ── 시스템 상태 ─────────────────────────────────────────────────────────────
_LOG_LINE_RE = re.compile(r"^=== (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z (시작|종료|스킵|수동 중지)(.*)===")


def _parse_daily_log(log_path: Path) -> dict:
    """마지막 시작·종료 줄. 로그가 없으면 빈 dict."""
    out: dict = {}
    if not log_path.exists():
        return out
    try:
        tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-4000:]
    except OSError:
        return out
    for ln in tail:
        m = _LOG_LINE_RE.match(ln)
        if not m:
            continue
        stamp, kind, rest = m.groups()
        if kind == "시작":
            out = {"started_at": stamp + "+00:00", "finished_at": None, "exit": None}
        elif kind in ("종료", "수동 중지") and out:
            out["finished_at"] = stamp + "+00:00"
            em = re.search(r"exit (\d+)", rest)
            out["exit"] = int(em.group(1)) if em else None
            if kind == "수동 중지":
                out["exit"] = "stopped"
    return out


def system_status(db: Path, root: Path, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    kst_now = now.astimezone(KST)
    daily = _parse_daily_log(root / "logs" / "daily_scan.log")
    next_daily = kst_now.replace(hour=5, minute=0, second=0, microsecond=0)
    if next_daily <= kst_now:
        next_daily += timedelta(days=1)
    # 주간 관리는 2026-09-19 에 **월요일 새벽**으로 옮겼다(§8-163) — 일일 스캔 바로 앞에서 돈다.
    # 화면만 금요일 17:00 을 계속 계산하고 있었다(2026-09-20 지적): 실행은 맞는데 표시가 거짓이었다.
    days_ahead = (0 - kst_now.weekday()) % 7          # 월요일
    next_weekly = (kst_now + timedelta(days=days_ahead)).replace(hour=5, minute=0, second=0, microsecond=0)
    if next_weekly <= kst_now:
        next_weekly += timedelta(days=7)
    weekly = None
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='agent_runs'").fetchone():
            weekly = con.execute("SELECT week, status, error, started_at FROM agent_runs ORDER BY started_at DESC LIMIT 1").fetchone()
        weekly = dict(weekly) if weekly else None
        retention = None
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='retention_runs'").fetchone():
            r = con.execute("SELECT finished_at, applied, error FROM retention_runs ORDER BY finished_at DESC LIMIT 1").fetchone()
            retention = dict(r) if r else None
    today = kst_now.strftime("%Y-%m-%d")
    ran_today = bool(daily.get("started_at")) and _kst_day(daily["started_at"]) == today
    return {
        "daily": daily, "daily_ran_today": ran_today,
        "next_daily_kst": next_daily.strftime("%m-%d %H:%M"), "next_weekly_kst": next_weekly.strftime("%m-%d(%a) %H:%M"),
        "weekly": weekly, "retention": retention,
        "buttons_configured": feedback_links.config() is not None,
    }


# ── 프로필 요약 ─────────────────────────────────────────────────────────────
def _reaction_rows(con: sqlite3.Connection, profile_id: str) -> list[dict]:
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='feedback_events'").fetchone():
        return []
    return [dict(r) for r in con.execute(
        "SELECT e.event_id, e.action, e.received_at, e.status, t.paper_key, t.issue_id, t.item_no, t.position, t.recipient_hash "
        "FROM feedback_events e JOIN feedback_tokens t ON t.tid = e.tid WHERE t.profile_id=? ORDER BY e.received_at DESC",
        (profile_id,))]


def latest_valid_reactions(rows: list[dict]) -> list[dict]:
    """학습(feedback_weights)과 같은 기준 — 회차·수신자·논문마다 **마지막 유효 반응** 하나. 같은 버튼을 두 번 누르거나 마음을
    바꾼 것을 두 번 세지 않는다(외부 검토 2026-09-16: 화면과 학습의 숫자가 달랐다)."""
    last: dict[tuple, dict] = {}
    for r in rows:
        if r["status"] != feedback_links.STATUS_VALID or r["action"] not in ACTION_LABELS:
            continue
        k = (r["issue_id"], r.get("recipient_hash"), r["paper_key"])
        if k not in last or str(r["received_at"]) >= str(last[k]["received_at"]):
            last[k] = r
    return list(last.values())


def reaction_counts(rows: list[dict]) -> dict:
    latest = latest_valid_reactions(rows)
    valid = Counter(r["action"] for r in latest)
    other = sum(1 for r in rows if r["status"] != feedback_links.STATUS_VALID)
    return {"more": valid["more"], "useful": valid["useful"], "out": valid["out"], "valid": len(latest), "other": other}


def profile_overview(db: Path, profile_id: str) -> dict | None:
    profile = research_profile.get_profile(db, profile_id)
    if not profile:
        return None
    freq, at = research_profile.get_schedule(db, profile_id)
    mails = mail_ledger.counts(db, profile_id)
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        reactions = reaction_counts(_reaction_rows(con, profile_id))
        last_agent = None
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='agent_runs'").fetchone():
            r = con.execute("SELECT week, status, error, new_revision FROM agent_runs WHERE profile_id=? "
                            "ORDER BY started_at DESC LIMIT 1", (profile_id,)).fetchone()
            last_agent = dict(r) if r else None
    return {
        "profile_id": profile_id, "name": profile["name"], "field": profile["name"].split(" — ")[0].strip(),
        "schedule": freq, "schedule_time": at,
        "recipients": research_profile.get_recipients(db, profile_id),
        "keywords": {"core": len(profile["core_topics"]), "seed": len(profile["s2_seeds"]),
                     "domain": len(profile["target_domain"]), "exclude": len(profile["exclude"])},
        "mails": mails, "reactions": reactions,
        "revision": research_profile.current_revision(db, profile_id),
        "last_agent": last_agent, "max_items": profile["max_items"],
    }


# ── 키워드·가중치 ────────────────────────────────────────────────────────────
def weight_history(db: Path, profile_id: str) -> dict[str, list[tuple[str, int, float]]]:
    """핵심 키워드별 [(created_at, revision, weight)] — revision 스냅숏에서. 화면의 가중치 추이 그래프 재료."""
    out: dict[str, list[tuple[str, int, float]]] = {}
    with sqlite3.connect(db) as con:
        for rev, created, snap in con.execute(
                "SELECT revision, created_at, snapshot FROM profile_revisions WHERE profile_id=? ORDER BY revision", (profile_id,)):
            try:
                keywords = json.loads(snap).get("keywords") or []
            except (TypeError, ValueError):
                continue
            for kw, kind, w in keywords:
                if kind == "core":
                    out.setdefault(kw, []).append((created, int(rev), float(w if w is not None else 1.0)))
    return out


def keyword_table(db: Path, profile_id: str, now: datetime | None = None) -> list[dict]:
    """키워드 한 줄씩: 종류·가중치·출처·최근 28일 적중 편수·그 논문들에 온 반응·가중치 변화(첫 값 → 지금)."""
    profile = research_profile.get_profile(db, profile_id)
    if not profile:
        return []
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=HIT_WINDOW_DAYS)).isoformat()
    origins = agent_maintenance._origins(db, profile_id)
    history = weight_history(db, profile_id)
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        hits: dict[str, set[str]] = {}
        for key, raw in con.execute("SELECT paper_key, core_hits FROM candidate_observations WHERE profile_id=? AND observed_at>=?",
                                    (profile_id, since)):
            for h in json.loads(raw or "[]"):
                hits.setdefault(str(h).lower(), set()).add(key)
        reactions = latest_valid_reactions(_reaction_rows(con, profile_id))
        # 반응 논문이 걸린 키워드 — 학습과 같은 기준으로 **반응 시각 이전의 마지막 관측** core_hits(외부 검토 2026-09-16:
        # 최신 관측을 쓰면 반응 뒤 키워드가 바뀐 논문의 반응이 다른 키워드로 옮겨 간다).
        per_kw_reactions: dict[str, Counter] = {}
        for r in reactions:
            row = con.execute("SELECT core_hits FROM candidate_observations WHERE profile_id=? AND paper_key=? AND observed_at<=? "
                              "ORDER BY observed_at DESC LIMIT 1", (profile_id, r["paper_key"], str(r["received_at"]))).fetchone()
            for kw in json.loads((row["core_hits"] if row else None) or "[]"):
                per_kw_reactions.setdefault(str(kw).lower(), Counter())[r["action"]] += 1

    rows = []
    for kind, terms in (("core", profile["core_topics"]), ("s2_seed", profile["s2_seeds"]),
                        ("target", profile["target_domain"]), ("exclude", profile["exclude"])):
        for term in sorted(terms, key=str.lower):
            hist = history.get(term, []) if kind == "core" else []
            weight = profile["core_weights"].get(term) if kind == "core" else None
            rc = per_kw_reactions.get(term.lower(), Counter()) if kind == "core" else Counter()
            rows.append({
                "term": term, "kind": kind,
                "weight": None if weight is None else round(weight, 3),
                "origin": origins.get((term.lower(), kind), "user"),
                "hits_28d": len(hits.get(term.lower(), ())) if kind in ("core", "target", "exclude") else None,
                "more": rc["more"], "useful": rc["useful"], "out": rc["out"],
                "first_weight": hist[0][2] if hist else None,
                "since_revision": hist[0][1] if hist else None,
                "history": hist,
            })
    return rows


# ── 보낸 메일·반응 ───────────────────────────────────────────────────────────
def issues_with_reactions(db: Path, profile_id: str, limit: int | None = None) -> list[dict]:
    """회차마다 논문 목록 + 논문별 유효 반응 수. 복원 회차(legacy)에는 반응이 붙지 않는다(회차 id 가 없다)."""
    issues = mail_ledger.list_issues(db, profile_id, limit)
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        rows = _reaction_rows(con, profile_id)
    by_issue_paper: dict[tuple[str, str], Counter] = {}
    for r in latest_valid_reactions(rows):
        by_issue_paper.setdefault((r["issue_id"], r["paper_key"]), Counter())[r["action"]] += 1
    for issue in issues:
        for it in issue["items"]:
            c = by_issue_paper.get((issue["issue_id"], it["paper_key"]), Counter())
            it["reactions"] = {"more": c["more"], "useful": c["useful"], "out": c["out"]}
        issue["reactions"] = sum(sum(it["reactions"].values()) for it in issue["items"])
    return issues


def reaction_log(db: Path, profile_id: str, limit: int = 200) -> list[dict]:
    """반응 한 건씩: 시각·논문 제목·반응·상태. 제목은 회차 표 → profile_shown 순으로 찾는다."""
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        rows = _reaction_rows(con, profile_id)[:limit]
        titles: dict[str, str] = {}
        for r in rows:
            if r["paper_key"] in titles:
                continue
            t = con.execute("SELECT title FROM mail_issue_items WHERE issue_id=? AND paper_key=?",
                            (r["issue_id"], r["paper_key"])).fetchone() if con.execute(
                "SELECT 1 FROM sqlite_master WHERE name='mail_issue_items'").fetchone() else None
            if not t:
                t = con.execute("SELECT title FROM profile_shown WHERE profile_id=? AND paper_key=?",
                                (profile_id, r["paper_key"])).fetchone()
            titles[r["paper_key"]] = (t[0] if t else None) or r["paper_key"]
    return [{"received_at": r["received_at"], "when": _kst(r["received_at"]), "title": titles[r["paper_key"]],
             "paper_key": r["paper_key"], "action": ACTION_LABELS.get(r["action"], r["action"]),
             "status": STATUS_LABELS.get(r["status"], r["status"]), "valid": r["status"] == feedback_links.STATUS_VALID,
             "issue_day": _kst_day(r["received_at"])} for r in rows]


# ── 변경 이력 ────────────────────────────────────────────────────────────────
def _snapshot_state(snapshot: str) -> tuple[dict[tuple[str, str], float | None], int | None]:
    try:
        snap = json.loads(snapshot)
    except (TypeError, ValueError):
        return {}, None
    state = {(kw, kind): (float(w) if (kind == "core" and w is not None) else (1.0 if kind == "core" else None))
             for kw, kind, w in snap.get("keywords") or []}
    return state, snap.get("max_items")


KIND_LABELS = {"core": "키워드", "s2_seed": "검색어", "target": "도메인", "exclude": "제외어"}


def revision_history(db: Path, profile_id: str, limit: int = 50) -> list[dict]:
    """revision 마다 누가(origin)·언제·무엇을 바꿨나 — 앞 revision 과의 차이(추가·삭제·가중치 변경)를 문장으로."""
    with sqlite3.connect(db) as con:
        rows = con.execute("SELECT revision, created_at, origin, note, snapshot FROM profile_revisions "
                           "WHERE profile_id=? ORDER BY revision", (profile_id,)).fetchall()
    out = []
    prev: dict[tuple[str, str], float | None] = {}
    for rev, created, origin, note, snapshot in rows:
        state, _max = _snapshot_state(snapshot)
        changes: list[str] = []
        for (kw, kind) in sorted(set(state) - set(prev), key=lambda k: (k[1], k[0].lower())):
            w = state[(kw, kind)]
            changes.append(f"+ {KIND_LABELS.get(kind, kind)} {kw}" + (f" {w:g}" if kind == "core" else ""))
        for (kw, kind) in sorted(set(prev) - set(state), key=lambda k: (k[1], k[0].lower())):
            changes.append(f"− {KIND_LABELS.get(kind, kind)} {kw}")
        for (kw, kind) in sorted(set(prev) & set(state), key=lambda k: k[0].lower()):
            if kind == "core" and prev[(kw, kind)] is not None and state[(kw, kind)] is not None \
                    and abs(prev[(kw, kind)] - state[(kw, kind)]) > 1e-9:
                changes.append(f"가중치 {kw} {prev[(kw, kind)]:g} → {state[(kw, kind)]:g}")
        out.append({"revision": int(rev), "created_at": created, "when": _kst(created), "origin": origin,
                    "origin_label": ORIGIN_LABELS.get(origin, origin), "note": display_text(note),
                    "changes": changes if prev else [f"초기 상태 · 키워드 {sum(1 for k in state if k[1] == 'core')}개"]})
        prev = state
    out.reverse()
    return out[:limit]


def agent_history(db: Path, profile_id: str, limit: int = 20) -> list[dict]:
    """주차별 에이전트 실행: Claude 제안·Codex 판정·적용·기각(사유)."""
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name='agent_runs'").fetchone():
            return []
        rows = [dict(r) for r in con.execute("SELECT * FROM agent_runs WHERE profile_id=? ORDER BY started_at DESC LIMIT ?",
                                             (profile_id, limit))]
    out = []
    for r in rows:
        def _j(col):
            try:
                return json.loads(r.get(col) or "null")
            except ValueError:
                return None
        proposal, judge = _j("proposal_json"), _j("judge_json")
        out.append({
            "week": r["week"], "status": r["status"], "error": r.get("error"), "when": _kst(r.get("started_at")),
            "base_revision": r.get("base_revision"), "new_revision": r.get("new_revision"),
            "proposed": (proposal or {}).get("actions") or [],
            "reviews": (judge or {}).get("reviews") or [],
            "final": (judge or {}).get("actions") or [],
            "applied": _j("applied_json") or [], "rejected": _j("rejected_json") or [],
            "reported_at": r.get("reported_at"),
        })
    return out


def agent_status_label(run: dict | None) -> str:
    if not run:
        return "아직 실행 없음"
    s = run.get("status")
    if s == "applied":
        return f"{run.get('week')} 변경 적용 (rev {run.get('new_revision')})"
    if s == "no_change":
        return f"{run.get('week')} 바꿀 것 없음"
    if s == "skipped_no_signal":
        return f"{run.get('week')} 반응 없어 건너뜀"
    if s == "failed":
        return f"{run.get('week')} 실패 — {agent_maintenance.FAIL_LABELS.get(run.get('error') or '', run.get('error') or '')}"
    return f"{run.get('week')} {s}"


# ── 시스템 페이지(2026-09-16) — 실행 로그·주간 작업·DB·백업·cron 을 한 화면에 ────────────────────
_BLOCK_START_RE = re.compile(r"^=== (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z 시작 \(pid (\d+)\) ===")
_BLOCK_END_RE = re.compile(r"^=== (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z (종료 \(exit (-?\d+)\)|수동 중지.*|스킵.*) ===")
_API_TOTAL_RE = re.compile(r"\[계측\] 실행 합계: API 호출 (\d+)회 — (.*)$")


def recent_runs(root: Path, limit: int = 10, log_name: str = "daily_scan.log") -> list[dict]:
    """새벽 실행 블록(시작~종료)을 최근 것부터. 소요·exit·경고 수·API 호출·프로필별 발송 결과(블록 끝 JSON)."""
    path = root / "logs" / log_name
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    runs: list[dict] = []
    cur: dict | None = None
    for ln in lines:
        m = _BLOCK_START_RE.match(ln)
        if m:
            cur = {"started_at": m.group(1) + "+00:00", "pid": int(m.group(2)), "finished_at": None, "exit": None,
                   "warnings": 0, "api_calls": None, "api_detail": "", "profiles": {}, "_json": []}
            runs.append(cur)
            continue
        if cur is None:
            m = _BLOCK_END_RE.match(ln)
            if m and m.group(2).startswith("스킵"):
                runs.append({"started_at": m.group(1) + "+00:00", "pid": None, "finished_at": m.group(1) + "+00:00", "exit": "skipped",
                             "warnings": 0, "api_calls": None, "api_detail": "", "profiles": {}, "_json": []})
            continue
        m = _BLOCK_END_RE.match(ln)
        if m and m.group(2).startswith("스킵"):
            # 스킵 줄은 **다른 실행**(겹친 두 번째 트리거)의 기록이다 — 돌고 있는 블록을 닫지 않는다. 그전엔 여기서 블록을 닫아 실제로
            # 1시간 넘게 돈 실행이 "0분 스킵"으로 보였다(2026-09-16 실측: 9/14 05:00).
            runs.append({"started_at": m.group(1) + "+00:00", "pid": None, "finished_at": m.group(1) + "+00:00", "exit": "skipped",
                         "warnings": 0, "api_calls": None, "api_detail": "", "profiles": {}, "_json": []})
            continue
        if m:
            cur["finished_at"] = m.group(1) + "+00:00"
            cur["exit"] = int(m.group(3)) if m.group(3) is not None else "stopped"
            cur = None
            continue
        if "[경고]" in ln:
            cur["warnings"] += 1
        a = _API_TOTAL_RE.search(ln)
        if a:
            cur["api_calls"], cur["api_detail"] = int(a.group(1)), a.group(2)
        cur["_json"].append(ln)
    for r in runs:
        text = "\n".join(r.pop("_json"))
        start = text.rfind("\n{")
        if start >= 0:
            try:
                data = json.loads(text[start + 1:])
                if isinstance(data, dict):
                    r["profiles"] = {k: v for k, v in data.items() if isinstance(v, dict)}
            except ValueError:
                pass
        if r["finished_at"]:
            try:
                s, e = datetime.fromisoformat(r["started_at"]), datetime.fromisoformat(r["finished_at"])
                r["minutes"] = round((e - s).total_seconds() / 60, 1)
            except ValueError:
                r["minutes"] = None
        else:
            r["minutes"] = None
        r["when"] = _kst(r["started_at"])
    runs.sort(key=lambda r: r["started_at"], reverse=True)
    return runs[:limit]


def cron_entries(root: Path) -> list[str] | None:
    """crontab 에서 이 저장소를 부르는 줄만. crontab 을 못 읽으면 None."""
    import subprocess
    try:
        out = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    key = str(root)
    return [ln.strip() for ln in out.stdout.splitlines() if key in ln and not ln.strip().startswith("#")]


DB_TABLES = ("papers", "summaries", "candidate_observations", "search_candidates", "profile_shown", "mail_issues",
             "feedback_events", "profile_revisions", "profile_keyword_events", "repro_results", "code_ladder",
             "agent_runs", "feedback_weight_runs", "retention_runs")


def db_status(db: Path) -> dict:
    """파일 크기·WAL 크기·표별 행 수·최신 백업 목록·마지막 보존 정리."""
    out: dict = {"path": str(db), "bytes": db.stat().st_size if db.exists() else 0,
                 "wal_bytes": (db.with_name(db.name + "-wal").stat().st_size if db.with_name(db.name + "-wal").exists() else 0),
                 "tables": {}, "backups": [], "retention": None}
    if not db.exists():
        return out
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as con:
        existing = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in DB_TABLES:
            out["tables"][t] = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] if t in existing else None
        if "retention_runs" in existing:
            r = con.execute("SELECT finished_at, applied, result_json, error FROM retention_runs ORDER BY finished_at DESC LIMIT 1").fetchone()
            if r:
                try:
                    res = json.loads(r[2] or "{}")
                    deleted = sum((v or {}).get("deleted", 0) for v in (res.get("tables") or {}).values() if isinstance(v, dict))
                    files = (res.get("files") or {}).get("count", 0)
                except ValueError:
                    deleted, files = None, None
                out["retention"] = {"finished_at": r[0], "when": _kst(r[0]), "applied": bool(r[1]), "rows_deleted": deleted,
                                    "files_deleted": files, "error": r[3]}
    bdir = db.parent / "backups"
    if bdir.is_dir():
        entries = []
        for p in bdir.iterdir():
            if p.is_file():
                st = p.stat()
                entries.append({"name": p.name, "bytes": st.st_size,
                                "when": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).astimezone(KST).strftime("%m-%d %H:%M")})
        out["backups"] = sorted(entries, key=lambda e: e["name"], reverse=True)[:8]
    return out


def log_tail(root: Path, name: str, lines: int = 200) -> str:
    path = root / "logs" / name
    if not path.exists():
        return ""
    try:
        return display_text("\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]))
    except OSError:
        return ""


def fmt_bytes(n: int | None) -> str:
    if n is None:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


# ── 논문 DB 페이지(2026-09-16) — 저장된 논문을 한 표로 ────────────────────────────────────────
def _norm_title(title: str | None) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", (title or "").lower()).split())


def paper_catalog(db: Path, query: str = "", profile_id: str | None = None, limit: int = 500) -> list[dict]:
    """저장된 논문 한 줄씩: 제목·발표일·저장일·출처·요약·재현·코드 단계·이 논문을 보낸 프로필. 최신 저장부터.

    어느 프로필이 보냈는지는 `profile_shown` 과 **arxiv_id 또는 정규화 제목**으로 잇는다 — 저널 논문(pdf-* 합성 ID)은 paper_key 가
    doi:/title: 형태라 arxiv_id 로는 안 이어진다(실측 2026-09-16: 224행 중 194행만 arxiv_id 로 이어졌다)."""
    q = (query or "").strip().lower()
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        existing = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        papers = [dict(r) for r in con.execute(
            "SELECT p.arxiv_id, p.title, p.published, p.fetched_at, p.source, p.abstract, p.is_retracted, p.injection_suspect, "
            "s.created_at AS summarized_at, s.engine FROM papers p LEFT JOIN summaries s ON s.arxiv_id = p.arxiv_id "
            "ORDER BY p.fetched_at DESC")]
        shown: dict[str, list[tuple[str, str]]] = {}
        shown_by_title: dict[str, list[tuple[str, str]]] = {}
        for pid, key, title, at in con.execute("SELECT profile_id, paper_key, title, shown_at FROM profile_shown"):
            shown.setdefault(key, []).append((pid, at))
            shown_by_title.setdefault(_norm_title(title), []).append((pid, at))
        repro: dict[str, dict] = {}
        if "repro_results" in existing:
            for aid, success, stage in con.execute("SELECT arxiv_id, success, stage FROM repro_results"):
                cur = repro.setdefault(aid, {"success": False, "attempts": 0, "stage": stage})
                cur["attempts"] += 1
                cur["success"] = cur["success"] or bool(success)
        ladder = {r[0]: (r[1], r[2]) for r in con.execute("SELECT arxiv_id, tier, full_name FROM code_ladder")} \
            if "code_ladder" in existing else {}
    out = []
    for p in papers:
        delivered = shown.get(p["arxiv_id"]) or shown_by_title.get(_norm_title(p["title"])) or []
        profiles = sorted({pid for pid, _ in delivered})
        if profile_id and profile_id not in profiles:
            continue
        if q and q not in (p["title"] or "").lower() and q not in (p["abstract"] or "").lower() and q not in (p["arxiv_id"] or "").lower():
            continue
        r = repro.get(p["arxiv_id"])
        tier = ladder.get(p["arxiv_id"])
        out.append({
            "arxiv_id": p["arxiv_id"], "title": p["title"] or "", "published": (p["published"] or "")[:10],
            "fetched": _kst_day(p["fetched_at"]), "source": "arXiv" if not p["source"] else ("업로드 PDF" if "manual" in p["source"] else "저널(OA)"),
            "summarized": bool(p["summarized_at"]), "engine": p["engine"] or "",
            "repro": "—" if not r else ("성공" if r["success"] else f"실패({r['stage']})"),
            "code_tier": tier[0] if tier else None, "code_repo": tier[1] if tier else None,
            "profiles": profiles, "first_sent": _kst_day(min((at for _, at in delivered), default=None)) if delivered else "—",
            "flags": [f for f, on in (("철회", p["is_retracted"] == 1), ("인젝션 의심", bool(p["injection_suspect"]))) if on],
        })
        if len(out) >= limit:
            break
    return out


def paper_detail(db: Path, arxiv_id: str) -> dict | None:
    """논문 하나: 초록·요약 본문(마크다운)·재현 시도·코드 단계·SOTA 주장."""
    import code_ladder
    import sota_claims
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        p = con.execute("SELECT * FROM papers WHERE arxiv_id=?", (arxiv_id,)).fetchone()
        if not p:
            return None
        s = con.execute("SELECT path, engine, created_at, coverage_ratio FROM summaries WHERE arxiv_id=?", (arxiv_id,)).fetchone()
        repro = [dict(r) for r in con.execute(
            "SELECT repo_url, source, success, stage, fail_detail, created_at FROM repro_results WHERE arxiv_id=? ORDER BY created_at",
            (arxiv_id,))]
    summary = ""
    if s and s["path"]:
        try:
            summary = Path(s["path"]).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            summary = ""
    try:
        ladder = code_ladder.mail_line(arxiv_id, db) or ("공식 코드" if (code_ladder.get(arxiv_id, db) or {}).get("tier") == "official" else "")
    except Exception:  # noqa: BLE001 — 표가 없는 DB 에서도 상세는 보인다
        ladder = ""
    claims, _src = sota_claims.claims_for(db, arxiv_id)
    import digest
    return {"arxiv_id": arxiv_id, "title": p["title"], "abstract": p["abstract"] or "", "published": p["published"],
            "link": digest.paper_link(dict(p)), "summary_md": summary, "summary_engine": s["engine"] if s else None,
            "repro": repro, "code_line": ladder, "sota_line": sota_claims.mail_line(claims)}


_HTTP_ERR_RE = re.compile(r"'(\d{3}) ([^']{1,40})' for url '(https?://[^/']+)")


def short_error(detail: str | None, limit: int = 80) -> str:
    """검색 실패 사유를 한 줄로. httpx 오류는 URL 전체(검색어 수백 자)가 붙어 화면을 밀어낸다(2026-09-16 사용자 지적) —
    "429 Unknown Error · export.arxiv.org" 처럼 코드·호스트만 남긴다."""
    if not detail:
        return ""
    m = _HTTP_ERR_RE.search(detail)
    if m:
        return f"{m.group(1)} {m.group(2).strip()} · {m.group(3).split('//', 1)[1]}"
    one = " ".join(str(detail).split())
    return one if len(one) <= limit else one[:limit].rstrip() + "…"
