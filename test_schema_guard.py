"""§8-98·99 — 승인 없는 DDL 을 코드로 막고, 백업은 WAL 을 포함한 일관 백업이어야 한다.
각 테스트: 무엇을 망가뜨리면 실패하는가."""
import os
import shutil
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


def _ddl_other(con):
    con.execute("CREATE TABLE IF NOT EXISTS w (z TEXT)")


def test_플래그_없이는_빈_DB_에도_DDL_을_돌리지_않고_대조만_한다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 플래그 없이 DDL 을 돌리는 것(승인 우회), 빈 DB 예외(첫 owner 뒤
    둘째 owner 가 막히는 반쪽 규칙), 빠진 것을 이름까지 못 짚는 것, 다 있을 때 '적용'이라 하는 것."""
    monkeypatch.delenv(schema_guard.APPLY_ENV, raising=False)
    db = tmp_path / "t.db"
    sqlite3.connect(db).close()
    with pytest.raises(schema_guard.SchemaOutOfDate):
        schema_guard.ensure(db, _ddl_v1, "x")
    monkeypatch.setenv(schema_guard.APPLY_ENV, "1")
    assert schema_guard.ensure(db, _ddl_v1, "x") == "applied"
    monkeypatch.delenv(schema_guard.APPLY_ENV)
    assert schema_guard.ensure(db, _ddl_v1, "x") == "up_to_date"
    with pytest.raises(schema_guard.SchemaOutOfDate) as ei:
        schema_guard.ensure(db, _ddl_v2, "x")
    assert ei.value.missing == ["table:t.b", "table:u", "index:idx_u"]
    with pytest.raises(schema_guard.SchemaOutOfDate) as ei:   # 둘째 owner 도 같은 규칙
        schema_guard.ensure(db, _ddl_other, "y")
    assert ei.value.missing == ["table:w"]
    with sqlite3.connect(db) as con:                          # 실제로 아무것도 안 바뀌었다
        assert {r[1] for r in con.execute("PRAGMA table_info(t)")} == {"a"}
        assert con.execute("SELECT count(*) FROM sqlite_master WHERE name IN ('u','w')").fetchone()[0] == 0


def test_migrate_는_새_설치를_부트스트랩하고_운영_DB_는_백업_뒤_적용한다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: --apply 없이 쓰는 것, 백업 없이 적용하는 것, 적용 뒤 대조를
    안 하는 것, 평가 scope 가 운영 owner 를 건드리는 것, 플래그를 켜 둔 채 끝나는 것."""
    monkeypatch.delenv(schema_guard.APPLY_ENV, raising=False)
    import migrate
    monkeypatch.setattr(migrate, "owners", lambda scope="operational": {
        "operational": [("x", _ddl_v2)], "evaluation": [("y", _ddl_other)],
        "all": [("x", _ddl_v2), ("y", _ddl_other)]}[scope])
    monkeypatch.setattr(migrate, "BACKUP_DIR", tmp_path / "backups")
    db = tmp_path / "t.db"
    assert migrate.main(["--db", str(db)]) == 2, "없는 DB 는 --apply 로만 만든다"
    assert migrate.main(["--db", str(db), "--apply"]) == 0
    assert not (tmp_path / "backups").exists(), "새 설치는 백업할 게 없다"
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM sqlite_master WHERE name='u'").fetchone()[0] == 1
    # 운영 DB 에 새 owner 스키마가 필요해지면: 대조 → 승인 → 백업 → 적용 → 재대조
    monkeypatch.setattr(migrate, "owners", lambda scope="operational": {
        "operational": [("x", _ddl_v2), ("y", _ddl_other)], "evaluation": [], "all": [("x", _ddl_v2), ("y", _ddl_other)]}[scope])
    assert migrate.main(["--db", str(db)]) == 1, "--apply 없이는 쓰지 않는다"
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM sqlite_master WHERE name='w'").fetchone()[0] == 0
    assert migrate.main(["--db", str(db), "--apply"]) == 0
    assert list((tmp_path / "backups").glob("*_pre_migration.db")), "적용 전에 백업이 남는다"
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM sqlite_master WHERE name='w'").fetchone()[0] == 1
    assert migrate.main(["--db", str(db)]) == 0, "최신이면 할 일 없음"
    assert schema_guard.APPLY_ENV not in os.environ, "플래그는 적용 뒤 끈다"


