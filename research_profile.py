"""research_profile.py — Research Profile 저장소 + search_runs 이력.

설계 문서(2026-08-19) §1·§3을 코드로 옮긴 것. server.py의 papers.db를
그대로 재사용한다(같은 SQLite 파일, 새 테이블만 추가) — 하네스 핵심
스키마(papers/summaries/repro_results/paper_embeddings)는 전혀 안 건드리고,
_init_storage()와 같은 자리에 새 테이블을 얹는 방식도 그대로 따른다(멱등한
CREATE TABLE IF NOT EXISTS).

키워드를 profile_keywords로 별도 테이블에 둔 이유: 설계 문서 Phase 4(키워드
자동 확장)에서 "새 키워드를 행으로 추가 + 승인 이력"이 필요해지는데, 지금
행 단위로 둬야 그때 added_by/approved_at 같은 컬럼을 얹기 쉽다.

search_runs로 "마지막에 어디까지 봤는지"를 기록한다(§3, "빠진 논문" 문제).
다음 실행이 봐야 할 since 시각은 이 이력에서 결정된다 — 마지막 실행이
done이면 그 실행의 window_to부터, partial/failed면 그 실행의 window_from을
그대로 다시 본다(그 구간을 다 못 봤을 수 있으니 앞당기지 않는다).
"""

from __future__ import annotations

