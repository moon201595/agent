"""② 선별 — 사용자 반응으로 키워드 가중치를 매일 자동 조정한다(결정적, LLM 없음)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import feedback_links
import research_profile


ACTION_VALUES: dict[str, float] = {"more": 1.0, "useful": 0.7, "out": -1.0}
HALF_LIFE_DAYS = 90.0
# 키워드 하나가 움직이려면 **서로 다른 논문 2편 이상**에 반응이 있어야 한다(2026-09-16 사용자 결정). 클릭 한 번에 계층이
# 한 칸 오르면 프로필이 클릭에 흔들린다 — 한 편은 그 논문이 좋았다는 뜻이지 그 키워드가 좋다는 뜻이 아니다.
MIN_DISTINCT_PAPERS = 2
KST = timezone(timedelta(hours=9))


def _ddl(con: sqlite3.Connection) -> None:
    """피드백 가중치의 기준선과 일일 적용 이력을 만든다."""
    con.execute(
        "CREATE TABLE IF NOT EXISTS feedback_weight_base ("
        " profile_id TEXT NOT NULL,"
        " keyword TEXT NOT NULL,"
        " base_weight REAL NOT NULL,"
        " set_at TEXT NOT NULL,"
        " source TEXT NOT NULL,"
        " PRIMARY KEY (profile_id, keyword))"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS feedback_weight_runs ("
        " profile_id TEXT NOT NULL,"
        " run_date TEXT NOT NULL,"
        " created_at TEXT NOT NULL,"
        " revision INTEGER NOT NULL,"
        " changes_json TEXT NOT NULL,"
        " skipped_no_observation INTEGER NOT NULL,"
        " reactions_used INTEGER NOT NULL,"
        " PRIMARY KEY (profile_id, run_date))"
    )


def init_db(db: Path) -> None:
    """DDL은 schema_guard를 통해서만 적용하거나 검증한다."""
    import schema_guard

    schema_guard.ensure(db, _ddl, "feedback_weights")


def _utc(value: datetime) -> datetime:
    """시간대가 없는 입력은 기존 저장소의 UTC 관행으로 해석한다."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return _utc(parsed)


def _keyword_key(keyword: Any) -> str:
    """profile_scoring의 대소문자 무시 매칭과 profile 이벤트의 소문자 키를 따른다."""
    return str(keyword).strip().lower()


def _run_date(now: datetime) -> str:
    return _utc(now).astimezone(KST).strftime("%Y-%m-%d")


def _latest_reactions(rows: list[dict]) -> list[dict]:
    """한 회차·수신자·논문 조합에서 가장 늦은 유효 반응만 남긴다."""
    chosen: dict[tuple[str, str, str], tuple[datetime, str, dict]] = {}
    for row in rows:
        received = _parse_time(row.get("received_at"))
        action = str(row.get("action") or "")
        if received is None or action not in ACTION_VALUES:
            continue
        key = (str(row.get("issue_id") or ""), str(row.get("recipient_hash") or ""),
               str(row.get("paper_key") or ""))
        tie = str(row.get("received_at") or "")
        old = chosen.get(key)
        candidate = (received, tie, row)
        if old is None or (received, tie, action) >= (old[0], old[1], str(old[2].get("action") or "")):
            chosen[key] = candidate
    return [item[2] for item in sorted(chosen.values(), key=lambda item: (item[0], item[1]))]


