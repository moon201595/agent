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

import http_client
import json
import logging
import re
import subprocess
import threading
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx

import api_usage
import pacing

import storage
import server

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# GitHub 검색 엔드포인트 스로틀(M6, 2026-08-28). 실측: 인증 상태에서 분당
# 30회(미인증은 10회) — 코어 API 5,000/시간과는 별개의 낮은 한도다. 분당 30회면
# 2초 간격이 정확히 상한이라, 여유를 두고 2.5초로 잡는다. 한도를 코드에
# 하드코딩하지 않는다는 원칙(CLAUDE.md 3)과 안 부딪힌다 — 이건 "한도 값"이
# 아니라 우리가 스스로 지키는 호출 간격이다.
GITHUB_SEARCH_MIN_INTERVAL = 2.5
# 간격 계산 규칙은 pacing.py 한 곳에만 있다(§8-30). code_finder 는 동기
# 코드라 SyncPacer 를 쓴다 — 여기에 async 를 끌어들이면 ⑦ 재현 경로
# 전체를 바꿔야 한다.
_gh_pacer = pacing.SyncPacer(GITHUB_SEARCH_MIN_INTERVAL)





# 알려진 코드/모델 호스팅 도메인 — 이 안에 있으면 "저장소 링크"로 확신한다.
_KNOWN_HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "huggingface.co")

# 코드 저장소일 수 없는 도메인 — cue 단어 근처에 우연히 있어도(예: "자세한 내용은
# arxiv.org 참고", "GPL 라이선스 gnu.org") 후보에서 제외한다. 실측(2405.15793,
# 2503.23037)에서 이런 링크가 project_page 후보로 잘못 섞여 나왔다.
_DENY_HOSTS = ("arxiv.org", "semanticscholar.org", "doi.org", "gnu.org",
               "wikipedia.org", "rapidapi.com")

# 논문이 **도구로 인용한** 저장소 — 저자 코드가 아니다. 실측(2026-08-31):
# CSymPlan(2608.22983)에서 본문이 시뮬레이터로 언급한 isaac-sim/IsaacSim 이
# 저자 저장소로 잡혀 재현이 stage=no_target 으로 실패했다. 이런 건 클론해봐야
# 그 논문을 재현하는 게 아니므로 후보에서 뺀다.
#
# 목록은 좁게 유지한다 — "저자가 자기 논문 코드를 여기 올릴 일이 사실상 없는"
# 대형 프레임워크·시뮬레이터만 넣는다. 넓히면 진짜 저자 저장소를 조용히
# 버리게 되고, 그건 지금 고치려는 문제보다 나쁘다.
_DENY_REPOS = (
    "isaac-sim/", "nvidia-omniverse/", "pytorch/pytorch", "tensorflow/tensorflow",
    "huggingface/transformers", "huggingface/diffusers", "huggingface/accelerate",
    "huggingface/peft", "ultralytics/ultralytics", "open-mmlab/",
    "opencv/opencv", "scikit-learn/scikit-learn", "numpy/numpy",
    # 2026-08-31 실측: 참고문헌 항목에 적힌 Label Studio 인용이 저자 저장소로
    # 잡혀 두 번째 시도를 통째로 낭비했다. 데이터 라벨링 도구라 위 기준
    # ("저자가 자기 논문 코드를 여기 올릴 일이 사실상 없는 것")에 그대로 맞는다.
    "humansignal/label-studio", "heartexlabs/label-studio",
)


def _is_tool_repo(url: str) -> bool:
    lowered = url.lower()
    return any(f"/{owner_repo}" in lowered for owner_repo in _DENY_REPOS)

# 임의의 URL을 다 잡으면 참고문헌·인용 링크까지 섞인다. "이 근처에 코드 관련
# 표현이 있어야 후보로 친다"는 문맥 단서.
_CUE_RE = re.compile(r"(code|repo|implementation|release[sd]?|available|reproduc\w*|github|source)", re.I)
_URL_RE = re.compile(r"https?://[\w.-]+(?:/[\w./#?=-]*)?")
_CONTEXT_WINDOW = 60

_HF_REPO_RE = re.compile(r"^https://huggingface\.co/([\w.-]+/[\w.-]+)")
_GH_ROOT_RE = re.compile(r"https://github\.com/([\w.-]+)/([\w.-]+)")


@dataclass
class RepoCandidate:
    url: str
    source: str  # "in_text" | "github_search"
    confidence: str  # "author-stated" | "unconfirmed"
    context: str = ""
    stars: int | None = None
    # GitHub 검색 결과에만 채워진다 — 이름 충돌을 걸러내려면 저장소가
    # 무엇에 관한 것인지 알아야 하는데, 지금까지는 URL 과 별점만 받아와서
    # 판별할 근거 자체가 없었다(2026-08-31 실측: GRAFT 논문에 무관한 저장소
    # 세 개가 연달아 클론됐다).
    full_name: str | None = None
    description: str | None = None

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "source": self.source,
            "confidence": self.confidence,
            "context": self.context,
            "stars": self.stars,
            "full_name": self.full_name,
            "description": self.description,
        }


