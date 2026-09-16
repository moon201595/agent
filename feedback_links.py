"""⑨ 배달 — 메일 논문 카드의 반응 버튼(더 보고 싶음 · 유용함 · 관심 밖)과 반응 수집.

2026-09-14 사용자 결정: 사용자 피드백이 1순위 신호다(CLAUDE.md 규칙 1). 버튼은 Google Apps Script 웹앱
(`apps_script/feedback_webapp.gs`)으로 가는 1클릭 링크다. 사용자와 합의한 보안 설계(계획 v2 §2·§4):

- Google 쪽에는 **무작위 tid · 버튼 종류 · 만료 시각 · 서명**만 간다. tid → (메일 회차 · P 번호 · 논문 · 수신자)
  대응은 이 DB 에만 둔다 — Google 시트가 새도 논문·수신자를 알 수 없다.
- 토큰은 HMAC-SHA256 서명(`FEEDBACK_HMAC_SECRET`)이다. 비밀키 없이는 유효한 링크를 만들 수 없고, 만료 뒤에는 무효다.
- 메일 보안 스캐너의 링크 선열람은 수집할 때 격리한다: 발송 직후 `PREFETCH_WINDOW_S` 안의 열람, 같은 tid 의 서로 다른
  버튼 셋이 `BURST_WINDOW_S` 안에 모두 열린 경우. Apps Script 웹앱은 요청의 User-Agent·IP 를 넘겨주지 않으므로
  봇 판정은 시각 패턴으로만 한다.
- 환경변수(`FEEDBACK_WEBAPP_URL` · `FEEDBACK_HMAC_SECRET`)가 없으면 버튼을 싣지 않는다 — 배포 전에도 메일은 그대로 나간다
  (CLAUDE.md 규칙 6).

가중치 반영(계획 v2 §3)은 이 모듈의 일이 아니다. 여기서는 링크를 만들고, 반응을 모으고, 검증해 상태를 붙인다.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

ACTIONS: tuple[tuple[str, str], ...] = (("more", "더 보고 싶음"), ("useful", "유용함"), ("out", "관심 밖"))
UNDO = "undo"
TOKEN_VERSION = "v1"
TOKEN_TTL_DAYS = 14          # 메일을 늦게 열어도 누를 수 있게 2주. 운영 시작값.
PREFETCH_WINDOW_S = 120      # 발송 직후 이 안의 열람은 보안 스캐너 선열람으로 본다. 운영 시작값.
BURST_WINDOW_S = 30          # 같은 논문 버튼 셋이 이 안에 모두 열리면 사람이 아니다. 운영 시작값.
EXPORT_TTL_S = 300           # 수집 요청 서명의 유효 시간

STATUS_VALID = "valid"
STATUS_UNDO = "undo"
STATUS_CANCELLED = "cancelled"
STATUS_EXPIRED = "expired"
STATUS_BAD_SIGNATURE = "bad_signature"
STATUS_UNKNOWN_TID = "unknown_tid"
STATUS_PREFETCH = "quarantined_prefetch"
STATUS_BURST = "quarantined_burst"


# ---------------------------------------------------------------- 설정 · 서명

def _env(name: str) -> str:
    import summarize_engine as engine   # .env 를 이미 읽어 둔 곳 — 이름만 참조한다(CLAUDE.md 규칙 5)
    return str(engine.ENV.get(name) or "").strip()


def config() -> tuple[str, str] | None:
    """(웹앱 URL, 비밀키). 둘 중 하나라도 없거나 약하면 None — 버튼을 싣지 않는다."""
    url, secret = _env("FEEDBACK_WEBAPP_URL"), _env("FEEDBACK_HMAC_SECRET")
    if not url.startswith("https://") or len(secret) < 32:
        return None
    return url, secret


# Apps Script 웹앱 URL 에서 배포 id 를 꺼낸다 — `https://script.google.com/macros/s/<배포 id>/exec`
_DEPLOY_ID_RE = re.compile(r"^https://script\.google\.com/macros/s/([A-Za-z0-9_-]{20,200})/exec/?$")


def page_url() -> str:
    """버튼이 먼저 가는 **스스로 닫히는 페이지**(`web/reaction/index.html`, GitHub Pages) 주소. 없으면 빈 문자열 = 웹앱으로 직행(종전 동작).

    왜 페이지를 하나 더 두는가(2026-09-16, 사용자: "버튼 누르고 나서 그 탭이 자동으로 닫히게 할 순 없어?"): Apps Script 응답은 **항상**
    구글 샌드박스 iframe 안에서 돌고(ContentService 는 HTML MIME 자체가 없다), 그 iframe 은 사용자가 안에서 클릭하기 전에는 바깥 탭을
    못 닫는다(실측). 반면 메일 링크로 열린 새 탭은 샌드박스가 아닌 문서면 스스로 `window.close()` 할 수 있다(크로미움 실측: 직접 열기·
    리다이렉트 경유 모두 닫힘). 그래서 버튼은 우리 정적 페이지로 가고, 그 페이지가 웹앱에 `mode=json` 으로 기록을 요청한 뒤 탭을 닫는다.
    토큰·서명·수집 경로는 그대로다 — 바뀌는 건 "누가 웹앱을 부르느냐"(브라우저의 페이지 스크립트)뿐이다."""
    url = _env("FEEDBACK_PAGE_URL")
    return url if url.startswith("https://") else ""


def deploy_id(webapp_url: str) -> str:
    """웹앱 URL 의 배포 id. 형식이 다르면 빈 문자열 — 그때는 페이지를 쓰지 않고 웹앱으로 직행한다.
    **저장소에 웹앱 주소를 넣지 않기 위해** 페이지가 주소를 하드코딩하지 않고 이 id 를 링크로 받는다(저장소가 공개다)."""
    m = _DEPLOY_ID_RE.match(webapp_url.strip())
    return m.group(1) if m else ""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def sign(payload: str, secret: str) -> str:
    """Apps Script 의 `Utilities.computeHmacSha256Signature` + `base64EncodeWebSafe`(패딩 제거)와 같은 값."""
    return _b64(hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest())


def make_token(tid: str, action: str, expires_at: int, secret: str) -> str:
    payload = f"{TOKEN_VERSION}.{tid}.{action}.{expires_at}"
    return f"{payload}.{sign(payload, secret)}"


def verify_token(token: str, secret: str) -> tuple[str, str, int] | None:
    """(tid, action, 만료 epoch) 또는 None(형식·서명·버튼 종류가 틀림). 만료 판단은 받은 시각으로 호출부가 한다."""
    parts = (token or "").split(".")
    if len(parts) != 5 or parts[0] != TOKEN_VERSION:
        return None
    if not hmac.compare_digest(sign(".".join(parts[:4]), secret), parts[4]):
        return None
    if parts[2] not in {a for a, _ in ACTIONS} | {UNDO}:
        return None
    try:
        return parts[1], parts[2], int(parts[3])
    except ValueError:
        return None


def recipient_hash(recipient: str, secret: str) -> str:
    """수신자를 되돌릴 수 없게 가린 값 — 반응 표에 메일 주소를 두지 않는다."""
    return sign("recipient." + recipient.strip().lower(), secret)[:16]


# ---------------------------------------------------------------- 저장

def _ddl(con: sqlite3.Connection) -> None:
    """이 모듈의 스키마. schema_guard 를 통해서만 돈다."""
    con.execute("CREATE TABLE IF NOT EXISTS feedback_tokens ("
                "tid TEXT PRIMARY KEY, issue_id TEXT NOT NULL, profile_id TEXT NOT NULL, item_no TEXT NOT NULL, "
                "paper_key TEXT NOT NULL, recipient_hash TEXT NOT NULL, position INTEGER NOT NULL, "
                "created_at TEXT NOT NULL, expires_at INTEGER NOT NULL, delivered_at TEXT)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_feedback_tokens_issue ON feedback_tokens(issue_id, recipient_hash)")
    con.execute("CREATE TABLE IF NOT EXISTS feedback_events ("
                "event_id TEXT PRIMARY KEY, tid TEXT, action TEXT, received_at TEXT NOT NULL, "
                "status TEXT NOT NULL, imported_at TEXT NOT NULL)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_feedback_events_tid ON feedback_events(tid, received_at)")
    con.execute("CREATE TABLE IF NOT EXISTS feedback_sync ("
                "id INTEGER PRIMARY KEY CHECK (id = 1), last_received_at TEXT)")


def init_db(db: Path) -> None:
    import schema_guard
    schema_guard.ensure(db, _ddl, "feedback_links")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def new_issue_id(profile_id: str, now: datetime | None = None) -> str:
    now = now or _now()
    return f"{profile_id}:{now.strftime('%Y%m%dT%H%M%S')}:{secrets.token_hex(3)}"


def issue_links(db: Path, profile_id: str, issue_id: str, recipient: str, papers: list[dict],
                now: datetime | None = None) -> dict[str, dict[str, str]]:
    """수신자 한 명의 메일에 실을 버튼 링크 {paper_key: {action: url}}. 설정이 없으면 빈 dict.

    논문·수신자마다 tid 를 새로 만든다 — 두 수신자가 같은 링크를 공유하면 누가 눌렀는지 모른다.
    """
    cfg = config()
    if not cfg or not papers:
        return {}
    url, secret = cfg
    # 닫히는 페이지가 설정돼 있고 웹앱 주소에서 배포 id 를 꺼낼 수 있으면 버튼을 그 페이지로 보낸다(`?t=토큰&d=배포 id`).
    page, did = page_url(), deploy_id(url)
    relay = f"{page}?" + urlencode({"d": did}) + "&" if page and did else ""
    import research_profile
    init_db(db)
    now = now or _now()
    expires = int((now + timedelta(days=TOKEN_TTL_DAYS)).timestamp())
    rhash = recipient_hash(recipient, secret)
    links: dict[str, dict[str, str]] = {}
    with sqlite3.connect(db) as con:
        for position, paper in enumerate(papers, start=1):
            key = research_profile.paper_key(paper)
            tid = secrets.token_urlsafe(12)
            con.execute("INSERT INTO feedback_tokens (tid, issue_id, profile_id, item_no, paper_key, recipient_hash, "
                        "position, created_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (tid, issue_id, profile_id, f"P{position}", key, rhash, position, now.isoformat(), expires))
            links[key] = {
                action: (f"{relay}{urlencode({'t': make_token(tid, action, expires, secret)})}" if relay
                         else f"{url}?{urlencode({'t': make_token(tid, action, expires, secret)})}")
                for action, _label in ACTIONS}
    return links


def mark_delivered(db: Path, issue_id: str, recipient: str, when: datetime | None = None) -> None:
    """발송이 수락된 뒤에만 부른다 — 선열람 격리의 기준 시각이다."""
    cfg = config()
    if not cfg:
        return
    init_db(db)
    with sqlite3.connect(db) as con:
        con.execute("UPDATE feedback_tokens SET delivered_at=? WHERE issue_id=? AND recipient_hash=?",
                    ((when or _now()).isoformat(), issue_id, recipient_hash(recipient, cfg[1])))


# ---------------------------------------------------------------- 수집

def export_url(url: str, secret: str, since: str, now: datetime | None = None) -> str:
    exp = int((now or _now()).timestamp()) + EXPORT_TTL_S
    return f"{url}?{urlencode({'mode': 'export', 'since': since, 'exp': exp, 'sig': sign(f'export.{since}.{exp}', secret)})}"


def import_rows(db: Path, rows: list[dict], secret: str, now: datetime | None = None) -> Counter:
    """웹앱 시트의 행({received_at, token})을 검증해 `feedback_events` 에 넣는다. 상태별 건수를 돌려준다.

    같은 행을 두 번 가져와도 한 번만 들어간다(event_id = 토큰+받은 시각 해시).
    """
    init_db(db)
    now = now or _now()
    counts: Counter = Counter()
    latest = None
    with sqlite3.connect(db) as con:
        for row in sorted(rows, key=lambda r: str(r.get("received_at") or "")):
            token, received_raw = str(row.get("token") or ""), str(row.get("received_at") or "")
            received = _parse_time(received_raw)
            if received is None:
                counts["bad_row"] += 1
                continue
            latest = max(latest, received_raw) if latest else received_raw
            event_id = hashlib.sha256(f"{token}|{received_raw}".encode()).hexdigest()[:32]
            if con.execute("SELECT 1 FROM feedback_events WHERE event_id=?", (event_id,)).fetchone():
                counts["duplicate"] += 1
                continue
            parsed = verify_token(token, secret)
            tid = action = None
            if parsed is None:
                status = STATUS_BAD_SIGNATURE
            else:
                tid, action, expires = parsed
                token_row = con.execute("SELECT delivered_at FROM feedback_tokens WHERE tid=?", (tid,)).fetchone()
                if received.timestamp() > expires:
                    status = STATUS_EXPIRED
                elif token_row is None:
                    status = STATUS_UNKNOWN_TID
                elif action == UNDO:
                    status = STATUS_UNDO
                    con.execute("UPDATE feedback_events SET status=? WHERE tid=? AND status=? AND received_at<=?",
                                (STATUS_CANCELLED, tid, STATUS_VALID, received_raw))
                else:
                    delivered = _parse_time(token_row[0]) if token_row[0] else None
                    if delivered and 0 <= (received - delivered).total_seconds() < PREFETCH_WINDOW_S:
                        status = STATUS_PREFETCH
                    else:
                        status = STATUS_VALID
                        window_lo = (received - timedelta(seconds=BURST_WINDOW_S)).isoformat()
                        near = con.execute("SELECT event_id, action FROM feedback_events WHERE tid=? AND "
                                           "received_at>=? AND action IN ('more','useful','out')",
                                           (tid, window_lo)).fetchall()
                        if len({a for _eid, a in near} | {action}) >= len(ACTIONS):
                            status = STATUS_BURST
                            con.executemany("UPDATE feedback_events SET status=? WHERE event_id=?",
                                            [(STATUS_BURST, eid) for eid, _a in near])
            con.execute("INSERT INTO feedback_events (event_id, tid, action, received_at, status, imported_at) "
                        "VALUES (?,?,?,?,?,?)", (event_id, tid, action, received_raw, status, now.isoformat()))
            counts[status] += 1
        if latest:
            con.execute("INSERT INTO feedback_sync (id, last_received_at) VALUES (1, ?) "
                        "ON CONFLICT(id) DO UPDATE SET last_received_at=max(COALESCE(last_received_at,''), excluded.last_received_at)",
                        (latest,))
    return counts


async def sync(db: Path, client) -> Counter | None:
    """웹앱에서 새 반응을 가져온다. 설정이 없으면 None. 실패는 예외로 올리고 호출부가 삼킨다(메일은 나가야 한다)."""
    cfg = config()
    if not cfg:
        return None
    url, secret = cfg
    init_db(db)
    with sqlite3.connect(db) as con:
        row = con.execute("SELECT last_received_at FROM feedback_sync WHERE id=1").fetchone()
    since = (row[0] if row and row[0] else "")
    resp = await client.get(export_url(url, secret, since), timeout=30, follow_redirects=True)
    resp.raise_for_status()
    data = resp.json()
    if data.get("error"):
        raise RuntimeError(f"웹앱 수집 거부: {data['error']}")
    return import_rows(db, list(data.get("rows") or []), secret)


def valid_reactions(db: Path, profile_id: str) -> list[dict]:
    """학습에 쓸 반응 — valid 만. (paper_key, action, recipient_hash, item_no, issue_id, received_at)."""
    init_db(db)
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(
            "SELECT t.paper_key, e.action, t.recipient_hash, t.item_no, t.issue_id, e.received_at "
            "FROM feedback_events e JOIN feedback_tokens t ON t.tid = e.tid "
            "WHERE t.profile_id=? AND e.status=? ORDER BY e.received_at", (profile_id, STATUS_VALID))]
