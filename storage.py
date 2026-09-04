"""storage.py — SQLite 스키마·연결의 단일 소유자 (2026-09-04).

**왜 갈라냈나.** 그전에는 `server.py` 가 MCP 서버·HTTP 클라이언트·파싱·
스토리지·도메인 로직을 다 갖고 있었고, `_db()` 하나를 digest·review_core·
trend_report·docker_runner·code_finder 다섯 모듈이 **비공개 이름 그대로**
빌려 썼다. 두 가지가 실제로 아팠다:

1. `server.py` 46행의 `from mcp.server.mcpserver import MCPServer` 가 하드
   임포트라, MCP SDK 가 없는 환경에서는 `digest.py` 같은 **순수 텍스트 포맷
   모듈조차 임포트가 안 됐다.** 테스트 18개 파일이 collection 단계에서 죽는다.
2. `CREATE TABLE` 이 server·research_profile·import_local_embeddings 세 곳에
   흩어져 있고, 테스트 픽스처 다섯 곳이 **손으로 스키마를 다시 썼다.**
   2026-09-03 에 `test_trend_report` 가 `no such column: p.abstract` 로 깨진
   사고가 정확히 이것이다 — 손으로 쓴 픽스처가 실제 스키마와 어긋났다.

이 파일은 **표준 라이브러리만 임포트한다.** 그래야 위 1번이 구조적으로 사라진다.

옮긴 것뿐이고 로직은 한 줄도 안 바꿨다 — 회귀를 잡을 그물(server 커버리지
63%)이 있는 상태에서 "이동만" 과 "호출부 정리" 를 따로 커밋한다.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# 경로도 여기로 옮긴다 — 스키마와 파일 배치는 같은 관심사다.
BASE_DIR = Path(__file__).resolve().parent
import os

DATA_DIR = Path(os.environ.get("PAPER_HARNESS_DATA", BASE_DIR / "data"))
PDF_DIR = DATA_DIR / "pdfs"
TEXT_DIR = DATA_DIR / "text"
SUMMARY_DIR = DATA_DIR / "summaries"
IMAGE_DIR = DATA_DIR / "images"
REPRO_DIR = DATA_DIR / "repro"
DB_PATH = DATA_DIR / "papers.db"


def init_storage(db_path=None) -> None:
    for d in (PDF_DIR, TEXT_DIR, SUMMARY_DIR, IMAGE_DIR, REPRO_DIR):
        d.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path or DB_PATH) as con:
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

        # 어느 엔진이 만들었고 원문을 얼마나 봤는지(2026-09-02, §8-25).
        # Groq 경로는 청크 상한에 걸리면 긴 논문의 뒤를 통째로 안 보는데,
        # ⑤ 검증은 인용한 문장이 원문에 있는지만 보므로 절반만 보고 쓴
        # 요약도 pass_ratio 1.0 이 나온다 — 따로 재서 남겨야 보인다.
        existing_s = {row[1] for row in con.execute("PRAGMA table_info(summaries)")}
        if "engine" not in existing_s:
            con.execute("ALTER TABLE summaries ADD COLUMN engine TEXT")
        if "coverage_ratio" not in existing_s:
            con.execute("ALTER TABLE summaries ADD COLUMN coverage_ratio REAL")

        # ① 하이브리드 검색(2026-08-06)용 임베딩 캐시. 논문 텍스트(제목+초록)가
        # 바뀌지 않는 한 임베딩도 안 바뀌므로, 검색할 때마다 다시 계산하지
        # 않고 여기 저장해 재사용한다 — hybrid_search.py 는 이 캐시를 모른다
        # (DB 접근 없는 순수 계산 모듈로 남겨둠, 다른 모듈들과 같은 경계 원칙).
        con.execute(
            """CREATE TABLE IF NOT EXISTS paper_embeddings (
                arxiv_id TEXT PRIMARY KEY, model TEXT, embedding TEXT, updated_at TEXT
            )"""
        )


def db(db_path=None) -> sqlite3.Connection:
    """연결을 연다. 경로를 안 주면 모듈 기본값.

    **경로를 인자로 받는 이유**: 함수가 모듈을 옮기면 *어느 모듈의 전역을
    읽는지*가 같이 바뀐다. 테스트 55개가 `server.DB_PATH` 를 monkeypatch 하고
    있었는데, 그대로 옮겼더니 `storage.DB_PATH` 를 읽게 되어 임시 DB 가 무시
    됐다(2026-09-04 실측 — 옮기자마자 55개가 깨졌다).
    호출부가 자기 경로를 넘기면 이 결합이 사라진다.
    """
    con = sqlite3.connect(db_path or DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_arxiv_id(raw: str) -> str:
    """URL·버전 표기를 걷어내고 순수 ID만 남긴다. 예: 'abs/1706.03762v5' → '1706.03762'

    2026-08-05 실측: /html/ 링크(arxiv.org/html/2505.19433v1)는 못 걷어내던
    버그를 발견 — abs·pdf 만 처리했었다. 논문 목록에 arXiv HTML 링크를
    그대로 붙여넣는 경우가 실제로 있어서 셋 다 처리하도록 고쳤다.
    """
    raw = raw.strip()
    raw = re.sub(r"^https?://arxiv\.org/(abs|pdf|html)/", "", raw)
    raw = re.sub(r"\.pdf$", "", raw)
    return re.sub(r"v\d+$", "", raw)
