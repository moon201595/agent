"""paper_harness_mcp — 논문 수집·정리 하네스의 결정적 도구 계층 (Phase 0).

에이전트 루프(판단·요약·코드)는 MCP 클라이언트(Claude Code 등)가 담당하고,
이 서버는 결정적으로 동작하는 도구만 제공한다. 이 분리 덕분에 Phase 1에서
내부망 로컬 LLM으로 백엔드를 바꿔도 이 파일은 그대로 재사용된다.

도구 목록:
  - arxiv_search_papers      ① arXiv 검색 (키 불필요, 호출 간 3초 간격 준수)
  - s2_search_papers         ① Semantic Scholar 검색 (인용수 포함, 키는 선택)
  - s2_get_references        ① 인용망 backward — 이 논문이 인용한 것
  - s2_get_citations         ① 인용망 forward — 이 논문을 인용한 것
  - hybrid_search_local_papers ① 로컬 저장 논문 대상 BM25+임베딩 하이브리드 검색
  - dedupe_and_rank_papers   ② 중복 제거·선별 (결정적 규칙, 네트워크 미사용)
  - fetch_paper              ③ 원문 수집 (HTML 우선, 없으면 PDF) + 텍스트 추출·저장
  - get_paper_text           ③ 저장된 원문 텍스트 페이지 단위 열람
  - verify_summary_numbers   ⑤ 요약문 수치를 원문과 대조 (읽기 전용)
  - save_summary             요약 저장 (+ 자동 수치 검증, 경고만 하고 저장은 함)
  - get_summary_json         저장된 요약을 구조화 JSON으로 변환 (읽기 전용)
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
import api_usage
from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, ConfigDict, Field

import hybrid_search
import injection_scan
import retraction
import summarize_engine
import summary_parser
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
# DOI → 합법적 오픈액세스 PDF 위치 조회. 키 불필요, 대신 email 로 정중한 사용을
# 식별해야 한다(공식 요구사항) — 스크레이핑이 아니라 이미 공개된 버전의 소재를
# 알려주는 색인 서비스다. 페이월 우회 아님: 오픈액세스가 없으면 그냥 못 찾는다.
UNPAYWALL_API = "https://api.unpaywall.org/v2/{doi}"
# Unpaywall 은 example.com 류 더미 이메일을 실제로 거부한다(실측 확인,
# 422 "Please use your own email address"). .env 에 UNPAYWALL_EMAIL 로
# 재정의할 수 있게 하되, 기본값은 실제 연락 가능한 주소로 둔다.
UNPAYWALL_EMAIL = os.environ.get("UNPAYWALL_EMAIL", "answnsgur030@naver.com")

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
        # WAL — 다이제스트 생성(reader)과 ⑦ 재현 결과 기록(writer)이 동시에
        # 붙는 구조라서 켠다(M2, 2026-08-28). 기본 journal_mode=delete 에서는
        # writer 가 reader 를 막는다. WAL 은 DB 파일에 한 번 기록되면 이후
        # 연결에도 계속 적용되는 영구 설정이라 여기서 한 번만 실행하면 된다.
        #
        # WAL 은 로컬 디스크 전용이라(네트워크/공유 FS 에서 깨진다) 쓰기 전에
        # DB 위치를 실측했다: /home/mjh/paper-harness/data 는 /dev/sdd ext4,
        # 즉 WSL 네이티브 파일시스템이다(/mnt/c 가 아님) — 안전.
        #
        # busy_timeout 은 별도로 안 건다: Python sqlite3 의 기본 connect
        # timeout 이 5초이고 실측으로 PRAGMA busy_timeout=5000 이 이미
        # 걸려 있는 걸 확인했다. 쓰기 경로(save_repro_result)도 단일
        # INSERT OR REPLACE 라 read-then-write 업그레이드 교착이 없어서
        # BEGIN IMMEDIATE 까지는 필요 없다.
        con.execute("PRAGMA journal_mode=WAL")
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
        # arXiv 밖 논문(저널 PDF 수동 업로드·오픈액세스 자동 수집) 지원(2026-08-04).
        # arxiv_id 컬럼은 이름 그대로 두되 값으로 합성 ID(pdf-<hash>)도 받는다 —
        # 이 컬럼이 어디서 왔는지는 source 로 구분한다: 'arxiv' | 'manual-pdf: <출처>'
        # | 'open-access: <URL>'. 기존 arXiv 행은 NULL로 남고 fetch_paper 는 안 건드림.
        if "source" not in existing:
            con.execute("ALTER TABLE papers ADD COLUMN source TEXT")
        # ⑧ 철회 여부(M5, 2026-08-28). NULL = 미조회/판정 불가, 0 = 정상,
        # 1 = 철회 확정(OpenAlex + Crossref 교차확인), 2 = 요주의(OpenAlex 는
        # 철회라는데 교차확인이 안 됨). 기존 행은 NULL 로 남는다 — "조회 안
        # 했다"와 "정상이다"를 절대 같은 값으로 두지 않는다(retraction.py 참고).
        if "is_retracted" not in existing:
            con.execute("ALTER TABLE papers ADD COLUMN is_retracted INTEGER")
        # ③ 프롬프트 인젝션 사전 스캔 결과(M7, 2026-08-28). NULL·빈 문자열 =
        # 걸린 것 없음, 그 외에는 사람이 읽을 사유 문자열. 요약을 막지 않고
        # 표시만 한다 — injection_scan.py 참고.
        if "injection_suspect" not in existing:
            con.execute("ALTER TABLE papers ADD COLUMN injection_suspect TEXT")

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
        # 성공한 시도의 clone 코드가 어디 남아있는지. 원래 docker_runner.py는
        # 성공이든 실패든 clone을 즉시 지웠다("설치+실행 성공 여부만 본다"는
        # 원칙 — 코드 보관은 처음부터 범위 밖이었음) — 그런데 사용자가 "재현이
        # 됐으면 그 코드를 어디서 보냐"고 실제로 물어봐서(2026-08-12), 성공한
        # 경우에 한해 코드를 남기기로 했다. 실패한 시도는 그대로 안 남긴다
        # (디스크 낭비 — 실패 이유는 이미 stage/exit_code로 충분히 남음).
        existing_r = {row[1] for row in con.execute("PRAGMA table_info(repro_results)")}
        if "local_path" not in existing_r:
            con.execute("ALTER TABLE repro_results ADD COLUMN local_path TEXT")

        # 실패 사유를 stage 안에서 한 단계 더 나눈 코드(2026-09-01).
        # stage 만으로는 "저자 저장소가 404" 와 "클론이 다른 이유로 실패"가,
        # "실행이 네트워크 없이 죽었다" 와 "그냥 exit 1" 이 구분되지 않는다.
        # 다이제스트가 [재현 ✗] 하나로 서로 다른 사실을 뭉개고 있었고, 그
        # 뭉갬이 §8-16(egress allowlist 가 필요한가)의 근거까지 같이 삼켰다 —
        # network_suspected 가 계산만 되고 저장되지 않아 29건이 쌓이는 동안
        # 아무것도 안 모였다. 판정에는 쓰지 않는 주석 전용 값이다.
        if "fail_detail" not in existing_r:
            con.execute("ALTER TABLE repro_results ADD COLUMN fail_detail TEXT")

        # ① 하이브리드 검색(2026-08-06)용 임베딩 캐시. 논문 텍스트(제목+초록)가
        # 바뀌지 않는 한 임베딩도 안 바뀌므로, 검색할 때마다 다시 계산하지
        # 않고 여기 저장해 재사용한다 — hybrid_search.py 는 이 캐시를 모른다
        # (DB 접근 없는 순수 계산 모듈로 남겨둠, 다른 모듈들과 같은 경계 원칙).
        con.execute(
            """CREATE TABLE IF NOT EXISTS paper_embeddings (
                arxiv_id TEXT PRIMARY KEY, model TEXT, embedding TEXT, updated_at TEXT
            )"""
        )


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_arxiv_id(raw: str) -> str:
    """URL·버전 표기를 걷어내고 순수 ID만 남긴다. 예: 'abs/1706.03762v5' → '1706.03762'

    2026-08-05 실측: /html/ 링크(arxiv.org/html/2505.19433v1)는 못 걷어내던
    버그를 발견 — abs·pdf 만 처리했었다. 논문 목록에 arXiv HTML 링크를
    그대로 붙여넣는 경우가 실제로 있어서 셋 다 처리하도록 고쳤다.
    """
    raw = raw.strip()
    raw = re.sub(r"^https?://arxiv\.org/(abs|pdf|html)/", "", raw)
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
            # 2026-08-31 실측: arXiv 응답 시간이 크게 흔들린다 — 같은 시각에
            # 키워드 3개짜리 짧은 쿼리가 45초 타임아웃이 나고 21개짜리 긴
            # 쿼리는 15초에 왔다. 쿼리 복잡도가 아니라 서버 쪽 변동이다.
            # 정상 응답이 43초 걸린 사례를 실제로 측정해서, 30초로는 멀쩡한
            # 응답을 실패로 버리게 된다 — 60초로 올린다(_with_retry 가 상한
            # 2회까지 재시도하므로 최악 대기는 여전히 유한하다).
            resp = await client.get(ARXIV_API, params=params, timeout=60)
            _last_arxiv_call = time.monotonic()
        api_usage.record("arxiv", "ok" if resp.status_code == 200 else str(resp.status_code))
        resp.raise_for_status()
        return resp

    return await _with_retry(once, "arXiv API")


async def _throttled_s2_get(
    client: httpx.AsyncClient, params: dict, headers: dict, url: str = S2_API,
) -> httpx.Response:
    """Semantic Scholar 호출 간 최소 간격(S2_MIN_INTERVAL)을 서버 전역에서 강제한다.
    "초당 1회, 전체 엔드포인트 합산" 이 키 등록 여부와 무관하게 적용되는 공식 한도라
    _throttled_arxiv_get 과 같은 패턴으로 막는다 — 재시도마다 다시 적용해야
    재시도가 한도를 또 넘기지 않는다.

    url 을 파라미터로 받는다(기본값은 검색 엔드포인트) — "전체 엔드포인트 합산"이라
    references/citations 처럼 다른 엔드포인트를 불러도 이 락을 그대로 같이 써야
    간격이 실제로 지켜진다. 엔드포인트마다 별도 락을 두면 한도를 우회하게 된다.
    """

    async def once() -> httpx.Response:
        global _last_s2_call
        async with _s2_lock:
            wait = S2_MIN_INTERVAL - (time.monotonic() - _last_s2_call)
            if wait > 0:
                await asyncio.sleep(wait)
            resp = await client.get(url, params=params, headers=headers, timeout=30)
            _last_s2_call = time.monotonic()
        api_usage.record("s2", "ok" if resp.status_code == 200 else str(resp.status_code))
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


class S2CitationGraphInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    arxiv_id: str = Field(..., description="기준 논문의 arXiv ID (fetch_paper로 이미 저장돼 있을 필요는 없음)")
    limit: int = Field(default=20, ge=1, le=100, description="최대 결과 수 (1~100)")


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


class HybridSearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    query: str = Field(..., min_length=1, max_length=300,
                       description="검색어. fetch_paper 로 이미 저장해 둔 논문 안에서 찾는다")
    top_k: int = Field(default=10, ge=1, le=50, description="최대 결과 수 (1~50)")


class SummaryJsonInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    arxiv_id: str = Field(..., description="요약이 저장된 논문의 arXiv ID")


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


async def _s2_citation_graph(arxiv_id: str, limit: int, edge: str) -> str:
    """s2_get_references/s2_get_citations 공용 구현. edge: 'references'(backward,
    이 논문이 인용한 것) | 'citations'(forward, 이 논문을 인용한 것).

    문헌 조사에서 실제로 자주 필요한 동작인데 지금까지 완전히 빠져 있던 축이다
    (2026-08-04 조사 문서 §0.3, §1-A-5). Crawler/Selector 패턴(PaSa)에서
    Crawler 에 해당하는 결정적 부분만 구현한다 — depth는 항상 1(이 논문 기준
    한 홉만), 후보 수는 limit 으로 코드가 상한을 강제한다. 어떤 후보가
    사용자 관심사와 관련 있는지 판정(Selector)은 이 서버의 일이 아니다 —
    사람이나 Claude Code 가 반환된 제목·초록을 보고 판단한다.
    """
    headers = {}
    if os.environ.get("S2_API_KEY"):
        headers["x-api-key"] = os.environ["S2_API_KEY"]
    url = f"https://api.semanticscholar.org/graph/v1/paper/ARXIV:{arxiv_id}/{edge}"
    api_params = {
        "fields": "title,abstract,year,citationCount,externalIds",
        "limit": limit,
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await _throttled_s2_get(client, api_params, headers, url=url)
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        return _http_error_to_message(e, "Semantic Scholar")
    key = "citedPaper" if edge == "references" else "citingPaper"
    papers = []
    for item in data.get("data", []):
        p = item.get(key) or {}
        ext = p.get("externalIds") or {}
        papers.append(
            {
                "title": p.get("title"),
                "year": p.get("year"),
                "citation_count": p.get("citationCount"),
                "arxiv_id": ext.get("ArXiv"),
                "abstract": (p.get("abstract") or "")[:600],
            }
        )
    return json.dumps(
        {"arxiv_id": arxiv_id, "edge": edge, "count": len(papers), "papers": papers},
        ensure_ascii=False, indent=2,
    )


@mcp.tool(
    name="s2_get_references",
    annotations={"title": "① 인용망 — 이 논문이 인용한 것(backward)", "readOnlyHint": True,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def s2_get_references(params: S2CitationGraphInput) -> str:
    """이 논문이 참고문헌으로 인용한 논문 목록(backward). 예: 이 논문이 쓴
    벤치마크·베이스라인의 원 논문을 찾을 때 쓴다.

    Returns:
        str: JSON — {arxiv_id, edge:"references", count, papers:[{title, year,
             citation_count, arxiv_id, abstract}, ...]}
    """
    return await _s2_citation_graph(_clean_arxiv_id(params.arxiv_id), params.limit, "references")


@mcp.tool(
    name="s2_get_citations",
    annotations={"title": "① 인용망 — 이 논문을 인용한 것(forward)", "readOnlyHint": True,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def s2_get_citations(params: S2CitationGraphInput) -> str:
    """이 논문을 인용한 후속 논문 목록(forward). 예: 이 논문의 벤치마크·방법론에
    대한 후속 지적·개선이 있었는지 찾을 때 쓴다.

    Returns:
        str: JSON — {arxiv_id, edge:"citations", count, papers:[{title, year,
             citation_count, arxiv_id, abstract}, ...]}
    """
    return await _s2_citation_graph(_clean_arxiv_id(params.arxiv_id), params.limit, "citations")


_EMBED_MODEL = "gemini-embedding-001"
_EMBED_CALL_DELAY = 0.5  # 임베딩 캐시가 비어 있을 때(첫 실행) 연속 호출 사이 여유


async def _get_or_compute_embedding(
    client: httpx.AsyncClient, arxiv_id: str, text: str,
) -> tuple[list[float] | None, bool]:
    """paper_embeddings 캐시를 먼저 보고, 없으면 계산해서 채운다. 논문 텍스트
    (제목+초록)는 저장 후 안 바뀌므로 한 번 계산하면 재사용해도 안전하다.
    GOOGLE_API_KEY 가 없거나 호출이 실패하면 None 을 반환한다 — 그 논문은
    hybrid_search.rank_documents 에서 BM25 랭킹에만 참여하고 하이브리드
    검색 자체는 죽지 않는다(hybrid_search.py 의 부분 실패 허용 설계 참고).

    returns (embedding, was_cache_hit) — 호출부가 실제로 네트워크를 탄
    경우에만 호출 간 지연을 넣게 하려고 캐시 적중 여부를 함께 돌려준다.
    """
    with _db() as con:
        row = con.execute(
            "SELECT embedding FROM paper_embeddings WHERE arxiv_id=? AND model=?",
            (arxiv_id, _EMBED_MODEL),
        ).fetchone()
    if row:
        return json.loads(row["embedding"]), True
    try:
        vec = await hybrid_search.embed_text(client, text, "RETRIEVAL_DOCUMENT")
    except Exception:  # noqa: BLE001 — 임베딩 실패는 검색 전체를 막지 않는다
        return None, False
    with _db() as con:
        con.execute(
            "INSERT OR REPLACE INTO paper_embeddings (arxiv_id, model, embedding, updated_at) "
            "VALUES (?,?,?,?)",
            (arxiv_id, _EMBED_MODEL, json.dumps(vec), _now()),
        )
    return vec, False


@mcp.tool(
    name="hybrid_search_local_papers",
    annotations={"title": "① 로컬 저장 논문 하이브리드 검색", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
)
async def hybrid_search_local_papers(params: HybridSearchInput) -> str:
    """`fetch_paper`로 이미 로컬에 저장해 둔 논문들 안에서 검색한다(외부
    "심층 조사와 발전 설계" 문서가 제안한 Hybrid Search, 2026-08-06).
    arxiv_search_papers/s2_search_papers 는 외부 API 자체를 검색하는 도구고,
    이건 그와 달리 **이미 모아둔 논문 중에서** 다시 찾는 용도다 — 평가셋이나
    수집한 문헌이 쌓일수록 "전에 저장해 둔 논문 중에 관련된 게 있었나"를
    찾기 어려워지는 문제를 푼다.

    BM25(어휘 일치)와 임베딩 코사인 유사도(의미 일치, gemini-embedding-001)를
    Reciprocal Rank Fusion으로 합친다 — 자세한 설계 이유는 hybrid_search.py
    모듈 docstring 참고. 논문 임베딩은 paper_embeddings 테이블에 캐시된다
    (idempotentHint=False로 표시한 이유: 첫 호출에서 캐시를 채우는 부수효과가
    있다 — 검색 결과 자체는 몇 번을 불러도 같다). GOOGLE_API_KEY 가 없으면
    BM25 단독으로 동작한다(하이브리드가 아니라도 검색 자체는 계속 됨).

    Returns:
        str: JSON — {count, query, embeddings_used: bool, papers: [{arxiv_id,
             title, bm25_score, cosine_score, fused_score}, ...]}
    """
    with _db() as con:
        rows = con.execute("SELECT arxiv_id, title, abstract FROM papers").fetchall()
    if not rows:
        return json.dumps({"count": 0, "query": params.query, "papers": []}, ensure_ascii=False)

    documents = [f"{r['title'] or ''}. {r['abstract'] or ''}" for r in rows]
    corpus_tokens = [hybrid_search.tokenize(d) for d in documents]
    bm25 = hybrid_search.BM25(corpus_tokens)
    query_tokens = hybrid_search.tokenize(params.query)

    query_vec: list[float] | None = None
    doc_vecs: list[list[float] | None] = [None] * len(rows)
    if os.environ.get("GOOGLE_API_KEY"):
        async with httpx.AsyncClient() as client:
            try:
                query_vec = await hybrid_search.embed_text(client, params.query, "RETRIEVAL_QUERY")
            except Exception:  # noqa: BLE001
                query_vec = None
            if query_vec is not None:
                for i, r in enumerate(rows):
                    doc_vecs[i], cache_hit = await _get_or_compute_embedding(
                        client, r["arxiv_id"], documents[i]
                    )
                    if not cache_hit:
                        await asyncio.sleep(_EMBED_CALL_DELAY)

    results = hybrid_search.rank_documents(query_tokens, bm25, query_vec, doc_vecs, params.top_k)
    papers = [
        {
            "arxiv_id": rows[r["index"]]["arxiv_id"],
            "title": rows[r["index"]]["title"],
            "bm25_score": r["bm25_score"],
            "cosine_score": r["cosine_score"],
            "fused_score": r["fused_score"],
        }
        for r in results
    ]
    return json.dumps(
        {"count": len(papers), "query": params.query, "embeddings_used": query_vec is not None,
         "papers": papers},
        ensure_ascii=False, indent=2,
    )


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
                pdf_path, text_path, text_chars, fetched_at, extract_method,
                injection_suspect)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (arxiv_id, meta["title"], json.dumps(meta["authors"], ensure_ascii=False),
             meta["published"], json.dumps(meta["categories"]), meta["abstract"],
             str(pdf_path) if pdf_path else None, str(text_path), len(text), _now(), method,
             # ③ 인젝션 사전 스캔(M7) — 요약을 막지 않고 표시만 한다.
             "; ".join(injection_scan.scan(text)) or None),
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


def read_full_text(arxiv_id: str) -> str:
    """저장된 논문 원문 전체를 그대로 읽는다. MCP 도구가 아니다.

    get_paper_text 는 max_chars 상한이 80,000자다(채팅 컨텍스트 절약용,
    GetTextInput 검증 규칙) — 배치 스크립트가 요약 엔진에 원문 전체를 한
    번에 넘길 때는 이 상한이 방해만 된다(2026-08-06 실측: 긴 논문이 상한
    안에서 잘려 결과 절을 통째로 못 보는 사고가 있었다). summarize_engine
    이 자체적으로 청크(300,000자 단위)를 나누므로 여기서는 자르지 않는다.
    """
    arxiv_id = _clean_arxiv_id(arxiv_id)
    with _db() as con:
        row = con.execute("SELECT text_path FROM papers WHERE arxiv_id=?", (arxiv_id,)).fetchone()
    if not row:
        raise ValueError(f"'{arxiv_id}'는 아직 저장되지 않음 — fetch_paper 먼저 호출할 것")
    return Path(row["text_path"]).read_text(encoding="utf-8")


# PDF·오픈액세스로 들여온 논문의 제목을 직접 타이핑해야 하는 게 번거롭다는
# 지적(2026-08-12, "PDF 제목 따라 입력하면 되잖아") — 세 단계 폴백으로
# 자동화한다: ①PDF 메타데이터 →②추출 텍스트 첫 줄 휴리스틱→③(둘 다 실패
# 시) 이 자리표시자로 저장해 두고 요약 생성 후 요약문 "제목 :" 줄에서 다시
# 채운다(_summarize_target 참고). 접두어로 검색해 "아직 못 채운" 논문을
# 찾을 수 있게 상수로 뺐다.
_TITLE_PLACEHOLDER_PREFIX = "(제목 미확인"


# 제목 후보가 "정상적인 글자로 된 문구"인지 보는 필터. 두 가지 실제 사례를
# 실측(2026-08-12, pdf-5bd2ec925e.pdf — 한글 학술 DB PDF)해서 만들었다:
# ① pypdf가 지원 안 하는 폰트 인코딩(UniKS-UTF16-H 등)을 쓰는 PDF는 본문
#    첫 줄들이 통째로 깨진 제어문자·사설 유니코드로 나온다.
# ② 이런 PDF는 /Title 메타데이터조차 논문 제목이 아니라 배포 플랫폼
#    워터마크("DBPIA-NURIMEDIA")인 경우가 있다 — 메타데이터라고 무조건
#    믿으면 안 된다. 두 경로(메타데이터/본문 첫 줄) 모두 이 필터를 거친다.
_TITLE_OK_CHAR_RE = re.compile(r"[A-Za-z0-9가-힣\s.,:;'\"()\-–—/&%+·]")


def _looks_like_title(candidate: str) -> bool:
    if not (8 <= len(candidate) <= 300):
        return False
    if candidate.lower().startswith(("arxiv:", "http", "untitled")):
        return False
    if " " not in candidate:
        return False  # 실제 제목은 거의 항상 여러 단어 — 워터마크 같은 단일 토큰 제외
    ok_ratio = len(_TITLE_OK_CHAR_RE.findall(candidate)) / len(candidate)
    # 0.85로는 부족했다 — 실측(2026-08-12, 같은 PDF)에서 "-Ի fault detection
    # and classification (FDC)іԂ ଡ." 처럼 키릴·아르메니아·오리야 문자 4개가
    # 섞인 줄(ratio 0.92)이 통과해 반쯤 깨진 문장을 제목으로 잘못 골랐다.
    # 정상적인 영문/한글 제목에 다른 문자 체계가 섞일 이유가 없다는 점에
    # 착안해 0.95로 올렸다 — 애매하면 빈 문자열을 돌려주고 요약 생성 후
    # 폴백(extract_title_from_summary)에 넘기는 쪽이 더 안전하다.
    return ok_ratio >= 0.95


def _guess_title_from_pdf(pdf_path: Path, text: str) -> str:
    """PDF에서 제목을 기계적으로 추정한다. 완벽한 정답이 목표가 아니라
    "빈 칸·수동 타이핑보다 낫다"가 목표라 실패하면 조용히 빈 문자열을
    반환한다(호출자가 다음 폴백으로 넘어감) — 틀린 값을 억지로 만들어
    내지 않는다.
    """
    try:
        from pypdf import PdfReader  # 지연 임포트

        reader = PdfReader(str(pdf_path))
        meta_title = ((reader.metadata.title if reader.metadata else None) or "").strip()
        if _looks_like_title(meta_title):
            return meta_title
    except Exception:  # noqa: BLE001
        pass

    for line in text.splitlines():
        candidate = line.strip()
        if _looks_like_title(candidate):
            return candidate
    return ""


_TITLE_LINE_RE = re.compile(r"^-\s*제목\s*[:：]\s*(.+)$", re.MULTILINE)


def extract_title_from_summary(markdown: str) -> str:
    """생성된 요약문의 "### 기본정보 - 제목 : ..." 줄에서 제목을 뽑는다.
    템플릿이 고정한 구두점 구조를 그대로 읽는 기계적 파싱이지 LLM 재해석이
    아니다(review_app.py의 _FIELD_LABEL_RE와 같은 성격). PDF에서 제목을
    못 찾아 자리표시자로 저장된 논문의 제목을 요약 생성 후 사후에 채우는
    용도(2026-08-12) — LLM이 원문 전체를 읽고 뽑은 값이라 첫 줄 휴리스틱
    보다 신뢰도가 높다.
    """
    m = _TITLE_LINE_RE.search(markdown)
    if not m:
        return ""
    value = m.group(1).strip()
    if not value or "확인 불가" in value or "미상" in value:
        return ""
    return value


def update_paper_title(arxiv_id: str, title: str) -> None:
    """papers.title을 갱신한다. save_repro_result·set_review_status와 같은
    plain 함수 패턴 — MCP 도구가 아니다."""
    title = title.strip()
    if not title:
        return
    with _db() as con:
        con.execute("UPDATE papers SET title=? WHERE arxiv_id=?", (title, arxiv_id))


def ingest_local_pdf(pdf_bytes: bytes, title: str = "", source_note: str = "manual-pdf") -> dict:
    """arXiv 밖 논문(저널·컨퍼런스 PDF)을 수동으로 들여온다(2026-08-04).

    이미 합법적으로 접근 가능한 파일(기관 구독 등으로 사용자가 이미 갖고 있는
    PDF)을 사용자가 직접 올리는 경로다 — 페이월 우회나 스크레이핑이 아니다.
    MCP 도구가 아니다: review_app.py 가 파일 업로더에서 받은 bytes 를 그대로
    넘겨 호출한다 (바이너리를 JSON 파라미터로 감싸는 건 MCP 에 안 맞는다 —
    save_repro_result·set_review_status 와 같은 "plain 함수" 패턴).

    arxiv_id 대신 파일 내용 해시로 합성 ID(pdf-<hash10>)를 만든다 — 같은
    파일을 다시 올려도 같은 ID 가 나와 fetch_paper 처럼 멱등하다.

    title을 안 주면(빈 문자열) _guess_title_from_pdf로 자동 추정한다
    (2026-08-12) — 그것도 실패하면 _TITLE_PLACEHOLDER_PREFIX 자리표시자로
    저장해 둔다. 반환값의 title_auto가 True면(= 호출자가 title을 안 줘서
    서버가 추정/자리표시자를 채운 경우) review_app._summarize_target이
    생성된 요약문에서 제목을 다시 뽑아 개선할 수 있다 — 사용자가 직접 친
    제목(title_auto=False)은 절대 안 건드린다.

    Returns:
        dict: {arxiv_id, title, title_auto, text_chars, pdf_path, text_path, preview}
    Raises:
        ValueError: 텍스트 추출 실패(스캔본·손상 파일 등)
    """
    import hashlib

    synth_id = f"pdf-{hashlib.sha1(pdf_bytes).hexdigest()[:10]}"

    with _db() as con:
        row = con.execute("SELECT * FROM papers WHERE arxiv_id=?", (synth_id,)).fetchone()
    if row and row["text_path"] and Path(row["text_path"]).exists():
        # 이미 저장된 파일 재업로드 — 이전에 제목이 자동/수동 어느 쪽으로
        # 채워졌는지 기록이 없어 title_auto는 보수적으로 False로 둔다(재
        # 처리를 안 하니 어차피 이후 backfill 대상도 아님).
        return {"arxiv_id": synth_id, "title": row["title"], "title_auto": False,
                "text_chars": row["text_chars"], "pdf_path": row["pdf_path"],
                "text_path": row["text_path"], "note": "이미 저장된 파일 — 재처리 생략"}

    pdf_path = PDF_DIR / f"{synth_id}.pdf"
    pdf_path.write_bytes(pdf_bytes)
    try:
        text = _text_from_pdf(pdf_path)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"PDF 텍스트 추출 실패: {type(e).__name__}: {e}") from e
    if not text.strip():
        raise ValueError("PDF에서 텍스트를 추출하지 못함 — 스캔본이거나 손상된 파일일 수 있음")

    given_title = (title or "").strip()
    title = given_title
    if not title:
        title = _guess_title_from_pdf(pdf_path, text)
    if not title:
        title = f"{_TITLE_PLACEHOLDER_PREFIX} · {synth_id})"
    title_auto = not given_title

    text_path = TEXT_DIR / f"{synth_id}.txt"
    text_path.write_text(text, encoding="utf-8")

    with _db() as con:
        con.execute(
            """INSERT OR REPLACE INTO papers
               (arxiv_id, title, authors, published, categories, abstract,
                pdf_path, text_path, text_chars, fetched_at, extract_method, source,
                injection_suspect)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (synth_id, title, json.dumps([], ensure_ascii=False), None,
             json.dumps([], ensure_ascii=False), None, str(pdf_path), str(text_path),
             len(text), _now(), "pdf", source_note,
             "; ".join(injection_scan.scan(text)) or None),
        )
    return {"arxiv_id": synth_id, "title": title, "title_auto": title_auto, "text_chars": len(text),
            "pdf_path": str(pdf_path), "text_path": str(text_path), "preview": text[:400]}


