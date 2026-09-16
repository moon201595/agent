"""⑨ 반응 버튼 — 서명 토큰 · 링크 발급 · 반응 수집 · 선열람 격리 · 메일 렌더 (2026-09-15)."""
import asyncio
import base64
import hashlib
import hmac
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import digest
import feedback_links as fl

SECRET = "s" * 40
DEPLOY = "AKfycb" + "x" * 40
URL = f"https://script.google.com/macros/s/{DEPLOY}/exec"
PAGE = "https://example.github.io/agent/reaction/"


@pytest.fixture
def configured(monkeypatch):
    import summarize_engine as engine
    monkeypatch.setitem(engine.ENV, "FEEDBACK_WEBAPP_URL", URL)
    monkeypatch.setitem(engine.ENV, "FEEDBACK_HMAC_SECRET", SECRET)


@pytest.fixture
def db(tmp_path):
    return tmp_path / "f.db"


def _papers():
    return [{"arxiv_id": "2609.00001", "title": "A"}, {"arxiv_id": "2609.00002", "title": "B"}]


def _token_of(url: str) -> str:
    from urllib.parse import parse_qs, urlparse
    return parse_qs(urlparse(url).query)["t"][0]


# ---------------------------------------------------------------- 서명

def test_signature_is_websafe_base64_hmac_sha256_without_padding():
    """Apps Script 의 computeHmacSha256Signature + base64EncodeWebSafe(패딩 제거)와 같은 값이어야 링크가 통한다.
    이 테스트가 잡는 것: 해시·인코딩·패딩 처리를 바꿔 웹앱과 서명이 어긋나는 것."""
    expected = base64.urlsafe_b64encode(hmac.new(SECRET.encode(), b"v1.abc.more.9", hashlib.sha256).digest()).decode().rstrip("=")
    assert fl.sign("v1.abc.more.9", SECRET) == expected
    assert "=" not in expected


def test_token_roundtrip_and_tampering_is_rejected():
    """이 테스트가 잡는 것: 버튼 종류·tid·만료를 바꾼 토큰, 다른 비밀키, 모르는 버튼을 통과시키는 것."""
    token = fl.make_token("tid1", "more", 2000000000, SECRET)
    assert fl.verify_token(token, SECRET) == ("tid1", "more", 2000000000)
    v, tid, action, exp, sig = token.split(".")
    for forged in (f"{v}.{tid}.out.{exp}.{sig}", f"{v}.tid2.{action}.{exp}.{sig}", f"{v}.{tid}.{action}.2100000000.{sig}"):
        assert fl.verify_token(forged, SECRET) is None
    assert fl.verify_token(token, "x" * 40) is None
    assert fl.verify_token(fl.make_token("tid1", "delete_all", 2000000000, SECRET), SECRET) is None
    assert fl.verify_token("garbage", SECRET) is None


def test_config_requires_https_url_and_a_long_secret(monkeypatch):
    """설정이 없거나 약하면 버튼을 싣지 않는다 — 배포 전에도 메일은 예전 그대로 나가야 한다."""
    import summarize_engine as engine
    monkeypatch.setitem(engine.ENV, "FEEDBACK_WEBAPP_URL", "")
    monkeypatch.setitem(engine.ENV, "FEEDBACK_HMAC_SECRET", SECRET)
    assert fl.config() is None
    monkeypatch.setitem(engine.ENV, "FEEDBACK_WEBAPP_URL", "http://script.google.com/x")
    assert fl.config() is None
    monkeypatch.setitem(engine.ENV, "FEEDBACK_WEBAPP_URL", URL)
    monkeypatch.setitem(engine.ENV, "FEEDBACK_HMAC_SECRET", "short")
    assert fl.config() is None
    monkeypatch.setitem(engine.ENV, "FEEDBACK_HMAC_SECRET", SECRET)
    assert fl.config() == (URL, SECRET)


# ---------------------------------------------------------------- 발급

