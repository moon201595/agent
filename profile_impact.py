"""⑧ 축적 — 프로필 변경 하나의 **적용 전 영향 분석**(C단계, 2026-09-11).

docs/ASTRA_PLAN_2026-09-10.md §7. 순수 계산이다 — LLM 없음, 네트워크 없음,
검색·요약·배달을 부르지 않는다(규칙 6). 입력은 (고정 스냅샷, 프로필 before,
프로필 after, K), 출력은 diff·영향·게이트 상태다. **적용하지 않는다** —
`applied` 는 D단계의 몫이다.

게이트 임계값(최대 변경량·상위 K 이탈 한도·위험률 한도)은 **미설정으로
시작한다**(외부 점검 2026-09-11). A단계의 진입 3·이탈 3 은 정렬 정책을
교체한 결과라 "프로필만 바꿨을 때 허용 이탈률"의 근거가 못 된다. 설정이
없는 변경안은 `insufficient_evidence`(`apply_rules_unconfigured`)로 끝난다 —
기존 프로필 유지가 기본이다(§9.4).

결과에 `priority` 를 넣지 않는다. `recency_score` 가 현재 시각을 쓰므로 같은
입력을 내일 돌리면 값이 달라진다. 순위 계약(rank_key)은 시각 무관이라
순위·적중·적격성만 투영한다.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import profile_scoring
import research_profile
import s2_delta

# ── 게이트 상태 (§7.5 여섯 + `held`)
INVALID = "invalid"
INSUFFICIENT = "insufficient_evidence"
NEEDS_SHADOW = "needs_shadow_search"
HELD = "held"                      # 필수 조건이 설정돼 있고 측정값이 한도를 넘음
VALID = "valid"                    # 정규화 후 실제 변경 없음 — 적용 대상 아님
ELIGIBLE = "eligible_for_apply"

IMPACT_VERSION = "impact-v1"

# 자동 적용 조건. **None 은 미설정**이다. 값이 들어가면 `held` 판정이 살아난다.
DEFAULT_APPLY_RULES: dict = {
    "max_core_changes": None,      # 한 변경안이 바꿀 수 있는 core 항목 수
    "max_topk_left_ratio": None,   # 상위 K 이탈 / K
    "max_exclude_risk": None,      # 제외어 위험률 상한
}

ALLOWED_FIELDS = {"core_topics", "core_weights", "target_domain", "exclude", "s2_seeds", "max_items"}


# ── 스냅샷 ────────────────────────────────────────────────────────────────
def snapshot(db: Path, profile_id: str, start: datetime, end: datetime) -> dict:
    """관측에서 논문 단위 고정 스냅샷을 뽑는다.

    같은 논문의 관측이 여럿이면 `(observed_at, scan_id)` 가 가장 뒤인 행의
    메타데이터를 쓴다(동률 규칙을 고정한다 — 입력 순서에 기대지 않는다).
    발견 출처·씨앗은 기간 내 관측의 **합집합**이다 — 최신 행 값만 쓰면 이전
    발견 경로가 사라진다.

    초록은 `abstract_ref` 를 따라 **보유 행에서 직접** 복원한다. 참조 대상이
    기간 밖이어도 된다(복원에만 쓰고 편수에는 안 넣는다). 해시가 안 맞거나
    참조가 없거나 참조 대상도 초록이 없으면 **손상**으로 표시하고 개체
    테이블(search_candidates)로 대체하지 않는다 — 그 열은 재수집 때 덮어써진다.

    snapshot_id 는 **내용 해시**다. 키 목록만 해시하면 초록·날짜가 바뀌어도
    같은 ID 가 나와 재현을 보장 못 한다.
    """
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM candidate_observations WHERE profile_id=? AND observed_at >= ? "
            "AND observed_at < ? ORDER BY observed_at, scan_id, paper_key",
            (profile_id, start.isoformat(), end.isoformat()))]
        scans = con.execute(
            "SELECT count(*), sum(observations IS NOT NULL), sum(observation_error IS NOT NULL) "
            "FROM scan_runs WHERE profile_id=? AND started_at >= ? AND started_at < ?",
            (profile_id, start.isoformat(), end.isoformat())).fetchone()

        by: dict[str, dict] = {}
        for r in rows:               # 정렬돼 있으므로 뒤 행이 앞 행을 덮는다 = 최신 우선
            cur = by.setdefault(r["paper_key"], {"sources": set(), "seeds": set()})
            cur["row"] = r
            srcs = json.loads(r["retrieval_sources"]) if r["retrieval_sources"] else [r["source"]]
            cur["sources"].update(s for s in srcs if s)
            cur["seeds"].update(json.loads(r["s2_seeds"]) if r["s2_seeds"] else [])

        papers, corrupt, title_only = [], [], 0
        for key, cur in by.items():
            r = cur["row"]
            abstract, status = _restore_abstract(con, r, not_after=end.isoformat())
            if status in ("corrupt", "future_ref"):
                corrupt.append(key)
            if not abstract:
                title_only += 1
            papers.append({
                "_paper_key": key, "title": r["title"] or "", "abstract": abstract or "",
                "published": r["published"], "source": r["source"],
                "retrieval_sources": sorted(cur["sources"]), "s2_seeds": sorted(cur["seeds"]),
                "abstract_status": status,
            })
    papers.sort(key=lambda p: p["_paper_key"])
    canon = json.dumps({
        "profile_id": profile_id, "start": start.isoformat(), "end": end.isoformat(),
        "papers": [{k: p[k] for k in ("_paper_key", "title", "abstract", "published",
                                      "retrieval_sources", "s2_seeds")} for p in papers],
        # 손상 목록도 내용이다 — 같은 텍스트라도 손상 표시가 있으면 게이트 결과가 다르므로
        # 옛 정상 분석을 재사용하면 안 된다(2026-09-12).
        "abstract_corrupt": sorted(corrupt),
        "impact_version": IMPACT_VERSION,
    }, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    return {
        "snapshot_id": digest[:12], "content_sha256": digest,
        "profile_id": profile_id, "start": start.isoformat(), "end": end.isoformat(),
        "papers": papers, "paper_count": len(papers),
        "title_only": title_only, "abstract_corrupt": corrupt,
        "scans": {"total": scans[0] or 0, "observed": scans[1] or 0, "failed": scans[2] or 0},
    }


def _restore_abstract(con, row: dict, not_after: str | None = None) -> tuple[str | None, str]:
    """returns (초록, 상태) — 상태는 'stored' | 'restored' | 'empty' | 'corrupt' | 'future_ref'.
    `not_after` 를 주면 참조 대상 관측이 그 시각 뒤일 때 **미래 참조**로 거부한다 — 과거
    재생에서 이후에 관측한 초록을 끌어오면 시점 누수다(외부 점검 2026-09-11)."""
    if row["abstract"]:
        return row["abstract"], "stored"
    if not row["abstract_sha"]:
        return None, "empty"        # 원래 초록이 없던 관측 — 손상이 아니다
    ref = row["abstract_ref"]
    if not ref:
        return None, "corrupt"
    holder = con.execute(
        "SELECT abstract, observed_at FROM candidate_observations WHERE scan_id=? AND paper_key=? "
        "AND profile_id=? AND abstract IS NOT NULL", (ref, row["paper_key"], row["profile_id"])).fetchone()
    if not holder:
        return None, "corrupt"      # 참조 대상이 없거나 초록을 안 갖고 있다(사슬)
    if not_after and holder[1] >= not_after:
        return None, "future_ref"   # 참조 대상이 cutoff 뒤 — 누수
    text = holder[0]
    if hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] != row["abstract_sha"]:
        return None, "corrupt"
    return text, "restored"


# ── 프로필 diff ───────────────────────────────────────────────────────────
def _norm(profile: dict) -> dict:
    return {
        "core_topics": sorted({k.strip() for k in profile.get("core_topics") or [] if k and k.strip()}),
        "core_weights": {k.strip(): float(v) for k, v in (profile.get("core_weights") or {}).items()},
        "target_domain": sorted({k.strip() for k in profile.get("target_domain") or [] if k and k.strip()}),
        "exclude": sorted({k.strip() for k in profile.get("exclude") or [] if k and k.strip()}),
        "s2_seeds": sorted({k.strip() for k in profile.get("s2_seeds") or [] if k and k.strip()}),
        "max_items": int(profile.get("max_items") or 0),
    }


def profile_hash(profile: dict) -> str:
    return hashlib.sha256(json.dumps(_norm(profile), sort_keys=True, ensure_ascii=False)
                          .encode("utf-8")).hexdigest()[:12]


def diff_profiles(before: dict, after: dict) -> dict:
    """구조 검사 + 변경 목록. `errors` 가 비어 있지 않으면 invalid 다.
    **diff 가 설명하는 것과 자동 적용을 허용하는 것은 다르다** — 제외어·도메인
    변경, core 삭제는 diff 로 보이지만 §8.2 허용 액션이 아니라 `disallowed` 에 든다."""
    errors: list[str] = []
    unknown = set(after) - ALLOWED_FIELDS - {"profile_id", "name", "venues"}
    if unknown:
        errors.append(f"unknown_fields:{sorted(unknown)}")
    a, b = _norm(before), _norm(after)
    if not b["core_topics"]:
        errors.append("empty_core")
    if b["max_items"] <= 0:
        errors.append("invalid_max_items")
    for k, v in b["core_weights"].items():
        if not (v == v and abs(v) != float("inf")) or v < 0:
            errors.append(f"bad_weight:{k}")
    if set(b["core_weights"]) - set(b["core_topics"]):
        errors.append("weight_without_core")
    added = sorted(set(b["core_topics"]) - set(a["core_topics"]))
    removed = sorted(set(a["core_topics"]) - set(b["core_topics"]))
    tier_changes = {k: (a["core_weights"].get(k, 1.0), b["core_weights"].get(k, 1.0))
                    for k in set(a["core_topics"]) & set(b["core_topics"])
                    if a["core_weights"].get(k, 1.0) != b["core_weights"].get(k, 1.0)}
    # **실효** S2 질의 비교 — 명시 씨앗이 비면 가중치 폴백이라 계층 변경만으로도 바뀐다
    seeds_before = sorted(s2_delta.keywords_for_s2(before))
    seeds_after = sorted(s2_delta.keywords_for_s2(after))
    disallowed = []
    if removed: disallowed.append("core_removed")
    if a["exclude"] != b["exclude"]: disallowed.append("exclude_changed")
    if a["target_domain"] != b["target_domain"]: disallowed.append("domain_changed")
    if a["max_items"] != b["max_items"]: disallowed.append("max_items_changed")
    no_change = (a == b)
    return {
        "errors": errors, "added_core": added, "removed_core": removed,
        "tier_changes": tier_changes,
        "seed_changed": seeds_before != seeds_after,
        "effective_seeds": {"before": seeds_before, "after": seeds_after},
        "arxiv_query_changed": bool(added or removed),
        "disallowed": disallowed, "no_change": no_change,
        "before_hash": profile_hash(before), "after_hash": profile_hash(after),
    }


# ── 영향 ─────────────────────────────────────────────────────────────────
def _project(papers: list[dict], profile: dict, k: int) -> dict:
    """결정적 필드만 — 순위·적중·적격성. priority/recency 는 안 넣는다."""
    out = profile_scoring.score_and_rank(papers, profile)
    weights = profile.get("core_weights") or {}
    table = profile_scoring.tier_table(profile)
    ranked = {}
    for pos, p in enumerate(out["papers"], start=1):
        sc = p["_score"]
        ranked[p["_paper_key"]] = {
            "rank": pos, "core_hits": list(sc["core_hits"]),
            "top_weight": max(float(weights.get(h, 1.0)) for h in sc["core_hits"]),
            "tier_rank": sc["tier_rank"], "domain_hits": len(sc["domain_hits"]),
        }
    excluded, unmatched = set(), set()
    for p in papers:
        if p["_paper_key"] in ranked:
            continue
        r = profile_scoring.score_paper(p, profile)
        (excluded if r["excluded"] else unmatched).add(p["_paper_key"])
    return {"ranked": ranked, "excluded": excluded, "unmatched": unmatched,
            "topk": [p["_paper_key"] for p in out["papers"][:k]], "tier_table": table}


def _core_eligible_before_exclude(papers: list[dict], profile: dict) -> set[str]:
    """제외 필터 **전** core 적격 집합 — 위험률 분모용(외부 점검 §7.3 정의)."""
    no_ex = dict(profile, exclude=[])
    return {p["_paper_key"] for p in papers if profile_scoring.score_paper(p, no_ex)["core_hits"]}


def impact(snap: dict, before: dict, after: dict, k: int,
           consumed_keys: set[str] | None = None) -> dict:
    """같은 후보·같은 정책·같은 K 로 전후 재채점. consumed_keys 를 주면 그 집합을
    **전후 동일하게** 뺀 '전달 후보 분석'도 함께 낸다 — 운영은 이미 보낸 논문을
    먼저 빼므로 전체 분석의 상위 K 를 '다음 메일 변경'이라 부르면 안 된다."""
    if k <= 0:
        raise ValueError("K must be positive")
    papers = snap["papers"]
    a, b = _project(papers, before, k), _project(papers, after, k)
    keys_a, keys_b = set(a["ranked"]), set(b["ranked"])
    gained = sorted(keys_b - keys_a)
    lost = {key: ("exclude_hit" if key in b["excluded"] else "no_core_hit") for key in sorted(keys_a - keys_b)}
    union_topk = sorted(set(a["topk"]) | set(b["topk"]))
    rank_moves = {key: {"before": a["ranked"].get(key, {}).get("rank"),
                        "after": b["ranked"].get(key, {}).get("rank")} for key in union_topk}
    # 가중치 자체의 변화 vs 계층 번호 변화 — 번호만으로 "강등"이라 쓰지 않는다
    weight_moves = {}
    for key in keys_a & keys_b:
        wa, wb = a["ranked"][key]["top_weight"], b["ranked"][key]["top_weight"]
        ta, tb = a["ranked"][key]["tier_rank"], b["ranked"][key]["tier_rank"]
        if wa != wb or ta != tb:
            weight_moves[key] = {"weight": (wa, wb), "tier_rank": (ta, tb),
                                 "hits": (a["ranked"][key]["core_hits"], b["ranked"][key]["core_hits"])}
    # 제외어 위험률: 제외 전 core 적격의 전후 차집합 G 중 after 제외어에 맞는 비율
    g = _core_eligible_before_exclude(papers, after) - _core_eligible_before_exclude(papers, before)
    by_key = {p["_paper_key"]: p for p in papers}
    hit = [key for key in g if profile_scoring.score_paper(by_key[key], after)["excluded"]]
    risk = {"numerator": len(hit), "denominator": len(g),
            "rate": (len(hit) / len(g)) if g else None,
            "note": None if g else "no_new_core_eligible_candidates", "examples": sorted(hit)[:5]}
    # 새 core 별 유효 적중 — 묶음 합계로 0편 용어를 숨기지 않는다
    added_hits = {}
    for term in sorted(set(after.get("core_topics") or []) - set(before.get("core_topics") or [])):
        probe = {"core_topics": [term], "core_weights": {term: 1.0}, "target_domain": [], "exclude": []}
        added_hits[term] = sum(1 for p in papers if profile_scoring.score_paper(p, probe)["core_hits"])
    # 출처 × 날짜 정밀도 × 이동
    cross: dict[str, dict] = {}
    for key in union_topk:
        p = by_key[key]
        _, prec = profile_scoring.publication_day(p.get("published"))
        cell = cross.setdefault(f"{p.get('source')}|{prec}", {"entered": 0, "left": 0, "stayed": 0})
        ia, ib = key in a["topk"], key in b["topk"]
        cell["entered" if (ib and not ia) else "left" if (ia and not ib) else "stayed"] += 1
    result = {
        "k": k, "eligible_before": len(keys_a), "eligible_after": len(keys_b),
        "eligible_gained": gained, "eligible_lost": lost,
        "topk_entered": [key for key in b["topk"] if key not in a["topk"]],
        "topk_left": [key for key in a["topk"] if key not in b["topk"]],
        "rank_moves": rank_moves, "weight_moves": weight_moves,
        "tier_table": {"before": a["tier_table"], "after": b["tier_table"]},
        "exclude_risk": risk, "added_core_hits": added_hits,
        "source_x_precision": cross,
        "title_only": snap["title_only"], "abstract_corrupt": len(snap["abstract_corrupt"]),
    }
    if consumed_keys is not None:
        rest = [p for p in papers if p["_paper_key"] not in consumed_keys]
        sub = dict(snap, papers=rest)
        result["delivery_view"] = {
            "consumed": len(consumed_keys),
            "topk_before": _project(rest, before, k)["topk"],
            "topk_after": _project(rest, after, k)["topk"],
        } if rest else {"consumed": len(consumed_keys), "note": "no_unconsumed_candidates"}
    return result


# ── 게이트 ────────────────────────────────────────────────────────────────
# shadow 검색 결과를 읽는 규칙(⑦, §8-100). DEFAULT_APPLY_RULES 와 같은 이유로 None 으로 시작 —
# 첫 실험 결과를 보고 숫자를 정한다. None 이면 shadow 가 있어도 insufficient_evidence.
DEFAULT_SHADOW_RULES: dict = {
    "max_eligible_lost": None,      # 후보 팔에서 사라진 적격 논문 수 상한
    "min_topk_overlap": None,       # 상위 K 겹침 하한
    "max_noise_after": None,        # 후보 팔의 잡음(제외어·무적중) 편수 상한
}


def gate(diff: dict, imp: dict, snap: dict, rules: dict | None = None,
         shadow: dict | None = None, shadow_rules: dict | None = None) -> tuple[str, list[str]]:
    """상태 하나 + 사유 전부. 차단 사유가 여럿이면 다 남긴다.
    우선순위: invalid > needs_shadow_search > insufficient_evidence > held > eligible.
    `shadow` 는 shadow_runs 의 metrics(diff 포함) — 있으면 needs_shadow_search 를 넘어가되
    shadow 규칙이 미설정이면 insufficient, 설정돼 있고 한도를 넘으면 held."""
    rules = {**DEFAULT_APPLY_RULES, **(rules or {})}
    srules = {**DEFAULT_SHADOW_RULES, **(shadow_rules or {})}
    reasons: list[str] = []
    if diff["errors"]:
        return INVALID, [f"structure:{e}" for e in diff["errors"]]
    if diff["disallowed"]:
        return INVALID, [f"disallowed:{d}" for d in diff["disallowed"]]
    if diff["no_change"]:
        return VALID, ["no_change"]
    if diff["seed_changed"]:
        reasons.append("effective_s2_seeds_changed")
    if diff["arxiv_query_changed"]:
        reasons.append("arxiv_query_changed")
    shadow_held: list[str] = []
    if reasons:
        if shadow is None:
            return NEEDS_SHADOW, reasons     # 로컬 영향은 계산했지만 검색 효과는 못 잰다(§7.4)
        if shadow.get("status") not in ("done",):
            return INSUFFICIENT, reasons + [f"shadow_status:{shadow.get('status')}"]
        sd = shadow["diff"]
        unconfigured = [n for n, v in srules.items() if v is None]
        if unconfigured:
            return INSUFFICIENT, reasons + ["shadow_rules_unconfigured:" + ",".join(unconfigured)]
        if len(sd["eligible_lost"]) > srules["max_eligible_lost"]:
            shadow_held.append(f"shadow_eligible_lost:{len(sd['eligible_lost'])}>{srules['max_eligible_lost']}")
        if sd["topk_overlap"] is not None and sd["topk_overlap"] < srules["min_topk_overlap"]:
            shadow_held.append(f"shadow_topk_overlap:{sd['topk_overlap']:.2f}<{srules['min_topk_overlap']}")
        if sd["noise_after"] > srules["max_noise_after"]:
            shadow_held.append(f"shadow_noise_after:{sd['noise_after']}>{srules['max_noise_after']}")
        shadow_tag = [f"shadow:{shadow.get('shadow_id', '?')}"]   # 검색 효과를 쟀다 — 아래 로컬 규칙으로 계속
        reasons = []
    else:
        shadow_tag = []
    if snap["paper_count"] == 0:
        return INSUFFICIENT, ["no_observations"]
    if snap["scans"]["total"] == 0:
        return INSUFFICIENT, ["no_scan_records"]
    if snap["abstract_corrupt"]:
        reasons.append(f"abstract_corrupt:{len(snap['abstract_corrupt'])}")
    for term, n in imp["added_core_hits"].items():
        if n == 0:
            reasons.append(f"added_core_zero_hits:{term}")
    if reasons:
        return INSUFFICIENT, shadow_tag + reasons
    unconfigured = [name for name, v in rules.items() if v is None]
    if unconfigured:
        return INSUFFICIENT, shadow_tag + ["apply_rules_unconfigured:" + ",".join(unconfigured)]
    held: list[str] = list(shadow_held)
    n_changes = len(diff["added_core"]) + len(diff["tier_changes"])
    if n_changes > rules["max_core_changes"]:
        held.append(f"core_changes_exceed_limit:{n_changes}>{rules['max_core_changes']}")
    left_ratio = len(imp["topk_left"]) / imp["k"]
    if left_ratio > rules["max_topk_left_ratio"]:
        held.append(f"rank_impact_exceeds_limit:{left_ratio:.2f}>{rules['max_topk_left_ratio']}")
    rate = imp["exclude_risk"]["rate"]
    if rate is not None and rate > rules["max_exclude_risk"]:
        held.append(f"exclude_risk_exceeds_limit:{rate:.2f}>{rules['max_exclude_risk']}")
    if held:
        return HELD, shadow_tag + held
    return ELIGIBLE, shadow_tag + ["all_rules_passed"]


def regate_with_shadow(db: Path, analysis_id: str, shadow_id: str,
                       rules: dict | None = None, shadow_rules: dict | None = None) -> dict:
    """저장된 분석(needs_shadow_search)에 **저장된** shadow 를 붙여 게이트를 다시 계산하고 그 행의
    gate_status·reasons 를 갱신한다. 임시 dict 나 미저장 dry-run 은 못 붙인다 — DB 의 shadow_runs
    행만 받고, profile_id·analysis_id·before/after 해시·정책 버전·shadow 구현 버전이 **전부** 같을
    때만 쓴다(외부 검토 2026-09-12, P0: 해시 둘만 보면 같은 설정의 다른 프로필·다른 매처 정책의
    실험도 붙는다). 스냅샷 불변량(스캔 수·손상 초록)은 scope_json 에서 그대로 복원한다."""
    import shadow_search
    init_db(db)
    shadow_search.init_db(db)       # 표가 없으면(운영 DB 미이관) 여기서 크게 멈춘다 — 조용히 못 붙이는 게 맞다
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        an = con.execute("SELECT * FROM impact_analyses WHERE analysis_id=?", (analysis_id,)).fetchone()
        if not an:
            return {"updated": False, "reason": "analysis_not_found"}
        sh = con.execute("SELECT * FROM shadow_runs WHERE shadow_id=?", (shadow_id,)).fetchone()
        if not sh:
            return {"updated": False, "reason": "shadow_not_found"}
        mismatch = [f for f, want, got in (
            ("profile_id", an["profile_id"], sh["profile_id"]), ("analysis_id", analysis_id, sh["analysis_id"]),
            ("before_hash", an["before_hash"], sh["before_hash"]), ("after_hash", an["after_hash"], sh["after_hash"]),
            ("policy_version", an["policy_version"], sh["policy_version"]),
            ("shadow_version", shadow_search.SHADOW_VERSION, sh["version"])) if want != got]
        if mismatch:
            return {"updated": False, "reason": "binding_mismatch:" + ",".join(mismatch)}
        diff, imp = json.loads(an["diff_json"]), json.loads(an["impact_json"])
        scope = json.loads(an["scope_json"])
        if "scans" not in scope:        # 불변량을 안 남긴 옛 분석 — 재판정하지 않는다
            return {"updated": False, "reason": "scope_without_invariants"}
        snap = {"paper_count": scope.get("papers", 0), "scans": scope["scans"],
                "abstract_corrupt": scope.get("abstract_corrupt") or []}
        metrics = {**json.loads(sh["metrics_json"]), "status": sh["status"], "shadow_id": shadow_id}
        status, reasons = gate(diff, imp, snap, rules or json.loads(an["rules_json"]), metrics, shadow_rules)
        con.execute("UPDATE impact_analyses SET gate_status=?, reasons_json=? WHERE analysis_id=?",
                    (status, json.dumps(reasons), analysis_id))
    return {"updated": True, "gate_status": status, "reasons": reasons}


# ── 저장 ─────────────────────────────────────────────────────────────────
def _ddl(con: sqlite3.Connection) -> None:
    """이 모듈의 스키마. schema_guard 를 통해서만 돈다(§8-98)."""
    con.execute(
        "CREATE TABLE IF NOT EXISTS impact_analyses ("
        " analysis_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, created_at TEXT NOT NULL,"
        " snapshot_id TEXT NOT NULL, snapshot_sha256 TEXT NOT NULL, scope_json TEXT NOT NULL,"
        " k INTEGER NOT NULL, policy_version TEXT NOT NULL, impact_version TEXT NOT NULL,"
        " rules_json TEXT NOT NULL, before_hash TEXT NOT NULL, after_hash TEXT NOT NULL,"
        " before_json TEXT NOT NULL, after_json TEXT NOT NULL, diff_json TEXT NOT NULL,"
        " impact_json TEXT NOT NULL, gate_status TEXT NOT NULL, reasons_json TEXT NOT NULL,"
        " input_sha256 TEXT NOT NULL)")


def init_db(db: Path) -> None:
    import schema_guard
    schema_guard.ensure(db, _ddl, "profile_impact")
def input_hash(snap: dict, before: dict, after: dict, k: int, rules: dict | None,
               consumed_keys: set[str] | None = None) -> str:
    """분석 입력 전체의 해시 — after·K·정책·설정·**소비 집합** 중 하나라도 바뀌면
    옛 판정을 재사용 못 한다. 소비 집합이 빠져 있던 결함을 외부 점검(2026-09-11)이
    잡았다 — 소비 키만 바뀌면 오래된 delivery_view 를 돌려주고 있었다."""
    canon = json.dumps({"snapshot": snap["content_sha256"], "before": profile_hash(before),
                        "after": profile_hash(after), "k": k,
                        "rules": {**DEFAULT_APPLY_RULES, **(rules or {})},
                        "consumed": sorted(consumed_keys) if consumed_keys is not None else None,
                        "policy": research_profile.RANK_POLICY_VERSION, "impact": IMPACT_VERSION},
                       sort_keys=True)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def analyze_and_store(db: Path, snap: dict, before: dict, after: dict, k: int,
                      rules: dict | None = None, consumed_keys: set[str] | None = None) -> dict:
    """분석 한 번을 저장하고 돌려준다. 같은 입력 해시가 이미 있으면 그걸 돌려준다(재사용은
    입력이 **완전히** 같을 때만)."""
    init_db(db)
    ih = input_hash(snap, before, after, k, rules, consumed_keys)
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        prior = con.execute("SELECT * FROM impact_analyses WHERE input_sha256=?", (ih,)).fetchone()
        if prior:
            return {**dict(prior), "reused": True}
    diff = diff_profiles(before, after)
    imp = impact(snap, before, after, k, consumed_keys) if not diff["errors"] else {}
    status, reasons = gate(diff, imp, snap, rules) if imp else (INVALID, [f"structure:{e}" for e in diff["errors"]])
    row = {
        "analysis_id": uuid.uuid4().hex[:12], "profile_id": snap["profile_id"],
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "snapshot_id": snap["snapshot_id"], "snapshot_sha256": snap["content_sha256"],
        # 게이트가 보는 스냅샷 불변량을 그대로 남긴다 — shadow 뒤 재판정이 이걸로 같은 로컬 차단
        # 사유(스캔 없음·손상 초록)를 다시 낸다. 처음엔 scans=1·corrupt=[] 로 합성해 차단이
        # 사라졌다(외부 검토 2026-09-12, P0).
        "scope_json": json.dumps({"start": snap["start"], "end": snap["end"], "papers": snap["paper_count"],
                                  "consumed": len(consumed_keys) if consumed_keys is not None else None,
                                  "scans": snap["scans"], "abstract_corrupt": list(snap["abstract_corrupt"])}),
        "k": k, "policy_version": research_profile.RANK_POLICY_VERSION, "impact_version": IMPACT_VERSION,
        "rules_json": json.dumps({**DEFAULT_APPLY_RULES, **(rules or {})}),
        "before_hash": diff["before_hash"], "after_hash": diff["after_hash"],
        "before_json": json.dumps(_norm(before), ensure_ascii=False),
        "after_json": json.dumps(_norm(after), ensure_ascii=False),
        "diff_json": json.dumps(diff, ensure_ascii=False, default=list),
        "impact_json": json.dumps(imp, ensure_ascii=False, default=list),
        "gate_status": status, "reasons_json": json.dumps(reasons, ensure_ascii=False),
        "input_sha256": ih,
    }
    with sqlite3.connect(db) as con:
        con.execute(f"INSERT INTO impact_analyses ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
                    tuple(row.values()))
    return {**row, "reused": False}
