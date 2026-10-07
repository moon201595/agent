"""⑨ 배달 — 메일에 넣을 외부 링크를 허용 목록으로 제한한다.

메일 링크는 클릭 한 번이면 끝나므로 차단 목록으로는 새 호스트를 막을 수
없다. 논문 독자가 실제로 누를 링크만 명시적으로 허용해, 새 도메인이
추가되거나 오타가 생겨도 메일에서 임의 URL로 이어지지 않게 한다.
"""

from __future__ import annotations

import html as _html
import ipaddress
import re
import socket
import time
from typing import Callable
from urllib.parse import urljoin, urlparse


MAX_LINK_LENGTH = 2000

ALLOWED_HOSTS = frozenset({
    "arxiv.org",
    "doi.org",
    "semanticscholar.org",
    "openreview.net",
    "github.com",
    "huggingface.co",
    "ieeexplore.ieee.org",
    "dl.acm.org",
    "link.springer.com",
    "www.nature.com",
    "www.sciencedirect.com",
    "www.mdpi.com",
    "aclanthology.org",
    "proceedings.mlr.press",
    "papers.nips.cc",
    "openaccess.thecvf.com",
    "journals.plos.org",
    "www.frontiersin.org",
    "onlinelibrary.wiley.com",
    "pubs.acs.org",
    "iopscience.iop.org",
    "www.biorxiv.org",
    "www.medrxiv.org",
    "europepmc.org",
    "www.ncbi.nlm.nih.gov",
    # 2026-09-30 추가 — 최근 메일의 DOI 60개를 실제로 풀어 본 착지 호스트 중 알려진 학술 출판사(실측: SPIE 9·T&F 2·PLOS 리다이렉터 2·
    # De Gruyter·IOS Press·Fuji Press 각 1)와, 표본엔 없었지만 같은 급의 대형 출판사 착지 호스트. 소규모·출처 불명 출판사
    # (impactfactor.org·irjernet.com·dipscie.com 등 표본의 12개 호스트)는 **일부러 넣지 않았다** — 그 논문은 링크 없이 DOI 글자로 나간다.
    "spiedigitallibrary.org",
    "tandfonline.com",
    "dx.plos.org",
    "degruyterbrill.com",
    "iospress.nl",
    "fujipress.jp",
    "linkinghub.elsevier.com",
    "academic.oup.com",
    "www.cambridge.org",
    "journals.sagepub.com",
    "www.science.org",
    # 2026-10-01 사용자 결정 — ISPRS Annals 의 DOI 가 isprs-annals.copernicus.org 로 착지해 막혔다(오늘 메일 실측). Copernicus 는
    # 오픈액세스 학술 출판사이고 학술지마다 하위 도메인을 쓴다(접미사 대조라 `*.copernicus.org` 전체가 열린다).
    "copernicus.org",
    "ojs.aaai.org",
    "www.jstage.jst.go.jp",
})


