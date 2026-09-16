"""PDF·저장소·링크·새벽 메일 감시의 보안 회귀 테스트."""

from __future__ import annotations

import asyncio
import importlib
import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

import docker_runner as dr
import server


def _run(coro):
    return asyncio.run(coro)


class _Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], delay: float = 0.0):
        self.chunks = chunks
        self.delay = delay

    async def __aiter__(self):
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk


@pytest.fixture
def isolated_server(tmp_path, monkeypatch):
    """서버의 저장 경로를 테스트 전용으로 고정해 운영 DB를 건드리지 않는다."""
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server, "DB_PATH", tmp_path / "papers.db")
    monkeypatch.setattr(server, "PDF_DIR", tmp_path / "pdfs")
    monkeypatch.setattr(server, "TEXT_DIR", tmp_path / "text")
    monkeypatch.setattr(server, "SUMMARY_DIR", tmp_path / "summaries")
    monkeypatch.setattr(server, "IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr(server, "REPRO_DIR", tmp_path / "repro")
    for directory in ("pdfs", "text", "summaries", "images", "repro"):
        (tmp_path / directory).mkdir()
    server._init_storage()
    return tmp_path


_ARXIV_FEED = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2608.1v1</id>
    <title>Security Test Paper</title>
    <summary>초록이다.</summary>
    <published>2026-08-28T00:00:00Z</published>
    <author><name>A. Author</name></author>
  </entry>
</feed>"""


def _patch_arxiv_transport(monkeypatch, pdf_response_factory):
    """arXiv 메타·HTML·PDF 응답을 네트워크 없이 주입한다."""
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "export.arxiv.org" in url:
            return httpx.Response(200, text=_ARXIV_FEED, request=request)
        if "/html/" in url:
            return httpx.Response(404, request=request)
        if "/pdf/" in url:
            return pdf_response_factory(request)
        return httpx.Response(404, request=request)

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(server.httpx, "AsyncClient", patched)


def test_arxiv_pdf_content_length_is_rejected_before_body_read(monkeypatch, isolated_server):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: Content-Length가 상한을 넘은 PDF를
    운영 코드가 스트림으로 읽거나 디스크에 쓰면 실패한다."""
    monkeypatch.setattr(server, "MAX_PDF_BYTES", 2 * 1024 * 1024)

    def response(request):
        return httpx.Response(
            200,
            headers={"content-length": str(server.MAX_PDF_BYTES + 1)},
            stream=_Chunks([b"must-not-be-read"]),
            request=request,
        )

    _patch_arxiv_transport(monkeypatch, response)
    out = json.loads(_run(server.fetch_paper(server.FetchPaperInput(arxiv_id="2608.1"))))
    assert "PDF 크기 상한 초과 2 MB" in out["error"]
    assert not (isolated_server / "pdfs" / "2608.1.pdf").exists()


def test_arxiv_pdf_without_content_length_is_rejected_while_streaming(monkeypatch, isolated_server):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: Content-Length 없는 응답을 누적
    검사하지 않고 모두 메모리와 디스크에 쓰면 실패한다."""
    monkeypatch.setattr(server, "MAX_PDF_BYTES", 2 * 1024 * 1024)

    def response(request):
        return httpx.Response(
            200,
            stream=_Chunks([b"%PDF-1.4", b"x" * (server.MAX_PDF_BYTES + 1)]),
            request=request,
        )

    _patch_arxiv_transport(monkeypatch, response)
    out = json.loads(_run(server.fetch_paper(server.FetchPaperInput(arxiv_id="2608.2"))))
    assert "PDF 크기 상한 초과 2 MB" in out["error"]
    assert not (isolated_server / "pdfs" / "2608.2.pdf").exists()


def _patch_oa_transport(monkeypatch, handler):
    """OA 다운로드용 AsyncClient만 MockTransport로 바꾼다."""
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(server.httpx, "AsyncClient", patched)


def test_oa_pdf_stream_limit_is_enforced_without_content_length(monkeypatch, tmp_path):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: OA PDF만 상한 검사에서 빠져
    Content-Length 없는 큰 응답을 저장하면 실패한다."""
    monkeypatch.setattr(server, "MAX_PDF_BYTES", 2 * 1024 * 1024)
    monkeypatch.setattr(server, "MAX_PDF_DOWNLOAD_SECONDS", 2)

    def handler(request):
        return httpx.Response(
            200,
            stream=_Chunks([b"%PDF-1.4", b"x" * (server.MAX_PDF_BYTES + 1)]),
            request=request,
        )

    _patch_oa_transport(monkeypatch, handler)
    monkeypatch.setattr(server, "ingest_local_pdf", lambda *args: {"path": str(tmp_path)})
    with pytest.raises(ValueError, match="PDF 크기 상한 초과 2 MB"):
        _run(server.fetch_pdf_from_url("https://papers.example.test/paper.pdf"))


def test_oa_redirect_to_private_ip_is_rejected_before_following(monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: redirect target을 검증하지 않고
    localhost 또는 사설 IP로 요청을 보내면 실패한다."""
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(
            302,
            headers={"location": "http://127.0.0.1/internal.pdf"},
            request=request,
        )

    _patch_oa_transport(monkeypatch, handler)
    with pytest.raises(ValueError, match="사설 IP"):
        _run(server.fetch_pdf_from_url("https://papers.example.test/start"))
    assert seen == ["https://papers.example.test/start"]


def test_oa_final_http_url_is_rejected(monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: HTTPS에서 HTTP로 끝나는 PDF
    리다이렉트를 최종 URL 검사 없이 허용하면 실패한다."""
    step = {"n": 0}

    def handler(request):
        step["n"] += 1
        if step["n"] == 1:
            return httpx.Response(
                302,
                headers={"location": "http://papers.example.test/final.pdf"},
                request=request,
            )
        return httpx.Response(200, content=b"%PDF-1.4", request=request)

    _patch_oa_transport(monkeypatch, handler)
    with pytest.raises(ValueError, match="최종 URL이 https가 아님"):
        _run(server.fetch_pdf_from_url("https://papers.example.test/start"))


def test_oa_download_has_total_time_limit(monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 읽기 타임아웃만 두고 전체 스트림
    시간 상한을 없애면 느린 응답이 무기한 작업이 된다."""
    monkeypatch.setattr(server, "MAX_PDF_DOWNLOAD_SECONDS", 0.01)

    def handler(request):
        return httpx.Response(
            200,
            stream=_Chunks([b"%PDF-1.4"], delay=0.05),
            request=request,
        )

    _patch_oa_transport(monkeypatch, handler)
    with pytest.raises(TimeoutError):
        _run(server.fetch_pdf_from_url("https://papers.example.test/slow.pdf"))


@pytest.mark.parametrize("url", [
    "http://arxiv.org/abs/1234.5678",
    "https://user:pass@arxiv.org/abs/1234.5678",
    "https://127.0.0.1/paper.pdf",
    "https://localhost/paper.pdf",
    "https://unknown.example/paper.pdf",
    "https://arxiv.org/" + "x" * 2000,
])
def test_safe_link_rejects_unsafe_mail_urls(url):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 메일 링크가 HTTPS·userinfo·호스트
    허용 목록·길이 정책 중 하나라도 우회하면 실패한다."""
    import link_policy

    assert link_policy.safe_link(url) is None


def test_safe_link_preserves_allowed_url_and_subdomain():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 허용된 논문 링크를 재작성하거나
    정당한 서브도메인을 차단하면 실패한다."""
    import link_policy

    url = "https://export.arxiv.org/abs/1234.5678?x=1"
    assert link_policy.safe_link(url) == url


def test_clone_rejects_github_repo_before_git_when_api_size_is_too_large(monkeypatch, tmp_path):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: GitHub size 사전 검사를 빼서 큰
    저장소에 git clone을 시작하면 실패한다."""
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[:2] == ["gh", "api"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps({"size": dr.MAX_REPO_KB + 1}), stderr=""
            )
        raise AssertionError(f"큰 저장소에서 실행되면 안 되는 명령: {cmd}")

    monkeypatch.setattr(dr.subprocess, "run", fake_run)
    path, detail = dr._clone("https://github.com/example/large", tmp_path)
    assert (path, detail) == (None, "repo_too_large")
    assert [call[0][:2] for call in calls] == [["gh", "api"]]


def test_clone_uses_filter_lfs_skip_and_post_clone_du(monkeypatch, tmp_path):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: partial clone·submodule 차단·LFS
    smudge 억제·clone 후 디스크 검사를 빠뜨리면 실패한다."""
    calls = []
    clone_dest = {}

    def fake_run(cmd, *args, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[:2] == ["gh", "api"]:
            raise subprocess.CalledProcessError(1, cmd, stderr="gh unavailable")
        if cmd == ["git", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="git version 2.53.0", stderr="")
        if cmd[:2] == ["git", "clone"]:
            destination = Path(cmd[-1])
            destination.mkdir()
            clone_dest["path"] = destination
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["du", "-sk"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="12\t" + cmd[-1], stderr="")
        raise AssertionError(f"예상 밖 명령: {cmd}")

    monkeypatch.setattr(dr.subprocess, "run", fake_run)
    path, detail = dr._clone("https://github.com/example/small", tmp_path)
    clone_cmd = next(cmd for cmd, _ in calls if cmd[:2] == ["git", "clone"])
    clone_kwargs = next(kwargs for cmd, kwargs in calls if cmd[:2] == ["git", "clone"])
    assert path == clone_dest["path"]
    assert detail == ""
    assert "--no-recurse-submodules" in clone_cmd
    assert "--filter=blob:limit=20m" in clone_cmd
    assert clone_kwargs["env"]["GIT_LFS_SKIP_SMUDGE"] == "1"
    assert any(cmd[:2] == ["du", "-sk"] for cmd, _ in calls)


def test_clone_removes_post_clone_repo_when_du_exceeds_limit(monkeypatch, tmp_path):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: gh를 쓰지 못하는 환경에서 clone
    후 실제 디스크 사용량 상한과 초과 디렉터리 삭제를 빼면 실패한다."""
    clone_dest = {}

    def fake_run(cmd, *args, **kwargs):
        if cmd == ["git", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="git version 2.53.0", stderr="")
        if cmd[:2] == ["git", "clone"]:
            destination = Path(cmd[-1])
            destination.mkdir()
            clone_dest["path"] = destination
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["du", "-sk"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout=f"{dr.MAX_REPO_KB + 1}\t{cmd[-1]}", stderr=""
            )
        raise AssertionError(f"예상 밖 명령: {cmd}")

    monkeypatch.setattr(dr.subprocess, "run", fake_run)
    path, detail = dr._clone("https://gitlab.com/example/large", tmp_path)
    assert (path, detail) == (None, "repo_too_large")
    assert not clone_dest["path"].exists()


def test_reproduce_preserves_repo_too_large_in_attempt_log_and_fail_detail(monkeypatch, tmp_path):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 새 clone 사유를 reproduce의
    attempts_log 또는 저장 결과에서 버리면 실패한다."""
    monkeypatch.setattr(dr.server, "REPRO_DIR", tmp_path / "repro")
    monkeypatch.setattr(
        dr.code_finder,
        "find_repo_candidates",
        lambda _arxiv_id: {"in_text": [{
            "url": "https://github.com/example/large",
            "source": "in_text",
            "confidence": "author-stated",
        }], "github_search": []},
    )
    monkeypatch.setattr(dr, "_clone", lambda _url, _parent: (None, "repo_too_large"))
    saved = []
    monkeypatch.setattr(dr.server, "save_repro_result", lambda *args, **kwargs: saved.append((args, kwargs)))

    result = dr.reproduce("2608.1", max_attempts=1)
    assert result["log"][0]["fail_detail"] == "repo_too_large"
    assert saved[0][1]["fail_detail"] == "repo_too_large"


def _daily_mail_module():
    return importlib.import_module("scripts.check_daily_mail")


def _make_daily_db(path: Path, *, first_digest: str | None = "2026-09-15T20:10:00Z"):
    with sqlite3.connect(path) as con:
        con.executescript(
            "CREATE TABLE profiles (profile_id TEXT PRIMARY KEY, schedule_frequency TEXT, last_digest_at TEXT);"
            "CREATE TABLE profile_recipients (profile_id TEXT, email TEXT, active INTEGER);"
        )
        con.execute("INSERT INTO profiles VALUES ('daily-a', 'daily', ?)", (first_digest,))
        con.execute("INSERT INTO profiles VALUES ('manual-x', 'manual', NULL)")
        con.execute("INSERT INTO profile_recipients VALUES ('daily-a', 'first@example.test', 1)")
        con.execute("INSERT INTO profile_recipients VALUES ('manual-x', 'manual@example.test', 1)")


def _daily_log(*, ended: bool = True, delivery: str = "발송 완료 → 1명") -> str:
    payload = json.dumps({"daily-a": {"status": "ok", "delivery": delivery}}, ensure_ascii=False, indent=2)
    end = "\n=== 2026-09-16T00:30:00Z 종료 (exit 0) ===" if ended else ""
    return "=== 2026-09-15T20:05:00Z 시작 (pid 123) ===\n" + payload + end + "\n"


_CHECK_NOW = datetime(2026, 9, 16, 0, 40, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _no_real_scan_lock(tmp_path, monkeypatch):
    """점검기의 기본 락 경로를 운영 `logs/daily_scan.lock` 에서 뗀다. 2026-09-16 실측: 새벽 스캔을 수동으로 돌리는 동안 전체 테스트를
    돌렸더니 `--lock` 을 안 준 세 테스트가 진짜 락을 보고 '보류(0)' 로 끝나 실패했다 — 테스트는 운영 상태를 보면 안 된다."""
    monkeypatch.setattr(_daily_mail_module(), "DEFAULT_LOCK", tmp_path / "unused_scan.lock")


def test_daily_mail_check_returns_zero_when_scan_db_and_delivery_are_current(tmp_path):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 정상 발송을 부재 알림으로 오판해
    메일을 보내거나 비정상 종료하면 실패한다."""
    checker = _daily_mail_module()
    db = tmp_path / "papers.db"
    log = tmp_path / "daily_scan.log"
    stamp = tmp_path / "daily_mail_alert.stamp"
    _make_daily_db(db)
    log.write_text(_daily_log(), encoding="utf-8")
    assert checker.main(["--db", str(db), "--log", str(log), "--stamp", str(stamp)], now=_CHECK_NOW) == 0
    assert not stamp.exists()


def test_daily_mail_alert_uses_first_daily_recipients_and_suppresses_duplicate(monkeypatch, tmp_path):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 문제 발견 때 첫 daily 프로필로
    알림을 보내지 않거나 stamp 중복 방지를 빼서 두 번 보내면 실패한다."""
    checker = _daily_mail_module()
    db = tmp_path / "papers.db"
    log = tmp_path / "daily_scan.log"
    stamp = tmp_path / "daily_mail_alert.stamp"
    _make_daily_db(db)
    log.write_text(_daily_log(delivery="발송 실패: SMTP"), encoding="utf-8")
    sent = []

    import email_delivery
    monkeypatch.setattr(email_delivery, "send_digest_email", lambda *args: sent.append(args))
    args = ["--db", str(db), "--log", str(log), "--stamp", str(stamp)]
    assert checker.main(args, now=_CHECK_NOW) == 2
    assert checker.main(args, now=_CHECK_NOW) == 2
    assert len(sent) == 1
    assert sent[0][1] == checker.ALERT_SUBJECT
    assert sent[0][2] == ["first@example.test"]
    assert sent[0][3].startswith("<pre>")
    assert stamp.read_text(encoding="utf-8").strip() == "2026-09-16"


def test_daily_mail_dry_run_reports_problem_without_sending_or_stamping(monkeypatch, tmp_path):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: --dry-run에서 SMTP를 호출하거나
    알림 stamp를 기록하면 실패한다."""
    checker = _daily_mail_module()
    db = tmp_path / "papers.db"
    log = tmp_path / "daily_scan.log"
    stamp = tmp_path / "daily_mail_alert.stamp"
    _make_daily_db(db, first_digest="2026-09-14T20:10:00Z")
    log.write_text(_daily_log(), encoding="utf-8")
    import email_delivery
    monkeypatch.setattr(email_delivery, "send_digest_email", lambda *args: pytest.fail("dry-run 발송"))
    assert checker.main(
        ["--dry-run", "--db", str(db), "--log", str(log), "--stamp", str(stamp)],
        now=_CHECK_NOW,
    ) == 2
    assert not stamp.exists()


def test_daily_mail_alert_goes_to_the_operator_common_to_all_daily_profiles(monkeypatch, tmp_path):
    """2026-09-16. 이 테스트가 무엇을 망가뜨리면 실패하는가: 알림을 첫 프로필의 수신자 전원(팀원 포함)에게 보내면 실패한다 — 모든 daily
    프로필에 공통인 주소(운영자)에게만 가야 한다. 공통 주소가 없으면 첫 프로필로 떨어진다."""
    checker = _daily_mail_module()
    db = tmp_path / "papers.db"
    log = tmp_path / "daily_scan.log"
    stamp = tmp_path / "daily_mail_alert.stamp"
    _make_daily_db(db)
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO profile_recipients VALUES ('daily-a', 'operator@example.test', 1)")
        con.execute("INSERT INTO profiles VALUES ('daily-b', 'daily', NULL)")
        con.execute("INSERT INTO profile_recipients VALUES ('daily-b', 'operator@example.test', 1)")
    log.write_text(_daily_log(delivery="발송 실패: SMTP"), encoding="utf-8")
    sent = []
    import email_delivery
    monkeypatch.setattr(email_delivery, "send_digest_email", lambda *args: sent.append(args))
    assert checker.main(["--db", str(db), "--log", str(log), "--stamp", str(stamp)], now=_CHECK_NOW) == 2
    assert sent[0][2] == ["operator@example.test"]


def test_daily_mail_check_defers_while_scan_holds_the_lock(monkeypatch, tmp_path):
    """2026-09-16. 이 테스트가 무엇을 망가뜨리면 실패하는가: 새벽 스캔이 아직 락을 잡고 도는 중인데 "종료 줄 없음"으로 알림을 보내면 실패한다
    (프로필 넷이면 06:30 을 넘길 수 있다). 락이 풀린 뒤에는 원래대로 판정해야 한다."""
    import fcntl
    checker = _daily_mail_module()
    db = tmp_path / "papers.db"
    log = tmp_path / "daily_scan.log"
    stamp = tmp_path / "daily_mail_alert.stamp"
    lock = tmp_path / "daily_scan.lock"
    _make_daily_db(db)
    log.write_text(_daily_log(ended=False), encoding="utf-8")
    sent = []
    import email_delivery
    monkeypatch.setattr(email_delivery, "send_digest_email", lambda *args: sent.append(args))
    args = ["--db", str(db), "--log", str(log), "--stamp", str(stamp), "--lock", str(lock)]
    with open(lock, "w") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        assert checker.main(args, now=_CHECK_NOW) == 0 and sent == []
    assert checker.main(args, now=_CHECK_NOW) == 2 and len(sent) == 1
