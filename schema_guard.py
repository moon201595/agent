"""⑧ 축적 — 스키마 변경을 코드로 막는다: 승인 없는 DDL 은 실행되지 않는다.

2026-09-12(§8-98). 이틀 사이에 두 번 "승인 전에 했지만 추가형이라 했다"가 나왔다
(§8-95 ALTER, §8-96 새 테이블). 원인은 구조다 — 각 모듈의 init_db 가 `CREATE TABLE IF NOT
EXISTS`·`ALTER TABLE` 을 갖고 있어 **코드를 실행하는 것만으로** 운영 DB 스키마가 바뀐다.
문서에 "backup → 승인 → migration → check" 라고 적어도 구현이 그 순서를 강제하지 않았다.

규칙: 모듈의 DDL 은 `ensure(db, ddl, owner)` 를 통해서만 돈다.
- DB 파일이 비어 있으면(테이블 0개) 적용한다 — 새 설치·테스트.
- 환경변수 PAPER_HARNESS_APPLY_DDL=1 이면 적용한다 — `migrate.py --apply` 가 백업 뒤 켠다.
- 그 밖에는 DDL 을 **실행하지 않고** 기대 스키마와 실제를 대조한다. 빠진 테이블·컬럼·
  인덱스·트리거가 있으면 SchemaOutOfDate 로 멈춘다(무엇이 빠졌고 어떤 명령으로 적용하는지
  적어서). 다 있으면 아무것도 쓰지 않는다.

기대 스키마는 같은 DDL 을 :memory: 에 적용해서 얻는다 — DDL 을 두 곳에 적지 않는다.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Callable

APPLY_ENV = "PAPER_HARNESS_APPLY_DDL"
MIGRATE_HINT = ".venv/bin/python migrate.py --apply"


class SchemaOutOfDate(RuntimeError):
    def __init__(self, owner: str, missing: list[str]):
        self.owner, self.missing = owner, missing
        super().__init__(f"[{owner}] 스키마가 코드보다 뒤에 있다 — 빠진 것: {', '.join(missing)}. "
                         f"백업 뒤 `{MIGRATE_HINT}` 로 적용한다(승인 필요).")


def _schema(con: sqlite3.Connection) -> dict[str, set[str]]:
    """{'table:이름': {컬럼...}, 'index:이름': set(), 'trigger:이름': set()} — sqlite 내부 객체 제외."""
    out: dict[str, set[str]] = {}
    for kind, name in con.execute(
            "SELECT type, name FROM sqlite_master WHERE type IN ('table','index','trigger') "
            "AND name NOT LIKE 'sqlite_%'"):
        if kind == "table":
            out[f"table:{name}"] = {r[1] for r in con.execute(f'PRAGMA table_info("{name}")')}
        else:
            out[f"{kind}:{name}"] = set()
    return out


def expected_schema(ddl: Callable[[sqlite3.Connection], None]) -> dict[str, set[str]]:
    with sqlite3.connect(":memory:") as mem:
        ddl(mem)
        return _schema(mem)


def missing_in(db_path: Path, ddl: Callable[[sqlite3.Connection], None]) -> list[str]:
    exp = expected_schema(ddl)
    with sqlite3.connect(db_path) as con:
        act = _schema(con)
    missing = []
    for key, cols in exp.items():
        if key not in act:
            missing.append(key)
        else:
            for c in sorted(cols - act[key]):
                missing.append(f"{key}.{c}")
    return missing


def ensure(db_path: Path, ddl: Callable[[sqlite3.Connection], None], owner: str) -> str:
    """returns 'applied' | 'up_to_date'. 그 밖에는 SchemaOutOfDate."""
    db_path = Path(db_path)
    with sqlite3.connect(db_path) as con:
        empty = con.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0] == 0
    if empty or os.environ.get(APPLY_ENV) == "1":
        with sqlite3.connect(db_path) as con:
            ddl(con)
        return "applied"
    missing = missing_in(db_path, ddl)
    if missing:
        raise SchemaOutOfDate(owner, missing)
    return "up_to_date"