def _is_ip_literal(host: str) -> bool:
    """DNS 조회 없이 URL에 직접 적힌 IPv4·IPv6 리터럴을 거부한다."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def safe_link(url: str) -> str | None:
    """허용된 HTTPS 논문 링크만 원문 그대로 돌려주고 나머지는 버린다."""
    if not isinstance(url, str) or len(url) > MAX_LINK_LENGTH:
        return None
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").rstrip(".").lower()
        _ = parsed.port
    except ValueError:
        return None
    if parsed.scheme.lower() != "https" or not host:
        return None
    if "@" in parsed.netloc or parsed.username is not None or parsed.password is not None:
        return None
    if _is_ip_literal(host) or host == "localhost" or host.endswith(".localhost"):
        return None
    if not any(host == allowed or host.endswith(f".{allowed}") for allowed in ALLOWED_HOSTS):
        return None
    return url


# ---------------------------------------------------------------- 발송 직전 최종 감사(2026-09-30)
#
# 링크는 만드는 자리가 여럿이다(논문 제목·원문·코드 단계·반응 버튼·동향 서술). 자리마다 검사가 있어도 새 자리가 생기면 빠진다 —
# 실제로 DOI 는 `digest.paper_link` 가 `safe_link` 를 거치지 않고 `https://doi.org/…` 로 바로 실었고, doi.org 는 리다이렉터라
# 최종 목적지가 다른 도메인이다. 그래서 SMTP 직전(`email_delivery.send_digest_email`)에 **메일에 실제로 실린 모든 링크**를 한 번 더 본다.
# 안전하지 않은 링크 하나 때문에 메일 전체를 실패시키지 않는다(규칙 6) — 그 링크만 빼고 글자로 남긴다.

# 반응 버튼이 가는 정적 페이지 — GitHub Pages **정확한 출처 하나**. 그전엔 `*.github.io` 전체를 받았다(누구나 만들 수 있는 도메인이다).
FEEDBACK_PAGE_PREFIX = "https://moon201595.github.io/agent/"
# 반응 버튼의 웹앱 직행 경로(페이지를 안 쓸 때) — Apps Script 표준 배포 주소 형식만.
_WEBAPP_RE = re.compile(r"^https://script\.google\.com/macros/s/[A-Za-z0-9_-]{20,200}/exec(?:[?#]|$)")

# 목적지를 남이 정하는 리다이렉터. 여기서 출발한 링크는 서버가 먼저 따라가 착지 호스트를 확인한다.
# **출판사 도메인에 닿으면 멈춘다** — 출판사 안쪽까지 따라가면 봇 차단 페이지로 튕긴다(2026-09-30 실측: iopscience 4건 중 1건이
# validate.perfdrive.com 으로 갔다). 사람의 브라우저는 그리 안 가므로, 따라가면 멀쩡한 링크를 무작위로 막게 된다.
REDIRECTOR_HOSTS = frozenset({"doi.org", "dx.doi.org"})
MAX_HOPS = 5
HOP_TIMEOUT_S = 6.0
AUDIT_BUDGET_S = 90.0          # 메일 한 통의 감사 전체 상한. 넘기면 남은 리다이렉터 링크는 막고 보낸다.

BLOCKED_LABEL = "[링크 차단됨]"
_TIMEOUT = "감사 시간 초과"
_BLOCKED_STYLE = "background-color:#FDF6EC;color:#8A4B00;font-size:12px;"


def _dns_lookup(host: str) -> list[str]:
    """호스트의 IP 목록. 테스트는 conftest 가 가짜로 바꾼다."""
    return sorted({info[4][0] for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)})


def _http_probe(url: str, timeout: float) -> tuple[int, str]:
    """(상태 코드, Location). 리다이렉트를 따라가지 않는다 — 한 홉씩 우리가 검사하고 넘어간다."""
    import httpx
    headers = {"User-Agent": "Mozilla/5.0 paper-harness/1.0 (mail link check)"}
    with httpx.Client(follow_redirects=False, timeout=timeout, headers=headers) as client:
        response = client.head(url)
        if response.status_code in (400, 403, 405, 501):     # HEAD 를 안 받는 서버가 있다 — 본문은 읽지 않는다
            with client.stream("GET", url) as streamed:
                return streamed.status_code, streamed.headers.get("location") or ""
        return response.status_code, response.headers.get("location") or ""


# doi.org 가 넘겨준 출판사 착지(2026-10-06 사용자 결정). 그전에는 착지 호스트도 허용 목록에 있어야 했고, 소규모 출판사는 일부러 뺐다
# (9/30). 그 결과 10/6 하루 메일에서 DOI 링크 20건이 글자로만 나갔다 — 착지 13곳(ASM·Emerald·IAES·ETASR·SPG·…). 사용자: "20편 다
# 들어오게, 위험 검증은 따로 하고 괜찮으면 쭉 허용 — 할 때마다 하는 건 말이 안 된다." DOI 는 출판사가 등록한 공식 경로라 착지는 그
# 출판사가 정한 곳이다. 그래서 **doi.org 를 거쳐 온 착지만** 아래 자동 검사로 받는다 — 본문·초록에서 온 일반 URL 은 여전히 허용 목록만.
# 자동 검사: https·기본 포트·userinfo 없음·IP 리터럴 아님·예약 도메인 아님·아래 범주(단축 URL·파일 공유·동적 DNS·터널) 아님, 그리고
# 홉마다 하던 그대로 DNS 가 **공인 주소만** 가리켜야 한다(_public). 출판사의 질(약탈적 학술지 여부)은 링크 안전과 다른 문제라 여기서 가르지 않는다.
DOI_LANDING_DENY = frozenset({
    # 단축 URL — 착지를 숨긴다
    "bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "is.gd", "buff.ly", "rebrand.ly", "cutt.ly", "shorturl.at", "tiny.cc",
    # 파일 공유·임의 업로드
    "drive.google.com", "docs.google.com", "dropbox.com", "dropboxusercontent.com", "mega.nz", "mediafire.com",
    "wetransfer.com", "1drv.ms", "onedrive.live.com", "box.com", "pastebin.com", "transfer.sh", "file.io",
    # 동적 DNS·터널 — 누구나 이름을 잡는다
    "duckdns.org", "no-ip.com", "no-ip.org", "ddns.net", "hopto.org", "zapto.org", "sytes.net",
    "ngrok.io", "ngrok-free.app", "ngrok.app", "trycloudflare.com", "loca.lt", "serveo.net",
})
_RESERVED_TLDS = frozenset({"example", "test", "invalid", "localhost", "local", "internal", "onion", "lan", "home", "arpa"})


def _doi_landing_ok(url: str) -> bool:
    """doi.org 리다이렉트의 착지가 허용 목록 밖일 때 받을 수 있는가 — 네트워크 없이 보는 부분만. DNS 공인 검사는 감사기가 따로 한다."""
    if len(url) > MAX_LINK_LENGTH:
        return False
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return False
    host = (parsed.hostname or "").rstrip(".").lower()
    if parsed.scheme != "https" or "@" in parsed.netloc or port not in (None, 443):
        return False
    if not host or "." not in host or _is_ip_literal(host) or host.rsplit(".", 1)[-1] in _RESERVED_TLDS:
        return False
    return not any(host == d or host.endswith(f".{d}") for d in DOI_LANDING_DENY)


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").rstrip(".").lower()


def _is_feedback_link(url: str) -> bool:
    return url.startswith(FEEDBACK_PAGE_PREFIX) or bool(_WEBAPP_RE.match(url))


def _statically_safe(url: str) -> bool:
    """네트워크 없이 보는 검사 — https·userinfo·IP 리터럴·localhost·허용 목록. 반응 버튼은 정확한 출처로만 따로 받는다."""
    if safe_link(url) is not None:
        return True
    if not _is_feedback_link(url):
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return len(url) <= MAX_LINK_LENGTH and "@" not in parsed.netloc


DNS_TIMEOUT_S = 5.0
_DNS_POOL = None


def _lookup_with_timeout(lookup: Callable[[str], list[str]], host: str, timeout: float) -> list[str]:
    """getaddrinfo 에는 시간 상한이 없다 — 스레드에서 돌리고 기다리는 시간만 자른다(Codex 검토 2026-09-30: DNS 가 예산 밖이었다)."""
    global _DNS_POOL
    import concurrent.futures
    if _DNS_POOL is None:
        _DNS_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="mail-dns")
    return _DNS_POOL.submit(lookup, host).result(timeout=timeout)


class MailLinkAuditor:
    """링크 하나의 판정(None = 통과, 문자열 = 막은 이유). 한 프로세스 안에서 같은 링크는 한 번만 잰다 —
    한 회차가 수신자마다 따로 나가서 같은 DOI 를 여러 번 만난다. **일시적 실패(시간 초과·DNS·HTTP 오류)는 캐시하지 않는다** —
    그러면 첫 메일의 순간 장애가 그 뒤 수신자 메일의 정상 버튼까지 막는다(Codex 검토 2026-09-30 재현)."""

    def __init__(self, *, budget_s: float = AUDIT_BUDGET_S,
                 lookup: Callable[[str], list[str]] | None = None,
                 probe: Callable[[str, float], tuple[int, str]] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._budget_s = budget_s
        self._clock = clock
        self._deadline = clock() + budget_s
        self._lookup = lookup or (lambda host: _dns_lookup(host))
        self._probe = probe or (lambda url, timeout: _http_probe(url, timeout))
        self._host_ok: dict[str, bool] = {}
        self._verdict: dict[str, str | None] = {}

    def _remaining(self) -> float:
        return self._deadline - self._clock()

    def _public(self, host: str) -> tuple[bool | None, str]:
        """(공인 여부, 이유). None = 이번엔 확인 못 함(일시적). DNS 로 푼 주소가 **전부** 공인이어야 한다."""
        if host in self._host_ok:
            return self._host_ok[host], ""
        remaining = self._remaining()
        if remaining <= 0:
            return None, _TIMEOUT
        try:
            addrs = _lookup_with_timeout(self._lookup, host, min(DNS_TIMEOUT_S, remaining))
        except Exception as error:                          # noqa: BLE001 — 시간 초과·해석 실패 모두 일시적으로 본다
            return None, f"DNS 확인 실패({type(error).__name__})"
        if self._remaining() <= 0:
            return None, _TIMEOUT
        try:
            ok = bool(addrs) and all(ipaddress.ip_address(a.split("%", 1)[0]).is_global for a in addrs)
        except ValueError:
            ok = False
        self._host_ok[host] = ok
        return ok, ""

    def start(self) -> None:
        """메일 한 통마다 시간 예산을 새로 준다(확정 판정 캐시는 유지)."""
        self._deadline = self._clock() + self._budget_s

    def check(self, url: str) -> str | None:
        if url in self._verdict:
            return self._verdict[url]
        verdict, final = self._check(url)
        if final:
            self._verdict[url] = verdict
        return verdict

    def _check(self, url: str) -> tuple[str | None, bool]:
        """(판정, 캐시해도 되는가)."""
        current = url
        for _ in range(MAX_HOPS + 1):
            # 루프는 리다이렉터(doi.org)일 때만 다음 홉으로 가므로 `current != url` 이면 doi.org 가 넘겨준 착지다.
            if not _statically_safe(current) and not (current != url and _doi_landing_ok(current)):
                return ("허용 목록 밖" if current == url else f"리다이렉트가 허용 목록 밖으로({_host(current) or '?'})"), True
            host = _host(current)
            public, why = self._public(host)
            if public is None:
                return why, False
            if not public:
                return f"공인 주소가 아님({host})", True
            if not any(host == r or host.endswith(f".{r}") for r in REDIRECTOR_HOSTS):
                return None, True
            remaining = self._remaining()
            if remaining <= 0:
                return _TIMEOUT, False
            try:
                status, location = self._probe(current, min(HOP_TIMEOUT_S, remaining))
            except Exception as error:                      # 네트워크 실패도 막는다 — 확인 못 한 리다이렉터는 싣지 않는다
                return f"리다이렉트 확인 실패({type(error).__name__})", False
            if not (300 <= status < 400 and location):
                return f"리다이렉터가 목적지를 주지 않음(HTTP {status})", status < 500
            current = urljoin(current, location)
        return f"리다이렉트 {MAX_HOPS}회 초과", True


# 평문 URL — 스킴 앞에 경계를 요구하지 않는다(`원문https://…` 처럼 한글에 붙어도 메일 앱은 링크로 만든다). `www.` 로 시작하는 주소도
# 자동 링크가 된다(Codex 검토 2026-09-30).
_TEXT_URL_RE = re.compile(r"(?i)(?:https?|javascript|vbscript|data|file|ftp):[^\s<>\"'\]]+|(?<![\w.@/-])www\.[^\s<>\"'\]]+")
_DOI_URL_RE = re.compile(r"^https://(?:dx\.)?doi\.org/(10\.\d{4,9}/[^\s:]+)$", re.I)
# 주소를 싣는 속성. 앵커 href 만 판정하고 나머지는 판정 없이 지운다 — 우리 메일은 이미지·폼을 싣지 않는다.
_URL_ATTRS = frozenset({"href", "src", "srcset", "action", "formaction", "background", "poster", "xlink:href",
                        "longdesc", "cite", "data", "codebase", "dynsrc", "lowsrc", "ping", "manifest", "icon"})
# 태그째(내용까지) 버리는 것 — 문서 이동·외부 로딩을 일으킨다.
_DROP_WITH_CONTENT = frozenset({"script", "style", "iframe", "object", "embed", "template", "noscript", "svg", "math"})
_DROP_TAG = frozenset({"meta", "base", "link", "form", "img", "image", "input", "button", "frame", "frameset", "area", "map",
                       "source", "track", "video", "audio", "picture"})


def _doi_of(url: str) -> str:
    """차단 문구에 남길 DOI. **DOI 모양일 때만** — `doi.org/https://evil…` 을 DOI 라며 URL 을 되살리지 않는다."""
    m = _DOI_URL_RE.match(url)
    return m.group(1) if m and "//" not in m.group(1) else ""


def _attr_value(v: str) -> str:
    return v.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def audit_html(html: str, check: Callable[[str], str | None]) -> tuple[str, list[tuple[str, str]]]:
    """HTML 의 링크를 판정해 막힌 것은 글자로 바꾼다. (새 HTML, [(url, 이유)]).

    정규식이 아니라 파서로 **실제 태그·실제 속성**을 본다(Codex 검토 2026-09-30: `data-href` 에 속은 정규식이 진짜 `href` 를 놓쳤고,
    허용 앵커 안의 중첩 앵커·`img src`·`meta refresh`·CSS `url()` 도 그대로 남았다). 출력은 파서가 본 것을 다시 쓴 것이다."""
    from html.parser import HTMLParser

    blocked: list[tuple[str, str]] = []
    out: list[str] = []
    anchors: list[str] = []          # 열린 <a> 마다 닫을 때 쓸 것: "</a>" 또는 차단 표시
    drop_depth = 0

    def start(tag: str, attrs: list[tuple[str, str | None]], closed: bool) -> None:
        nonlocal drop_depth
        if drop_depth or tag in _DROP_WITH_CONTENT:
            if tag in _DROP_WITH_CONTENT and not closed:
                drop_depth += 1
            return
        if tag in _DROP_TAG:
            return
        kept: list[str] = []
        href = None
        for name, value in attrs:
            name = name.lower()
            value = value or ""
            if name == "href" and tag == "a":
                href = value.strip()
                continue
            if name in _URL_ATTRS or name.startswith("on") or name == "srcdoc":
                continue
            # 우리 메일의 style 에는 역슬래시도 url 도 없다. CSS 이스케이프(`u\\72l(`)로 정규식을 비껴가므로(Codex 최종 검토) 역슬래시·
            # url·expression·import·image-set 이 하나라도 있으면 style 을 통째로 버린다.
            if name == "style" and ("\\" in value or re.search(r"(?i)url|expression|@import|image-set|src\s*\(", value)):
                continue
            kept.append(f' {name}="{_attr_value(value)}"')
        if tag == "a":
            if anchors:                                     # 앵커 안의 앵커 — 바깥 판정에 기대지 않고 안쪽을 글자로
                closer = ""
                if href:
                    blocked.append((href, "중첩 앵커"))
                    closer = f' <span style="{_BLOCKED_STYLE}">{BLOCKED_LABEL}</span>'
                anchors.append(closer)
                return
            if not href:
                anchors.append("")
                return
            if href.startswith("#"):
                out.append(f'<a href="{_attr_value(href)}"{"".join(kept)}>')
                anchors.append("</a>")
                return
            reason = check(href)
            if reason is None:
                out.append(f'<a href="{_attr_value(href)}"{"".join(kept)}>')
                anchors.append("</a>")
            else:
                blocked.append((href, reason))
                doi = _doi_of(href)
                note = f"DOI {_html.escape(doi)} {BLOCKED_LABEL}" if doi else BLOCKED_LABEL
                anchors.append(f' <span style="{_BLOCKED_STYLE}">{note}</span>')
            return
        out.append(f"<{tag}{''.join(kept)}{' /' if closed else ''}>")

    class _P(HTMLParser):
        def handle_starttag(self, tag, attrs): start(tag, attrs, False)
        def handle_startendtag(self, tag, attrs): start(tag, attrs, True)

        def handle_endtag(self, tag):
            nonlocal drop_depth
            if tag in _DROP_WITH_CONTENT:
                drop_depth = max(0, drop_depth - 1)
                return
            if drop_depth or tag in _DROP_TAG:
                return
            if tag == "a":
                if anchors:
                    out.append(anchors.pop())
                return
            out.append(f"</{tag}>")

        def handle_data(self, data):
            if not drop_depth:
                out.append(_html.escape(data, quote=False))

        def handle_entityref(self, name):
            if not drop_depth:
                out.append(f"&{name};")

        def handle_charref(self, name):
            if not drop_depth:
                out.append(f"&#{name};")

        def handle_decl(self, decl):
            out.append(f"<!{decl}>")

    parser = _P(convert_charrefs=False)
    parser.feed(html.replace("\x00", ""))
    parser.close()
    while anchors:                                          # 닫히지 않은 앵커 정리
        out.append(anchors.pop())
    return "".join(out), blocked


def audit_text(text: str, check: Callable[[str], str | None]) -> tuple[str, list[tuple[str, str]]]:
    """평문 파트의 URL — 메일 앱이 평문 URL 도 자동으로 링크로 만든다."""
    blocked: list[tuple[str, str]] = []

    def repl(m: re.Match) -> str:
        url = m.group(0)
        tail = ""
        while url and url[-1] in ".,;:!?)":
            url, tail = url[:-1], url[-1] + tail
        target = "https://" + url if url.lower().startswith("www.") else url
        reason = check(target)
        if reason is None:
            return url + tail
        blocked.append((url, reason))
        doi = _doi_of(url)
        return (f"DOI {doi} {BLOCKED_LABEL}" if doi else BLOCKED_LABEL) + tail

    return _TEXT_URL_RE.sub(repl, text), blocked


_SHARED: MailLinkAuditor | None = None


def audit_mail(text: str, html: str | None,
               auditor: MailLinkAuditor | None = None) -> tuple[str, str | None, list[tuple[str, str]]]:
    """SMTP 직전 최종 감사. 감사 자체가 예외로 죽으면 **링크를 전부 빼고** 보낸다 — 확인 못 한 링크를 싣지도, 메일을 멈추지도 않는다."""
    global _SHARED
    if auditor is None:
        if _SHARED is None:
            _SHARED = MailLinkAuditor()
        auditor = _SHARED
    auditor.start()
    try:
        new_text, blocked = audit_text(text, auditor.check)
        new_html = None
        if html is not None:
            new_html, blocked_html = audit_html(html, auditor.check)
            blocked += blocked_html
        return new_text, new_html, blocked
    except Exception as error:                               # 방어선이 스스로 깨진 경우 — 원인은 로그로
        def refuse(url: str) -> str:
            return f"감사 오류({type(error).__name__})"
        try:
            new_text, blocked = audit_text(text, refuse)
            new_html = None
            if html is not None:
                new_html, blocked_html = audit_html(html, refuse)
                blocked += blocked_html
            return new_text, new_html, blocked
        except Exception:                                    # HTML 처리까지 깨지면 평문만, URL 은 전부 지워서 보낸다
            return _TEXT_URL_RE.sub(BLOCKED_LABEL, text), None, [("*", f"감사 오류({type(error).__name__})")]