def test_links_are_per_recipient_and_google_sees_no_paper_or_email(db, configured):
    """이 테스트가 잡는 것: 수신자끼리 링크를 공유하는 것(누가 눌렀는지 모름), 토큰에 논문 키·메일 주소가 새는 것,
    DB 에 메일 주소를 그대로 저장하는 것."""
    a = fl.issue_links(db, "p", "i1", "alice@x.com", _papers())
    b = fl.issue_links(db, "p", "i1", "bob@x.com", _papers())
    assert set(a) == {"2609.00001", "2609.00002"} and set(a["2609.00001"]) == {"more", "useful", "out"}
    ta, tb = _token_of(a["2609.00001"]["more"]), _token_of(b["2609.00001"]["more"])
    assert fl.verify_token(ta, SECRET)[0] != fl.verify_token(tb, SECRET)[0]
    for token in (ta, tb):
        assert "2609" not in token and "alice" not in token and "@" not in token
    with sqlite3.connect(db) as con:
        rows = con.execute("SELECT item_no, paper_key, recipient_hash FROM feedback_tokens ORDER BY rowid").fetchall()
    assert [r[0] for r in rows[:2]] == ["P1", "P2"]
    assert all("@" not in r[2] for r in rows)


def test_no_config_means_no_links(db):
    assert fl.issue_links(db, "p", "i1", "alice@x.com", _papers()) == {}


# ---------------------------------------------------------------- 수집

def _issue(db, delivered_minutes_ago=60):
    links = fl.issue_links(db, "p", "i1", "alice@x.com", _papers())
    fl.mark_delivered(db, "i1", "alice@x.com", datetime.now(timezone.utc) - timedelta(minutes=delivered_minutes_ago))
    return links


def _at(minutes_ago=0, seconds=0):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago) + timedelta(seconds=seconds)).isoformat()


def test_valid_click_is_recorded_once_and_reaches_valid_reactions(db, configured):
    """이 테스트가 잡는 것: 같은 행을 두 번 가져오면 두 번 세는 것, 유효 반응이 학습 입력에 안 오는 것."""
    links = _issue(db)
    row = {"received_at": _at(10), "token": _token_of(links["2609.00001"]["more"])}
    assert fl.import_rows(db, [row], SECRET)["valid"] == 1
    assert fl.import_rows(db, [row], SECRET)["duplicate"] == 1
    got = fl.valid_reactions(db, "p")
    assert [(r["paper_key"], r["action"], r["item_no"]) for r in got] == [("2609.00001", "more", "P1")]


def test_forged_unknown_and_expired_tokens_never_count(db, configured):
    """이 테스트가 잡는 것: 서명 없는 행·우리가 발급하지 않은 tid·만료 뒤 클릭을 유효 반응으로 세는 것."""
    _issue(db)
    forged = {"received_at": _at(5), "token": "v1.x.more.2000000000.bad"}
    unknown = {"received_at": _at(5), "token": fl.make_token("never-issued", "more", 2000000000, SECRET)}
    expired = {"received_at": _at(5), "token": fl.make_token("never-issued", "out", 1000, SECRET)}
    counts = fl.import_rows(db, [forged, unknown, expired], SECRET)
    assert counts == {"bad_signature": 1, "unknown_tid": 1, "expired": 1}
    assert fl.valid_reactions(db, "p") == []


def test_scanner_prefetch_right_after_delivery_is_quarantined(db, configured):
    """메일 보안 스캐너는 발송 직후 링크를 연다. 이 테스트가 잡는 것: 그 열람을 사용자 반응으로 세는 것."""
    links = fl.issue_links(db, "p", "i1", "alice@x.com", _papers())
    delivered = datetime.now(timezone.utc) - timedelta(minutes=30)
    fl.mark_delivered(db, "i1", "alice@x.com", delivered)
    early = {"received_at": (delivered + timedelta(seconds=20)).isoformat(), "token": _token_of(links["2609.00001"]["useful"])}
    later = {"received_at": (delivered + timedelta(minutes=10)).isoformat(), "token": _token_of(links["2609.00002"]["useful"])}
    counts = fl.import_rows(db, [early, later], SECRET)
    assert counts == {"quarantined_prefetch": 1, "valid": 1}
    assert [r["paper_key"] for r in fl.valid_reactions(db, "p")] == ["2609.00002"]


def test_all_three_buttons_opened_together_are_quarantined(db, configured):
    """사람은 한 논문의 버튼 셋을 몇 초 안에 다 누르지 않는다. 이 테스트가 잡는 것: 링크를 전부 여는 봇의 열람을 세는 것."""
    links = _issue(db)
    rows = [{"received_at": _at(10, seconds=i), "token": _token_of(links["2609.00001"][a])}
            for i, a in enumerate(("more", "useful", "out"))]
    fl.import_rows(db, rows, SECRET)
    assert fl.valid_reactions(db, "p") == []
    with sqlite3.connect(db) as con:
        assert {s for (s,) in con.execute("SELECT status FROM feedback_events")} == {"quarantined_burst"}


