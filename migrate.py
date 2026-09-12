"""⑧ 축적 — 운영 DB 스키마 마이그레이션의 유일한 입구 (2026-09-12, §8-98).

절차를 코드가 강제한다: **backup → (사람 승인) → apply → check.**
    .venv/bin/python migrate.py            # 무엇이 빠졌는지만 본다(쓰지 않는다)
    .venv/bin/python migrate.py --apply    # data/backups/ 에 백업하고 적용한 뒤 다시 대조한다

각 모듈의 DDL 은 schema_guard.ensure 를 통해서만 돌고, 운영 DB(테이블이 있는 파일)에서는
PAPER_HARNESS_APPLY_DDL=1 없이는 실행되지 않는다 — 이 스크립트가 그 플래그를 켠다.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import schema_guard

ROOT = Path(__file__).parent
DEFAULT_DB = ROOT / "data" / "papers.db"
BACKUP_DIR = ROOT / "data" / "backups"


def owners() -> list[tuple[str, object]]:
    import evidence_state, profile_advisor, profile_impact, research_profile, storage
    return [("storage", storage._ddl), ("research_profile", research_profile._ddl),
            ("profile_impact", profile_impact._ddl), ("profile_advisor", profile_advisor._ddl),
            ("evidence_state", evidence_state._ddl)]


def pending(db: Path) -> dict[str, list[str]]:
    return {name: schema_guard.missing_in(db, ddl) for name, ddl in owners()}


def backup(db: Path) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    dest = BACKUP_DIR / f"{db.stem}_{stamp}_pre_migration.db"
    shutil.copy2(db, dest)
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--apply", action="store_true", help="백업 뒤 적용한다(사람 승인 뒤에만)")
    args = ap.parse_args(argv)
    if not args.db.exists():
        print(f"DB 없음: {args.db}"); return 2
    before = pending(args.db)
    todo = {k: v for k, v in before.items() if v}
    if not todo:
        print("스키마 최신 — 할 일 없음"); return 0
    for name, miss in todo.items():
        print(f"[{name}] 빠진 것 {len(miss)}: " + ", ".join(miss))
    if not args.apply:
        print("\n적용하려면 승인 뒤 --apply. 백업은 자동으로 만든다."); return 1
    dest = backup(args.db)
    print(f"백업: {dest}")
    os.environ[schema_guard.APPLY_ENV] = "1"
    try:
        for name, ddl in owners():
            if before[name]:
                print(f"[{name}] 적용 → {schema_guard.ensure(args.db, ddl, name)}")
    finally:
        os.environ.pop(schema_guard.APPLY_ENV, None)
    after = {k: v for k, v in pending(args.db).items() if v}
    if after:
        print("적용 뒤에도 빠진 것: " + str(after)); return 3
    print("대조 완료 — 스키마 최신"); return 0


if __name__ == "__main__":
    sys.exit(main())
