"""import_local_embeddings.py — 사람이 공유 GPU 서버에서 직접 계산한 임베딩
결과(JSON)를 papers.db의 paper_embeddings 캐시로 가져온다.

왜 이런 모양인가 (2026-08-24): 회사 GPU 서버는 여러 연구자가 같이 쓰는
자원이고, 관리자 정책상 "웹 서버·서비스 구축"이 지양 대상이며 접속 로그가
본원·산업부 상시 모니터링 대상이다. 그래서 하네스(이 에이전트 포함)가 그
서버에 직접 접속하거나 그 서버에 상시 서비스를 띄우는 구조는 처음부터
배제했다 — 대신 사람이 Jupyter로 그 서버에 개인 세션을 열어 배치로
임베딩을 계산하고, 결과를 JSON으로 내려받아 이 로컬 환경에 전달하면 그
파일만 가져온다. 이 스크립트는 그 마지막 단계(가져오기)만 담당한다 —
네트워크 호출이 전혀 없다.

server.py의 hybrid_search 임베딩 캐시(paper_embeddings 테이블, 2026-08-06
신설, INSERT OR REPLACE 패턴)와 정확히 같은 스키마를 쓴다 — 그래서 이
경로로 채운 임베딩도 hybrid_search_local_papers나 향후 profile_scoring의
relevance 계산이 출처(Gemini API였는지 로컬 GPU였는지)를 구분하지 않고
그대로 재사용한다.

기대하는 입력 JSON 형식 (리스트, 한 항목당 논문 하나):
    [
      {"arxiv_id": "2506.02153", "model": "local:all-MiniLM-L6-v2",
       "embedding": [0.0123, -0.045, ...]},
      ...
    ]
"model" 문자열은 자유 형식이지만 "local:" 접두사를 권장한다 — DB 조회 시
Gemini("gemini-embedding-001")와 로컬 모델을 구분해야 캐시가 섞이지
않는다(같은 arxiv_id라도 model이 다르면 별개 행으로 저장됨 — server.py의
_get_or_compute_embedding이 (arxiv_id, model) 조합으로 조회하는 것과
같은 이유).

사용법:
    python import_local_embeddings.py embeddings.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_records(records: list) -> list[str]:
    """형식 문제를 전부 모아서 반환한다 — 하나 잘못됐다고 나머지까지
    조용히 막지 않고, 무엇이 왜 잘못됐는지 한 번에 보여준다(사람이 만든
    JSON이라 오타 가능성이 code로 만든 것보다 높다고 보고 방어적으로 짰다).
    """
    errors = []
    for i, r in enumerate(records):
        if not isinstance(r, dict):
            errors.append(f"[{i}] 딕셔너리가 아님: {r!r}")
            continue
        if not r.get("arxiv_id"):
            errors.append(f"[{i}] arxiv_id 없음")
        if not r.get("model"):
            errors.append(f"[{i}] model 없음")
        emb = r.get("embedding")
        if not isinstance(emb, list) or not emb or not all(
            isinstance(x, (int, float)) for x in emb
        ):
            errors.append(f"[{i}] embedding이 비어있지 않은 숫자 리스트가 아님")
    return errors


def import_embeddings(db_path: Path, records: list) -> int:
    """검증을 통과한 레코드만 paper_embeddings에 INSERT OR REPLACE.
    하나라도 형식이 틀리면 아무것도 안 쓰고 예외를 올린다(부분 반영으로
    "몇 건은 들어갔고 몇 건은 안 들어갔는지" 헷갈리는 상태를 안 만든다)."""
    errors = validate_records(records)
    if errors:
        raise ValueError("입력 JSON 형식 오류:\n" + "\n".join(errors))

    with sqlite3.connect(db_path) as con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS paper_embeddings ("
            "arxiv_id TEXT PRIMARY KEY, model TEXT, embedding TEXT, updated_at TEXT)"
        )
        for r in records:
            con.execute(
                "INSERT OR REPLACE INTO paper_embeddings (arxiv_id, model, embedding, updated_at) "
                "VALUES (?,?,?,?)",
                (r["arxiv_id"], r["model"], json.dumps(r["embedding"]), _now()),
            )
    return len(records)


def main() -> None:
    import server  # 지연 import — 테스트는 이 의존 없이 임시 DB로 돈다

    parser = argparse.ArgumentParser(
        description="사람이 공유 GPU 서버에서 직접 계산해 내려받은 임베딩 JSON을 papers.db로 가져온다"
    )
    parser.add_argument("json_path", help="임베딩 결과 JSON 파일 경로")
    parser.add_argument("--db", default=None, help="대상 SQLite DB 경로 (기본: server.DB_PATH)")
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else server.DB_PATH
    records = json.loads(Path(args.json_path).read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise SystemExit("최상위가 리스트가 아님 — [{...}, {...}] 형식이어야 함")

    n = import_embeddings(db_path, records)
    print(f"{n}건 가져옴 → {db_path}")


if __name__ == "__main__":
    main()