async def resolve_unpaywall_pdf(doi: str) -> dict | None:
    """DOI로 합법적 오픈액세스 PDF 위치와 제목을 찾는다(Unpaywall API). 못
    찾으면 None — 그 경우 이 논문은 오픈액세스가 아니라는 뜻이고, 수동
    업로드로 가야 한다(ingest_local_pdf).

    Unpaywall 응답에 논문 제목이 이미 들어 있어서(2026-08-12, "오픈액세스도
    링크 따라가면 제목이 분명 있을거다" 지적) 사람이 따로 안 쳐도 되게
    같이 돌려준다 — 예전엔 url_for_pdf만 뽑고 title은 버렸었다.

    Returns:
        dict | None: {"url": PDF 직링크, "title": Unpaywall이 아는 제목(없으면 "")}
    """
    doi = doi.strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi.org/"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
            break
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            UNPAYWALL_API.format(doi=doi), params={"email": UNPAYWALL_EMAIL}, timeout=20,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
    best = data.get("best_oa_location") or {}
    url = best.get("url_for_pdf")
    if not url:
        return None
    return {"url": url, "title": (data.get("title") or "").strip()}


async def fetch_pdf_from_url(pdf_url: str, title: str = "", source_note: str = "") -> dict:
    """오픈액세스 PDF를 URL로 직접 받아 들여온다 — S2 검색 결과의
    open_access_pdf 필드나 resolve_unpaywall_pdf() 가 찾아준 링크용이다.
    이미 공개된 파일만 받으므로 페이월 우회가 아니다.

    검증은 파일 시그니처(매직 바이트)로 한다 — content-type 헤더나 URL이
    ".pdf"로 끝나는지는 안 믿는다. 실측(2026-08-05)으로 확인: nature.com의
    "*.pdf" URL이 실제로는 HTML 로그인/에러 페이지를 200 OK로 돌려주는
    경우가 있었고, URL이 ".pdf"로 끝난다는 이유로 그걸 그대로 받아들여
    pypdf 단계에서야 "invalid pdf header"로 깨졌다. 진짜 PDF는 항상
    %PDF- 로 시작한다 — 이거 하나만 보는 게 훨씬 신뢰할 만하다.

    Raises:
        ValueError: PDF가 아닌 응답(초록·로그인 페이지로 리다이렉트된 경우 등)
    """
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) paper-harness/1.0"}
    async with httpx.AsyncClient(follow_redirects=True, headers=headers) as client:
        resp = await client.get(pdf_url, timeout=60)
        resp.raise_for_status()
        pdf_bytes = resp.content
        if not pdf_bytes.startswith(b"%PDF-"):
            content_type = resp.headers.get("content-type", "")
            raise ValueError(
                f"PDF가 아닌 응답(파일 시그니처 불일치, content-type={content_type!r}) — "
                "링크가 초록·로그인 페이지일 수 있음"
            )
    # title이 비어 있으면 ingest_local_pdf 자체의 폴백 체인(PDF 메타데이터
    # →첫 줄 휴리스틱→자리표시자)이 이어받는다 — 여기서 하드코딩된 자리
    # 표시자로 덮어쓰지 않는다(2026-08-12, 이전엔 "(제목 미입력)"으로 바로
    # 덮어써서 자동 추정이 끼어들 틈이 없었다).
    return ingest_local_pdf(pdf_bytes, title, source_note or f"open-access: {pdf_url}")


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
    # expect_grounded=True — 여기로 오는 요약은 전부 방금 생성된 것이고,
    # 프롬프트가 모든 수치에 [S번호]를 달라고 지시한 뒤 만들어진 것이다(M4,
    # 2026-08-28). 그래서 태그 없는 수치는 "구형 요약이라 어쩔 수 없음"이
    # 아니라 "LLM이 근거를 빠뜨림"으로 봐야 한다. 신/구를 호출부가 확실히
    # 아는 지점이 여기라, 신규 저장 경로 전부(batch_summarize 배치·review_app
    # 재생성·PDF 업로드)가 이 함수 하나를 지나므로 여기 한 곳만 True 로 준다.
    report = verify_numbers(params.markdown, source, expect_grounded=True)

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


