"""db_retention.py의 허용 목록·백업 선행·파일 경로 안전성 회귀."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import db_retention as retention
import feedback_links
import profile_impact
import research_profile
import storage


NOW = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
OLD = (NOW - timedelta(days=365)).isoformat()
RECENT = (NOW - timedelta(days=5)).isoformat()
OLD_10 = (NOW - timedelta(days=40)).isoformat()


def _db(tmp_path: Path, *, data_dir: Path | None = None) -> tuple[Path, Path]:
    db = tmp_path / "papers.db"
    data = data_dir or (tmp_path / "data")
    data.mkdir(parents=True, exist_ok=True)
    storage.init_storage(db)
    research_profile.init_db(db)
    profile_impact.init_db(db)
    feedback_links.init_db(db)
    retention.init_db(db)
    return db, data


def _insert(con: sqlite3.Connection, table: str, columns: str, values: tuple) -> None:
    placeholders = ",".join("?" for _ in values)
    con.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", values)


def _paper(con: sqlite3.Connection, aid: str, when: str = OLD, *, title: str | None = None) -> None:
    _insert(
        con,
        "papers",
        "arxiv_id,title,pdf_path,text_path,fetched_at,source",
        (aid, title or aid, None, None, when, None),
    )
    _insert(con, "summaries", "arxiv_id,path,created_at", (aid, None, when))


def test_apply_false_keeps_rows_and_files(tmp_path):
    """이 테스트가 잡는 것: dry-run이 백업·삭제·DDL을 실행하는 것."""
    db, data = _db(tmp_path)
    cache = data / "text"
    cache.mkdir(parents=True)
    old_file = cache / "old.txt"
    old_file.write_text("keep before apply", encoding="utf-8")
    old_stamp = (NOW - timedelta(days=70)).timestamp()
    os.utime(old_file, (old_stamp, old_stamp))
    with sqlite3.connect(db) as con:
        _insert(con, "candidate_observations", "scan_id,profile_id,paper_key,observed_at", ("s", "p", "k", OLD))
        before = con.execute("SELECT count(*) FROM candidate_observations").fetchone()[0]
    result = retention.run(db, apply=False, now=NOW, data_dir=data)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM candidate_observations").fetchone()[0] == before
    assert old_file.exists()
    assert result["applied"] is False
    assert not (data / "backups").exists()


def test_old_observation_is_deleted_recent_is_kept(tmp_path):
    """이 테스트가 잡는 것: observed_at 대신 최근 행까지 지우거나 오래된 행을 남기는 것."""
    db, data = _db(tmp_path)
    with sqlite3.connect(db) as con:
        _insert(con, "candidate_observations", "scan_id,profile_id,paper_key,observed_at", ("old", "p", "old", OLD))
        _insert(con, "candidate_observations", "scan_id,profile_id,paper_key,observed_at", ("new", "p", "new", RECENT))
    result = retention.run(db, apply=True, now=NOW, data_dir=data)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT scan_id FROM candidate_observations").fetchall() == [("new",)]
    assert result["tables"]["candidate_observations"]["deleted"] == 1


def test_shown_and_feedback_papers_are_protected(tmp_path):
    """이 테스트가 잡는 것: paper_key 보호 집합을 빼고 papers·summaries를 삭제하는 것."""
    db, data = _db(tmp_path)
    with sqlite3.connect(db) as con:
        _paper(con, "shown", title="Shown")
        _paper(con, "token", title="Token")
        _insert(con, "profile_shown", "profile_id,paper_key,title,shown_at", ("p", "shown", "Shown", OLD))
        _insert(con, "feedback_tokens", "tid,issue_id,profile_id,item_no,paper_key,recipient_hash,position,created_at,expires_at", ("t", "i", "p", "1", "token", "h", 1, OLD, 0))
    retention.run(db, apply=True, now=NOW, data_dir=data)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM papers").fetchone()[0] == 2
        assert con.execute("SELECT count(*) FROM summaries").fetchone()[0] == 2


def test_feedback_profile_and_gate_tables_are_never_touched(tmp_path):
    """이 테스트가 잡는 것: 허용 목록을 넓혀 feedback/profile/gate 원자료를 지우는 것."""
    db, data = _db(tmp_path)
    with sqlite3.connect(db) as con:
        _insert(con, "feedback_events", "event_id,received_at,status,imported_at", ("e", OLD, "valid", OLD))
        _insert(con, "profile_revisions", "profile_id,revision,created_at,origin,content_sha,snapshot", ("p", 1, OLD, "user", "x", "{}"))
        _insert(con, "gate_decisions", "decision_id,analysis_id,profile_id,decided_at,source,gate_status,reasons_json,effective_rules_json,rules_hash", ("g", "a", "p", OLD, "analyze", "held", "{}", "{}", "x"))
    retention.run(db, apply=True, now=NOW, data_dir=data)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM feedback_events").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM profile_revisions").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM gate_decisions").fetchone()[0] == 1


def test_old_unprotected_paper_and_cache_files_are_deleted(tmp_path):
    """이 테스트가 잡는 것: 보호되지 않은 논문만 지우지 않거나 DB가 참조하지 않는 오래된 캐시를 남기는 것."""
    db, data = _db(tmp_path)
    pdf_dir, text_dir = data / "pdfs", data / "text"
    pdf_dir.mkdir(parents=True)
    text_dir.mkdir(parents=True)
    pdf, text = pdf_dir / "old.pdf", text_dir / "old.txt"
    pdf.write_bytes(b"pdf")
    text.write_text("text", encoding="utf-8")
    stamp = (NOW - timedelta(days=70)).timestamp()
    os.utime(pdf, (stamp, stamp))
    os.utime(text, (stamp, stamp))
    with sqlite3.connect(db) as con:
        _insert(con, "papers", "arxiv_id,title,pdf_path,text_path,fetched_at", ("old", "Old", str(pdf), str(text), OLD))
        _insert(con, "summaries", "arxiv_id,created_at", ("old", OLD))
    result = retention.run(db, apply=True, now=NOW, data_dir=data)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM papers WHERE arxiv_id='old'").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM summaries WHERE arxiv_id='old'").fetchone()[0] == 0
    assert not pdf.exists()
    assert not text.exists()
    assert result["files"]["count"] == 2


def test_old_failed_reproduction_artifacts_are_removed_but_success_stays(tmp_path):
    """이 테스트가 잡는 것: 성공 재현까지 지우거나 실패 clone·로그의 7일 보존을 무시하는 것."""
    db, data = _db(tmp_path)
    repro = data / "repro"
    repro.mkdir(parents=True)
    failed_log = repro / "failed.log"
    failed_clone = repro / "failed"
    very_old_log = repro / "very-old.log"
    failed_log.write_text("failed", encoding="utf-8")
    failed_clone.mkdir()
    (failed_clone / "code.py").write_text("x", encoding="utf-8")
    stamp = (NOW - timedelta(days=10)).timestamp()
    os.utime(failed_log, (stamp, stamp))
    os.utime(failed_clone, (stamp, stamp))
    very_old_log.write_text("very old", encoding="utf-8")
    very_old_stamp = (NOW - timedelta(days=200)).timestamp()
    os.utime(very_old_log, (very_old_stamp, very_old_stamp))
    with sqlite3.connect(db) as con:
        _insert(con, "repro_results", "arxiv_id,repo_url,success,created_at,log_path", ("failed", "u1", 0, OLD_10, str(failed_log)))
        _insert(con, "repro_results", "arxiv_id,repo_url,success,created_at,log_path", ("very-old", "u0", 0, OLD, str(very_old_log)))
        _insert(con, "repro_results", "arxiv_id,repo_url,success,created_at,log_path", ("success", "u2", 1, OLD, ""))
    retention.run(db, apply=True, now=NOW, data_dir=data)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT arxiv_id FROM repro_results ORDER BY arxiv_id").fetchall() == [("failed",), ("success",)]
    assert not failed_log.exists()
    assert not failed_clone.exists()
    assert not very_old_log.exists()


def test_plan_survives_missing_db_and_read_failure(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 계획의 두 대체 경로(DB 파일 없음 · DB 읽기 실패)가 정책 표 목록을 잘못 참조해 죽는 것.
    2026-09-17 advisor 정책을 지우면서 이 두 줄에 `_ADVISOR_POLICIES` 가 남아 NameError 였는데, 어느 테스트도 그 경로를 안 밟아
    전체 1,074 가 green 이었다(Codex 2차 검토가 실행으로 잡음). 정상 경로 테스트만으로는 대체 경로가 안 보인다."""
    missing = retention.plan(tmp_path / "없는.db", now=NOW, data_dir=tmp_path / "data")
    assert "DB 없음" in missing["skipped"]
    assert missing["tables"]["candidate_observations"]["status"] == "skipped: 표 없음"
    assert not (tmp_path / "없는.db").exists()                       # 읽기 전용 계획이 파일을 만들지 않는다

    db, data = _db(tmp_path)
    monkeypatch.setattr(retention, "_plan_tables", lambda con, now: (_ for _ in ()).throw(sqlite3.OperationalError("disk I/O error")))
    broken = retention.plan(db, now=NOW, data_dir=data)
    assert any(x.startswith("DB 읽기 실패") for x in broken["skipped"])
    assert broken["tables"]["repro_results"]["status"] == "skipped: DB 읽기 실패"