def test_undo_cancels_the_earlier_click(db, configured):
    """확인 화면의 "취소" 는 앞선 클릭을 무효로 만든다. 이 테스트가 잡는 것: 취소해도 반응이 학습에 남는 것."""
    links = _issue(db)
    token = _token_of(links["2609.00001"]["out"])
    tid, _action, exp = fl.verify_token(token, SECRET)
    undo = fl.make_token(tid, "undo", exp, SECRET)
    fl.import_rows(db, [{"received_at": _at(10), "token": token}, {"received_at": _at(9), "token": undo}], SECRET)
    assert fl.valid_reactions(db, "p") == []


def test_sync_imports_rows_and_remembers_where_it_stopped(db, configured):
    """이 테스트가 잡는 것: 수집 요청에 서명을 안 붙이는 것, 다음 수집이 처음부터 다시 가져가는 것, 웹앱 거부를 성공으로 넘기는 것."""
    links = _issue(db)
    seen = []
    row = {"received_at": _at(10), "token": _token_of(links["2609.00001"]["more"])}

    class Resp:
        def __init__(self, data): self._data = data
        def raise_for_status(self): pass
        def json(self): return self._data

    class Client:
        def __init__(self, data): self.data = data
        async def get(self, url, **kw):
            seen.append(url)
            return Resp(self.data)

    counts = asyncio.run(fl.sync(db, Client({"rows": [row]})))
    assert counts["valid"] == 1 and "sig=" in seen[0] and "since=&" in seen[0]
    asyncio.run(fl.sync(db, Client({"rows": []})))
    assert "since=" + row["received_at"].replace(":", "%3A").replace("+", "%2B") in seen[1]
    with pytest.raises(RuntimeError):
        asyncio.run(fl.sync(db, Client({"error": "forbidden"})))


def test_migrate_knows_the_feedback_tables():
    """이 테스트가 잡는 것: 운영 DB 이관 목록에서 반응 표가 빠져 새벽 스캔이 SchemaOutOfDate 로 멈추는 것."""
    import migrate
    assert "feedback_links" in dict(migrate.owners("operational"))


# ---------------------------------------------------------------- 메일

@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """digest 가 읽는 DB·재현 경로를 임시로(test_digest.isolated_db 와 같은 방식)."""
    import server, storage
    store = tmp_path / "test.db"
    repro = tmp_path / "repro"
    repro.mkdir()
    storage.init_storage(store)
    for mod in (server, storage):
        monkeypatch.setattr(mod, "DB_PATH", store)
        monkeypatch.setattr(mod, "REPRO_DIR", repro)


def test_buttons_render_in_both_mails_only_when_links_exist(isolated_store):
    """이 테스트가 잡는 것: 링크가 있는데 버튼이 한쪽 판에서 빠지는 것, 링크가 없는데 빈 버튼이 생기는 것, href 이스케이프 누락."""
    links = {"more": URL + "?t=a&x=<b>", "useful": URL + "?t=b", "out": URL + "?t=c"}
    paper = {"arxiv_id": "p1", "title": "논문", "_feedback_links": links,
             "_score": {"priority": 1.0, "core_hits": [], "domain_hits": [], "venue_hit": None}}
    scan = {"papers": [paper], "candidates_found": 1}
    html, text = digest.generate_digest_html(scan, "t"), digest.generate_digest(scan, "t")
    for label in ("더 보고 싶음", "유용함", "관심 밖"):
        assert label in html and label in text
    assert "&amp;x=&lt;b&gt;" in html and "<b>" not in html.split("더 보고 싶음")[0][-200:]
    bare = dict(paper); bare.pop("_feedback_links")
    plain = {"papers": [bare], "candidates_found": 1}
    assert "더 보고 싶음" not in digest.generate_digest_html(plain, "t")
    assert "반응 :" not in digest.generate_digest(plain, "t")