# 줄바꿈을 지우면 URL 바로 뒤 문장이 그대로 붙는다. 뒤 문장은 대개 마침표
# 다음에 대문자로 시작하므로("...DSSG." + "Index terms—") 경로 안의 ".대문자"
# 를 절단점으로 본다. 호스트에는 적용하지 않는다(github.com 이 잘리면 안 된다).
_PATH_SENTENCE_RE = re.compile(r"\.(?=[A-Z])")
_SPLIT_HOST_RE = re.compile(r"(https?://[^/]+)(/.*)?$")


def _clean_url(url: str) -> str:
    """URL 끝에 섞여 들어온 본문 조각을 잘라낸다.

    실측 사례(2026-08-31): 원문의 줄바꿈을 지우는 과정에서
    `github.com/mrmenand/DSSG` 뒤에 "Index terms" 가, `github.com/yanfeisu/COM_MATD3`
    뒤에 "TABLE I" 가 붙어 각각 `.../DSSG.Index`, `.../COM_MATD3.TABLE` 로
    잡혔고 둘 다 clone 단계에서 죽었다. 기존 방어(_drop_prefix_duplicates)는
    **짧은 정상 URL 이 본문 어딘가에 또 나왔을 때만** 동작해서, 한 번만 등장한
    이 두 건을 못 잡았다.

    소문자로 이어지는 진짜 저장소 이름(socket.io, next.js)은 그대로 둔다 —
    절단은 마침표 뒤가 대문자일 때로 한정한다.
    """
    url = url.rstrip(".,;:)")
    m = _SPLIT_HOST_RE.match(url)
    if not m or not m.group(2):
        return url
    host, path = m.group(1), m.group(2)
    cut = _PATH_SENTENCE_RE.search(path)
    if cut:
        path = path[: cut.start()]
    return (host + path).rstrip(".,;:)/")


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
        if _is_tool_repo(url):
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

    검색 엔드포인트는 코어 API(5,000/시간)와 **별개의 낮은 한도**를 쓴다 —
    실측(2026-08-28) 기준 인증 상태에서 분당 30회다. 논문을 연속으로 처리하면
    여기 먼저 걸리므로 호출 간 최소 간격을 강제한다(http_client.throttled_s2_get
    과 같은 발상). code_finder 는 동기 코드라 asyncio.Lock 대신 threading.Lock
    을 쓴다.
    """
    try:
        # 공백 포함 검색어를 인코딩 없이 URL에 그대로 붙이면 gh api 가 걸린 채
        # 30초 타임아웃까지 간다 (실측: "SWE-agent"처럼 한 단어일 때만 성공하고
        # 나머지 다중 단어 검색어는 전부 실패했다). quote() 로 반드시 인코딩한다.
        with _gh_pacer.gate():
            result = subprocess.run(
                ["gh", "api", f"search/repositories?q={quote(query)}&sort=stars&order=desc"],
                capture_output=True, text=True, timeout=30, check=True,
            )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        api_usage.record("github", "error")
        print(f"  [경고] GitHub 검색 실패: {e}")
        return []
    api_usage.record("github", "ok")
    data = json.loads(result.stdout)
    out = []
    for item in data.get("items", [])[:limit]:
        out.append(
            RepoCandidate(
                url=item["html_url"],
                source="github_search",
                confidence="unconfirmed — 저자 공식 아닐 수 있음, 커뮤니티 재구현체 가능성",
                stars=item.get("stargazers_count"),
                full_name=item.get("full_name"),
                description=item.get("description"),
            )
        )
    return out


def _resolve_hf_repo_link(hf_url: str) -> str | None:
    """HuggingFace 모델 페이지는 코드 저장소가 아니라 가중치 허브인 경우가 많다
    (⑦ install+run 스모크 테스트를 못 만든다 — 실측 2505.13033: 이 링크만으로는
    설치 대상 자체가 없어 "설치+실행" 판정이 공허해진다).

    모델 카드 README 를 한 번 더 읽으면 진짜 GitHub 코드 저장소가 나온다는 걸
    TSPulse 로 실측 확인했다(2026-08-03) — README 의 "Repository:" 필드가
    `github.com/ibm-granite/granite-tsfm` 를 가리켰다. README 안에 나온
    github.com 링크들을 저장소 루트(owner/repo)로 잘라 가장 많이 언급된 것을
    고른다 — "Repository:" 라는 라벨 문구에 의존하지 않아 모델 카드마다
    표현이 달라도 견딘다(같은 저장소의 /tree/, /blob/ 하위 경로가 여러 번
    인용되는 패턴을 실측으로 확인했다).
    """
    m = _HF_REPO_RE.match(hf_url)
    if not m:
        return None
    try:
        resp = httpx.get(
            f"https://huggingface.co/{m.group(1)}/raw/main/README.md", timeout=10
        )
        api_usage.record("huggingface", "ok" if resp.status_code == 200 else str(resp.status_code))
        resp.raise_for_status()
    except httpx.HTTPError:
        return None
    roots = _GH_ROOT_RE.findall(resp.text)
    if not roots:
        return None
    (owner, repo), _count = Counter(roots).most_common(1)[0]
    return f"https://github.com/{owner}/{repo}"


# ---------------------------------------------------------------- 후보 검증 (2026-08-31)
#
# github_search 결과는 정의상 "저자 공식인지 확인 안 됨"이다. 그런데 지금까지
# 확인 없이 별점 순으로 클론했고, 실측에서 그 대가가 드러났다 — GRAFT 논문
# (본문에 URL 이 하나도 없다)에 대해 이름만 같은 무관한 저장소 세 개
# (trailhq/Graft = 코딩 에이전트 도구, hmgle/graftcp = TCP 프록시,
# orbitinghail/graft = 스토리지 엔진)를 연달아 클론하고 Docker 까지 돌렸다.
#
# 그래서 "이 저장소가 이 논문의 것이라는 독립적 근거"를 요구한다. 검색어
# 자체는 근거가 될 수 없다 — 이름이 같아서 검색된 것이므로 순환논증이다.
#
# 근거 세 가지 중 하나면 채택한다. 실측 14건(성공 2건 포함) 전부에 대해
# 맞는 답을 내는 걸 확인하고 고른 조합이다:
#
#   (A) 검색어에 **없는** 논문 제목 낱말이 저장소 이름·설명에 나타난다.
#       LF-YOLO 가 이 경우다("Lighter and Faster YOLO ... defect ... weld").
#   (B) 소유자 이름이 논문 이름의 첫 낱말을 담고 있다. SWE-agent 가 이
#       경우다 — 설명에 "software engineering"이 없어서 (A)로는 못 잡지만
#       조직명 자체가 SWE-agent 다. 반대로 orbitinghail·trailhq·hmgle 은
#       "graft"를 담지 않는다.
#   (C) 저자 성이 소유자 이름과 일치한다.
#
# 근거가 없으면 후보에서 뺀다. 그 결과 "저장소 후보 없음"이 나오는 편이
# 엉뚱한 저장소를 재현하고 [재현 ✗] 를 붙이는 것보다 정직하다(CLAUDE.md 8)
# — 후자는 "저자 코드가 안 돈다"는 **틀린 사실**을 보고하는 것이다.

_CORROBORATION_STOPWORDS = frozenset("""
the and for with via using from that this are its our new novel towards toward
based into learning model models deep neural network networks efficient effective
robust fast framework approach method methods system systems data paper study
analysis evaluation benchmark scale large small real time end
""".split())

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# 코드가 아니라 소개 페이지인 저장소 — ⑦ 이 답해야 할 질문("설치해서 도는가")에
# 애초에 답할 수 없는 대상이다. 실측: Riemann-1.0-Website.
_NON_CODE_REPO_RE = re.compile(
    r"(?:[-_.](?:website|homepage|page|pages|docs|doc|site|blog))$"
    r"|^(?:website|homepage|docs|doc|site|blog)$"
    r"|\.github\.io$",
    re.IGNORECASE,
)


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def is_non_code_repo(url: str) -> bool:
    name = url.rstrip("/").rsplit("/", 1)[-1]
    return bool(_NON_CODE_REPO_RE.search(name))


def _author_surnames(authors_json: str | None) -> set[str]:
    """papers.authors 는 JSON 리스트다. 성만 뽑되 4글자 미만은 버린다 —
    "Ye", "Xu", "Sun" 같은 짧은 성은 아무 문자열에나 우연히 걸린다."""
    try:
        names = json.loads(authors_json or "[]")
    except (json.JSONDecodeError, TypeError):
        return set()
    out = set()
    for n in names:
        parts = _TOKEN_RE.findall(str(n).lower())
        if parts and len(parts[-1]) >= 4:
            out.add(parts[-1])
    return out


def corroborates_paper(candidate: dict, title: str, query: str,
                       authors_json: str | None = None) -> bool:
    """이 저장소가 이 논문의 것이라는 독립적 근거가 있는가 (위 (A)(B)(C))."""
    full_name = candidate.get("full_name") or ""
    haystack = _tokens(f"{full_name} {candidate.get('description') or ''}")

    # (A) 검색어 밖의 제목 낱말
    distinctive = {tok for tok in _tokens(title) - _tokens(query) - _CORROBORATION_STOPWORDS
                   if len(tok) >= 4}
    if distinctive & haystack:
        return True

    owner = full_name.split("/")[0] if "/" in full_name else ""
    owner_flat = "".join(_TOKEN_RE.findall(owner.lower()))

    # (B) 소유자 이름이 논문 이름의 첫 낱말을 담는가
    lead = next((tok for tok in _TOKEN_RE.findall(query.lower()) if len(tok) >= 3), "")
    if lead and owner_flat and lead in owner_flat:
        return True

    # (C) 저자 성
    return bool(_author_surnames(authors_json) & _tokens(owner))


def find_repo_candidates(arxiv_id: str) -> dict:
    """이 arxiv_id 논문의 코드 저장소 후보를 찾는다.

    Returns:
        {arxiv_id, title, in_text: [...], github_search: [...]}
    """
    arxiv_id = storage.clean_arxiv_id(arxiv_id)
    with storage.db() as con:
        row = con.execute(
            "SELECT title, text_path, authors FROM papers WHERE arxiv_id=?", (arxiv_id,)
        ).fetchone()
    if not row:
        raise ValueError(f"'{arxiv_id}'는 아직 저장되지 않음 — fetch_paper 먼저 호출할 것")

    text = Path(row["text_path"]).read_text(encoding="utf-8")
    in_text = find_links_in_text(text)

    # HuggingFace 모델 링크는 그 자체로는 설치 대상이 없다 — 모델 카드를 한 번
    # 더 따라가 진짜 GitHub 코드가 있으면 그걸 최우선 후보로 앞에 얹는다.
    hf_hops: list[RepoCandidate] = []
    for c in in_text:
        if "huggingface.co" not in c.url:
            continue
        gh_url = _resolve_hf_repo_link(c.url)
        if gh_url and gh_url not in {x.url for x in in_text}:
            hf_hops.append(
                RepoCandidate(
                    url=gh_url, source="in_text",
                    confidence="author-stated",
                    context=f"(HuggingFace 모델카드 경유: {c.url})",
                )
            )
    in_text = hf_hops + in_text

    # 검색어: 제목에서 콜론 이하 부제는 잘라내고 특수문자를 제거한다 —
    # 부제·부가어가 많을수록 검색 정확도가 떨어지는 걸 실측으로 확인했다.
    query = row["title"].split(":")[0]
    query = re.sub(r"[^\w\s-]", " ", query).strip()
    gh_results = github_search(query)

    in_text_dicts = [c.to_dict() for c in in_text]
    gh_dicts = [c.to_dict() for c in gh_results]

    # 소개 페이지 저장소는 양쪽에서 뺀다 — 클론은 되지만 ⑦ 의 질문
    # ("설치해서 도는가")에 답할 수 없어서 [재현 ✗] 만 만들어낸다.
    dropped_non_code = [c["url"] for c in in_text_dicts + gh_dicts
                        if is_non_code_repo(c["url"])]
    in_text_dicts = [c for c in in_text_dicts if not is_non_code_repo(c["url"])]
    gh_dicts = [c for c in gh_dicts if not is_non_code_repo(c["url"])]

    # 검색 결과는 근거가 있어야 남긴다. in_text 는 저자가 직접 적은 링크라
    # 이 검사를 걸지 않는다 — 저장소 이름이 논문과 안 닮은 진짜 저자
    # 저장소가 흔하고, 그걸 버리면 지금 고치려는 것보다 나쁜 손실이 된다.
    dropped_uncorroborated = [c["url"] for c in gh_dicts
                              if not corroborates_paper(c, row["title"], query, row["authors"])]
    gh_dicts = [c for c in gh_dicts
                if corroborates_paper(c, row["title"], query, row["authors"])]

    # ⑦ 은 launch_background 가 띄운 **별도 프로세스**라 스캔 쪽 계수기에
    # 안 잡힌다(§8-15). 여기서 따로 찍어 재현 로그에 남긴다.
    summary = api_usage.format_summary()
    if summary:
        print(f"  [계측] ⑦ 저장소 탐색: {summary}")

    for url in dropped_non_code:
        print(f"  [후보 제외] 코드가 아닌 소개 페이지: {url}")
    for url in dropped_uncorroborated:
        print(f"  [후보 제외] 이 논문의 저장소라는 근거 없음(이름만 일치): {url}")

    return {
        "arxiv_id": arxiv_id,
        "title": row["title"],
        "in_text": in_text_dicts,
        "github_search": gh_dicts,
        "dropped": {"non_code": dropped_non_code,
                    "uncorroborated": dropped_uncorroborated},
    }


if __name__ == "__main__":
    import sys

    for aid in sys.argv[1:]:
        result = find_repo_candidates(aid)
        print(json.dumps(result, ensure_ascii=False, indent=2))