@mcp.tool(
    name="get_summary_json",
    annotations={"title": "요약 구조화 JSON 변환", "readOnlyHint": True,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def get_summary_json(params: SummaryJsonInput) -> str:
    """저장된 요약 마크다운을 구조화 JSON으로 바꾼다(외부 "심층 조사와 발전
    설계" 문서가 제안한 항목, 2026-08-06). "### 절 제목" 구조를 그대로 살려
    절마다 불릿 목록으로 뽑고, verify.py 로 각 수치 주장을 다시 검증해
    found/grounded/sentence_id 를 함께 붙인다 — "이 요약이 뭐라고 썼는지"와
    "그중 뭐가 실제로 검증됐는지"를 한 번에 기계가 읽을 수 있게 준다.

    값의 조건/비교대상/지표 같은 자연어 세부 필드는 정규식으로 억지로
    쪼개지 않는다(summary_parser.py 모듈 docstring 참고) — 그건 애매한
    문장을 필드로 분류하는 판단이라 이 서버의 일이 아니다.

    Returns:
        str: JSON — {meta, sections: {절제목: [불릿, ...]}, verification: {
             total, matched, pass_ratio, grounded, claims: [{token, found,
             grounded, sentence_id, context}, ...]}}
    """
    arxiv_id = _clean_arxiv_id(params.arxiv_id)
    with _db() as con:
        row = con.execute(
            """SELECT p.title, p.authors, p.published, p.text_path, s.path AS summary_path
               FROM papers p LEFT JOIN summaries s ON p.arxiv_id = s.arxiv_id
               WHERE p.arxiv_id = ?""",
            (arxiv_id,),
        ).fetchone()
    if not row:
        return _error(f"'{arxiv_id}'는 아직 저장되지 않음", "fetch_paper를 먼저 호출할 것.")
    if not row["summary_path"]:
        return _error(f"'{arxiv_id}'는 아직 요약이 없음", "save_summary를 먼저 호출할 것.")
    summary_md = Path(row["summary_path"]).read_text(encoding="utf-8")
    source_text = Path(row["text_path"]).read_text(encoding="utf-8")
    meta = {
        "arxiv_id": arxiv_id, "title": row["title"],
        "authors": row["authors"], "published": row["published"],
    }
    result = summary_parser.parse_summary(summary_md, source_text, meta)
    return json.dumps(result, ensure_ascii=False, indent=2)


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


S2_BATCH_API = "https://api.semanticscholar.org/graph/v1/paper/batch"


async def fetch_s2_tldrs(client: httpx.AsyncClient, arxiv_ids: list[str]) -> dict[str, str]:
    """arXiv ID 목록 → {arxiv_id: tldr 한 줄}. 없는 논문은 키가 아예 안 들어간다.

    M6(2026-08-28): 이미 쓰는 Semantic Scholar Graph API 의 tldr 필드다 —
    신규 API 가 아니라 기존 API 의 필드 추가이고, /paper/batch 로 **논문 여러
    편을 한 번의 호출**로 받는다(실측: 3편을 1회에). 프로필 하나당 최대
    max_items(기본 8)편이므로 스캔당 S2 호출이 1회 늘어날 뿐이다.

    이 요약은 S2 모델이 만든 것이고 우리 ⑤ 검증을 통과한 게 아니다 —
    호출부(digest.py)가 반드시 그렇게 표기해야 한다.

    실패하면 빈 dict 를 돌려준다. tldr 은 있으면 좋은 부가 정보이지
    파이프라인 게이트가 아니다.
    """
    ids = [a for a in arxiv_ids if a and not a.startswith("pdf-")]
    if not ids:
        return {}
    headers = {}
    if os.environ.get("S2_API_KEY"):
        headers["x-api-key"] = os.environ["S2_API_KEY"]
    try:
        # 배치는 POST 라 _throttled_s2_get(GET 전용)을 그대로 못 쓴다. 다만
        # "초당 1회, 전체 엔드포인트 합산"이라는 S2 한도는 같이 적용되므로
        # 같은 락·같은 간격을 쓴다 — 엔드포인트마다 따로 세면 한도를 우회하게 된다.
        global _last_s2_call
        async with _s2_lock:
            wait = S2_MIN_INTERVAL - (time.monotonic() - _last_s2_call)
            if wait > 0:
                await asyncio.sleep(wait)
            resp = await client.post(
                S2_BATCH_API, params={"fields": "tldr"},
                json={"ids": [f"ARXIV:{a}" for a in ids]},
                headers=headers, timeout=40,
            )
            _last_s2_call = time.monotonic()
        resp.raise_for_status()
        data = resp.json()
    except Exception:  # noqa: BLE001 — 부가 정보라 실패해도 파이프라인은 계속된다
        return {}

    out: dict[str, str] = {}
    for arxiv_id, item in zip(ids, data if isinstance(data, list) else []):
        if not item:
            continue  # S2 에 없는 논문은 null 로 온다
        text = (item.get("tldr") or {}).get("text")
        if text:
            out[arxiv_id] = text
    return out


async def refresh_retraction_status(arxiv_id: str) -> int | None:
    """⑧ 철회 여부를 조회해 papers.is_retracted 에 저장한다(M5, 2026-08-28).
    MCP 도구가 아니다 — retraction.py 가 판정까지 끝낸 뒤 결과만 저장한다
    (save_repro_result·set_review_status 와 동일 패턴).

    이미 판정값이 있으면 다시 조회하지 않는다 — 철회는 되돌아가지 않는
    상태이고, 매 재요약마다 크레딧을 다시 쓸 이유가 없다. NULL(미조회)인
    행만 다시 본다: NULL 자체가 재시도 큐 역할을 한다(크레딧 소진·API 장애로
    실패한 논문은 다음 저장 때 자연히 다시 시도된다).

    어떤 실패에도 예외를 올리지 않는다 — 철회 조회가 요약 저장을 막으면 안 된다.
    """
    arxiv_id = _clean_arxiv_id(arxiv_id)
    with _db() as con:
        row = con.execute(
            "SELECT is_retracted FROM papers WHERE arxiv_id=?", (arxiv_id,)
        ).fetchone()
    if row is None:
        return None
    if row["is_retracted"] is not None:
        return row["is_retracted"]

    try:
        async with httpx.AsyncClient() as client:
            status = await retraction.check(
                client, arxiv_id,
                api_key=summarize_engine.ENV.get("OPENALEX_API_KEY"),
                # Crossref polite pool 용 이메일은 명시적으로 설정한 경우에만
                # 보낸다 — 개인 주소를 외부 서비스에 자동으로 흘리지 않는다.
                mailto=summarize_engine.ENV.get("CROSSREF_MAILTO"),
            )
    except Exception:  # noqa: BLE001
        return None
    if status is None:
        return None
    with _db() as con:
        con.execute(
            "UPDATE papers SET is_retracted=? WHERE arxiv_id=?", (status, arxiv_id)
        )
    return status


def save_repro_result(
    arxiv_id: str, repo_url: str, source: str, confidence: str,
    success: bool, exit_code: int | None, stage: str, attempt: int,
    network_used: bool, duration_s: float, log_path: str,
    local_path: str = "", fail_detail: str = "",
) -> None:
    """⑦ Docker 격리 실행 결과를 축적한다. MCP 도구가 아니다 — docker_runner.py
    가 판단(성공/실패)까지 끝낸 뒤 결과만 저장한다(set_review_status 와 동일 패턴).

    local_path: 성공한 시도만 docker_runner.py가 clone을 지우지 않고 여기 남긴다
    (review_app.py가 "재현된 코드 보기"에서 읽는다) — 실패한 시도는 빈 문자열.

    fail_detail: 실패 사유를 stage 안에서 한 단계 더 나눈 코드(2026-09-01).
    docker_runner.py 가 결정론적으로 만든다 — repo_not_found / clone_timeout /
    clone_failed / no_install_target / build_failed / run_network_suspected /
    run_timeout / run_nonzero_exit. 다이제스트 라벨과 §8-16 근거 집계가 이걸
    쓴다. 판정(성공/실패)에는 쓰지 않는 주석 전용 값이라, 이 값이 틀려도
    "성공했다"가 뒤집히지 않는다.
    """
    arxiv_id = _clean_arxiv_id(arxiv_id)
    with _db() as con:
        con.execute(
            """INSERT OR REPLACE INTO repro_results
               (arxiv_id, repo_url, source, confidence, success, exit_code,
                stage, attempt, network_used, duration_s, log_path, created_at,
                local_path, fail_detail)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (arxiv_id, repo_url, source, confidence, int(success), exit_code,
             stage, attempt, int(network_used), duration_s, log_path, _now(),
             local_path, fail_detail),
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