def test_백업은_WAL_에_남은_커밋을_포함하고_검증을_통과해야_백업이다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 파일 복사(copy2) 백업 — WAL 모드에서 -wal 에 남은 커밋이 빠진다,
    integrity_check 없이 백업을 인정하는 것."""
    import migrate
    monkeypatch.setattr(migrate, "BACKUP_DIR", tmp_path / "backups")
    db = tmp_path / "wal.db"
    con = sqlite3.connect(db)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t (a TEXT)")
    con.execute("INSERT INTO t VALUES ('in-wal')")
    con.commit()                         # 커밋은 됐지만 checkpoint 전 — 내용은 -wal 에 있다
    assert (tmp_path / "wal.db-wal").stat().st_size > 0, "전제: WAL 에 내용이 남아 있어야 한다"
    naive = tmp_path / "naive.db"
    shutil.copy2(db, naive)              # 옛 방식
    with sqlite3.connect(naive) as chk:
        naive_rows = chk.execute("SELECT count(*) FROM sqlite_master WHERE name='t'").fetchone()[0]
    dest = migrate.backup(db)
    with sqlite3.connect(dest) as chk:
        assert chk.execute("SELECT a FROM t").fetchall() == [("in-wal",)], "일관 백업은 WAL 내용을 담는다"
    assert naive_rows == 0, "파일 복사는 WAL 내용을 놓친다 — 이게 고친 이유다"
    con.close()
    # 검증 실패는 백업이 아니다
    monkeypatch.setattr(migrate, "_copy_pages", lambda src, dst: None)
    with pytest.raises(migrate.BackupFailed):
        migrate.backup(db)
    assert len(list((tmp_path / "backups").glob("*.db"))) == 1, "실패한 백업 파일은 남기지 않는다"
    # backup API 자체가 예외를 내도 부분 파일을 지우고 BackupFailed 로 올린다
    monkeypatch.setattr(migrate, "_copy_pages", lambda src, dst: (_ for _ in ()).throw(sqlite3.OperationalError("disk I/O")))
    with pytest.raises(migrate.BackupFailed):
        migrate.backup(db)
    assert len(list((tmp_path / "backups").glob("*.db"))) == 1
    # --scope all 과 명시적 --db 는 같이 못 쓴다
    assert migrate.main(["--scope", "all", "--db", str(db)]) == 2


def test_bootstrap_은_DDL_과_별개의_데이터_이관이라_DDL_뒤에_죽어도_다음_실행이_잡는다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: bootstrap 을 'DDL 이 이번에 빠져 있었을 때만' 하는 것(DDL 성공 → bootstrap 전
    crash → 영구 skip), 완료 판정을 '이벤트가 있다'로 하는 것(부분 bootstrap 통과)."""
    monkeypatch.delenv(schema_guard.APPLY_ENV, raising=False)
    import migrate, research_profile as rp
    monkeypatch.setattr(migrate, "BACKUP_DIR", tmp_path / "backups")
    db = tmp_path / "t.db"
    monkeypatch.setenv(schema_guard.APPLY_ENV, "1")
    rp.create_profile(db, "p", "이름", core_topics=["A", "B"], core_weights={"A": 1.0, "B": 1.0}, max_items=2, s2_seeds=["A"])
    monkeypatch.delenv(schema_guard.APPLY_ENV)
    # 이관 전 상태를 흉내: 이벤트 표를 통째로 없앤다(트리거 포함)
    with sqlite3.connect(db) as con:
        con.execute("DROP TABLE profile_keyword_events")
    monkeypatch.setattr(migrate, "owners", lambda scope="operational": {"operational": [("research_profile", rp._ddl)], "evaluation": [], "all": [("research_profile", rp._ddl)]}[scope])
    assert migrate.main(["--db", str(db)]) == 1                      # DDL pending
    # DDL 은 됐는데 bootstrap 전에 죽었다고 하자: 표만 만들고 종료
    monkeypatch.setenv(schema_guard.APPLY_ENV, "1"); rp.init_db(db); monkeypatch.delenv(schema_guard.APPLY_ENV)
    assert migrate.pending(db) == {"research_profile": []}, "DDL 은 최신"
    assert "keyword_events_bootstrap" in migrate.data_pending(db), "데이터 이관은 여전히 대기"
    assert migrate.main(["--db", str(db)]) == 1, "DDL todo 가 없어도 데이터 대기면 할 일이 있다"
    assert migrate.main(["--db", str(db), "--apply"]) == 0
    assert migrate.data_pending(db) == {} and set(rp.active_generations(db, "p")) == {("a", "core"), ("b", "core"), ("a", "s2_seed")}
    assert list((tmp_path / "backups").glob("*.db")), "데이터 이관도 백업 뒤에 한다"
    # 부분 bootstrap(이벤트는 있는데 집합이 다름)도 대기로 본다
    rp.create_profile(db, "p", "이름", core_topics=["A", "B", "C"], core_weights={"A": 1.0, "B": 1.0, "C": 1.0}, max_items=2, s2_seeds=["A"])
    assert migrate.data_pending(db) == {}, "정상 변경은 이벤트가 따라오므로 불일치가 아니다"
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO profile_keywords (profile_id, keyword, kind, weight, added_at) VALUES ('p','D','core',1.0,'t')")
    assert "keyword_events_bootstrap" in migrate.data_pending(db), "이벤트 없이 끼어든 키워드는 불일치다"
    assert migrate.main(["--db", str(db), "--apply"]) == 0, "부분 불일치도 보정된다"
    assert migrate.data_pending(db) == {}
    with sqlite3.connect(db) as con:
        d = con.execute("SELECT actor_origin, provenance_origin FROM profile_keyword_events WHERE keyword='d'").fetchone()
    assert d == ("bootstrap", "user")