import sqlite3
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    profile_id TEXT PRIMARY KEY,
    name TEXT,
    max_items INTEGER DEFAULT 8,
    schedule_frequency TEXT DEFAULT 'daily',
    schedule_time TEXT DEFAULT '05:00',
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS profile_keywords (
    profile_id TEXT,
    keyword TEXT,
    kind TEXT,              -- 'core' | 'target' | 'exclude' | 's2_seed'
                            -- 's2_seed' 는 **검색 씨앗**이고 채점에 안 쓴다.
                            -- 그전에는 core 가중치 1.0 이 씨앗 자리를 겸했는데,
                            -- "무엇이 중요한가"와 "S2 에 무엇을 물어볼까"는
                            -- 다른 질문이다(2026-09-09, §8-79).
    weight REAL DEFAULT 1.0,
    added_at TEXT,
    PRIMARY KEY (profile_id, keyword, kind)
);
CREATE TABLE IF NOT EXISTS profile_venues (
    profile_id TEXT,
    venue TEXT,
    PRIMARY KEY (profile_id, venue)
);
CREATE TABLE IF NOT EXISTS profile_recipients (
    profile_id TEXT,
    email TEXT,
    active INTEGER DEFAULT 1,
    PRIMARY KEY (profile_id, email)
);
CREATE TABLE IF NOT EXISTS search_runs (
    run_id TEXT PRIMARY KEY,
    profile_id TEXT,
    source TEXT,             -- 지금은 'arxiv'뿐 (S2는 day-level delta 불가, 2026-08-24 리뷰)
    query TEXT,
    window_from TEXT,
    window_to TEXT,
    status TEXT,              -- 'done' | 'partial' | 'failed'
    retrieved_count INTEGER,
    error_detail TEXT,
    started_at TEXT,
    finished_at TEXT
);
"""


# arXiv 검색 색인이 현재 시각보다 뒤처지는 만큼 매 실행이 되돌아볼 일수.
#
# 2026-09-01 실측: 그날 09:52 KST 기준으로 arXiv 에서 검색되는 가장 최신
# 논문이 8/28(금)이었다. 8/29(토)·8/30(일)·8/31(월) 사흘은 우리 키워드가
# 아니라 **cs.CV 전체에서** 0편이었다(8/28 은 73편). 제출·공지·색인 사이에
# 며칠이 뜨고, 주말이 끼면 더 벌어진다.
#
# 이 여유가 없으면 커서가 "아직 색인 안 된 구간"을 지나쳐 전진하고, 나중에
# 그 논문들이 색인돼도 이미 커서 뒤쪽이라 영영 안 걸린다(§8-26). 실제로
# 2026-09-01 정기 실행이 후보 0편으로 끝나면서 사흘치를 지나쳤다.
#
# 5일로 둔 이유: 주말(2일) + 실측된 색인 지연(2~3일)을 덮어야 한다. 겹치는
# 구간을 다시 조회하는 비용은 arXiv 페이지 요청 몇 번뿐이고, LLM 비용은
# 0 이다 — 이미 요약된 논문은 랭킹 전에 빠지기 때문이다(scan_profile).
REINDEX_SAFETY_DAYS = 5


def topic_signature(core_topics: list[str]) -> str:
    """핵심 키워드 집합의 지문. 순서·대소문자·앞뒤 공백은 무시한다 —
    같은 키워드를 순서만 바꿔 다시 저장한 걸 "바뀌었다"로 보면 안 된다.

    왜 필요한가(2026-08-31): next_since 는 마지막 실행의 창을 이어받는데,
    이건 "같은 쿼리로 계속 검색한다"를 전제로 한다. 키워드를 바꾸면 새
    키워드로는 과거 창을 검색한 적이 없으므로, 커서를 그대로 두면 **새
    키워드가 과거를 영영 못 본다**. 실제로 키워드를 12→27 개로 넓힌 날
    커서가 90분 전을 가리키고 있어서 손으로 되돌려야 했다.
    """
    normalized = sorted({kw.strip().lower() for kw in core_topics if kw and kw.strip()})
    return hashlib.sha256("\u0000".join(normalized).encode("utf-8")).hexdigest()[:16]


def init_db(db_path: Path) -> None:
    with sqlite3.connect(db_path) as con:
        con.executescript(_SCHEMA)
        # 2026-09-09: 평가 이력은 덮어쓰지 않는다. 모델 개선 전후 비교의 원자료다.
        con.execute("CREATE TABLE IF NOT EXISTS briefing_feedback ("
                    "feedback_id INTEGER PRIMARY KEY, profile_id TEXT NOT NULL, "
                    "paper_key TEXT NOT NULL, usefulness TEXT NOT NULL, claim TEXT NOT NULL, "
                    "support TEXT NOT NULL, created_at TEXT NOT NULL)")
        # 2026-08-24: 다이제스트를 st.session_state(브라우저 세션 전용)에만
        # 두면 cron이 새벽에 혼자 스캔을 돌려도 그 결과가 review_app.py
        # 화면 어디에도 안 남는다 — "이제 매일 아침 알아서 돌게 하자"
        # 단계에서 나온 실제 요구사항. profiles 테이블에 컬럼 두 개만
        # 얹는다(기존 DB를 지우지 않고 멱등하게, server.py의 ALTER TABLE
        # 패턴과 동일) — 프로필당 다이제스트 이력 전체가 아니라 "가장
        # 최근 것"만 필요해서 별도 테이블 대신 컬럼으로 충분하다.
        existing = {row[1] for row in con.execute("PRAGMA table_info(profiles)")}
        if "last_digest" not in existing:
            con.execute("ALTER TABLE profiles ADD COLUMN last_digest TEXT")
        if "last_digest_at" not in existing:
            con.execute("ALTER TABLE profiles ADD COLUMN last_digest_at TEXT")
        # 2026-08-31: 실행마다 그때의 핵심 키워드 지문을 같이 남긴다.
        # next_since 가 "지금 키워드가 지난 실행과 같은가"를 확인해야
        # 커서를 이어받을지 되돌릴지 판단할 수 있다.
        run_cols = {row[1] for row in con.execute("PRAGMA table_info(search_runs)")}
        if "topic_signature" not in run_cols:
            con.execute("ALTER TABLE search_runs ADD COLUMN topic_signature TEXT")
        # 2026-09-06: 다이제스트에 **내용 자리로** 실린 논문을 기록한다.
        # 왜 필요한지는 mark_shown() 의 주석에 있다.
        con.execute(
            "CREATE TABLE IF NOT EXISTS profile_shown ("
            " profile_id TEXT NOT NULL,"
            " paper_key  TEXT NOT NULL,"
            " title      TEXT,"
            " shown_at   TEXT NOT NULL,"
            " PRIMARY KEY (profile_id, paper_key))"
        )

        # ① 검색 후보를 **선택 이전에** 저장한다(2026-09-07, 외부 검토서 §182).
        #
        # 그전까지 후보는 그 실행의 메모리에만 있었다. 다이제스트에 실린
        # 논문만 papers/summaries 에 남고, 걸렸다가 밀린 논문은 흔적이 없다.
        # 그래서 답할 수 없던 질문들이 있다 — "왜 이 논문이 안 뽑혔나",
        # "이번 주에 후보로는 몇 편이 걸렸나", "저 저널 논문은 언제 처음
        # 보였나". 재채점도 못 한다. 채점 규칙을 바꿔도 **같은 후보 집합**이
        # 없으면 전후 비교가 안 된다.
        #
        # 테이블 **하나만** 만든다. 검토서는 관계 6개를 제안했지만 이 레포는
        # 결함 하나가 요구할 때 테이블 하나씩 늘려 왔다(규칙 6·12).
        # 이 하나가 ①(발표일 기준 집계) ④(저널 보존) ⑧(venue·citation 보존)
        # 과 "0점 후보 분석"을 동시에 연다.
        #
        # 키는 profile_shown 과 **같은 paper_key** 다 — 같은 논문을 두 테이블이
        # 다른 이름으로 부르면 조인이 안 된다(§8-64 에서 겪은 그 문제다).
        con.execute(
            "CREATE TABLE IF NOT EXISTS search_candidates ("
            " profile_id     TEXT NOT NULL,"
            " paper_key      TEXT NOT NULL,"
            " title          TEXT,"
            " abstract       TEXT,"
            " source         TEXT,"          # 'arxiv' | 's2' — 어디서 왔나
            " arxiv_id       TEXT,"
            " doi            TEXT,"
            " venue          TEXT,"
            " published      TEXT,"          # 발표일 (① 발표일 기준 집계)
            " citation_count INTEGER,"       # ⑧ 보존 — 나중에 다시 못 받는다
            " score          REAL,"          # 채점 결과 (0점 후보도 남는다)
            " outcome        TEXT,"          # 아래 OUTCOME_* 참고
            " signature      TEXT,"          # 그때의 키워드 지문 = 채점 설정 버전
            " window_from    TEXT,"
            " window_to      TEXT,"
            " first_seen     TEXT NOT NULL," # 처음 후보로 걸린 날
            " last_seen      TEXT NOT NULL,"
            " PRIMARY KEY (profile_id, paper_key))"
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_candidates_seen "
                    "ON search_candidates (profile_id, last_seen)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_candidates_published "
                    "ON search_candidates (profile_id, published)")

        # ── 실행별 관측 (2026-09-11, B단계 — docs/ASTRA_PLAN_2026-09-10.md §6.1)
        #
        # search_candidates 는 논문 **개체**의 최신 상태다 — 같은 논문이 다음 날
        # 또 보이면 last_seen·outcome 만 덮어써서 "지난 실행에서는 어떤 자리였나"가
        # 사라진다. 그러면 정책을 바꾼 뒤 "당시 선택"을 재생할 수 없고, 씨앗별
        # 수율("이 씨앗이 이번 실행에서 몇 편을 데려왔나")도 셀 수 없다.
        #
        # 그래서 **관측**을 따로 둔다. 실행 하나 × 논문 하나 = 행 하나. 개체
        # 테이블은 손대지 않는다(추가형, §12.1). 초록은 복사하지 않고 해시만
        # 남긴다 — 본문은 search_candidates 에 있고, 해시가 같으면 같은 초록이다.
        # **프로필 revision**(2026-09-11, D단계 §9.2). 내용 해시와 별개다 —
        # A→B→A 로 돌아오면 해시는 같지만 처음 A 에서 쓴 제안은 낡은 제안이다.
        # 모든 저장 경로(사용자 UI·create_profile·자동 적용)가 같은 트랜잭션 안에서
        # 올린다. 그래야 "분석 당시 revision 과 지금 revision 이 같은가"(stale 검사)가
        # 실제로 무언가를 지킨다. 행은 추가만 되고 지우지 않는다.
        con.execute(
            "CREATE TABLE IF NOT EXISTS profile_revisions ("
            " profile_id  TEXT NOT NULL,"
            " revision    INTEGER NOT NULL,"
            " created_at  TEXT NOT NULL,"
            " origin      TEXT NOT NULL,"    # 'user' | 'advisor' | 'rollback'
            " content_sha TEXT NOT NULL,"    # 정규화 프로필 내용 해시
            " snapshot    TEXT NOT NULL,"    # 프로필 JSON (복구용)
            " note        TEXT,"
            " PRIMARY KEY (profile_id, revision))"
        )

        # **스캔 단위 실행 기록.** search_runs 는 출처(arXiv·S2)마다 행 하나라
        # "이 스캔"을 가리키는 ID 가 없었다. 관측을 어느 스캔에 묶을지, 그 스캔이
        # 어떤 프로필·정책으로 돌았는지, 관측 저장이 끝났는지를 여기 남긴다.
        # 두 검색 지문은 키워드 집합의 해시라 가중치·제외어·도메인·K 를 복원하지
        # 못하므로 **프로필 스냅샷을 통째로** 둔다(외부 점검 2026-09-11 지적).
        con.execute(
            "CREATE TABLE IF NOT EXISTS scan_runs ("
            " scan_id          TEXT PRIMARY KEY,"
            " profile_id       TEXT NOT NULL,"
            " started_at       TEXT NOT NULL,"
            " profile_snapshot TEXT NOT NULL,"  # JSON — core/weights/target/exclude/seeds/max_items
            " policy_version   TEXT NOT NULL,"
            " arxiv_run_id     TEXT,"           # search_runs.run_id
            " s2_run_id        TEXT,"
            " seed_attempts    TEXT,"           # JSON — 씨앗별 {status, returned, reason}
            " observations     INTEGER,"        # 저장된 관측 행 수. NULL = 저장 실패/미완
            " observation_error TEXT)"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS candidate_observations ("
            " scan_id        TEXT NOT NULL,"   # scan_runs.scan_id
            " profile_id     TEXT NOT NULL,"
            " paper_key      TEXT NOT NULL,"
            " title          TEXT,"
            " abstract_sha   TEXT,"            # 초록 sha256 앞 16자리
            " abstract       TEXT,"            # **바뀐 경우에만** 저장. 같으면 NULL + 아래 참조
            " abstract_ref   TEXT,"            # 같은 초록을 실제로 가진 관측의 scan_id
            " source         TEXT,"
            " retrieval_sources TEXT,"         # JSON — 실제 발견 출처 합집합
            " s2_seeds       TEXT,"            # JSON 목록 — 어느 씨앗이 데려왔나 (arXiv 면 NULL)
            " published      TEXT,"
            " date_precision TEXT,"            # profile_scoring.publication_day 의 둘째 값
            " tier_rank      INTEGER,"         # 적중 없으면 NULL
            " rank_pos       INTEGER,"         # 그 실행·정책에서의 순위 (무적격이면 NULL)
            " outcome        TEXT,"
            " filter_reason  TEXT,"            # 아래 FILTER_* 참고, 적격이면 NULL
            " core_signature TEXT,"
            " seed_signature TEXT,"
            " policy_version TEXT,"            # 정렬 계약 버전 — rank_pos 는 이 정책의 값이다
            " observed_at    TEXT NOT NULL,"
            " PRIMARY KEY (scan_id, paper_key))"
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_observations_paper "
                    "ON candidate_observations (profile_id, paper_key, observed_at)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_observations_profile_time "
                    "ON candidate_observations (profile_id, observed_at)")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create_profile(
    db_path: Path, profile_id: str, name: str,
    core_topics: list[str], target_domain: list[str] | None = None,
    exclude: list[str] | None = None, venues: list[str] | None = None,
    max_items: int = 8, schedule_frequency: str = "daily", schedule_time: str = "05:00",
    core_weights: dict[str, float] | None = None,
    s2_seeds: list[str] | None = None,
    origin: str = "user", note: str | None = None,
) -> int:
    """기존 프로필이면 통째로 덮어쓴다(키워드도 전부 지우고 다시 씀) —
    "일부만 바뀐 것"과 "이전 키워드가 실수로 안 지워진 것"을 구분 못 하게
    두느니, 매번 전체 상태를 새로 쓰는 쪽을 택했다(batch_summarize.py의
    _write_progress와 같은 이유).

    core_weights 는 핵심 키워드별 가중치다(기본 1.0). 키워드마다 "걸렸을 때
    얼마나 우리 팀 얘기인가"가 다르기 때문에 필요하다 — "defect detection"이
    걸리면 거의 확실히 우리 주제지만, "quantization"은 제어·통신 논문에서
    전혀 다른 뜻으로 쓰인다(2026-08-31 실측: CSymPlan 이 상태공간 양자화로
    걸렸다). 이 값은 profile_keywords.weight 컬럼에 저장된다 — 컬럼 자체는
    처음부터 있었지만 아무도 읽지 않던 것을 여기서 실제로 쓰기 시작한다.

    s2_seeds 는 S2 에 **질의할** 키워드다(2026-09-09, §8-79). core_weights 와
    갈라 둔 이유: 가중치는 "이 논문이 얼마나 우리 얘기인가"를 재고, 씨앗은
    "어느 단어로 물어야 논문이 잘 나오나"를 정한다. 한 숫자가 둘 다 하던
    동안 실측이 어긋났다 — 씨앗이던 `surface inspection` 은 열흘치 적중이
    0편인데, 씨앗이 아니던 `vision-language-action` 이 16편으로 최다였다.

    **생략(None)하면 기존 씨앗을 보존하고, 빈 목록([])을 명시하면 지운다.**
    이 함수는 나머지를 전부 덮어쓰는데 씨앗만 예외로 둔 이유가 있다 —
    구형 호출부(테스트, review_app 의 옛 경로)가 씨앗을 모른 채 저장하면
    그 프로필의 검색 설정이 조용히 사라진다. 가중치가 바로 그렇게 날아가고
    있었다(§8-76)."""
    init_db(db_path)
    now = _now()
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO profiles (profile_id, name, max_items, schedule_frequency, "
            "schedule_time, created_at, updated_at) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(profile_id) DO UPDATE SET name=excluded.name, "
            "max_items=excluded.max_items, schedule_frequency=excluded.schedule_frequency, "
            "schedule_time=excluded.schedule_time, updated_at=excluded.updated_at",
            (profile_id, name, max_items, schedule_frequency, schedule_time, now, now),
        )
        kept_seeds: list[str] = []
        if s2_seeds is None:
            kept_seeds = [r[0] for r in con.execute(
                "SELECT keyword FROM profile_keywords WHERE profile_id=? AND kind='s2_seed'",
                (profile_id,))]
        con.execute("DELETE FROM profile_keywords WHERE profile_id=?", (profile_id,))
        con.execute("DELETE FROM profile_venues WHERE profile_id=?", (profile_id,))
        seeds = kept_seeds if s2_seeds is None else list(s2_seeds)
        seeds = list(dict.fromkeys(s.strip() for s in seeds if s and s.strip()))
        for kind, kws in (("core", core_topics), ("target", target_domain or []),
                          ("exclude", exclude or []), ("s2_seed", seeds)):
            for kw in kws:
                con.execute(
                    "INSERT INTO profile_keywords (profile_id, keyword, kind, weight, added_at) "
                    "VALUES (?,?,?,?,?)",
                    (profile_id, kw, kind,
                     float((core_weights or {}).get(kw, 1.0)) if kind == "core" else 1.0,
                     now),
                )
        for v in (venues or []):
            con.execute(
                "INSERT INTO profile_venues (profile_id, venue) VALUES (?,?)",
                (profile_id, v),
            )
        return _bump_revision(con, profile_id, origin, note, now)


def _bump_revision(con: sqlite3.Connection, profile_id: str, origin: str,
                   note: str | None, now: str) -> int:
    """같은 연결·트랜잭션 안에서 revision 을 하나 올리고 그때의 프로필 내용을 박는다.
    호출부가 connect 를 열고 닫는다 — 여기서 커밋하지 않는다."""
    import hashlib, json
    rows = con.execute(
        "SELECT keyword, kind, weight FROM profile_keywords WHERE profile_id=? ORDER BY kind, keyword",
        (profile_id,)).fetchall()
    head = con.execute("SELECT max_items FROM profiles WHERE profile_id=?", (profile_id,)).fetchone()
    snapshot = {"keywords": [list(r) for r in rows], "max_items": head[0] if head else None}
    sha = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
    cur = con.execute("SELECT COALESCE(MAX(revision), 0) FROM profile_revisions WHERE profile_id=?",
                      (profile_id,)).fetchone()[0]
    con.execute(
        "INSERT INTO profile_revisions (profile_id, revision, created_at, origin, content_sha, snapshot, note)"
        " VALUES (?,?,?,?,?,?,?)",
        (profile_id, cur + 1, now, origin, sha, json.dumps(snapshot, ensure_ascii=False), note))
    return cur + 1


def current_revision(db_path: Path, profile_id: str) -> int:
    """0 이면 revision 을 기록한 적이 없다(2026-09-11 이전 프로필)."""
    init_db(db_path)
    with sqlite3.connect(db_path) as con:
        return con.execute("SELECT COALESCE(MAX(revision), 0) FROM profile_revisions WHERE profile_id=?",
                           (profile_id,)).fetchone()[0]


def get_profile(db_path: Path, profile_id: str) -> dict | None:
    """profile_scoring.score_paper()가 바로 받는 모양으로 조립해서 돌려준다
    — 이 함수의 출력이 곧 그 함수의 입력이라는 계약을 여기 문서화해둔다."""
    init_db(db_path)  # DB 파일이 아직 없는 상태(테이블조차 없음)에서 조회해도
    # "없음(None)"으로 답해야지 OperationalError를 내면 안 된다.
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM profiles WHERE profile_id=?", (profile_id,)).fetchone()
        if not row:
            return None
        kw_rows = con.execute(
            "SELECT keyword, kind, weight FROM profile_keywords WHERE profile_id=?",
            (profile_id,),
        ).fetchall()
        venue_rows = con.execute(
            "SELECT venue FROM profile_venues WHERE profile_id=?", (profile_id,)
        ).fetchall()

    by_kind: dict[str, list[str]] = {"core": [], "target": [], "exclude": [], "s2_seed": []}
    core_weights: dict[str, float] = {}
    for r in kw_rows:
        by_kind.setdefault(r["kind"], []).append(r["keyword"])
        if r["kind"] == "core":
            # weight 가 NULL 인 구형 행은 1.0 으로 본다 — 하위 호환.
            core_weights[r["keyword"]] = 1.0 if r["weight"] is None else float(r["weight"])

    return {
        "profile_id": row["profile_id"], "name": row["name"],
        "max_items": row["max_items"],
        "core_topics": by_kind["core"], "target_domain": by_kind["target"],
        "exclude": by_kind["exclude"], "venues": [v["venue"] for v in venue_rows],
        "core_weights": core_weights,
        # 씨앗은 core_topics 에 안 섞는다 — 섞으면 채점 대상이 되어 분리한
        # 의미가 없어진다. 순서는 저장 순서에 기대지 않게 정렬한다.
        "s2_seeds": sorted(by_kind["s2_seed"]),
    }


def paper_key(paper: dict) -> str:
    """논문 한 편을 프로필 안에서 식별하는 키.

    arxiv_id 를 못 쓰는 이유: S2 경유 저널 논문은 arxiv_id 가 **없다**
    (실측 2026-09-06: 후보 478편 중 222편). DOI → 정규화 제목 순으로 내려간다.
    """
    # **보존 키 우선**(2026-09-11, C단계). 관측(candidate_observations)에서
    # 복원한 논문은 arxiv_id·doi 가 없고 그때의 paper_key 만 있다. 그걸 무시하고
    # 제목으로 키를 다시 만들면 운영과 다른 키가 되어 동률 순서·논문 대응이
    # 어긋난다(외부 점검 지적). 복원 쪽이 `_paper_key` 로 넘긴다.
    if paper.get("_paper_key"):
        return str(paper["_paper_key"])
    aid = (paper.get("arxiv_id") or "").strip()
    doi = (paper.get("doi") or "").strip().lower()
    # **합성 ID(`pdf-<해시>`)보다 DOI 가 먼저다**(2026-09-06, 실측으로 잡았다).
    # 그건 본문을 받은 **뒤에야** 생기는 저장용 ID다 — 검색 결과로 도착할 때
    # 그 논문은 `arxiv_id=None` + DOI 를 갖는다. 처리 직후의 모습으로 기록하면
    # 기록된 키는 `pdf-c8bbaedce8` 인데 다음 날 조회하는 키는
    # `doi:10.1007/...` 이라 **안 맞는다.**
    #
    # 실측: 오픈액세스 저널 논문 GED-YOLOv5 가 06:40 실행과 10:53 실행에서
    # 연속으로 1번 자리에 나왔다. 소비 기록이 있는데도 안 걸러진 것이다 —
    # 이 필터가 막으려던 바로 그 논문(본문이 실제로 열리는 저널)에 구멍이
    # 나 있었다. 게다가 본문을 다시 받고 요약을 다시 만들어 무료 한도까지
    # 태웠다(같은 논문의 검증 결과가 41/44 → 49/51 로 달라졌다).
    if aid.startswith("pdf-") and doi:
        return f"doi:{doi}"
    if aid:
        return aid
    if doi:
        return f"doi:{doi}"
    return "title:" + " ".join((paper.get("title") or "").lower().split())


def mark_shown(db_path: Path, profile_id: str, papers: list[dict]) -> None:
    """이 논문들을 "이미 내보냈다"로 기록한다.

    **왜 필요한가**(2026-09-06). 중복 발송을 막는 필터가 하나뿐이었고
    (`run_profile_scan._already_summarized`) 그건 `summaries` 테이블을 본다.
    그런데 저널 논문은 본문을 못 받아 요약이 저장되지 않는다 — 초록만
    정리한 갈래는 `_abstract_only_outcome` 이 `arxiv_id: ""` 를 돌려주므로
    **아무 기록도 남지 않는다.** 그래서:

      · arXiv 논문은 요약되면서 소비돼 다음 날 후보에서 빠지고
      · 저널 논문은 영원히 소비되지 않아 **매일 같은 순위로 다시 나간다**

    실측: 09-04 미리보기와 09-06 실제 메일의 **상위 3편이 동일**했다
    (PhyHGNet · 2-D Ambipolar · Beech Sawn Timber). 게다가 매일 같은 초록을
    Gemini 로 다시 정리하고 있었다 — 무료 한도를 그대로 태우는 낭비다.

    `_already_summarized` 의 원래 설계 의도는 "상위권이 소비되면서 뒤가
    올라온다"였다(그 docstring). 저널 논문에는 소비될 길이 없었던 것이고,
    여기서 그 길을 만든다.

    **제목만 실린 논문은 기록하지 않는다.** 그건 각주고, 내일 본문이
    열리면 제대로 실릴 자격이 있다 — 소비하면 그 기회를 뺏는다.
    """
    if not papers:
        return
    init_db(db_path)
    now = _now()
    with sqlite3.connect(db_path) as con:
        con.executemany(
            "INSERT INTO profile_shown (profile_id, paper_key, title, shown_at) "
            "VALUES (?,?,?,?) ON CONFLICT(profile_id, paper_key) DO NOTHING",
            [(profile_id, paper_key(p), (p.get("title") or "")[:300], now)
             for p in papers],
        )


def already_shown(db_path: Path, profile_id: str) -> set[str]:
    """이 프로필이 이미 내보낸 논문 키들."""
    init_db(db_path)
    with sqlite3.connect(db_path) as con:
        return {r[0] for r in con.execute(
            "SELECT paper_key FROM profile_shown WHERE profile_id=?", (profile_id,))}


def add_recipient(db_path: Path, profile_id: str, email: str, active: bool = True) -> None:
    init_db(db_path)
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO profile_recipients (profile_id, email, active) VALUES (?,?,?) "
            "ON CONFLICT(profile_id, email) DO UPDATE SET active=excluded.active",
            (profile_id, email, 1 if active else 0),
        )


def get_recipients(db_path: Path, profile_id: str) -> list[str]:
    init_db(db_path)
    with sqlite3.connect(db_path) as con:
        rows = con.execute(
            "SELECT email FROM profile_recipients WHERE profile_id=? AND active=1 ORDER BY email",
            (profile_id,),
        ).fetchall()
    return [r[0] for r in rows]


def list_runs(db_path: Path, profile_id: str, limit: int = 10) -> list[dict]:
    """review_app.py 리서치 프로필 탭이 "지금 상황"을 보여줄 때 쓴다 — 최신
    실행이 먼저 오게 정렬."""
    init_db(db_path)
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM search_runs WHERE profile_id=? ORDER BY started_at DESC LIMIT ?",
            (profile_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def list_profiles(db_path: Path) -> list[str]:
    init_db(db_path)
    with sqlite3.connect(db_path) as con:
        return [r[0] for r in con.execute("SELECT profile_id FROM profiles ORDER BY profile_id")]


def next_since(db_path: Path, profile_id: str, source: str = "arxiv",
               default_lookback_days: int = 7, signature: str | None = None) -> datetime:
    """다음 검색이 봐야 할 시작 시각. 프로필의 첫 실행이면 과거 N일부터
    (설계 문서 §3 기준, 이전 실행 이력이 없어 delta의 출발점을 정할 방법이
    없다 — 무한정 과거로 갈 수 없으니 상한을 둔다, 이 프로젝트의 "상한 있는
    예외 처리" 원칙과 같은 결).

    signature 를 주면 지난 실행의 키워드 지문과 대조한다. 다르면 커서를
    이어받지 않고 첫 실행처럼 과거 N일부터 다시 본다 — 새 키워드로는 과거
    창을 검색한 적이 없기 때문이다(2026-08-31, §8-21). 지문이 없는 구형
    행이나 signature 를 안 준 호출은 종전과 똑같이 동작한다: "모르는 것"을
    "바뀌었다"로 단정해 매번 과거를 다시 훑으면 그것대로 낭비다.
    """
    init_db(db_path)
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT status, window_from, window_to, topic_signature FROM search_runs "
            "WHERE profile_id=? AND source=? ORDER BY started_at DESC LIMIT 1",
            (profile_id, source),
        ).fetchone()
    now = datetime.now(timezone.utc)
    if not row:
        return now - timedelta(days=default_lookback_days)
    previous = row["topic_signature"]
    if signature and previous and signature != previous:
        return now - timedelta(days=default_lookback_days)
    field = "window_to" if row["status"] == "done" else "window_from"
    cursor = datetime.fromisoformat(row[field])
    # 색인 지연만큼은 무조건 되돌아본다 — 지난 실행이 "다 봤다"고 기록한
    # 구간이라도 그때는 아직 색인 전이었을 수 있다(§8-26).
    return min(cursor, now - timedelta(days=REINDEX_SAFETY_DAYS))


# ① 후보 기록의 결말. 다이제스트의 어느 자리로 갔는지, 혹은 왜 안 갔는지.
OUTCOME_CONTENT = "content"          # 내용 자리 — 요약/초록 정리가 실렸다
OUTCOME_TITLE_ONLY = "title_only"    # 각주 자리 (렌더링은 §8-68 로 빠졌다)
OUTCOME_RESERVE = "reserve"          # 순위 안에는 들었으나 자리 밖
OUTCOME_DROPPED = "dropped"          # 채점에서 자격 미달 (0점 후보 포함)
OUTCOME_FILTERED = "filtered"        # 이미 요약됐거나 이미 보여준 논문


def record_candidates(
    db_path: Path, profile_id: str, papers: list[dict], outcome: str,
    signature: str | None = None,
    window: tuple[datetime, datetime] | None = None,
) -> int:
    """후보를 **선택 이전의 모습 그대로** 남긴다. returns 기록한 편수.

    같은 논문이 다음 날 또 걸리면 last_seen 과 outcome 만 갱신하고
    **first_seen 은 지키다** — "언제 처음 보였나"가 신규 판단의 근거다.

    citation_count·venue 를 여기서 보존하는 이유: 그 값은 검색 응답에만 있고
    나중에 다시 받으려면 API 호출이 또 든다(비용 원칙). 후보로 걸린 순간이
    그 값을 공짜로 갖는 유일한 시점이다.

    기록 실패가 스캔을 막지 않는다 — 이건 관측이지 파이프라인이 아니다.
    """
    if not papers:
        return 0
    init_db(db_path)
    now = _now()
    w_from = window[0].isoformat() if window else None
    w_to = window[1].isoformat() if window else None
    rows = []
    for paper in papers:
        score = (paper.get("_score") or {}).get("priority")
        rows.append((
            profile_id, paper_key(paper), paper.get("title") or "",
            paper.get("abstract") or "", paper.get("source") or "",
            paper.get("arxiv_id"), paper.get("doi"), paper.get("venue") or "",
            paper.get("published"), paper.get("citation_count"),
            float(score) if score is not None else None,
            outcome, signature, w_from, w_to, now, now,
        ))
    with sqlite3.connect(db_path) as con:
        con.executemany(
            "INSERT INTO search_candidates (profile_id, paper_key, title, abstract, "
            " source, arxiv_id, doi, venue, published, citation_count, score, outcome, "
            " signature, window_from, window_to, first_seen, last_seen) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(profile_id, paper_key) DO UPDATE SET "
            " title=excluded.title, abstract=excluded.abstract, source=excluded.source, "
            " arxiv_id=COALESCE(excluded.arxiv_id, search_candidates.arxiv_id), "
            " doi=COALESCE(excluded.doi, search_candidates.doi), "
            " venue=excluded.venue, published=excluded.published, "
            " citation_count=COALESCE(excluded.citation_count, search_candidates.citation_count), "
            " score=excluded.score, outcome=excluded.outcome, signature=excluded.signature, "
            " window_from=excluded.window_from, window_to=excluded.window_to, "
            " last_seen=excluded.last_seen",
            rows,
        )
    return len(rows)


# 선별 순서 계약의 버전. 계약이 바뀌면 올린다 — 관측의 rank_pos 는 이 버전의 값이다.
RANK_POLICY_VERSION = "rank-tuple-v1"   # 2026-09-11, PROGRESS §8-86

# 관측의 탈락 사유. outcome 만으로는 "왜"가 안 보인다.
FILTER_EXCLUDE_HIT = "exclude_hit"       # 제외어 적중
FILTER_NO_CORE_HIT = "no_core_hit"       # 핵심 키워드 무적중
FILTER_ALREADY_SHOWN = "already_shown"   # 이미 배달·소비된 논문


def begin_scan(db_path: Path, profile_id: str, profile: dict,
               started_at: str | None = None) -> str:
    """스캔 실행 기록을 열고 scan_id 를 돌려준다. 프로필 스냅샷을 그대로 박는다."""
    import json
    init_db(db_path)
    scan_id = uuid.uuid4().hex[:12]
    snapshot = {k: profile.get(k) for k in
                ("core_topics", "core_weights", "target_domain", "exclude", "s2_seeds", "max_items")}
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO scan_runs (scan_id, profile_id, started_at, profile_snapshot, policy_version)"
            " VALUES (?,?,?,?,?)",
            (scan_id, profile_id, started_at or _now(),
             json.dumps(snapshot, ensure_ascii=False), RANK_POLICY_VERSION))
    return scan_id


def finish_scan(db_path: Path, scan_id: str, *, arxiv_run_id: str | None = None,
                s2_run_id: str | None = None, seed_attempts: list | None = None,
                observations: int | None = None, observation_error: str | None = None) -> None:
    import json
    with sqlite3.connect(db_path) as con:
        con.execute(
            "UPDATE scan_runs SET arxiv_run_id=COALESCE(?, arxiv_run_id),"
            " s2_run_id=COALESCE(?, s2_run_id), seed_attempts=COALESCE(?, seed_attempts),"
            " observations=COALESCE(?, observations),"
            " observation_error=COALESCE(?, observation_error) WHERE scan_id=?",
            (arxiv_run_id, s2_run_id,
             json.dumps(seed_attempts, ensure_ascii=False) if seed_attempts is not None else None,
             observations, observation_error, scan_id))


def record_observations(db_path: Path, scan_id: str, profile_id: str,
                        rows: list[dict], *, core_signature: str,
                        seed_signature: str, observed_at: str | None = None) -> int:
    """스캔 하나의 후보 관측을 남긴다. rows 의 각 항목은 논문 dict 에
    `_score`(profile_scoring.score_paper 결과) · `outcome` · `rank_pos` ·
    `filter_reason` 이 붙은 것이다. 같은 (scan_id, paper_key) 는 한 번만.

    개체 테이블(search_candidates)과 달리 **덮어쓰지 않고 쌓인다.** 같은 논문이
    다섯 스캔에서 보이면 다섯 행이다.

    **초록은 바뀐 경우에만 저장한다**(§6.1). 같은 논문의 앞선 관측이 같은 해시의
    초록을 실제로 갖고 있으면 그 scan_id 를 참조한다. search_candidates 의
    초록을 참조하면 안 된다 — 그 열은 재수집 때 덮어써져 과거를 복구 못 한다.
    참조 사슬을 만들지 않는다: 항상 초록을 **실제로 가진** 행을 가리킨다.
    묶음 전체를 한 트랜잭션으로 넣는다 — 반쪽 저장이 집계에 섞이면 안 된다."""
    import hashlib, json
    init_db(db_path)
    now = observed_at or _now()
    with sqlite3.connect(db_path) as con:
        out = []
        for p in rows:
            score = p.get("_score") or {}
            abstract = p.get("abstract") or ""
            sha = hashlib.sha256(abstract.encode("utf-8")).hexdigest()[:16] if abstract else None
            stored, ref = None, None
            if sha:
                holder = con.execute(
                    "SELECT scan_id FROM candidate_observations WHERE profile_id=? AND paper_key=?"
                    " AND abstract_sha=? AND abstract IS NOT NULL ORDER BY observed_at DESC LIMIT 1",
                    (profile_id, paper_key(p), sha)).fetchone()
                if holder:
                    ref = holder[0]
                else:
                    stored = abstract
            seeds = p.get("s2_seeds")
            rsrc = p.get("retrieval_sources") or ([p["source"]] if p.get("source") else None)
            out.append((
                scan_id, profile_id, paper_key(p), p.get("title"), sha, stored, ref,
                p.get("source"), json.dumps(rsrc) if rsrc else None,
                json.dumps(seeds, ensure_ascii=False) if seeds else None,
                p.get("published"), score.get("date_precision"),
                score.get("tier_rank"), p.get("rank_pos"), p.get("outcome"),
                p.get("filter_reason"), core_signature, seed_signature,
                RANK_POLICY_VERSION, now,
            ))
        con.executemany(
            "INSERT OR REPLACE INTO candidate_observations (scan_id, profile_id, paper_key,"
            " title, abstract_sha, abstract, abstract_ref, source, retrieval_sources, s2_seeds,"
            " published, date_precision, tier_rank, rank_pos, outcome, filter_reason,"
            " core_signature, seed_signature, policy_version, observed_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            out)
    return len(out)


def list_candidates(
    db_path: Path, profile_id: str, outcome: str | None = None,
    since: datetime | None = None, limit: int = 500,
) -> list[sqlite3.Row]:
    """기록된 후보. since 는 **발표일**이 아니라 마지막으로 걸린 날 기준이다."""
    init_db(db_path)
    sql = "SELECT * FROM search_candidates WHERE profile_id=?"
    args: list = [profile_id]
    if outcome:
        sql += " AND outcome=?"
        args.append(outcome)
    if since:
        sql += " AND last_seen>=?"
        args.append(since.isoformat())
    sql += " ORDER BY score DESC NULLS LAST, last_seen DESC LIMIT ?"
    args.append(limit)
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        return list(con.execute(sql, args))


def candidate_outcome_counts(
    db_path: Path, profile_id: str, since: datetime | None = None,
) -> dict[str, int]:
    """결말별 편수. "몇 편이 걸렸고 그중 몇 편이 실렸나"를 세는 데 쓴다."""
    init_db(db_path)
    sql = "SELECT outcome, COUNT(*) AS n FROM search_candidates WHERE profile_id=?"
    args: list = [profile_id]
    if since:
        sql += " AND last_seen>=?"
        args.append(since.isoformat())
    sql += " GROUP BY outcome"
    with sqlite3.connect(db_path) as con:
        return {row[0] or "(미기록)": row[1] for row in con.execute(sql, args)}


def _doi_from_source(source: str | None) -> str | None:
    """`papers.source` 에 적힌 출처 문자열에서 DOI 만 꺼낸다.

    형태는 `open-access: 10.1234/xyz` 또는 `manual-pdf: ...` 다. DOI 가 아닌
    출처(arxiv, 파일명 등)면 None — 아니면 제목 키로 떨어지므로 억지로 만들지 않는다.
    """
    text = (source or "").strip()
    for prefix in ("open-access:", "manual-pdf:"):
        if text.startswith(prefix):
            rest = text[len(prefix):].strip()
            return rest if rest.startswith("10.") else None
    return None


def backfill_shown_from_summaries(db_path: Path, profile_id: str,
                                  summaries_db: Path | None = None) -> int:
    """이미 요약된 논문을 "내보냈다"로 소급 기록한다. returns 새로 넣은 편수.

    **왜 필요한가**(2026-09-08, §8-77 고치며). 소비 기준을 "요약됨"에서
    "배달됨"으로 옮기면, 예전에 요약만 되고 `profile_shown` 기록이 없는 논문이
    한꺼번에 후보로 되살아난다. 실측: 요약 132편 중 기록 없는 것이 101편이고
    그중 9편이 최근 5일 창에 들어와 **내일 메일에 다시 나갈 참이었다.**

    그 논문들은 실제로는 이미 나갔다 — `profile_shown` 이 2026-09-06 에야
    생겨서 그 이전 발송이 기록되지 않았을 뿐이다. 그래서 되살리는 게 아니라
    **없던 기록을 채우는 것**이 사실에 맞다.

    한 번만 의미가 있고 두 번 불러도 안전하다(이미 있는 키는 그대로 둔다).
    """
    init_db(db_path)
    src = summaries_db or db_path
    with sqlite3.connect(src) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT p.arxiv_id, p.title, p.source FROM summaries s "
            "JOIN papers p ON p.arxiv_id = s.arxiv_id"
        ).fetchall()

    before = len(already_shown(db_path, profile_id))
    papers: list[dict] = []
    for row in rows:
        aid, title = row["arxiv_id"], row["title"]
        papers.append({"arxiv_id": aid, "title": title})
        # **합성 ID 는 도착할 때의 신원이 아니다**(AGENTS.md 함정 목록).
        # `pdf-<해시>` 는 본문을 받은 **뒤에** 생기는 저장용 ID이고, 그 논문이
        # 검색 결과로 다시 도착할 때의 키는 `doi:...` 다. 합성 ID 로만 소급하면
        # 다음 날 조회 키와 안 맞아 필터를 그냥 통과한다 — 실측(2026-09-08):
        # 합성 ID 요약 9편 중 **6편**이 도착 키로는 기록에 없었다.
        # `papers.source` 가 `open-access: <DOI>` 형태로 그 신원을 갖고 있다.
        doi = _doi_from_source(row["source"])
        if doi:
            papers.append({"arxiv_id": None, "doi": doi, "title": title})
    mark_shown(db_path, profile_id, papers)
    return len(already_shown(db_path, profile_id)) - before


def record_run(
    db_path: Path, profile_id: str, source: str, query: str,
    window_from: datetime, window_to: datetime, status: str,
    retrieved_count: int, error_detail: str | None = None,
    started_at: str | None = None, signature: str | None = None,
) -> str:
    init_db(db_path)
    run_id = uuid.uuid4().hex[:12]
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO search_runs (run_id, profile_id, source, query, window_from, "
            "window_to, status, retrieved_count, error_detail, started_at, finished_at, "
            "topic_signature) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, profile_id, source, query, window_from.isoformat(), window_to.isoformat(),
             status, retrieved_count, error_detail, started_at or _now(), _now(), signature),
        )
    return run_id


def save_digest(db_path: Path, profile_id: str, digest_text: str) -> None:
    """가장 최근 다이제스트만 남긴다(이력 전체 아님) — cron이든 review_app.py
    "지금 스캔 실행" 버튼이든, 누가 만들었는지와 무관하게 여기 하나만
    본다(단일 진실 공급원). 매번 덮어쓴다 — batch_summarize.py의
    _write_progress와 같은 이유로 "일부만 갱신"보다 "매번 전체 교체"가
    상태를 헷갈리지 않게 한다."""
    init_db(db_path)
    with sqlite3.connect(db_path) as con:
        con.execute(
            "UPDATE profiles SET last_digest=?, last_digest_at=? WHERE profile_id=?",
            (digest_text, _now(), profile_id),
        )


def get_latest_digest(db_path: Path, profile_id: str) -> tuple[str, str] | None:
    """returns (digest_text, generated_at) 또는 아직 없으면 None."""
    init_db(db_path)
    with sqlite3.connect(db_path) as con:
        row = con.execute(
            "SELECT last_digest, last_digest_at FROM profiles WHERE profile_id=?", (profile_id,)
        ).fetchone()
    if not row or not row[0]:
        return None
    return row[0], row[1]


def attach_observation_dates(db_path: Path, profile_id: str, papers: list[dict]) -> None:
    """발표일·발견일·요약일은 다른 사건이므로 메일에도 따로 전달한다."""
    with sqlite3.connect(db_path) as con:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for paper in papers:
            row = con.execute(
                "SELECT first_seen FROM search_candidates WHERE profile_id=? AND paper_key=?",
                (profile_id, paper_key(paper))).fetchone()
            paper["first_seen"] = row[0] if row else None
            if "summaries" in tables:
                row = con.execute("SELECT created_at FROM summaries WHERE arxiv_id=?",
                                  (paper.get("arxiv_id"),)).fetchone()
                paper["summarized_at"] = row[0] if row else None


FEEDBACK_LABELS = {"useful": "유용함", "known": "이미 아는 내용", "out_of_scope": "관심 밖"}
SUPPORT_LABELS = {"unassessed": "미평가", "supported": "근거가 지지함",
                  "unsupported": "근거가 지지하지 않음", "unclear": "판단 어려움"}


def record_feedback(db_path: Path, profile_id: str, key: str, usefulness: str,
                    claim: str = "", support: str = "unassessed") -> None:
    """⑧ 사람이 남긴 평가를 보존한다. 클릭을 승인이나 자동 채점 가중치로 쓰지 않는다.

    인용 존재는 기계가 확인하고, 주장을 지지하는지는 사람이 대조한다(ALCE).
    자유 서술은 로컬 DB에만 두며 LLM 입력으로 보내지 않는다.
    """
    if usefulness not in FEEDBACK_LABELS or support not in SUPPORT_LABELS:
        raise ValueError("알 수 없는 평가")
    if support != "unassessed" and not claim.strip():
        raise ValueError("근거를 평가할 주장 문장이 필요하다")
    init_db(db_path)
    with sqlite3.connect(db_path) as con:
        if not con.execute("SELECT 1 FROM profile_shown WHERE profile_id=? AND paper_key=?",
                           (profile_id, key)).fetchone():
            raise ValueError("이 프로필에서 배달 기록이 없는 논문이다")
        con.execute(
            "INSERT INTO briefing_feedback (profile_id,paper_key,usefulness,claim,support,created_at) "
            "VALUES (?,?,?,?,?,?)", (profile_id, key, usefulness, claim.strip(), support, _now()))


def list_feedback(db_path: Path, profile_id: str) -> list[dict]:
    init_db(db_path)
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(
            "SELECT * FROM briefing_feedback WHERE profile_id=? ORDER BY feedback_id", (profile_id,))]


def feedback_papers(db_path: Path, profile_id: str) -> list[dict]:
    init_db(db_path)
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(
            "SELECT paper_key,title,shown_at FROM profile_shown WHERE profile_id=? "
            "ORDER BY shown_at DESC,paper_key LIMIT 100", (profile_id,))]
