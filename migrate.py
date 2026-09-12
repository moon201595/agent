"""⑧ 축적 — 운영 DB 스키마 마이그레이션의 유일한 입구 (2026-09-12, §8-98).

절차를 코드가 강제한다: **backup → (사람 승인) → apply → check.**
    .venv/bin/python migrate.py                     # 운영 DB: 무엇이 빠졌는지만 본다(쓰지 않는다)
    .venv/bin/python migrate.py --apply             # 일관 백업(WAL 포함) → 적용 → 재대조
    .venv/bin/python migrate.py --scope evaluation  # 평가 DB(다른 파일)
    .venv/bin/python migrate.py --apply --scope all # 새 설치 부트스트랩

각 모듈의 DDL 은 schema_guard.ensure 를 통해서만 돌고, 운영 DB(테이블이 있는 파일)에서는
PAPER_HARNESS_APPLY_DDL=1 없이는 실행되지 않는다 — 이 스크립트가 그 플래그를 켠다.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import schema_guard

ROOT = Path(__file__).parent
DEFAULT_DB = ROOT / "data" / "papers.db"
BACKUP_DIR = ROOT / "data" / "backups"


DEFAULT_EVAL_DB = ROOT / "data" / "evaluation.db"


def owners(scope: str = "operational") -> list[tuple[str, object]]:
    """scope: operational(운영 DB) | evaluation(평가 DB — 다른 파일) | all."""
    import evaluation, evidence_state, profile_advisor, profile_impact, research_profile, shadow_search, storage
    op = [("storage", storage._ddl), ("research_profile", research_profile._ddl),
          ("profile_impact", profile_impact._ddl), ("profile_advisor", profile_advisor._ddl),
          ("evidence_state", evidence_state._ddl), ("shadow_search", shadow_search._ddl)]
    ev = [("evaluation", evaluation._ddl)]
    return {"operational": op, "evaluation": ev, "all": op + ev}[scope]


def pending(db: Path, scope: str = "operational") -> dict[str, list[str]]:
    return {name: schema_guard.missing_in(db, ddl) for name, ddl in owners(scope)}


class BackupFailed(RuntimeError):
    pass


def _copy_pages(src: sqlite3.Connection, dst: sqlite3.Connection) -> None:
    src.backup(dst)      # online backup API — WAL 에 남은 페이지까지 일관되게 옮긴다


def backup(db: Path) -> Path:
    """**일관 백업.** 이 DB 는 WAL 모드라 파일만 복사하면 `-wal` 에 남은 커밋이 빠진다 —
    "백업했다"가 거짓이 된다(외부 검토 2026-09-12). sqlite 의 online backup API 로 페이지
    단위로 옮기고, 백업본에 integrity_check 를 돌려 통과한 것만 백업으로 인정한다."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S.%fZ")
    dest = BACKUP_DIR / f"{db.stem}_{stamp}_pre_migration.db"
    if dest.exists():
        raise BackupFailed(f"백업 경로가 이미 있다 — 덮어쓰지 않는다: {dest}")
    try:
        with sqlite3.connect(db) as src, sqlite3.connect(dest) as dst:
            _copy_pages(src, dst)
            n_src = src.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        with sqlite3.connect(dest) as chk:
            verdict = chk.execute("PRAGMA integrity_check").fetchone()[0]
            n_dst = chk.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        if verdict != "ok" or n_dst != n_src:
            raise BackupFailed(f"백업 검증 실패: integrity={verdict}, tables {n_dst}/{n_src}")
    except Exception as e:  # noqa: BLE001 — backup API 자체 실패도 부분 파일을 남기면 안 된다(외부 검토)
        dest.unlink(missing_ok=True)
        raise e if isinstance(e, BackupFailed) else BackupFailed(f"백업 실패: {type(e).__name__}: {e}") from e
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=None, help="기본: scope 에 따라 data/papers.db 또는 data/evaluation.db")
    ap.add_argument("--scope", choices=("operational", "evaluation", "all"), default="operational")
    ap.add_argument("--apply", action="store_true", help="백업 뒤 적용한다(사람 승인 뒤에만)")
    args = ap.parse_args(argv)
    if args.scope == "all" and args.db is not None:
        print("--scope all 은 --db 와 같이 쓸 수 없다 — 운영·평가 스키마가 한 파일에 섞인다"); return 2
    if args.scope == "all" and args.db is None:
        rc = 0
        for scope in ("operational", "evaluation"):
            print(f"== scope {scope}")
            rc = max(rc, main([*(["--apply"] if args.apply else []), "--scope", scope]))
        return rc
    db = args.db or (DEFAULT_EVAL_DB if args.scope == "evaluation" else DEFAULT_DB)
    fresh = not db.exists()
    if fresh:
        if not args.apply:
            print(f"DB 없음: {db} — 새 설치는 --apply 로 만든다"); return 2
        db.parent.mkdir(parents=True, exist_ok=True)
        sqlite3.connect(db).close()
    before = pending(db, args.scope)
    todo = {k: v for k, v in before.items() if v}
    if not todo:
        print("스키마 최신 — 할 일 없음"); return 0
    for name, miss in todo.items():
        print(f"[{name}] 빠진 것 {len(miss)}: " + ", ".join(miss))
    if not args.apply:
        print("\n적용하려면 승인 뒤 --apply. 백업은 자동으로 만든다."); return 1
    if not fresh:
        dest = backup(db)
        print(f"백업(일관·integrity ok): {dest}")
    os.environ[schema_guard.APPLY_ENV] = "1"
    try:
        for name, ddl in owners(args.scope):
            if before[name]:
                print(f"[{name}] 적용 → {schema_guard.ensure(db, ddl, name)}")
    finally:
        os.environ.pop(schema_guard.APPLY_ENV, None)
    after = {k: v for k, v in pending(db, args.scope).items() if v}
    if after:
        print("적용 뒤에도 빠진 것: " + str(after)); return 3
    print("대조 완료 — 스키마 최신"); return 0


if __name__ == "__main__":
    sys.exit(main())