def test_deliver_gives_each_recipient_own_buttons_and_marks_delivery(tmp_path, monkeypatch, configured, isolated_store):
    """이 테스트가 잡는 것: 발송 루프가 버튼을 안 싣는 것, 두 수신자에게 같은 링크를 보내는 것, 발송 뒤 delivered_at 을 안 남겨
    선열람 격리가 못 도는 것, 링크 생성 실패가 메일 발송을 막는 것."""
    import research_profile as rp
    import run_profile_scan as rps
    import email_delivery
    db = tmp_path / "p.db"
    rp.create_profile(db, "p", "P", ["alpha"])
    rp.add_recipient(db, "p", "alice@x.com")
    rp.add_recipient(db, "p", "bob@x.com")
    sent = []
    monkeypatch.setattr(email_delivery, "send_digest_email", lambda text, subject, to, html: sent.append((to[0], html)))
    paper = {"arxiv_id": "2609.00001", "title": "A",
             "_score": {"priority": 1.0, "core_hits": [], "domain_hits": [], "venue_hit": None}}
    status = rps._deliver(db, "p", {"papers": [paper], "candidates_found": 1}, "")
    assert status.endswith("2명") and len(sent) == 2
    hrefs = [html.split('href="')[1].split('"')[0] for _to, html in sent if "더 보고 싶음" in html]
    assert len(hrefs) == 2 and hrefs[0] != hrefs[1]
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM feedback_tokens WHERE delivered_at IS NOT NULL").fetchone()[0] == 2

    def boom(*a, **k):
        raise RuntimeError("링크 생성 실패")

    monkeypatch.setattr(fl, "issue_links", boom)
    sent.clear()
    assert rps._deliver(db, "p", {"papers": [paper], "candidates_found": 1}, "").endswith("2명")
    assert len(sent) == 2 and all("더 보고 싶음" not in html for _to, html in sent)


def test_deliver_records_the_issue_ledger_even_without_buttons(tmp_path, monkeypatch, isolated_store):
    """2026-09-16 운영 화면용. 이 테스트가 잡는 것: 버튼 설정이 없을 때 회차 기록이 안 남는 것, 실패 회차를 성공으로 적는 것,
    회차 논문 목록에 위치·링크·적중 키워드가 빠지는 것."""
    import mail_ledger
    import research_profile as rp
    import run_profile_scan as rps
    import email_delivery
    db = tmp_path / "l.db"
    rp.create_profile(db, "p", "P", ["alpha"])
    rp.add_recipient(db, "p", "alice@x.com")
    rp.add_recipient(db, "p", "bob@x.com")
    paper = {"arxiv_id": "2609.00001", "title": "A", "_score": {"priority": 1.0, "core_hits": ["alpha"], "domain_hits": [], "venue_hit": None}}

    def half(text, subject, to, html):
        if to[0].startswith("bob"):
            raise RuntimeError("550")
    monkeypatch.setattr(email_delivery, "send_digest_email", half)
    rps._deliver(db, "p", {"papers": [paper], "candidates_found": 1}, "")
    issues = mail_ledger.list_issues(db, "p")
    assert len(issues) == 1 and issues[0]["status"] == "partial" and issues[0]["recipients_sent"] == 1
    assert issues[0]["subject"].startswith("[연구 동향 브리핑(P)]")
    assert issues[0]["items"] == [{"position": 1, "paper_key": "2609.00001", "title": "A",
                                   "link": "https://arxiv.org/abs/2609.00001", "core_hits": ["alpha"]}]
    assert mail_ledger.counts(db, "p")["issues"] == 1


# ── 스스로 닫히는 중계 페이지(2026-09-16) — 버튼 → 정적 페이지 → 웹앱(mode=json) → 탭 닫힘 ────────────────────
def test_relay_page_carries_token_and_deploy_id_but_never_the_webapp_url(configured, db, monkeypatch):
    """이 테스트가 잡는 것: 중계 페이지를 켰는데 버튼이 여전히 웹앱으로 직행하는 것, 토큰이 페이지 경유에서 달라지는 것,
    **공개 저장소에 웹앱 주소가 실리는 것**(페이지는 배포 id 만 받는다), 배포 id 없이 링크를 만드는 것."""
    import summarize_engine as engine
    monkeypatch.setitem(engine.ENV, "FEEDBACK_PAGE_URL", PAGE)
    links = fl.issue_links(db, "p", "i1", "alice@x.com", _papers())
    url = links["2609.00001"]["more"]
    assert url.startswith(PAGE + "?d=" + DEPLOY + "&t=")
    assert "script.google.com" not in url                      # 주소는 배포 id 로만 간다
    token = url.split("&t=", 1)[1]
    parts = token.split(".")
    assert len(parts) == 5 and parts[0] == fl.TOKEN_VERSION and parts[2] == "more"
    assert fl.sign(".".join(parts[:4]), SECRET) == parts[4]     # 서명은 페이지 경유와 무관하게 같다


