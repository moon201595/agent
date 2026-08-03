"""code_finder.py — ⑦ 코드 재현: 논문의 코드 저장소를 찾는다.

⑦ 은 논문을 읽고 코드를 새로 짜는 게 아니라, **저자(또는 커뮤니티)가 이미 공개한
코드를 찾아서 돌려보는** 단계다 (docs/PROGRESS.md §8-6). 이 파일은 그 첫 단계 —
"저장소 찾기" — 만 담당한다. Docker 격리 실행은 별도 파일에서 다룬다.

두 경로를 쓴다:
  1. 원문 텍스트에서 "code is available at" 류 문구 근처의 URL을 찾는다 (in_text,
     신뢰도 높음 — 저자가 직접 언급한 링크다).
  2. GitHub 검색 API로 논문 제목을 검색해 별점 상위 결과를 보완 후보로 낸다
     (github_search, 신뢰도 낮음 — 저자 공식인지 확인 안 됨. 커뮤니티 재구현체일
     수 있다. 반드시 그렇게 라벨링해서 보여준다 — 2026-07-31 결정).

실측으로 확인된 함정들:
  - PDF 추출 시 URL이 줄바꿈으로 끊긴다 (예: "huggingface.\nco/..."). 텍스트에서
    줄바꿈을 제거한 뒤 검색해야 한다.
  - 코드 링크가 항상 github.com 은 아니다 (예: SWE-agent 는 자체 도메인
    swe-agent.com 을 씀). 알려진 호스팅 도메인이 아니면 "project_page" 로만
    표시하고 저장소로 단정하지 않는다.
  - GitHub 검색에 저자·소속 같은 부가어를 넣으면 오히려 무관한 포크가 상위에
    뜬다. 논문 핵심 키워드만 넣고 별점(stars) 내림차순 정렬이 훨씬 정확했다.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import server

# 알려진 코드/모델 호스팅 도메인 — 이 안에 있으면 "저장소 링크"로 확신한다.
_KNOWN_HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "huggingface.co")

# 코드 저장소일 수 없는 도메인 — cue 단어 근처에 우연히 있어도(예: "자세한 내용은
# arxiv.org 참고", "GPL 라이선스 gnu.org") 후보에서 제외한다. 실측(2405.15793,
# 2503.23037)에서 이런 링크가 project_page 후보로 잘못 섞여 나왔다.
_DENY_HOSTS = ("arxiv.org", "semanticscholar.org", "doi.org", "gnu.org",
               "wikipedia.org", "rapidapi.com")

# 임의의 URL을 다 잡으면 참고문헌·인용 링크까지 섞인다. "이 근처에 코드 관련
# 표현이 있어야 후보로 친다"는 문맥 단서.
_CUE_RE = re.compile(r"(code|repo|implementation|release[sd]?|available|reproduc\w*|github|source)", re.I)
_URL_RE = re.compile(r"https?://[\w.-]+(?:/[\w./#?=-]*)?")
_CONTEXT_WINDOW = 60


@dataclass
class RepoCandidate:
    url: str
    source: str  # "in_text" | "github_search"
    confidence: str  # "author-stated" | "unconfirmed"
    context: str = ""
    stars: int | None = None

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "source": self.source,
            "confidence": self.confidence,
            "context": self.context,
            "stars": self.stars,
        }


def _clean_url(url: str) -> str:
    return url.rstrip(".,;:)")


def _drop_prefix_duplicates(candidates: list[RepoCandidate]) -> list[RepoCandidate]:
    """줄바꿈 제거 과정에서 뒤 문장이 URL 끝에 그대로 붙는 경우가 있다
    (예: "...tspulse-r1." + "1 INTRODUCTION" → "tspulse-r1.1", 또는
    ".AcknowledgementsWe" 처럼 구두점·대문자로 시작하는 군더더기).

    주의: 단순 문자열 접두사만 보면 "ogx"와 "ogx-k8s-operator"처럼 **같은
    조직의 서로 다른 진짜 저장소**까지 중복으로 오판해 지워버린다 (실측으로
    실제 발생 — 처음 짠 버전이 이 버그로 ogx-k8s-operator 를 삼켰다). 그래서
    "-"나 "/"로 시작하는 추가 부분은 정당한 별개 경로로 보고 남긴다. 군더더기는
    구분자 없이 글자/숫자가 바로 붙거나 "."로 시작하는 경우로 한정한다.
    """
    urls = sorted({c.url for c in candidates}, key=len)
    keep: set[str] = set()
    for url in urls:
        is_garbage_dup = False
        for shorter in keep:
            if url == shorter or not url.startswith(shorter):
                continue
            extra = url[len(shorter):]
            if not extra.startswith(("-", "/")):
                is_garbage_dup = True
                break
        if not is_garbage_dup:
            keep.add(url)
    return [c for c in candidates if c.url in keep]


def find_links_in_text(text: str) -> list[RepoCandidate]:
    """원문에서 코드 링크로 보이는 URL을 찾는다. 줄바꿈 제거는 필수 —
    PDF 추출 결과에서 URL이 줄 경계로 끊기는 사례가 실측으로 확인됐다.
    """
    flat = text.replace("\n", "")
    seen: set[str] = set()
    candidates: list[RepoCandidate] = []
    for m in _URL_RE.finditer(flat):
        url = _clean_url(m.group())
        if url in seen:
            continue
        start = max(0, m.start() - _CONTEXT_WINDOW)
        ctx = flat[start : m.start()]
        if not _CUE_RE.search(ctx):
            continue
        if any(host in url for host in _DENY_HOSTS):
            continue
        seen.add(url)
        is_known_host = any(host in url for host in _KNOWN_HOSTS)
        candidates.append(
            RepoCandidate(
                url=url,
                source="in_text",
                confidence="author-stated" if is_known_host else "author-stated (project_page — 저장소 링크 아닐 수 있음)",
                context=ctx[-40:],
            )
        )
    return _drop_prefix_duplicates(candidates)


def github_search(query: str, limit: int = 5) -> list[RepoCandidate]:
    """gh CLI(이미 인증됨)로 GitHub 검색. 별점 내림차순 — 부가어 없이 핵심
    키워드만 넣는 게 정확도가 훨씬 높다 (실측: "SWE-agent princeton" 검색은
    무관한 포크만 나왔지만 "SWE-agent" 단독 검색은 공식 저장소가 1위로 나왔다).
    """
    try:
        # 공백 포함 검색어를 인코딩 없이 URL에 그대로 붙이면 gh api 가 걸린 채
        # 30초 타임아웃까지 간다 (실측: "SWE-agent"처럼 한 단어일 때만 성공하고
        # 나머지 다중 단어 검색어는 전부 실패했다). quote() 로 반드시 인코딩한다.
        result = subprocess.run(
            ["gh", "api", f"search/repositories?q={quote(query)}&sort=stars&order=desc"],
            capture_output=True, text=True, timeout=30, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"  [경고] GitHub 검색 실패: {e}")
        return []
    data = json.loads(result.stdout)
    out = []
    for item in data.get("items", [])[:limit]:
        out.append(
            RepoCandidate(
                url=item["html_url"],
                source="github_search",
                confidence="unconfirmed — 저자 공식 아닐 수 있음, 커뮤니티 재구현체 가능성",
                stars=item.get("stargazers_count"),
            )
        )
    return out


def find_repo_candidates(arxiv_id: str) -> dict:
    """이 arxiv_id 논문의 코드 저장소 후보를 찾는다.

    Returns:
        {arxiv_id, title, in_text: [...], github_search: [...]}
    """
    arxiv_id = server._clean_arxiv_id(arxiv_id)
    with server._db() as con:
        row = con.execute(
            "SELECT title, text_path FROM papers WHERE arxiv_id=?", (arxiv_id,)
        ).fetchone()
    if not row:
        raise ValueError(f"'{arxiv_id}'는 아직 저장되지 않음 — fetch_paper 먼저 호출할 것")

    text = Path(row["text_path"]).read_text(encoding="utf-8")
    in_text = find_links_in_text(text)

    # 검색어: 제목에서 콜론 이하 부제는 잘라내고 특수문자를 제거한다 —
    # 부제·부가어가 많을수록 검색 정확도가 떨어지는 걸 실측으로 확인했다.
    query = row["title"].split(":")[0]
    query = re.sub(r"[^\w\s-]", " ", query).strip()
    gh_results = github_search(query)

    return {
        "arxiv_id": arxiv_id,
        "title": row["title"],
        "in_text": [c.to_dict() for c in in_text],
        "github_search": [c.to_dict() for c in gh_results],
    }


if __name__ == "__main__":
    import sys

    for aid in sys.argv[1:]:
        result = find_repo_candidates(aid)
        print(json.dumps(result, ensure_ascii=False, indent=2))
