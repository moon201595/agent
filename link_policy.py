"""⑨ 배달 — 메일에 넣을 외부 링크를 허용 목록으로 제한한다.

메일 링크는 클릭 한 번이면 끝나므로 차단 목록으로는 새 호스트를 막을 수
없다. 논문 독자가 실제로 누를 링크만 명시적으로 허용해, 새 도메인이
추가되거나 오타가 생겨도 메일에서 임의 URL로 이어지지 않게 한다.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse


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
