"""⑨ 배달 — SMTP 직전 최종 링크 감사(link_policy.audit_mail, 2026-09-30).

메일 링크는 만드는 자리가 여럿이라 자리마다 검사해도 새 자리가 빠진다(실제로 DOI 가 `safe_link` 를 안 거쳤다).
여기서는 **실제로 실린 링크**를 보는 마지막 방어선이 막아야 할 것을 막고, 막을 때도 메일은 보내는지를 본다.
"""
import pytest

import link_policy
from link_policy import MailLinkAuditor, audit_html, audit_mail, audit_text

PUBLIC = ["151.101.3.42"]


def _auditor(dns=None, redirects=None, **kw):
    """dns: host → 주소 목록(없으면 공인). redirects: url → (status, location)."""
    dns = dns or {}
    redirects = redirects or {}
    calls = []

    def lookup(host):
        return dns.get(host, PUBLIC)

    def probe(url, timeout):
        calls.append(url)
        if url not in redirects:
            raise ConnectionError("가짜 네트워크에 없는 주소")
        return redirects[url]
    a = MailLinkAuditor(lookup=lookup, probe=probe, **kw)
    a.calls = calls
    return a


@pytest.mark.parametrize("url", [
    "javascript:alert(1)",
    "http://arxiv.org/abs/2609.00001",
    "https://127.0.0.1/x",
    "https://[::1]/x",
    "https://localhost/x",
    "https://arxiv.org.evil.com/abs/1",
    "https://user@arxiv.org/abs/1",
    "https://arxiv.org@evil.com/abs/1",
    "data:text/html,<script>alert(1)</script>",
    "https://evil.example/paper",
    "https://example.github.io/agent/web/reaction/?t=x",        # 남의 Pages — 정확한 출처만
    "https://moon201595.github.io.evil.test/agent/x",
    "https://script.google.com.evil.test/macros/s/AKfycbAAAAAAAAAAAAAAAAAAAA/exec",
])
def test_unsafe_links_are_blocked(url):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 스킴·IP 리터럴·localhost·접미사 위장·userinfo·허용 목록·반응 버튼 출처 검사 중
    하나라도 빠지면 그 URL 이 통과해 실패한다."""
    assert _auditor().check(url) is not None


def test_allowed_links_pass_including_feedback_origin():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 정상 논문 링크나 우리 반응 버튼을 막으면(메일에서 버튼·제목 링크가 사라진다) 실패한다."""
    a = _auditor()
    for url in ("https://arxiv.org/abs/2609.00001",
                "https://www.mdpi.com/1424-8220/26/19/6075",
                link_policy.FEEDBACK_PAGE_PREFIX + "web/reaction/?d=x&t=v1.a.b.c.d",
                "https://script.google.com/macros/s/AKfycbAAAAAAAAAAAAAAAAAAAA/exec?t=v1.a"):
        assert a.check(url) is None, url
    assert a.calls == []                                     # 리다이렉터가 아니면 HTTP 를 치지 않는다


def test_doi_is_resolved_and_the_landing_host_must_be_allowed():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: DOI 를 풀지 않고 통과시키거나(doi.org 는 남이 목적지를 정한다), 허용 도메인에서
    비허용 도메인으로 가는 리다이렉트를 놓치거나, 여러 홉을 따라가지 않으면 실패한다."""
    good, bad, hop = ("https://doi.org/10.3390/s26196075", "https://doi.org/10.9999/evil", "https://doi.org/10.1/two")
    a = _auditor(redirects={
        good: (302, "https://www.mdpi.com/1424-8220/26/19/6075"),
        bad: (302, "https://predatory.example/paper"),
        hop: (301, "https://dx.doi.org/10.1/two"),
        "https://dx.doi.org/10.1/two": (302, "https://127.0.0.1/admin"),
    })
    assert a.check(good) is None
    assert "허용 목록 밖" in a.check(bad)
    assert a.check(hop) is not None                          # 두 번째 홉에서 IP 리터럴
    assert a.check("https://doi.org/10.1/unreachable").startswith("리다이렉트 확인 실패")
    b = _auditor(redirects={good: (404, "")})
    assert b.check(good).startswith("리다이렉터가 목적지를 주지 않음")


def test_allowed_host_resolving_to_a_private_address_is_blocked():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: DNS 검사를 빼거나(허용 도메인이 내부망을 가리켜도 통과) 주소 중 **하나만** 보면 실패한다."""
    a = _auditor(dns={"arxiv.org": ["151.101.3.42", "10.0.0.5"], "www.mdpi.com": ["fe80::1"], "github.com": []})
    assert "공인 주소가 아님" in a.check("https://arxiv.org/abs/1")
    assert a.check("https://www.mdpi.com/x") is not None
    assert a.check("https://github.com/a/b") is not None
    redir = _auditor(dns={"www.mdpi.com": ["192.168.0.2"]},
                     redirects={"https://doi.org/10.1/x": (302, "https://www.mdpi.com/x")})
    assert redir.check("https://doi.org/10.1/x") is not None   # 리다이렉트 착지 호스트도 DNS 로 본다


