"""paper_harness_mcp — 논문 수집·정리 하네스의 결정적 도구 계층 (Phase 0).

에이전트 루프(판단·요약·코드)는 MCP 클라이언트(Claude Code 등)가 담당하고,
이 서버는 결정적으로 동작하는 도구만 제공한다. 이 분리 덕분에 Phase 1에서
내부망 로컬 LLM으로 백엔드를 바꿔도 이 파일은 그대로 재사용된다.

도구 목록:
  - arxiv_search_papers      ① arXiv 검색 (키 불필요, 호출 간 3초 간격 준수)
  - s2_search_papers         ① Semantic Scholar 검색 (인용수 포함, 키는 선택)
  - dedupe_and_rank_papers   ② 중복 제거·선별 (결정적 규칙, 네트워크 미사용)
  - fetch_paper              ③ 원문 수집 (HTML 우선, 없으면 PDF) + 텍스트 추출·저장
  - get_paper_text           ③ 저장된 원문 텍스트 페이지 단위 열람
  - verify_summary_numbers   ⑤ 요약문 수치를 원문과 대조 (읽기 전용)
  - save_summary             요약 저장 (+ 자동 수치 검증, 경고만 하고 저장은 함)
  - list_stored_papers       로컬 저장소 목록

①~③ 은 실패 시 상한 2회까지 코드가 재시도한다 (MAX_RETRIES). 이는 에이전틱
루프가 아니라 예외 처리다 — 재시도 여부와 대상을 LLM 이 정하지 않는다.
④ 요약과 ⑥ 사람 판단, ⑦ 코드 재현은 이 서버의 일이 아니다.

실행: python server.py  (stdio transport)
데이터 경로: 환경변수 PAPER_HARNESS_DATA (기본값: 이 파일 옆의 ./data)
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, ConfigDict, Field

from selection import dedupe_and_rank
from verify import verify_numbers

# ---------------------------------------------------------------- .env 로드

# 이 파일은 MCP 서버로 `claude`(별도 프로세스)가 띄우기 때문에, 터미널에서
# export 한 환경변수를 물려받지 못하는 경우가 많다. summarize_engine.py 와
# 같은 방식으로 .env 를 직접 읽어 os.environ 에 채운다 — 새 의존성 없이
# 표준 라이브러리만 쓴다. 이미 설정된 환경변수는 덮어쓰지 않는다.
_ENV_PATH = Path(__file__).resolve().parent / ".env"
if _ENV_PATH.exists():
    for _line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

# ---------------------------------------------------------------- 상수/경로

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("PAPER_HARNESS_DATA", BASE_DIR / "data"))
PDF_DIR = DATA_DIR / "pdfs"
TEXT_DIR = DATA_DIR / "text"
SUMMARY_DIR = DATA_DIR / "summaries"
IMAGE_DIR = DATA_DIR / "images"
# ⑦ 코드 재현 clone 대상. /mnt/c 밑이 아니라 WSL 네이티브 경로여야 한다 —
# Docker 가 Windows 마운트 경로를 물면 느리고 권한이 꼬인다(§2 확인됨).
REPRO_DIR = DATA_DIR / "repro"
DB_PATH = DATA_DIR / "papers.db"

ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_PDF = "https://arxiv.org/pdf/{arxiv_id}"
ARXIV_HTML = "https://arxiv.org/html/{arxiv_id}"
S2_API = "https://api.semanticscholar.org/graph/v1/paper/search"

# arXiv 공식 안내에 따른 예의상 호출 간격 (초)
ARXIV_MIN_INTERVAL = 3.0

# Semantic Scholar 공식 문서: "1 request per second, cumulative across all
# endpoints" — 키를 발급받아도 이 한도는 그대로 적용된다(더 완화되지 않음).
# 2026-08-01 키 등록 시점에 사용자가 직접 확인한 값. 반드시 지킬 것.
S2_MIN_INTERVAL = 1.0

# ①~③ 구간의 제한 재시도 상한. 최초 1회 + 재시도 MAX_RETRIES 회.
# 이것은 에이전틱 루프가 아니라 예외 처리다 — 무엇을 다시 부를지 LLM 이 정하지 않고
# 코드가 정해진 횟수만 다시 부른다. 상한을 올리기 전에 왜 올리는지부터 정할 것.
MAX_RETRIES = 2

# 다시 불러서 결과가 달라질 수 있는 것만. 4xx 는 다시 불러도 같은 답이 온다.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

# mcp 2.0 에서 FastMCP 가 MCPServer 로 개편됐다. 데코레이터·annotations·run() 형태는
# 그대로라 이식은 이 두 줄뿐이었다. annotations 는 2.0 에서도 dict 를 받는다.
mcp = MCPServer("paper_harness_mcp")

# ---------------------------------------------------------------- 공용 유틸

_arxiv_lock = asyncio.Lock()
_last_arxiv_call = 0.0

_s2_lock = asyncio.Lock()
_last_s2_call = 0.0


def _init_storage() -> None:
    for d in (PDF_DIR, TEXT_DIR, SUMMARY_DIR, IMAGE_DIR, REPRO_DIR):
        d.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            """CREATE TABLE IF NOT EXISTS papers (
                arxiv_id TEXT PRIMARY KEY,
                title TEXT, authors TEXT, published TEXT, categories TEXT,
                abstract TEXT, pdf_path TEXT, text_path TEXT,
                text_chars INTEGER, fetched_at TEXT
            )"""
        )
        con.execute(
            """CREATE TABLE IF NOT EXISTS summaries (
                arxiv_id TEXT PRIMARY KEY,
                path TEXT, numbers_total INTEGER, numbers_matched INTEGER,
                created_at TEXT
            )"""
        )
        # ③ 이 HTML 로 뽑았는지 PDF 로 뽑았는지 남긴다. ⑤ 불일치를 해석할 때
        # "PDF 표 깨짐"을 의심해야 하는지가 이 값으로 갈린다.
        # 기존 DB 를 지우지 않고 열 하나만 추가한다 (멱등).
        existing = {row[1] for row in con.execute("PRAGMA table_info(papers)")}
        if "extract_method" not in existing:
            con.execute("ALTER TABLE papers ADD COLUMN extract_method TEXT")

        # ⑥ 사람 판단 상태. review_app.py 가 쓴다 — 이 서버는 판단하지 않고
        # 값을 저장·조회만 한다. 기본값 'pending' — 저장된 요약은 검토 전이 기본.
        existing_s = {row[1] for row in con.execute("PRAGMA table_info(summaries)")}
        if "review_status" not in existing_s:
            con.execute(
                "ALTER TABLE summaries ADD COLUMN review_status TEXT DEFAULT 'pending'"
            )
        if "review_note" not in existing_s:
            con.execute("ALTER TABLE summaries ADD COLUMN review_note TEXT")
        if "reviewed_at" not in existing_s:
            con.execute("ALTER TABLE summaries ADD COLUMN reviewed_at TEXT")

        # ⑦ 코드 재현 결과 축적. docker_runner.py 가 쓴다 — 이 서버는 여기서도
        # 판단하지 않고 실행 결과(성공 여부·exit code)만 저장한다.
        con.execute(
            """CREATE TABLE IF NOT EXISTS repro_results (
                arxiv_id TEXT, repo_url TEXT, source TEXT, confidence TEXT,
                success INTEGER, exit_code INTEGER, stage TEXT, attempt INTEGER,
                network_used INTEGER, duration_s REAL, log_path TEXT, created_at TEXT,
                PRIMARY KEY (arxiv_id, repo_url)
            )"""
        )


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_arxiv_id(raw: str) -> str:
    """URL·버전 표기를 걷어내고 순수 ID만 남긴다. 예: 'abs/1706.03762v5' → '1706.03762'"""
    raw = raw.strip()
    raw = re.sub(r"^https?://arxiv\.org/(abs|pdf)/", "", raw)
    raw = re.sub(r"\.pdf$", "", raw)
    return re.sub(r"v\d+$", "", raw)


async def _with_retry(attempt_fn, what: str) -> httpx.Response:
    """①~③ 의 제한 재시도. 상한까지 시도하고 그래도 실패하면 마지막 예외를 올린다.

    루프가 아니라 예외 처리다. 재시도 여부를 LLM 이 판단하지 않고, 재시도할 대상도
    바꾸지 않는다. 상한을 넘으면 조용히 넘어가지 않고 예외를 올려 호출부가
    사용자에게 보고하게 한다.

    attempt_fn 은 매번 새로 await 할 수 있는 코루틴 팩토리다 (코루틴은 재사용 불가).
    """
    last: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            return await attempt_fn()
        except httpx.HTTPStatusError as e:
            if e.response.status_code not in RETRYABLE_STATUS:
                raise  # 404·400 등 — 다시 불러도 같은 답
            last = e
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last = e
        if attempt < MAX_RETRIES:
            # 지수 백오프 + 지터. 429 를 동시에 맞은 호출들이 다시 겹치지 않게.
            await asyncio.sleep(2.0**attempt + random.uniform(0, 0.5))
    raise last if last else RuntimeError(f"{what} 재시도 실패")


async def _throttled_arxiv_get(client: httpx.AsyncClient, params: dict) -> httpx.Response:
    """arXiv API 호출 간 최소 간격을 서버 전역에서 강제하고, 상한까지 재시도한다.

    간격 강제가 재시도마다 다시 적용된다 — 재시도가 arXiv 권장 간격을 무시하면
    한도에 걸려 상황이 나빠진다.
    """

    async def once() -> httpx.Response:
        global _last_arxiv_call
        async with _arxiv_lock:
            wait = ARXIV_MIN_INTERVAL - (time.monotonic() - _last_arxiv_call)
            if wait > 0:
                await asyncio.sleep(wait)
            resp = await client.get(ARXIV_API, params=params, timeout=30)
            _last_arxiv_call = time.monotonic()
        resp.raise_for_status()
        return resp

    return await _with_retry(once, "arXiv API")


async def _throttled_s2_get(client: httpx.AsyncClient, params: dict, headers: dict) -> httpx.Response:
    """Semantic Scholar 호출 간 최소 간격(S2_MIN_INTERVAL)을 서버 전역에서 강제한다.
    "초당 1회, 전체 엔드포인트 합산" 이 키 등록 여부와 무관하게 적용되는 공식 한도라
    _throttled_arxiv_get 과 같은 패턴으로 막는다 — 재시도마다 다시 적용해야
    재시도가 한도를 또 넘기지 않는다.
    """

    async def once() -> httpx.Response:
        global _last_s2_call
        async with _s2_lock:
            wait = S2_MIN_INTERVAL - (time.monotonic() - _last_s2_call)
            if wait > 0:
                await asyncio.sleep(wait)
            resp = await client.get(S2_API, params=params, headers=headers, timeout=30)
            _last_s2_call = time.monotonic()
        resp.raise_for_status()
        return resp

    return await _with_retry(once, "Semantic Scholar")


def _parse_arxiv_feed(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    papers = []
    for entry in root.findall("atom:entry", ATOM_NS):
        raw_id = entry.findtext("atom:id", default="", namespaces=ATOM_NS)
        title = " ".join(
            (entry.findtext("atom:title", default="", namespaces=ATOM_NS) or "").split()
        )
        abstract = " ".join(
            (entry.findtext("atom:summary", default="", namespaces=ATOM_NS) or "").split()
        )
        authors = [
            a.findtext("atom:name", default="", namespaces=ATOM_NS)
            for a in entry.findall("atom:author", ATOM_NS)
        ]
        categories = [
            c.attrib.get("term", "") for c in entry.findall("atom:category", ATOM_NS)
        ]
        papers.append(
            {
                "arxiv_id": _clean_arxiv_id(raw_id),
                "title": title,
                "authors": authors,
                "published": entry.findtext("atom:published", default="", namespaces=ATOM_NS),
                "categories": categories,
                "abstract": abstract,
            }
        )
    return papers


# 본문이 아닌 것들. 남겨두면 네비게이션·각주의 숫자가 원문 텍스트를 오염시켜
# ⑤ 대조에서 없는 숫자를 통과시킨다.
_HTML_DROP = ["script", "style", "nav", "header", "footer", "noscript"]


def _text_from_html(html: str) -> str:
    """arXiv HTML(LaTeXML 판)에서 본문 텍스트만 뽑는다."""
    from bs4 import BeautifulSoup  # 지연 임포트: 서버 기동 속도 유지

    # lxml 을 쓰지 않는다 — 의존성을 늘릴 만큼 이득이 크지 않다
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_HTML_DROP):
        tag.decompose()
    root = soup.find("article") or soup.body or soup
    return root.get_text("\n", strip=True)


def _text_from_pdf(path: Path) -> str:
    from pypdf import PdfReader  # 지연 임포트

    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


# 최소 이미지 크기(바이트) — 두 경로가 기준이 다르다.
# PDF: <figure> 같은 구조적 단서가 없어 크기만으로 걸러야 한다. 실측(2601.12538,
# 복수 기관 공저 논문): 3KB 기준으로는 92개가 뽑혔는데 다수가 로고·아이콘 조각
# 이었다. 임계값을 크게 올려야 잡음이 줄어든다.
# HTML: 이미 <figure> 태그로 걸러진 뒤라 크기 기준은 보조 수단일 뿐이다. 낮게
# 잡아야 한다 — 실측(1706.03762)에서 진짜 Figure 2가 26KB로, PDF 기준(5만)을
# 그대로 쓰면 정상적인 그림까지 잘려나간다.
_MIN_IMAGE_BYTES_PDF = 50_000
_MIN_IMAGE_BYTES_HTML = 5_000

# 사람 판단(review_app.py)이 표·그림을 눈으로 확인할 수 있게 이미지를 추출한다.
# 정확히 "Figure 4" 라고 매칭하는 건 못 한다 — PyMuPDF(AGPL)를 의도적으로 배제한
# 상태에서 pypdf 만으로는 PDF 레이아웃(캡션-이미지 연결)까지 읽어내지 못한다.
# 그래서 순서대로 뽑아 "그림 N" 으로 번호만 매긴다 — 갤러리를 보며 사람이 논문의
# Figure/Table 번호와 눈으로 맞춰야 한다. HTML 원문은 figcaption/alt 가 있으면
# 그걸 라벨로 쓴다 (품질이 더 좋다).
# 표(Table)는 대부분 PDF 안에서 텍스트·벡터로 그려져 있어 이 방식으로는 거의
# 못 뽑는다 — 실제로 뽑히는 건 대부분 Figure(사진·차트) 쪽이다.


def _extract_images_from_html(html: str, base_url: str) -> list[dict]:
    """반환: [{"url": 절대경로, "label": 캡션 또는 alt 또는 ""}]"""
    from urllib.parse import urljoin

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_HTML_DROP):
        tag.decompose()
    root = soup.find("article") or soup.body or soup

    items: list[dict] = []
    for img in root.find_all("img"):
        src = img.get("src")
        if not src:
            continue
        # <figure> 로 감싸지 않은 이미지는 저자 소속기관 로고·배너·아이콘일
        # 가능성이 높다 — 논문 Figure 는 LaTeXML 변환 HTML 에서 거의 항상
        # <figure><figcaption> 구조로 나온다. 이 기준 하나로 로고를 걸러낸다.
        fig = img.find_parent("figure")
        if fig is None:
            continue
        cap = fig.find("figcaption")
        label = cap.get_text(" ", strip=True) if cap is not None else (img.get("alt") or "").strip()
        items.append({"url": urljoin(base_url, src), "label": label})
    return items


async def _download_html_images(
    client: httpx.AsyncClient, items: list[dict], out_dir: Path
) -> None:
    labels: dict[str, str] = {}
    i = 0
    for item in items:
        try:
            resp = await client.get(item["url"], timeout=30)
            resp.raise_for_status()
        except Exception:  # noqa: BLE001
            continue
        if len(resp.content) < _MIN_IMAGE_BYTES_HTML:
            continue
        i += 1
        ext = Path(item["url"]).suffix or ".png"
        if len(ext) > 5:  # 쿼리 스트링이 확장자로 잘못 붙는 경우 방지
            ext = ".png"
        fname = f"{i:03d}{ext}"
        (out_dir / fname).write_bytes(resp.content)
        if item["label"]:
            labels[fname] = item["label"]
    if labels:
        (out_dir / "_labels.json").write_text(
            json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def _save_pdf_images(pdf_path: Path, out_dir: Path) -> None:
    from pypdf import PdfReader  # 지연 임포트

    reader = PdfReader(str(pdf_path))
    i = 0
    for page_idx, page in enumerate(reader.pages):
        # 1페이지(표지)는 건너뛴다 — 저자 소속기관 로고가 각각 별도 이미지로
        # 박혀 있는 경우가 흔하다(실측: 공동연구 논문에서 대학·기업 로고 6~8개가
        # Figure 로 오인돼 뽑힘). HTML 의 <figure> 태그 같은 구조적 단서가 PDF엔
        # 없어서, "본문 Figure는 표지에 거의 안 나온다"는 경험칙으로 대신한다.
        # 완벽하지 않다 — 표지에 진짜 그래픽 초록이 있는 논문은 그것도 같이 빠진다.
        if page_idx == 0:
            continue
        for img in page.images:
            if len(img.data) < _MIN_IMAGE_BYTES_PDF:
                continue
            i += 1
            ext = Path(img.name).suffix or ".png"
            (out_dir / f"{i:03d}{ext}").write_bytes(img.data)


async def ensure_images_extracted(arxiv_id: str) -> Path:
    """review_app.py 전용 — 이미지 폴더가 비어 있으면 지금 추출한다 (지연·멱등).

    server.py 는 판단하지 않는다는 원칙과는 무관한 순수 데이터 준비 작업이다.
    PDF 는 이미 로컬에 있는 파일을 읽을 뿐이라 네트워크가 안 든다. HTML 은
    본문 텍스트만 저장해뒀지 원본 HTML은 안 남겨서, 이미지가 필요해지는
    시점(리뷰 화면을 열 때)에 그때 한 번 다시 받는다.
    """
    arxiv_id = _clean_arxiv_id(arxiv_id)
    out_dir = IMAGE_DIR / arxiv_id.replace("/", "_")
    if out_dir.exists() and any(out_dir.iterdir()):
        return out_dir

    with _db() as con:
        row = con.execute(
            "SELECT extract_method, pdf_path FROM papers WHERE arxiv_id=?", (arxiv_id,)
        ).fetchone()
    if not row:
        return out_dir

    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        if row["extract_method"] == "pdf" and row["pdf_path"]:
            _save_pdf_images(Path(row["pdf_path"]), out_dir)
        elif row["extract_method"] == "html":
            async with httpx.AsyncClient(follow_redirects=True) as client:
                resp = await client.get(ARXIV_HTML.format(arxiv_id=arxiv_id), timeout=60)
                resp.raise_for_status()
                # 상대경로(예: "1706.03762v7/Figures/x.png")가 이미 버전 붙은 id를
                # 포함하므로, base 에 arxiv_id 를 또 붙이면 경로가 중복돼 404 난다.
                # 실제 응답 URL(리다이렉트 반영)을 그대로 base 로 쓴다.
                items = _extract_images_from_html(resp.text, str(resp.url))
                await _download_html_images(client, items, out_dir)
    except Exception:  # noqa: BLE001
        # 이미지 추출 실패는 요약·검증 파이프라인과 무관하다 — 조용히 포기한다.
        # (갤러리가 비어 보이는 것으로 실패가 드러나므로 사람이 알아챌 수 있다.)
        pass
    return out_dir


def _error(msg: str, hint: str = "") -> str:
    payload = {"error": msg}
    if hint:
        payload["hint"] = hint
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _http_error_to_message(e: Exception, service: str) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        if code == 429:
            return _error(
                f"{service} 요청 한도 초과 (429)",
                "잠시 후 재시도. Semantic Scholar는 S2_API_KEY 환경변수로 키를 등록하면 한도가 완화됨.",
            )
        if code == 404:
            return _error(f"{service}에서 대상을 찾을 수 없음 (404)", "ID·질의를 확인할 것.")
        return _error(f"{service} 요청 실패 (HTTP {code})")
    if isinstance(e, httpx.TimeoutException):
        return _error(f"{service} 응답 시간 초과", "네트워크 상태 확인 후 재시도.")
    return _error(f"{service} 처리 중 예기치 못한 오류: {type(e).__name__}: {e}")


# ---------------------------------------------------------------- 입력 모델


class ArxivSearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    query: str = Field(..., min_length=1, max_length=300,
                       description="검색어. 영문 키워드 권장 (예: 'quantization vision language action')")
    category: Optional[str] = Field(default=None,
                                    description="arXiv 카테고리 필터 (예: 'cs.CV', 'cs.LG', 'cs.CL')")
    max_results: int = Field(default=10, ge=1, le=50, description="최대 결과 수 (1~50)")
    start: int = Field(default=0, ge=0, description="페이지네이션 오프셋")
    sort_by: str = Field(default="relevance",
                         description="'relevance' 또는 'submittedDate' (최신순)")


class S2SearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    query: str = Field(..., min_length=1, max_length=300, description="검색어 (영문 권장)")
    limit: int = Field(default=10, ge=1, le=50, description="최대 결과 수 (1~50)")
    year_from: Optional[int] = Field(default=None, ge=1990, le=2100,
                                     description="이 연도 이후 논문만 (예: 2023)")


class SelectPapersInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    papers: list[dict] = Field(
        ..., min_length=1, max_length=300,
        description="검색 도구가 돌려준 papers 배열. arXiv 결과와 S2 결과를 "
                    "합쳐서 한꺼번에 넣을 것 — 인용수는 S2 만 주므로 함께 넣어야 "
                    "랭킹이 의미 있다.",
    )
    top_k: int = Field(default=5, ge=1, le=50, description="선별해 남길 논문 수")


class FetchPaperInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    arxiv_id: str = Field(..., min_length=5, max_length=60,
                          description="arXiv ID 또는 URL (예: '1706.03762', 'https://arxiv.org/abs/2402.12345')")


class GetTextInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    arxiv_id: str = Field(..., description="fetch_paper로 저장해 둔 논문의 arXiv ID")
    offset: int = Field(default=0, ge=0, description="시작 문자 위치")
    max_chars: int = Field(default=20000, ge=500, le=80000,
                           description="이번 호출에서 읽을 최대 문자 수")


class VerifyInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    arxiv_id: str = Field(..., description="대조 대상 논문의 arXiv ID (fetch_paper 선행 필요)")
    summary_text: str = Field(..., min_length=1, description="검증할 요약문 전체")


class SaveSummaryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    arxiv_id: str = Field(..., description="요약 대상 논문의 arXiv ID (fetch_paper 선행 필요)")
    markdown: str = Field(..., min_length=1, description="템플릿 형식을 따른 요약 마크다운 전문")


class ListPapersInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


# ---------------------------------------------------------------- 도구


@mcp.tool(
    name="arxiv_search_papers",
    annotations={"title": "arXiv 논문 검색", "readOnlyHint": True,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def arxiv_search_papers(params: ArxivSearchInput) -> str:
    """arXiv에서 논문을 검색한다 (API 키 불필요, 호출 간 3초 간격 자동 준수).

    Returns:
        str: JSON — {"count": int, "start": int, "papers": [{arxiv_id, title,
             authors, published, categories, abstract}, ...]}
    """
    q = f"all:{params.query}"
    if params.category:
        q = f"cat:{params.category} AND ({q})"
    api_params = {
        "search_query": q,
        "start": params.start,
        "max_results": params.max_results,
        "sortBy": params.sort_by,
        "sortOrder": "descending",
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await _throttled_arxiv_get(client, api_params)
        papers = _parse_arxiv_feed(resp.text)
    except Exception as e:  # noqa: BLE001 — 도구 경계에서 메시지로 변환
        return _http_error_to_message(e, "arXiv")
    return json.dumps(
        {"count": len(papers), "start": params.start, "papers": papers},
        ensure_ascii=False, indent=2,
    )


@mcp.tool(
    name="s2_search_papers",
    annotations={"title": "Semantic Scholar 논문 검색", "readOnlyHint": True,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def s2_search_papers(params: S2SearchInput) -> str:
    """Semantic Scholar Graph API로 검색한다. 인용 수(citationCount)가 포함되어
    랭킹·선별에 유용하다. 환경변수 S2_API_KEY가 있으면 사용한다.

    공식 한도가 "초당 1회, 전체 엔드포인트 합산"이라 키 등록 여부와 무관하게
    _throttled_s2_get 으로 서버 전역 간격을 강제한다 — 키가 있어도 이 한도 자체가
    없어지는 게 아니라서, 안 막으면 여러 호출이 겹칠 때 여전히 429 가 난다.

    Returns:
        str: JSON — {"count": int, "papers": [{title, year, citation_count,
             arxiv_id, abstract, open_access_pdf}, ...]}
    """
    headers = {}
    if os.environ.get("S2_API_KEY"):
        headers["x-api-key"] = os.environ["S2_API_KEY"]
    api_params = {
        "query": params.query,
        "limit": params.limit,
        "fields": "title,abstract,year,citationCount,externalIds,openAccessPdf",
    }
    if params.year_from:
        api_params["year"] = f"{params.year_from}-"
    try:
        async with httpx.AsyncClient() as client:
            resp = await _throttled_s2_get(client, api_params, headers)
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        return _http_error_to_message(e, "Semantic Scholar")
    papers = []
    for item in data.get("data", []):
        ext = item.get("externalIds") or {}
        papers.append(
            {
                "title": item.get("title"),
                "year": item.get("year"),
                "citation_count": item.get("citationCount"),
                "arxiv_id": ext.get("ArXiv"),
                "abstract": (item.get("abstract") or "")[:600],
                "open_access_pdf": (item.get("openAccessPdf") or {}).get("url"),
            }
        )
    return json.dumps({"count": len(papers), "papers": papers}, ensure_ascii=False, indent=2)


@mcp.tool(
    name="dedupe_and_rank_papers",
    annotations={"title": "② 중복 제거·선별", "readOnlyHint": True,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def dedupe_and_rank_papers(params: SelectPapersInput) -> str:
    """검색 결과에서 같은 논문을 합치고 고정된 규칙으로 상위 k개를 고른다.

    네트워크를 쓰지 않는 결정적 규칙이다. 선별을 판단으로 하지 않는 이유는
    기준이 매 호출마다 달라지면 왜 이 논문이 뽑혔는지 사후에 설명할 수 없고,
    품질 변화를 비교할 기준선도 생기지 않기 때문이다.

    합치는 기준: arXiv ID 또는 정규화한 제목이 겹치면 같은 논문으로 본다.
    정렬 기준: 인용수 → 연도 내림차순. 인용수를 모르면 0으로 보고 뒤로 민다.

    Returns:
        str: JSON — {input_count, deduped_count, selected_count, papers: [...]}
    """
    return json.dumps(
        dedupe_and_rank(params.papers, params.top_k), ensure_ascii=False, indent=2
    )


@mcp.tool(
    name="fetch_paper",
    annotations={"title": "③ 원문 수집·파싱·저장 (HTML 우선)", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def fetch_paper(params: FetchPaperInput) -> str:
    """arXiv 원문을 내려받아 텍스트를 추출하고 로컬 저장소에 넣는다.
    이미 저장된 논문이면 다시 받지 않고 기존 정보를 반환한다 (멱등).

    추출 경로는 HTML 우선, 없으면 PDF 폴백이다. HTML 이 있으면 그쪽이 낫다 —
    pypdf 는 2단 조판과 표를 자주 뭉개고 그게 ⑤ 수치 검증의 거짓 불일치로
    직결된다. HTML 은 그 원인을 구조적으로 없앤다.

    HTML 제공 여부는 논문마다 다르고 투고 시점으로 예측할 수 없다 (실측: 2017년
    1706.03762 는 HTML 이 있고 2024년 2405.15793 은 없다). arXiv 가 구논문 HTML 을
    소급 생성했고 LaTeXML 변환이 실패하는 논문도 있기 때문이다. 그래서 날짜로
    분기하지 않고 무조건 HTML 을 먼저 시도한 뒤 404 면 폴백한다.

    어느 경로였는지는 extract_method 로 남긴다 — ⑤ 불일치를 볼 때
    'PDF 표 깨짐'을 의심해야 하는지가 이 값으로 갈린다.

    Phase 0 의 PDF 추출은 pypdf(BSD)다. 라이선스 때문에 PyMuPDF(AGPL)는 배제했다.
    GROBID/Docling 으로 교체할 경우 지점은 이 함수 하나다.

    Returns:
        str: JSON — {arxiv_id, title, text_chars, extract_method, pdf_path,
             text_path, preview}
    """
    arxiv_id = _clean_arxiv_id(params.arxiv_id)
    with _db() as con:
        row = con.execute("SELECT * FROM papers WHERE arxiv_id=?", (arxiv_id,)).fetchone()
    if row and row["text_path"] and Path(row["text_path"]).exists():
        return json.dumps(
            {"arxiv_id": arxiv_id, "title": row["title"], "text_chars": row["text_chars"],
             "extract_method": row["extract_method"],
             "pdf_path": row["pdf_path"], "text_path": row["text_path"],
             "note": "이미 저장된 논문 — 재다운로드 생략"},
            ensure_ascii=False, indent=2,
        )

    text = ""
    method = ""
    pdf_path: Path | None = None

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            meta_resp = await _throttled_arxiv_get(
                client, {"id_list": arxiv_id, "max_results": 1}
            )
            metas = _parse_arxiv_feed(meta_resp.text)
            if not metas:
                return _error(f"arXiv에서 ID '{arxiv_id}'를 찾을 수 없음", "ID 형식을 확인할 것.")
            meta = metas[0]

            # ── HTML 우선 ────────────────────────────────────────────
            try:
                async def html_once() -> httpx.Response:
                    r = await client.get(ARXIV_HTML.format(arxiv_id=arxiv_id), timeout=60)
                    r.raise_for_status()
                    return r

                html_resp = await _with_retry(html_once, "arXiv HTML")
                text = _text_from_html(html_resp.text)
                method = "html"
            except httpx.HTTPStatusError:
                # 404 는 흔하다 — HTML 판이 없는 논문이라는 뜻일 뿐이므로 폴백한다
                text, method = "", ""

            # ── PDF 폴백 ─────────────────────────────────────────────
            if not text.strip():
                async def pdf_once() -> httpx.Response:
                    r = await client.get(ARXIV_PDF.format(arxiv_id=arxiv_id), timeout=120)
                    r.raise_for_status()
                    return r

                pdf_resp = await _with_retry(pdf_once, "arXiv PDF")
                pdf_path = PDF_DIR / f"{arxiv_id.replace('/', '_')}.pdf"
                pdf_path.write_bytes(pdf_resp.content)
                try:
                    text = _text_from_pdf(pdf_path)
                    method = "pdf"
                except Exception as e:  # noqa: BLE001
                    return _error(
                        f"PDF 텍스트 추출 실패: {type(e).__name__}: {e}",
                        "스캔본이거나 손상된 PDF일 수 있음. 원문 링크로 직접 확인할 것.",
                    )
    except Exception as e:  # noqa: BLE001
        return _http_error_to_message(e, "arXiv")

    if not text.strip():
        return _error(
            f"'{arxiv_id}' 본문 텍스트를 얻지 못했음 (HTML·PDF 모두)",
            "PDF 가 없는 논문일 수 있다. abs 페이지를 직접 확인할 것.",
        )

    text_path = TEXT_DIR / f"{arxiv_id.replace('/', '_')}.txt"
    text_path.write_text(text, encoding="utf-8")

    with _db() as con:
        con.execute(
            """INSERT OR REPLACE INTO papers
               (arxiv_id, title, authors, published, categories, abstract,
                pdf_path, text_path, text_chars, fetched_at, extract_method)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (arxiv_id, meta["title"], json.dumps(meta["authors"], ensure_ascii=False),
             meta["published"], json.dumps(meta["categories"]), meta["abstract"],
             str(pdf_path) if pdf_path else None, str(text_path), len(text), _now(), method),
        )
    return json.dumps(
        {"arxiv_id": arxiv_id, "title": meta["title"], "text_chars": len(text),
         "extract_method": method,
         "pdf_path": str(pdf_path) if pdf_path else None, "text_path": str(text_path),
         "preview": text[:400]},
        ensure_ascii=False, indent=2,
    )


