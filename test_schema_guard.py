"""§8-98 — 승인 없는 DDL 을 코드로 막는다. 각 테스트: 무엇을 망가뜨리면 실패하는가."""
import sqlite3

import pytest

import schema_guard


def _ddl_v1(con):
    con.execute("CREATE TABLE IF NOT EXISTS t (a TEXT)")


def _ddl_v2(con):
    con.execute("CREATE TABLE IF NOT EXISTS t (a TEXT)")
    cols = {r[1] for r in con.execute("PRAGMA table_info(t)")}
    if "b" not in cols:
        con.execute("ALTER TABLE t ADD COLUMN b TEXT")
    con.execute("CREATE TABLE IF NOT EXISTS u (x TEXT)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_u ON u(x)")


def test_빈_DB_에는_적용하고_운영_DB_에는_대조만_한다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 테이블이 있는 DB 에 플래그 없이 DDL 을 돌리는 것(승인 우회),
    빠진 것을 이름까지 못 짚는 것, 다 있을 때 '적용'이라고 하는 것."""
    monkeypatch.delenv(schema_guard.APPLY_ENV, raising=False)
    db = tmp_path / "t.db"
    assert schema_guard.ensure(db, _ddl_v1, "x") == "applied"
    assert schema_guard.ensure(db, _ddl_v1, "x") == "up_to_date"
    with pytest.raises(schema_guard.SchemaOutOfDate) as ei:
        schema_guard.ensure(db, _ddl_v2, "x")
    assert ei.value.missing == ["table:t.b", "table:u", "index:idx_u"]
    with sqlite3.connect(db) as con:                       # 실제로 아무것도 안 바뀌었다
        assert {r[1] for r in con.execute("PRAGMA table_info(t)")} == {"a"}
        assert con.execute("SELECT count(*) FROM sqlite_master WHERE name='u'").fetchone()[0] == 0


def test_플래그가_있으면_적용하고_migrate_가_백업_뒤_그_플래그를_켠다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: migrate --apply 가 백업 없이 적용하는 것, 적용 뒤 대조를 안 하는 것,
    --apply 없이 쓰는 것."""
    monkeypatch.delenv(schema_guard.APPLY_ENV, raising=False)
    db = tmp_path / "t.db"
    schema_guard.ensure(db, _ddl_v1, "x")
    monkeypatch.setenv(schema_guard.APPLY_ENV, "1")
    assert schema_guard.ensure(db, _ddl_v2, "x") == "applied"
    monkeypatch.delenv(schema_guard.APPLY_ENV)
    assert schema_guard.ensure(db, _ddl_v2, "x") == "up_to_date"

    import migrate
    monkeypatch.setattr(migrate, "owners", lambda: [("x", _ddl_v2), ("y", lambda con: con.execute("CREATE TABLE IF NOT EXISTS w (z TEXT)"))])
    monkeypatch.setattr(migrate, "BACKUP_DIR", tmp_path / "backups")
    assert migrate.main(["--db", str(db)]) == 1, "--apply 없이는 쓰지 않는다"
    assert not (tmp_path / "backups").exists()
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM sqlite_master WHERE name='w'").fetchone()[0] == 0
    assert migrate.main(["--db", str(db), "--apply"]) == 0
    assert list((tmp_path / "backups").glob("*_pre_migration.db")), "적용 전에 백업이 남는다"
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM sqlite_master WHERE name='w'").fetchone()[0] == 1
    assert migrate.main(["--db", str(db)]) == 0, "최신이면 할 일 없음"
    assert schema_guard.APPLY_ENV not in __import__("os").environ, "플래그는 적용 뒤 끈다"