def test_blocked_anchor_keeps_its_text_and_shows_the_doi():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 막힌 링크의 href 가 남거나, 제목 글자까지 지워지거나, DOI 를 글자로 안 남기거나,
    통과한 링크를 건드리면 실패한다."""
    html = ('<p><a href="https://arxiv.org/abs/1" style="color:#000">좋은 논문</a> · '
            '<a href="https://doi.org/10.9999/x">원문 DOI ↗</a> · <a href=\'javascript:alert(1)\'>클릭</a></p>')
    out, blocked = audit_html(html, _auditor().check)
    assert '<a href="https://arxiv.org/abs/1" style="color:#000">좋은 논문</a>' in out
    assert "doi.org" not in out.split("DOI 10.9999/x")[0].split("좋은 논문")[1]
    assert "javascript" not in out and 'href="https://doi.org' not in out
    assert "원문 DOI ↗" in out and "클릭" in out
    assert f"DOI 10.9999/x {link_policy.BLOCKED_LABEL}" in out
    assert [u for u, _ in blocked] == ["https://doi.org/10.9999/x", "javascript:alert(1)"]


def test_url_attributes_outside_closed_anchors_are_stripped():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 짝 없는 <a>·이미지 src 처럼 앵커 판정을 비껴가는 자리의 주소를 그대로 두면 실패한다."""
    out, _ = audit_html('<img src="https://evil.example/t.gif"><a href="https://evil.example/x">열린 앵커', _auditor().check)
    assert "evil.example" not in out and "<a" not in out


def test_escaped_href_is_judged_after_unescaping():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: `&amp;` 등 HTML 이스케이프를 풀지 않고 판정해 정상 링크를 막거나, 이스케이프로
    위장한 스킴을 통과시키면 실패한다."""
    ok = link_policy.FEEDBACK_PAGE_PREFIX + "web/reaction/?d=x&amp;t=v1"
    out, blocked = audit_html(f'<a href="{ok}">더 보고 싶음</a><a href="&#106;avascript:alert(1)">x</a>', _auditor().check)
    assert f'href="{ok}"' in out and len(blocked) == 1 and blocked[0][0] == "javascript:alert(1)"


def test_plain_text_urls_are_audited_too():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 평문 파트를 감사하지 않으면(메일 앱이 평문 URL 도 링크로 만든다) 실패한다."""
    text = "원문: https://arxiv.org/abs/1.\n원문: https://doi.org/10.9999/x\n나쁜: http://evil.example/x)"
    out, blocked = audit_text(text, _auditor().check)
    assert "https://arxiv.org/abs/1." in out
    assert f"DOI 10.9999/x {link_policy.BLOCKED_LABEL}" in out
    assert "evil.example" not in out and out.endswith(")")
    assert len(blocked) == 2