def test_relay_page_falls_back_to_the_webapp_when_unset_or_unusable(configured, db, monkeypatch):
    """이 테스트가 잡는 것: 페이지를 설정하지 않았는데 링크 모양이 바뀌는 것(종전 메일과 달라진다), http·빈 값을 페이지로 받는 것,
    웹앱 주소가 표준 형식이 아닐 때 배포 id 를 엉뚱하게 뽑아 링크를 깨는 것."""
    import summarize_engine as engine
    assert fl.issue_links(db, "p", "i1", "a@x.com", _papers())["2609.00001"]["more"].startswith(URL + "?t=")
    monkeypatch.setitem(engine.ENV, "FEEDBACK_PAGE_URL", "http://insecure.example/reaction/")
    assert fl.issue_links(db, "p", "i2", "a@x.com", _papers())["2609.00001"]["more"].startswith(URL + "?t=")
    monkeypatch.setitem(engine.ENV, "FEEDBACK_PAGE_URL", PAGE)
    monkeypatch.setitem(engine.ENV, "FEEDBACK_WEBAPP_URL", "https://script.google.com/a/macros/x/exec")
    got = fl.issue_links(db, "p", "i3", "a@x.com", _papers())["2609.00001"]["more"]
    assert got.startswith("https://script.google.com/a/macros/x/exec?t=") and PAGE not in got


def test_deploy_id_only_accepts_the_apps_script_web_app_form():
    """이 테스트가 잡는 것: 다른 호스트·다른 경로의 URL 에서 id 를 뽑아 중계 페이지가 남의 주소를 부르게 되는 것(열린 전달자)."""
    assert fl.deploy_id(URL) == DEPLOY
    assert fl.deploy_id(URL + "/") == DEPLOY
    for bad in ("https://evil.example.com/macros/s/%s/exec" % DEPLOY,
                "https://script.google.com.evil.test/macros/s/%s/exec" % DEPLOY,
                "https://script.google.com/macros/s/short/exec",
                "https://script.google.com/macros/s/%s/dev" % DEPLOY, "", "not a url"):
        assert fl.deploy_id(bad) == "", bad


def test_relay_page_source_has_no_endpoint_or_secret():
    """이 테스트가 잡는 것: 공개 저장소의 정적 페이지에 웹앱 주소·비밀키·배포 id 가 박히는 것, 페이지가 임의 호스트를 부르게 되는 것."""
    from pathlib import Path
    src = Path("web/reaction/index.html").read_text(encoding="utf-8")
    assert '"https://script.google.com/macros/s/" + deployId + "/exec"' in src     # 주소는 배포 id 로 조립한다
    assert "AKfycb" not in src and "FEEDBACK_HMAC_SECRET" not in src
    code = src.split("<script>", 1)[1]                                             # 주석이 아니라 코드에서 세어야 한다
    assert code.count('"https://') == 1                                            # 코드가 부를 수 있는 절대 주소는 하나뿐
    assert "mode=json" in src and 'name="robots" content="noindex' in src


def test_relay_page_must_be_github_pages(configured, db, monkeypatch):
    """Codex 검토(2026-09-16 P1). 이 테스트가 잡는 것: 임의 https 주소를 중계 페이지로 받아 서명 토큰이 제3자 페이지로 가는 것."""
    import summarize_engine as engine
    for bad in ("https://evil.example/reaction/", "https://github.io.evil.test/x/", "https://example.github.io.evil/x/", "http://user.github.io/x/"):
        monkeypatch.setitem(engine.ENV, "FEEDBACK_PAGE_URL", bad)
        assert fl.page_url() == "", bad
        assert fl.issue_links(db, "p", f"i-{hash(bad)}", "a@x.com", _papers())["2609.00001"]["more"].startswith(URL + "?t=")
    monkeypatch.setitem(engine.ENV, "FEEDBACK_PAGE_URL", "https://someone.github.io/agent/web/reaction/")
    assert fl.page_url().startswith("https://someone.github.io/")