@mcp.tool(
    name="get_paper_text",
    annotations={"title": "저장된 논문 원문 열람", "readOnlyHint": True,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def get_paper_text(params: GetTextInput) -> str:
    """저장된 논문의 추출 텍스트를 offset/max_chars 단위로 읽는다.
    긴 논문은 여러 번 나눠 읽어 컨텍스트를 아낄 것.

    Returns:
        str: JSON — {arxiv_id, offset, returned_chars, total_chars, has_more, text}
    """
    arxiv_id = _clean_arxiv_id(params.arxiv_id)
    with _db() as con:
        row = con.execute("SELECT text_path, text_chars FROM papers WHERE arxiv_id=?",
                          (arxiv_id,)).fetchone()
    if not row:
        return _error(f"'{arxiv_id}'는 아직 저장되지 않음", "fetch_paper를 먼저 호출할 것.")
    text = Path(row["text_path"]).read_text(encoding="utf-8")
    chunk = text[params.offset: params.offset + params.max_chars]
    return json.dumps(
        {"arxiv_id": arxiv_id, "offset": params.offset, "returned_chars": len(chunk),
         "total_chars": len(text),
         "has_more": params.offset + len(chunk) < len(text), "text": chunk},
        ensure_ascii=False, indent=2,
    )


@mcp.tool(
    name="verify_summary_numbers",
    annotations={"title": "요약 수치 원문 대조", "readOnlyHint": True,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def verify_summary_numbers(params: VerifyInput) -> str:
    """요약문에 등장하는 모든 숫자(두 자리 이상 또는 소수)가 원문 텍스트에
    실제로 존재하는지 대조한다. 불일치 항목은 '오류 확정'이 아니라
    '사람이 원문 확인' 신호다 — PDF 추출 누락 가능성이 있기 때문.

    Returns:
        str: JSON — {total_numbers, matched, pass_ratio, unmatched: [{token, context}]}
    """
    arxiv_id = _clean_arxiv_id(params.arxiv_id)
    with _db() as con:
        row = con.execute("SELECT text_path FROM papers WHERE arxiv_id=?", (arxiv_id,)).fetchone()
    if not row:
        return _error(f"'{arxiv_id}'는 아직 저장되지 않음", "fetch_paper를 먼저 호출할 것.")
    source = Path(row["text_path"]).read_text(encoding="utf-8")
    report = verify_numbers(params.summary_text, source)
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2)


@mcp.tool(
    name="save_summary",
    annotations={"title": "요약 저장(자동 수치 검증 포함)", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def save_summary(params: SaveSummaryInput) -> str:
    """요약 마크다운을 저장한다. 저장 직전에 수치 검증을 자동 수행하며,
    불일치가 있어도 저장은 하되 보고서에 명시한다 — KPI '(목표)' 수치처럼
    원문 밖 출처의 숫자는 정당하게 불일치할 수 있어서 차단하지 않는다.
    불일치 항목은 요약문에 출처를 명시했는지 사람이 확인할 것.

    Returns:
        str: JSON — {saved_path, verification: {total_numbers, matched, pass_ratio, unmatched}}
    """
    arxiv_id = _clean_arxiv_id(params.arxiv_id)
    with _db() as con:
        row = con.execute("SELECT text_path FROM papers WHERE arxiv_id=?", (arxiv_id,)).fetchone()
    if not row:
        return _error(f"'{arxiv_id}'는 아직 저장되지 않음", "fetch_paper를 먼저 호출할 것.")
    source = Path(row["text_path"]).read_text(encoding="utf-8")
    report = verify_numbers(params.markdown, source)

    out_path = SUMMARY_DIR / f"{arxiv_id.replace('/', '_')}.md"
    out_path.write_text(params.markdown, encoding="utf-8")
    with _db() as con:
        # 컬럼명을 명시한다 — summaries 는 ⑥ 사람 판단용 컬럼(review_*)이 추가돼
        # 위치 기반 INSERT 로는 값 개수가 안 맞는다. 요약을 새로 저장(재생성 포함)
        # 하면 이전 검토 상태는 의미가 없어지므로 review_status 를 'pending' 으로
        # 되돌린다 — 새 버전은 다시 사람이 봐야 한다.
        con.execute(
            """INSERT OR REPLACE INTO summaries
               (arxiv_id, path, numbers_total, numbers_matched, created_at,
                review_status, review_note, reviewed_at)
               VALUES (?,?,?,?,?,'pending',NULL,NULL)""",
            (arxiv_id, str(out_path), report.total, report.matched, _now()),
        )
    return json.dumps(
        {"saved_path": str(out_path), "verification": report.to_dict()},
        ensure_ascii=False, indent=2,
    )


def set_review_status(arxiv_id: str, status: str, note: str = "") -> None:
    """⑥ 사람 판단 결과를 기록한다. MCP 도구가 아니다 — 이 서버는 판단하지
    않는다는 원칙대로, 판단은 review_app.py(사람)가 하고 이 함수는 그 결과를
    저장만 한다. status: 'approved' | 'rejected' | 'pending'.
    """
    if status not in ("approved", "rejected", "pending"):
        raise ValueError(f"알 수 없는 review_status: {status}")
    arxiv_id = _clean_arxiv_id(arxiv_id)
    with _db() as con:
        con.execute(
            "UPDATE summaries SET review_status=?, review_note=?, reviewed_at=? WHERE arxiv_id=?",
            (status, note or None, _now(), arxiv_id),
        )


def save_repro_result(
    arxiv_id: str, repo_url: str, source: str, confidence: str,
    success: bool, exit_code: int | None, stage: str, attempt: int,
    network_used: bool, duration_s: float, log_path: str,
) -> None:
    """⑦ Docker 격리 실행 결과를 축적한다. MCP 도구가 아니다 — docker_runner.py
    가 판단(성공/실패)까지 끝낸 뒤 결과만 저장한다(set_review_status 와 동일 패턴).
    """
    arxiv_id = _clean_arxiv_id(arxiv_id)
    with _db() as con:
        con.execute(
            """INSERT OR REPLACE INTO repro_results
               (arxiv_id, repo_url, source, confidence, success, exit_code,
                stage, attempt, network_used, duration_s, log_path, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (arxiv_id, repo_url, source, confidence, int(success), exit_code,
             stage, attempt, int(network_used), duration_s, log_path, _now()),
        )


@mcp.tool(
    name="list_stored_papers",
    annotations={"title": "로컬 저장소 목록", "readOnlyHint": True,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def list_stored_papers(params: ListPapersInput) -> str:
    """저장된 논문과 요약 상태를 나열한다.

    Returns:
        str: JSON — {count, papers: [{arxiv_id, title, text_chars, summary_saved,
             numbers_matched, numbers_total}]}
    """
    with _db() as con:
        rows = con.execute(
            """SELECT p.arxiv_id, p.title, p.text_chars, p.extract_method,
                      s.path AS summary_path, s.numbers_total, s.numbers_matched
               FROM papers p LEFT JOIN summaries s ON p.arxiv_id = s.arxiv_id
               ORDER BY p.fetched_at DESC LIMIT ? OFFSET ?""",
            (params.limit, params.offset),
        ).fetchall()
    papers = [
        {"arxiv_id": r["arxiv_id"], "title": r["title"], "text_chars": r["text_chars"],
         "extract_method": r["extract_method"],
         "summary_saved": r["summary_path"] is not None,
         "numbers_matched": r["numbers_matched"], "numbers_total": r["numbers_total"]}
        for r in rows
    ]
    return json.dumps({"count": len(papers), "papers": papers}, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- 엔트리포인트

_init_storage()

if __name__ == "__main__":
    mcp.run()