def _observation_features(
    con: sqlite3.Connection, profile_id: str, paper_key: str, received_at: datetime,
) -> list[str] | None:
    """반응 시각까지 존재한 가장 최신 관측의 core_hits를 복원한다."""
    rows = con.execute(
        "SELECT scan_id, observed_at, core_hits FROM candidate_observations "
        "WHERE profile_id=? AND paper_key=?",
        (profile_id, paper_key),
    ).fetchall()
    eligible: list[tuple[datetime, str, Any]] = []
    for scan_id, observed_at, core_hits in rows:
        observed = _parse_time(observed_at)
        if observed is not None and observed < received_at:
            eligible.append((observed, str(scan_id), core_hits))
    if not eligible:
        return None
    _observed, _scan_id, raw_hits = max(eligible, key=lambda item: (item[0], item[1]))
    try:
        hits = raw_hits if isinstance(raw_hits, list) else json.loads(raw_hits or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [str(hit) for hit in hits] if isinstance(hits, list) else []


def _source_revision(source: str) -> int:
    try:
        return int(str(source).rsplit(":", 1)[-1])
    except (TypeError, ValueError):
        return -1


def _latest_non_feedback_weights(con: sqlite3.Connection, profile_id: str) -> dict[str, tuple[int, float]]:
    """키워드별로 **비-피드백 revision 이 그 키워드의 가중치를 직접 정한** 마지막 시점 (revision, 가중치).

    "직접 정했다" = 그 revision 에서 키워드가 새로 들어왔거나, 바로 앞 revision(피드백 포함)과 값이 다르다.
    마지막 비-피드백 revision 의 값을 그대로 쓰면 안 된다(2026-09-15 검토) — 사람·에이전트가 **다른** 키워드만
    고쳐도 스냅샷에는 피드백으로 이미 올라간 값이 실려 있어서, 그 값이 새 기준선이 되고 같은 반응이 한 번 더
    더해진다. 주간 에이전트가 매주 revision 을 만들면 매주 오르는 래칫이었다(1.337 → 1.657 재현)."""
    out: dict[str, tuple[int, float]] = {}
    previous: dict[str, float] = {}
    rows = con.execute(
        "SELECT revision, origin, snapshot FROM profile_revisions WHERE profile_id=? ORDER BY revision",
        (profile_id,),
    ).fetchall()
    for revision, origin, raw_snapshot in rows:
        try:
            snapshot = json.loads(raw_snapshot)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        current: dict[str, float] = {}
        for keyword, kind, weight in snapshot.get("keywords") or []:
            if kind == "core":
                current[_keyword_key(keyword)] = float(weight if weight is not None else 1.0)
        if origin != "feedback":
            for key, weight in current.items():
                if key not in previous or abs(previous[key] - weight) > 1e-9:
                    out[key] = (int(revision), weight)
        previous = current
    return out


def _prepare_bases(
    con: sqlite3.Connection, profile_id: str, current_weights: dict[str, float],
    handled: set[str], now: datetime,
) -> tuple[dict[str, float], list[tuple[str, float, str]]]:
    """반영 대상의 기준선을 읽고, 직접 수정된 revision을 반영할 준비를 한다."""
    rows = con.execute(
        "SELECT keyword, base_weight, source FROM feedback_weight_base WHERE profile_id=?",
        (profile_id,),
    ).fetchall()
    bases = {_keyword_key(keyword): (str(keyword), float(base), str(source))
             for keyword, base, source in rows}
    last_direct = _latest_non_feedback_weights(con, profile_id)
    values: dict[str, float] = {}
    updates: list[tuple[str, float, str]] = []
    current_by_key = {_keyword_key(keyword): keyword for keyword in current_weights}
    current_revision = max((revision for revision, _weight in last_direct.values()), default=0)
    for key in sorted(handled):
        keyword = current_by_key[key]
        current_weight = float(current_weights[keyword])
        existing = bases.get(key)
        if existing is None:
            direct_revision = last_direct.get(key, (current_revision, current_weight))[0]
            base = current_weight
            source = f"initial:revision:{direct_revision}"
            updates.append((keyword, base, source))
        else:
            _stored_keyword, base, source = existing
            direct = last_direct.get(key)
            if direct is not None and direct[0] > _source_revision(source):
                direct_weight = direct[1]
                base = direct_weight
                source = f"revision:{direct[0]}"
                updates.append((keyword, base, source))
        values[key] = base
    return values, updates


def _save_base_and_run(
    db: Path, profile_id: str, run_date: str, created_at: str, revision: int,
    base_updates: list[tuple[str, float, str]], changes: list[dict],
    skipped: int, reactions_used: int,
) -> dict:
    """기준선과 일일 이력을 하나의 SQLite 트랜잭션으로 남긴다."""
    with sqlite3.connect(db) as con:
        for keyword, base, source in base_updates:
            con.execute(
                "INSERT INTO feedback_weight_base (profile_id, keyword, base_weight, set_at, source) "
                "VALUES (?,?,?,?,?) ON CONFLICT(profile_id, keyword) DO UPDATE SET "
                "base_weight=excluded.base_weight, set_at=excluded.set_at, source=excluded.source",
                (profile_id, keyword, base, created_at, source),
            )
        con.execute(
            "INSERT INTO feedback_weight_runs (profile_id, run_date, created_at, revision, changes_json, "
            "skipped_no_observation, reactions_used) VALUES (?,?,?,?,?,?,?)",
            (profile_id, run_date, created_at, revision, json.dumps(changes, ensure_ascii=False, sort_keys=True),
             skipped, reactions_used),
        )
    status = "updated" if changes else ("no_reactions" if reactions_used == 0 else "no_change")
    return {"status": status, "changes": changes, "revision": revision,
            "skipped_no_observation": skipped, "reactions_used": reactions_used}


def _result_from_run(row: sqlite3.Row, already_ran: bool = False) -> dict:
    try:
        changes = json.loads(row["changes_json"])
    except (TypeError, ValueError, json.JSONDecodeError):
        changes = []
    return {
        "status": "already_ran" if already_ran else (
            "updated" if changes else ("no_reactions" if row["reactions_used"] == 0 else "no_change")),
        "changes": changes,
        "revision": row["revision"],
        "skipped_no_observation": row["skipped_no_observation"],
        "reactions_used": row["reactions_used"],
    }


def update_profile(db: Path, profile_id: str, now: datetime | None = None) -> dict:
    """한 KST 날짜에 한 번, 유효 반응을 해당 프로필의 가중치에 적용한다."""
    init_db(db)
    now_utc = _utc(now or _now())
    run_date = _run_date(now_utc)
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        existing_run = con.execute(
            "SELECT * FROM feedback_weight_runs WHERE profile_id=? AND run_date=?",
            (profile_id, run_date),
        ).fetchone()
    if existing_run is not None:
        return _result_from_run(existing_run, already_ran=True)

    profile = research_profile.get_profile(db, profile_id)
    if profile is None:
        raise ValueError(f"unknown_profile:{profile_id}")
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        config = con.execute(
            "SELECT name, max_items, schedule_frequency, schedule_time "
            "FROM profiles WHERE profile_id=?", (profile_id,)
        ).fetchone()
        current_revision = con.execute(
            "SELECT COALESCE(MAX(revision), 0) FROM profile_revisions WHERE profile_id=?", (profile_id,)
        ).fetchone()[0]
        reactions = _latest_reactions(feedback_links.valid_reactions(db, profile_id))
        current_weights = {str(k): float(v) for k, v in (profile.get("core_weights") or {}).items()}
        current_by_key = {_keyword_key(keyword): keyword for keyword in profile.get("core_topics") or []}
        pos_neg: dict[str, list[float]] = {}
        papers_of: dict[str, set[str]] = {}
        skipped = 0
        reactions_used = 0
        for reaction in reactions:
            received = _parse_time(reaction.get("received_at"))
            features = _observation_features(con, profile_id, str(reaction.get("paper_key") or ""), received)
            if features is None:
                skipped += 1
                continue
            if not features:
                continue
            active_features = [key for key in (_keyword_key(hit) for hit in features) if key in current_by_key]
            if not active_features:
                continue
            reactions_used += 1
            # 지금 남아 있는 키워드끼리 나눈다 — 지워진 키워드 몫으로 반응이 새지 않게(2026-09-15 검토).
            share = ACTION_VALUES[str(reaction["action"])] / len(active_features)
            days = max(0.0, (now_utc - received).total_seconds() / 86400.0)
            decay = 0.5 ** (days / HALF_LIFE_DAYS)
            for key in active_features:
                papers_of.setdefault(key, set()).add(str(reaction.get("paper_key") or ""))
                values = pos_neg.setdefault(key, [0.0, 0.0])
                amount = share * decay
                if amount >= 0:
                    values[0] += amount
                else:
                    values[1] += -amount
        bases, base_updates = _prepare_bases(con, profile_id, current_weights, set(pos_neg), now_utc)

    new_weights = dict(current_weights)
    changes: list[dict] = []
    for key in sorted(pos_neg):
        keyword = current_by_key[key]
        if len(papers_of.get(key, ())) < MIN_DISTINCT_PAPERS:
            continue
        pos, neg = pos_neg[key]
        signal = (pos - neg) / (pos + neg + 4.0)
        target = min(2.0, max(0.35, bases[key] + 0.8 * signal))
        before = float(current_weights[keyword])
        moved = before + min(0.1, max(-0.1, target - before))
        after = round(moved, 3)
        if abs(after - before) < 0.005:
            continue
        new_weights[keyword] = after
        changes.append({
            "keyword": keyword,
            "before": round(before, 3),
            "after": after,
            "target": round(target, 6),
            "signal": round(signal, 6),
            "pos": round(pos, 6),
            "neg": round(neg, 6),
        })

    revision = current_revision
    if changes:
        if config is None:
            raise ValueError(f"profile_config_missing:{profile_id}")
        note = "feedback: " + ", ".join(
            f"{change['keyword']} {change['before']:.3f}->{change['after']:.3f}" for change in changes
        )
        revision = research_profile.create_profile(
            db, profile_id, config["name"] or profile_id,
            core_topics=list(profile.get("core_topics") or []),
            target_domain=list(profile.get("target_domain") or []),
            exclude=list(profile.get("exclude") or []),
            venues=list(profile.get("venues") or []),
            max_items=int(config["max_items"] or 8),
            schedule_frequency=config["schedule_frequency"] or "daily",
            schedule_time=config["schedule_time"] or "05:00",
            core_weights=new_weights,
            s2_seeds=list(profile.get("s2_seeds") or []),
            origin="feedback",
            note=note,
            expected_revision=current_revision,
        )
    return _save_base_and_run(
        db, profile_id, run_date, now_utc.isoformat(), revision,
        base_updates, changes, skipped, reactions_used,
    )


def update_all(db: Path, profile_ids: list[str], now: datetime | None = None) -> dict[str, dict]:
    """여러 프로필을 독립적으로 갱신하고 프로필별 결과를 돌려준다."""
    return {profile_id: update_profile(db, profile_id, now=now) for profile_id in profile_ids}