def test_a_blocked_link_never_stops_the_mail(monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 감사를 SMTP 경로에서 빼거나, 나쁜 링크 하나로 발송을 멈추면 실패한다(규칙 6)."""
    import smtplib
    import email_delivery
    import summarize_engine as engine
    monkeypatch.setattr(engine, "ENV", {"SMTP_USER": "me@gmail.com", "SMTP_PASSWORD": "pw"})
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port): pass
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def starttls(self): pass
        def login(self, user, password): pass
        def send_message(self, msg): sent["msg"] = msg

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    email_delivery.send_digest_email("원문: http://evil.example/x", "제목", ["a@x.com"],
                                     '<a href="https://evil.example/x">제목</a> <a href="https://arxiv.org/abs/1">A</a>')
    body = sent["msg"].get_body(("html",)).get_content()
    assert "evil.example" not in body and 'href="https://arxiv.org/abs/1"' in body and link_policy.BLOCKED_LABEL in body
    assert "evil.example" not in sent["msg"].get_body(("plain",)).get_content()


def test_audit_crash_strips_every_link_but_still_returns_a_mail(monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 감사기가 예외로 죽을 때 확인 안 된 링크를 그대로 싣거나, 예외를 발송까지 올려 메일을
    막으면 실패한다."""
    a = _auditor()
    monkeypatch.setattr(a, "check", lambda url: 1 / 0)
    text, html, blocked = audit_mail("https://arxiv.org/abs/1", '<a href="https://arxiv.org/abs/1">A</a>', a)
    assert "href" not in html and "arxiv.org" not in text and blocked


def test_timeout_is_not_cached_but_real_verdicts_are():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 시간 초과를 캐시해 다음 수신자 메일에서도 멀쩡한 DOI 를 막거나, 판정을 캐시하지 않아
    수신자마다 같은 DOI 를 다시 치면 실패한다."""
    url = "https://doi.org/10.3390/x"
    a = _auditor(redirects={url: (302, "https://www.mdpi.com/x")}, budget_s=0)
    assert a.check(url) == link_policy._TIMEOUT
    a._budget_s = 60
    a.start()
    assert a.check(url) is None and a.check(url) is None
    assert a.calls == [url]


# ── Codex 검토(2026-09-30)가 직접 재현한 우회·결함 — 회귀 방지 ─────────────────────────────────────────────

def test_parser_sees_the_real_href_not_a_lookalike_attribute():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: `data-href` 같은 이름이 비슷한 속성을 판정하고 진짜 href 를 통과시키면 실패한다."""
    out, blocked = audit_html('<a data-href="https://arxiv.org/abs/1" href="https://evil.example/x">X</a>', _auditor().check)
    assert "evil.example" not in out and blocked and blocked[0][0] == "https://evil.example/x"


def test_nested_anchor_image_meta_and_css_url_are_all_removed():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 허용 앵커 안의 중첩 앵커·이미지, meta refresh, CSS url() 중 하나라도 남으면 실패한다."""
    html = ('<a href="https://arxiv.org/abs/1">ok <a href="https://evil.example/n">in</a><img src="https://evil.example/i"></a>'
            '<meta http-equiv="refresh" content="0;url=https://evil.example/m">'
            '<div style="background:url(https://evil.example/c)">t</div><script>location="https://evil.example/s"</script>'
            '<a href="https://evil.example/b"><img src="https://evil.example/j">b</a>')
    out, _ = audit_html(html, _auditor().check)
    assert "evil.example" not in out
    assert 'href="https://arxiv.org/abs/1"' in out and ">t</div>" in out


def test_blocked_log_with_unparseable_url_still_sends(monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 차단 로그가 이상한 URL(`https://[oops`)을 파싱하다 예외를 내 SMTP 까지 못 가면 실패한다."""
    import smtplib
    import email_delivery
    import summarize_engine as engine
    monkeypatch.setattr(engine, "ENV", {"SMTP_USER": "me@gmail.com", "SMTP_PASSWORD": "pw"})
    sent = []

    class FakeSMTP:
        def __init__(self, host, port): pass
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def starttls(self): pass
        def login(self, user, password): pass
        def send_message(self, msg): sent.append(msg)

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    email_delivery.send_digest_email("x https://[oops y", "제목", ["a@x.com"], '<a href="https://[oops">t</a>')
    assert len(sent) == 1


def test_dns_time_counts_against_the_budget():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: DNS 조회가 예산 밖이라 느린 DNS 가 메일 감사를 끝없이 끌면 실패한다."""
    now = [0.0]

    def slow_lookup(host):
        now[0] += 10
        return PUBLIC
    a = MailLinkAuditor(budget_s=1, lookup=slow_lookup, probe=lambda u, t: (200, ""), clock=lambda: now[0])
    assert a.check("https://arxiv.org/abs/1") == link_policy._TIMEOUT
    assert a.check("https://github.com/o/r") is not None


def test_transient_dns_failure_is_retried_on_the_next_mail():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 첫 메일의 순간 DNS 실패를 캐시해 뒤 수신자 메일의 정상 버튼까지 막으면 실패한다."""
    state = {"fail": True}

    def flaky(host):
        if state["fail"]:
            raise OSError("temporary")
        return PUBLIC
    a = MailLinkAuditor(lookup=flaky, probe=lambda u, t: (200, ""))
    page = link_policy.FEEDBACK_PAGE_PREFIX + "web/reaction/?t="
    assert a.check(page + "a") is not None
    state["fail"] = False
    a.start()
    assert a.check(page + "b") is None and a.check(page + "a") is None


def test_plain_text_catches_glued_and_www_urls_and_never_reexposes_a_url_as_doi():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 한글에 붙은 URL·`www.` 주소를 놓치거나, `doi.org/https://evil…` 을 DOI 라며 주소를
    되살리면 실패한다."""
    out, blocked = audit_text("원문https://evil.example/x · www.evil.example/y · https://doi.org/https://evil.example/z", _auditor().check)
    assert "evil.example" not in out and len(blocked) == 3
    ok, _ = audit_text("www.arxiv.org/abs/1", _auditor().check)
    assert ok == "www.arxiv.org/abs/1"


def test_real_landing_hosts_from_2026_10_01_mail():
    """10-01 아침 메일에서 막힌 두 착지 호스트(실측). 망가뜨리면 실패하는 것: copernicus.org 를 목록에서 빼는 것(사용자 결정으로 허용) ·
    하위 도메인 접미사 대조를 흉내 낸 도메인을 받는 것. 2026-10-06 사용자 결정으로 doi.org 착지는 자동 검사만 통과하면 싣는다 —
    여러 회사가 같이 쓰는 콘텐츠 배포 서버(sitecorecontenthub.cloud, 미국용접학회 DOI)도 등록자가 정한 착지라 이제 통과한다(10-01 의 반대 결정).
    다만 doi 를 거치지 않은 그 서버 직접 링크는 여전히 막힌다."""
    isprs = "https://doi.org/10.5194/isprs-annals-xii-4-w1-2026-267-2026"
    welding = "https://doi.org/10.29391/2026.105.022"
    a = _auditor(redirects={
        isprs: (302, "https://isprs-annals.copernicus.org/articles/XII-4-W1-2026/267/2026/"),
        welding: (302, "https://aws-p-001-delivery.sitecorecontenthub.cloud/api/public/content/x"),
    })
    assert a.check(isprs) is None
    assert a.check(welding) is None
    assert a.check("https://aws-p-001-delivery.sitecorecontenthub.cloud/api/public/content/x") == "허용 목록 밖"
    assert a.check("https://evilcopernicus.org/x") is not None


def test_doi_landing_on_an_unlisted_publisher_passes_only_the_automatic_checks():
    """2026-10-06 사용자 결정: doi.org 가 넘겨준 착지는 허용 목록에 없어도 자동 검사를 통과하면 싣는다(10/6 메일 20건이 글자로만 나갔다).
    이 테스트가 무엇을 망가뜨리면 실패하는가: 착지 예외를 지우면 첫 단언이, 예외를 doi 를 거치지 않은 일반 URL 까지 넓히면 둘째 단언이,
    http·단축 URL·파일 공유·동적 DNS·IP·포트·예약 도메인 검사를 하나라도 빼면 해당 단언이, 착지의 DNS 공인 검사를 건너뛰면 마지막 단언이 실패한다."""
    ok_url = "https://doi.org/10.31399/asm.cp.istfa2026p0394"
    landings = {
        "https://doi.org/10.1/plainhttp": "http://journal.example-pub.org/a",
        "https://doi.org/10.1/short": "https://bit.ly/abc",
        "https://doi.org/10.1/drive": "https://drive.google.com/file/d/x",
        "https://doi.org/10.1/ddns": "https://paper.duckdns.org/x",
        "https://doi.org/10.1/ip": "https://203.0.113.9/x",
        "https://doi.org/10.1/port": "https://journal.publisher.org:8443/x",
        "https://doi.org/10.1/reserved": "https://journal.test/x",
        "https://doi.org/10.1/private": "https://intranet-journal.org/x",
    }
    redirects = {ok_url: (302, "https://dl.asminternational.org/istfa/proceedings/ISTFA2026/1/394")}
    redirects.update({k: (302, v) for k, v in landings.items()})
    a = _auditor(dns={"intranet-journal.org": ["10.0.0.5"]}, redirects=redirects)
    assert a.check(ok_url) is None
    assert a.check("https://dl.asminternational.org/istfa/proceedings/ISTFA2026/1/394") == "허용 목록 밖"   # doi 를 안 거친 직접 링크
    for src in landings:
        assert a.check(src) is not None, src
    assert a.check("https://doi.org/10.1/private").startswith("공인 주소가 아님")