def test_missing_retention_column_is_reported_without_failure(tmp_path):
    """이 테스트가 잡는 것: 설치별 구형 표에서 없는 컬럼을 가정해 정리 전체를 중단하는 것."""
    db = tmp_path / "partial.db"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE candidate_observations (id INTEGER PRIMARY KEY)")
    result = retention.plan(db, now=NOW, data_dir=tmp_path / "data")
    assert result["tables"]["candidate_observations"]["status"] == "skipped: 컬럼 없음"


def test_backup_failure_leaves_database_and_files_untouched(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 백업 실패 뒤 일부 DB 행·파일을 먼저 지우는 것."""
    db, data = _db(tmp_path)
    with sqlite3.connect(db) as con:
        _insert(con, "candidate_observations", "scan_id,profile_id,paper_key,observed_at", ("old", "p", "old", OLD))
    monkeypatch.setattr(retention.migrate, "backup", lambda _db: (_ for _ in ()).throw(RuntimeError("backup failed")))
    result = retention.run(db, apply=True, now=NOW, data_dir=data)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM candidate_observations").fetchone()[0] == 1
    assert result["applied"] is False
    assert "backup failed" in result["error"]


def test_outside_symlink_is_not_deleted(tmp_path):
    """이 테스트가 잡는 것: 캐시 심볼릭 링크를 resolve하지 않고 unlink해 외부 파일을 지우는 것."""
    db, data = _db(tmp_path)
    cache = data / "text"
    cache.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("do not delete", encoding="utf-8")
    link = cache / "old.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("심볼릭 링크를 만들 수 없는 환경")
    result = retention.run(db, apply=True, now=NOW, data_dir=data)
    assert outside.exists()
    assert link.exists()
    assert any(item["path"] == str(link) for item in result["files"]["skipped"])


def test_backup_rotation_keeps_new_backup_and_latest_four(tmp_path):
    """이 테스트가 잡는 것: 새 백업을 회전에서 지우거나 오래된 백업을 4개 이상 남기는 것."""
    db, data = _db(tmp_path)
    backup_dir = data / "backups"
    backup_dir.mkdir(parents=True)
    for index in range(5):
        path = backup_dir / f"old-{index}.db"
        path.write_bytes(b"backup")
        stamp = (NOW - timedelta(days=10 - index)).timestamp()
        os.utime(path, (stamp, stamp))
    result = retention.run(db, apply=True, now=NOW, data_dir=data, backup_dir=backup_dir)
    kept = sorted(path.name for path in backup_dir.iterdir())
    assert len(kept) == 4
    assert Path(result["backup_path"]).exists()
    assert Path(result["backup_path"]).name in kept


def test_retention_run_is_recorded(tmp_path):
    """이 테스트가 잡는 것: 정리는 실행됐지만 retention_runs 감사 행을 남기지 않는 것."""
    db, data = _db(tmp_path)
    result = retention.run(db, apply=True, now=NOW, data_dir=data)
    with sqlite3.connect(db) as con:
        row = con.execute("SELECT applied,plan_json,result_json,error FROM retention_runs").fetchone()
    assert row[0] == 1
    assert json.loads(row[1])["now"] == NOW.isoformat(timespec="microseconds")
    assert json.loads(row[2])["backup_path"] == result["backup_path"]
    assert row[3] is None


def test_old_abstract_holder_referenced_by_a_kept_observation_survives(tmp_path):
    """이 테스트가 잡는 것: 180일 넘은 초록 보유 관측을 지워, 그 초록을 참조하는 **최근** 관측의 초록 복원이 깨지는 것
    (profile_impact._restore_abstract 가 'corrupt' 를 돌려준다). 참조가 없는 오래된 보유 행은 그대로 지워져야 한다."""
    import hashlib
    db, data = _db(tmp_path)
    text = "an abstract that only the holder row stores"
    sha = hashlib.sha256(text.encode()).hexdigest()[:16]
    cols = "scan_id,profile_id,paper_key,observed_at,abstract,abstract_sha,abstract_ref"
    with sqlite3.connect(db) as con:
        _insert(con, "candidate_observations", cols, ("old-holder", "p", "k", OLD, text, sha, None))
        _insert(con, "candidate_observations", cols, ("new-ref", "p", "k", RECENT, None, sha, "old-holder"))
        _insert(con, "candidate_observations", cols, ("old-lonely", "p", "k2", OLD, text, sha, None))
    retention.run(db, apply=True, now=NOW, data_dir=data)
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        assert sorted(r[0] for r in con.execute("SELECT scan_id FROM candidate_observations")) == ["new-ref", "old-holder"]
        row = con.execute("SELECT * FROM candidate_observations WHERE scan_id='new-ref'").fetchone()
        assert profile_impact._restore_abstract(con, row) == (text, "restored")


def test_artifacts_shared_with_a_success_or_recent_attempt_are_kept(tmp_path):
    """이 테스트가 잡는 것: 산출물 경로가 논문 단위로 공유되는데 옛 실패 행만 보고 지워, 같은 논문의 **성공** 재현 폴더·로그나
    최근 재시도·지금 도는 재현의 산출물까지 지우는 것(2026-09-15 운영 계획에서 성공 기록이 있는 2110.15045 가 삭제 대상이었다)."""
    db, data = _db(tmp_path)
    repro = data / "repro"
    repro.mkdir(parents=True)
    old_stamp = (NOW - timedelta(days=30)).timestamp()
    made = {}
    for aid in ("won-later", "retried", "running", "plain-fail"):
        clone, log = repro / aid, repro / f"{aid}.log"
        clone.mkdir()
        log.write_text("log", encoding="utf-8")
        for path in (clone, log):
            os.utime(path, (old_stamp, old_stamp))
        made[aid] = (clone, log)
    (repro / "running.running").write_text("", encoding="utf-8")
    cols = "arxiv_id,repo_url,success,created_at,log_path"
    with sqlite3.connect(db) as con:
        for aid in made:
            _insert(con, "repro_results", cols, (aid, "u", 0, OLD_10, str(made[aid][1])))
        _insert(con, "repro_results", cols, ("won-later", "u2", 1, OLD_10, str(made["won-later"][1])))
        _insert(con, "repro_results", cols, ("retried", "u2", 0, RECENT, str(made["retried"][1])))
    retention.run(db, apply=True, now=NOW, data_dir=data)
    for aid in ("won-later", "retried", "running"):
        assert all(path.exists() for path in made[aid]), aid
    assert not any(path.exists() for path in made["plain-fail"])
