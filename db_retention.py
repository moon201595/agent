"""⑧ 축적 — 주 1회 보존표에 따른 SQLite·캐시 정리. LLM·네트워크를 쓰지 않는다."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote

import migrate
import research_profile
import storage


# 운영 시작값은 첫 달 저장량을 보고 조정하되, 피드백·프로필·평가 원자료는 이 모듈의
# 허용 목록에 넣지 않는다. 보존 대상이 아닌 표를 이름 패턴으로 지우면 새 표가 생길
# 때 감사 기록을 잃을 수 있어서 정리 표를 명시적으로 고정한다.
CANDIDATE_DAYS = 90
OBSERVATION_DAYS = 180
RUN_DAYS = 180
PAPER_DAYS = 180
ADVISOR_RAW_DAYS = 30
REPRO_FAILURE_DAYS = 180
REPRO_ARTIFACT_DAYS = 7
CACHE_DAYS = 60
BACKUPS_TO_KEEP = 4

_TABLE_TIME_POLICIES: dict[str, tuple[str, int]] = {
    "candidate_observations": ("observed_at", OBSERVATION_DAYS),
    "search_candidates": ("last_seen", CANDIDATE_DAYS),
    "scan_runs": ("started_at", RUN_DAYS),
    "scan_health": ("computed_at", RUN_DAYS),
    "search_runs": ("started_at", RUN_DAYS),
}

_ADVISOR_POLICIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "advisor_runs": ("created_at", ("sent_input_json", "prompt_text")),
    "advisor_attempts": ("started_at", ("raw_response",)),
}

_PAPER_TABLES = {"papers", "summaries"}
_REPRO_TABLE = "repro_results"
_CACHE_DIRS = ("pdfs", "text")


def _ddl(con: sqlite3.Connection) -> None:
    """정리 실행 이력만 추가한다. 원자료 표의 DDL을 이 모듈에서 재정의하지 않는다."""
    con.execute(
        "CREATE TABLE IF NOT EXISTS retention_runs ("
        " run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT NOT NULL,"
        " applied INTEGER NOT NULL, plan_json TEXT NOT NULL, result_json TEXT NOT NULL,"
        " error TEXT)"
    )


def init_db(db: Path) -> None:
    """정리 이력 표도 다른 표와 같은 승인된 schema_guard 입구를 사용한다."""
    import schema_guard

    schema_guard.ensure(Path(db), _ddl, "db_retention")


def _utc(value: datetime | None) -> datetime:
    value = value or datetime.now(timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat(timespec="microseconds")


def _parse_time(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return _utc(parsed)


def _cutoff(now: datetime, days: int) -> datetime:
    return _utc(now) - timedelta(days=days)


def _read_only(db: Path) -> sqlite3.Connection:
    """읽기 전용 URI를 써서 plan이 없던 DB 파일을 만들지 않게 한다."""
    absolute = Path(db).resolve()
    con = sqlite3.connect(f"file:{quote(str(absolute))}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


@contextmanager
def _temporary_backup_dir(directory: Path) -> Iterator[None]:
    """테스트·임시 data_dir도 migrate.backup의 online backup 경로를 그대로 쓰게 한다."""
    original = migrate.BACKUP_DIR
    migrate.BACKUP_DIR = directory
    try:
        yield
    finally:
        migrate.BACKUP_DIR = original


def _backup_database(db: Path, backup_dir: Path) -> Path:
    """migrate.py의 SQLite online backup과 integrity 검사를 재사용한다."""
    backup_dir = Path(backup_dir)
    if backup_dir.resolve() == Path(migrate.BACKUP_DIR).resolve():
        return migrate.backup(Path(db))
    with _temporary_backup_dir(backup_dir):
        return migrate.backup(Path(db))


def _table_columns(con: sqlite3.Connection, table: str) -> set[str] | None:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not row:
        return None
    return {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}


def _skip(table: str, reason: str) -> dict[str, Any]:
    return {"status": f"skipped: {reason}", "deleted": 0, "nulled": 0, "targets": []}


def _row_target(row: sqlite3.Row) -> dict[str, Any]:
    return {"rowid": int(row["__retention_rowid"])}


def _old_rows(
    con: sqlite3.Connection, table: str, time_column: str, cutoff: datetime
) -> list[sqlite3.Row]:
    rows = con.execute(
        f'SELECT rowid AS __retention_rowid, * FROM "{table}"'
    ).fetchall()
    return [row for row in rows if (value := _parse_time(row[time_column])) is not None and value < cutoff]


def _normal_title(title: Any) -> str:
    return " ".join(str(title or "").lower().split())


def _doi_from_source(source: Any) -> str | None:
    text = str(source or "").strip()
    for prefix in ("open-access:", "manual-pdf:"):
        if text.startswith(prefix):
            value = text[len(prefix):].strip()
            if value.startswith("10."):
                return value.lower()
    return None


def _paper_keys(row: sqlite3.Row) -> set[str]:
    """papers 행이 delivery/feedback에서 가질 수 있는 paper_key를 복원한다."""
    keys: set[str] = set()
    aid = str(row["arxiv_id"] or "").strip() if "arxiv_id" in row.keys() else ""
    doi = _doi_from_source(row["source"] if "source" in row.keys() else None)
    title = _normal_title(row["title"] if "title" in row.keys() else None)
    if aid:
        keys.add(aid)
    if aid.startswith("pdf-") and doi:
        keys.add(f"doi:{doi}")
    elif doi:
        keys.add(f"doi:{doi}")
    if not aid and not doi and title:
        keys.add(f"title:{title}")
    if aid or doi or title:
        keys.add(research_profile.paper_key({
            "arxiv_id": aid or None,
            "doi": doi,
            "title": title,
        }))
    return keys


def _protected_keys(con: sqlite3.Connection) -> set[str]:
    protected: set[str] = set()
    for table in ("profile_shown", "feedback_tokens"):
        columns = _table_columns(con, table)
        if columns is not None and "paper_key" in columns:
            protected.update(
                row[0] for row in con.execute(f'SELECT paper_key FROM "{table}"') if row[0]
            )
    return protected


def _summary_ids(con: sqlite3.Connection) -> set[str]:
    columns = _table_columns(con, "summaries")
    if not columns or "arxiv_id" not in columns:
        return set()
    return {row[0] for row in con.execute("SELECT arxiv_id FROM summaries") if row[0]}


def _summary_keys(con: sqlite3.Connection, summary_ids: set[str]) -> set[str]:
    """합성 PDF ID와 DOI paper_key도 summary가 있는 논문으로 묶는다."""
    keys = set(summary_ids)
    columns = _table_columns(con, "papers")
    if not columns or "arxiv_id" not in columns:
        return keys
    for row in con.execute("SELECT * FROM papers"):
        if row["arxiv_id"] in summary_ids:
            keys.update(_paper_keys(row))
    return keys


def _keep_abstract_holders(con: sqlite3.Connection, rows: list[sqlite3.Row], columns: set[str]) -> list[sqlite3.Row]:
    """남는 관측이 `abstract_ref` 로 가리키는 초록 보유 행은 오래돼도 지우지 않는다(2026-09-15 검토).

    관측은 초록이 바뀐 경우에만 본문을 저장하고, 나머지는 보유 행의 scan_id 를 참조한다
    (`profile_impact._restore_abstract`). 보유 행만 지우면 **최근** 관측의 초록이 'corrupt' 로 떨어져 탐색 풀·재생 평가가
    그 논문을 초록 없이 본다. 보유 행은 다른 행을 참조하지 않으므로(사슬 금지) 한 번 거르면 된다."""
    if not rows or not {"abstract_ref", "abstract", "scan_id", "paper_key", "profile_id"}.issubset(columns):
        return rows
    doomed = {int(r["__retention_rowid"]) for r in rows}
    referenced = {
        (ref, key, pid)
        for rowid, ref, key, pid in con.execute(
            "SELECT rowid, abstract_ref, paper_key, profile_id FROM candidate_observations "
            "WHERE abstract IS NULL AND abstract_ref IS NOT NULL")
        if rowid not in doomed
    }
    return [r for r in rows if (r["scan_id"], r["paper_key"], r["profile_id"]) not in referenced]


def _plan_tables(con: sqlite3.Connection, now: datetime) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    tables: dict[str, dict[str, Any]] = {}
    internal: dict[str, Any] = {"paper_rows": {}, "summary_rows": {}, "retained_paths": set()}
    protected = _protected_keys(con)
    summary_ids = _summary_ids(con)
    summary_keys = _summary_keys(con, summary_ids)

    for table, (time_column, days) in _TABLE_TIME_POLICIES.items():
        columns = _table_columns(con, table)
        if columns is None:
            tables[table] = _skip(table, "표 없음")
            continue
        if time_column not in columns:
            tables[table] = _skip(table, "컬럼 없음")
            continue
        rows = _old_rows(con, table, time_column, _cutoff(now, days))
        if table == "candidate_observations":
            rows = _keep_abstract_holders(con, rows, columns)
        targets: list[dict[str, Any]] = []
        for row in rows:
            if table == "search_candidates":
                candidate_key = row["paper_key"] if "paper_key" in row.keys() else None
                has_summary = (
                    row["arxiv_id"] in summary_ids or candidate_key in summary_keys
                    if "arxiv_id" in row.keys()
                    else candidate_key in summary_keys
                )
                if candidate_key in protected or has_summary:
                    continue
            targets.append(_row_target(row))
        tables[table] = {
            "status": "planned",
            "cutoff_column": time_column,
            "cutoff": _iso(_cutoff(now, days)),
            "deleted": len(targets),
            "nulled": 0,
            "targets": targets,
        }

    paper_columns = _table_columns(con, "papers")
    summary_columns = _table_columns(con, "summaries")
    old_papers: list[sqlite3.Row] = []
    deletable_paper_ids: set[str] = set()
    if paper_columns is None:
        tables["papers"] = _skip("papers", "표 없음")
    elif not {"fetched_at", "arxiv_id"}.issubset(paper_columns):
        tables["papers"] = _skip("papers", "컬럼 없음")
    else:
        old_papers = _old_rows(con, "papers", "fetched_at", _cutoff(now, PAPER_DAYS))
        for row in old_papers:
            if not (_paper_keys(row) & protected):
                deletable_paper_ids.add(str(row["arxiv_id"]))
        # 최근 summary가 있으면 본문과 요약의 연결을 보존한다. 오래된 summary만
        # 남은 논문은 아래에서 함께 지워 고아 DB 행을 만들지 않는다.
        if summary_columns and {"arxiv_id", "created_at"}.issubset(summary_columns):
            recent_summary_ids = {
                str(row["arxiv_id"])
                for row in con.execute("SELECT arxiv_id, created_at FROM summaries")
                if row["arxiv_id"] in deletable_paper_ids
                and (_parse_time(row["created_at"]) is None or _parse_time(row["created_at"]) >= _cutoff(now, PAPER_DAYS))
            }
            deletable_paper_ids -= recent_summary_ids
        paper_targets = [
            _row_target(row) for row in old_papers if str(row["arxiv_id"]) in deletable_paper_ids
        ]
        tables["papers"] = {
            "status": "planned",
            "cutoff_column": "fetched_at",
            "cutoff": _iso(_cutoff(now, PAPER_DAYS)),
            "deleted": len(paper_targets),
            "nulled": 0,
            "targets": paper_targets,
        }
        internal["paper_rows"] = {
            str(row["arxiv_id"]): row for row in old_papers if str(row["arxiv_id"]) in deletable_paper_ids
        }

    if summary_columns is None:
        tables["summaries"] = _skip("summaries", "표 없음")
    elif not {"arxiv_id", "created_at"}.issubset(summary_columns):
        tables["summaries"] = _skip("summaries", "컬럼 없음")
    else:
        summary_targets: list[dict[str, Any]] = []
        summary_rows: dict[int, sqlite3.Row] = {}
        for row in _old_rows(con, "summaries", "created_at", _cutoff(now, PAPER_DAYS)):
            aid = str(row["arxiv_id"] or "")
            paper_row = internal["paper_rows"].get(aid)
            if paper_row is not None or not paper_columns:
                summary_targets.append(_row_target(row))
                summary_rows[int(row["__retention_rowid"])] = row
            elif aid not in protected:
                # 오래된 orphan summary는 본문 참조가 없으므로 같이 정리한다.
                paper_ids = set()
                if paper_columns and "arxiv_id" in paper_columns:
                    paper_ids = {
                        str(p["arxiv_id"])
                        for p in con.execute("SELECT arxiv_id FROM papers")
                        if p[0]
                    }
                if aid not in paper_ids:
                    summary_targets.append(_row_target(row))
                    summary_rows[int(row["__retention_rowid"])] = row
        tables["summaries"] = {
            "status": "planned",
            "cutoff_column": "created_at",
            "cutoff": _iso(_cutoff(now, PAPER_DAYS)),
            "deleted": len(summary_targets),
            "nulled": 0,
            "targets": summary_targets,
        }
        internal["summary_rows"] = summary_rows

    advisor_tables: dict[str, dict[str, Any]] = {}
    for table, (time_column, raw_columns) in _ADVISOR_POLICIES.items():
        columns = _table_columns(con, table)
        if columns is None:
            advisor_tables[table] = _skip(table, "표 없음")
            continue
        available_raw = [column for column in raw_columns if column in columns]
        if time_column not in columns or not available_raw:
            advisor_tables[table] = _skip(table, "컬럼 없음")
            continue
        targets = [_row_target(row) for row in _old_rows(con, table, time_column, _cutoff(now, ADVISOR_RAW_DAYS))]
        advisor_tables[table] = {
            "status": "planned",
            "cutoff_column": time_column,
            "raw_columns": available_raw,
            "cutoff": _iso(_cutoff(now, ADVISOR_RAW_DAYS)),
            "deleted": 0,
            "nulled": len(targets) * len(available_raw),
            "rows": len(targets),
            "targets": targets,
        }
    tables.update(advisor_tables)

    repro_columns = _table_columns(con, _REPRO_TABLE)
    failed_old_rows: list[sqlite3.Row] = []
    failed_artifact_rows: list[sqlite3.Row] = []
    if repro_columns is None:
        tables[_REPRO_TABLE] = _skip(_REPRO_TABLE, "표 없음")
    elif not {"created_at", "success"}.issubset(repro_columns):
        tables[_REPRO_TABLE] = _skip(_REPRO_TABLE, "컬럼 없음")
    else:
        failed_old_rows = [
            row for row in _old_rows(con, _REPRO_TABLE, "created_at", _cutoff(now, REPRO_FAILURE_DAYS))
            if row["success"] == 0
        ]
        every_row = list(con.execute(f'SELECT rowid AS __retention_rowid, * FROM "{_REPRO_TABLE}"'))
        # 산출물 경로(repro/<id>, <id>.log, code/<id>)는 **논문 단위**라 시도끼리 공유한다. 옛 실패 행만 보고 지우면 같은 논문의
        # 성공 재현이나 최근 재시도의 clone·로그까지 지운다(2026-09-15 운영 계획 실측: 성공 기록이 있는 2110.15045 폴더가 삭제 대상).
        # 성공이 한 번이라도 있거나 7일 안에 시도가 있는 논문의 산출물은 건드리지 않는다. DB 행 삭제(180일)는 행 단위라 그대로다.
        keep_artifacts = {
            str(row["arxiv_id"]) for row in every_row
            if row["success"] == 1
            or (_parse_time(row["created_at"]) is None)
            or _parse_time(row["created_at"]) >= _cutoff(now, REPRO_ARTIFACT_DAYS)
        }
        all_failed_rows = [
            row for row in every_row
            if row["success"] == 0
            and str(row["arxiv_id"]) not in keep_artifacts
        ]
        failed_artifact_rows = all_failed_rows
        targets = [_row_target(row) for row in failed_old_rows]
        tables[_REPRO_TABLE] = {
            "status": "planned",
            "cutoff_column": "created_at",
            "cutoff": _iso(_cutoff(now, REPRO_FAILURE_DAYS)),
            "deleted": len(targets),
            "nulled": 0,
            "targets": targets,
        }

    # DB에 남길 papers의 경로가 파일 캐시를 보호한다. 삭제 예정 행의 경로는
    # 보호하지 않아야 파일과 DB의 수명이 함께 끝난다.
    if paper_columns:
        for row in con.execute("SELECT * FROM papers"):
            if str(row["arxiv_id"]) not in internal["paper_rows"]:
                for column in ("pdf_path", "text_path"):
                    if column in paper_columns and row[column]:
                        internal["retained_paths"].add(str(Path(row[column]).resolve()))
    internal["failed_old_rows"] = failed_old_rows
    internal["failed_artifact_rows"] = failed_artifact_rows
    return tables, internal


def _safe_path(path: Path, root: Path) -> tuple[Path | None, str | None]:
    """resolve 결과와 모든 중간 경로를 확인해 data_dir 밖 삭제를 막는다."""
    path = Path(path)
    root = Path(root)
    try:
        if path.is_symlink():
            return None, "심볼릭 링크"
        resolved_root = root.resolve()
        resolved = path.resolve(strict=False)
        resolved.relative_to(resolved_root)
        current = path
        parts: list[Path] = []
        while current != root and current != current.parent:
            parts.append(current)
            current = current.parent
        if any(part.is_symlink() for part in parts):
            return None, "중간 경로 심볼릭 링크"
        return resolved, None
    except (OSError, RuntimeError, ValueError):
        return None, "허용 디렉터리 밖"


def _file_record(path: Path, root: Path, reason: str) -> tuple[dict[str, Any] | None, str | None]:
    safe, why = _safe_path(path, root)
    if safe is None or not path.exists():
        return None, why or "파일 없음"
    try:
        stat = path.stat()
    except OSError as exc:
        return None, f"stat 실패: {type(exc).__name__}"
    return {"path": str(path), "bytes": stat.st_size, "reason": reason}, None


def _repro_candidates(row: sqlite3.Row, repro_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for column in ("log_path", "local_path"):
        if column in row.keys() and row[column]:
            paths.append(Path(row[column]))
    aid = str(row["arxiv_id"] or "") if "arxiv_id" in row.keys() else ""
    stem = aid.replace("/", "_")
    if stem:
        paths.extend((repro_dir / f"{stem}.log", repro_dir / stem, repro_dir / "code" / stem))
    return paths


def _plan_files(
    con: sqlite3.Connection,
    data_dir: Path,
    now: datetime,
    internal: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, str]], dict[str, Any]]:
    delete: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, str]] = []
    for aid, row in internal["paper_rows"].items():
        for column in ("pdf_path", "text_path"):
            if column in row.keys() and row[column]:
                cache_root = data_dir / ("pdfs" if column == "pdf_path" else "text")
                try:
                    is_old = datetime.fromtimestamp(
                        Path(row[column]).stat().st_mtime, tz=timezone.utc
                    ) < _cutoff(now, CACHE_DAYS)
                except OSError:
                    is_old = False
                if not is_old:
                    continue
                record, why = _file_record(Path(row[column]), cache_root, f"paper {aid} 삭제와 함께")
                if record:
                    record["root"] = str(cache_root)
                    delete[record["path"]] = record
                elif why and Path(row[column]).exists():
                    skipped.append({"path": str(row[column]), "reason": why})

    retained = internal["retained_paths"]
    for directory_name in _CACHE_DIRS:
        directory = data_dir / directory_name
        if not directory.exists() or not directory.is_dir():
            continue
        try:
            children = list(directory.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_dir() or child.is_symlink():
                if child.is_symlink():
                    skipped.append({"path": str(child), "reason": "심볼릭 링크"})
                continue
            if str(child.resolve()) in retained:
                continue
            try:
                old = datetime.fromtimestamp(child.stat().st_mtime, tz=timezone.utc) < _cutoff(now, CACHE_DAYS)
            except OSError:
                continue
            if not old:
                continue
            record, why = _file_record(child, directory, "오래된 미참조 캐시")
            if record:
                record["root"] = str(directory)
                delete[record["path"]] = record
            elif why:
                skipped.append({"path": str(child), "reason": why})

    repro_dir = data_dir / "repro"
    for row in internal["failed_artifact_rows"]:
        created = _parse_time(row["created_at"])
        if created is None or created >= _cutoff(now, REPRO_ARTIFACT_DAYS):
            continue
        stem = str(row["arxiv_id"] or "").replace("/", "_")
        if stem and (repro_dir / f"{stem}.running").exists():
            continue        # 지금 도는 재현(docker_runner 의 .running 표지)
        for candidate in _repro_candidates(row, repro_dir):
            if not candidate.exists():
                continue
            try:
                if datetime.fromtimestamp(candidate.stat().st_mtime, tz=timezone.utc) >= _cutoff(now, REPRO_ARTIFACT_DAYS):
                    continue
            except OSError:
                continue
            record, why = _file_record(candidate, repro_dir, "실패 재현 7일 초과")
            if record:
                record["root"] = str(repro_dir)
                delete[record["path"]] = record
            elif why:
                skipped.append({"path": str(candidate), "reason": why})

    files = sorted(delete.values(), key=lambda record: record["path"])
    stats = {"count": len(files), "bytes": sum(record["bytes"] for record in files)}
    return files, skipped, stats


def _backup_plan(backup_dir: Path) -> dict[str, Any]:
    if not backup_dir.exists() or not backup_dir.is_dir():
        return {"keep": [], "delete": [], "count": 0, "bytes": 0}
    entries: list[tuple[float, str, Path]] = []
    try:
        for path in backup_dir.iterdir():
            if not path.is_file() or path.is_symlink():
                continue
            try:
                entries.append((path.stat().st_mtime, path.name, path))
            except OSError:
                continue
    except OSError:
        return {"keep": [], "delete": [], "count": 0, "bytes": 0}
    entries.sort(key=lambda item: (item[0], item[1]), reverse=True)
    keep = [str(path) for _, _, path in entries[:BACKUPS_TO_KEEP]]
    delete_records = []
    for _, _, path in entries[BACKUPS_TO_KEEP:]:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        delete_records.append({"path": str(path), "bytes": size, "reason": "최신 4개 밖"})
    return {
        "keep": keep,
        "delete": delete_records,
        "count": len(delete_records),
        "bytes": sum(record["bytes"] for record in delete_records),
    }


def _plan_impl(
    db: Path,
    now: datetime | None = None,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """삭제·비움 대상만 계산한다. 읽기 전용 URI로 DB와 파일을 변경하지 않는다."""
    started = time.monotonic()
    db = Path(db)
    data_dir = Path(data_dir) if data_dir is not None else Path(storage.DATA_DIR)
    current = _utc(now)
    result: dict[str, Any] = {
        "version": 1,
        "db": str(db),
        "data_dir": str(data_dir),
        "now": _iso(current),
        "cutoffs": {
            "candidate_observations": _iso(_cutoff(current, OBSERVATION_DAYS)),
            "search_candidates": _iso(_cutoff(current, CANDIDATE_DAYS)),
            "runs": _iso(_cutoff(current, RUN_DAYS)),
            "advisor_raw": _iso(_cutoff(current, ADVISOR_RAW_DAYS)),
            "papers": _iso(_cutoff(current, PAPER_DAYS)),
            "repro_artifacts": _iso(_cutoff(current, REPRO_ARTIFACT_DAYS)),
            "cache": _iso(_cutoff(current, CACHE_DAYS)),
        },
        "tables": {},
        "files": {"delete": [], "skipped": [], "count": 0, "bytes": 0},
        "backups": _backup_plan(data_dir / "backups"),
        "skipped": [],
        "elapsed_seconds": 0.0,
        "_internal": {},
    }
    if not db.exists():
        for table in (*_TABLE_TIME_POLICIES, *_PAPER_TABLES, *_ADVISOR_POLICIES, _REPRO_TABLE):
            result["tables"][table] = _skip(table, "표 없음")
        result["skipped"].append("DB 없음")
    else:
        try:
            with _read_only(db) as con:
                tables, internal = _plan_tables(con, current)
                files, skipped_files, stats = _plan_files(con, data_dir, current, internal)
                result["tables"] = tables
                result["files"] = {"delete": files, "skipped": skipped_files, **stats}
                result["_internal"] = {"tables": tables, "internal": internal}
        except sqlite3.Error as exc:
            result["skipped"].append(f"DB 읽기 실패: {type(exc).__name__}")
            for table in (*_TABLE_TIME_POLICIES, *_PAPER_TABLES, *_ADVISOR_POLICIES, _REPRO_TABLE):
                result["tables"].setdefault(table, _skip(table, "DB 읽기 실패"))
    result["elapsed_seconds"] = round(time.monotonic() - started, 6)
    return result


def _public_plan(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result.pop("_internal", None)
    return result


def plan(
    db: Path,
    now: datetime | None = None,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """공개 계획 결과는 JSON으로 기록·출력할 수 있는 값만 돌려준다."""
    return _public_plan(_plan_impl(db, now=now, data_dir=data_dir))


def _execute_targets(
    con: sqlite3.Connection,
    table: str,
    info: dict[str, Any],
    *,
    null_columns: tuple[str, ...] = (),
) -> dict[str, Any]:
    if info.get("status") != "planned":
        return {"deleted": 0, "nulled": 0}
    rowids = [int(target["rowid"]) for target in info.get("targets", [])]
    nulled = 0
    if null_columns and rowids:
        assignments = ", ".join(f'"{column}" = NULL' for column in null_columns)
        placeholders = ",".join("?" for _ in rowids)
        con.execute(f'UPDATE "{table}" SET {assignments} WHERE rowid IN ({placeholders})', rowids)
        nulled = len(rowids) * len(null_columns)
    elif rowids:
        placeholders = ",".join("?" for _ in rowids)
        con.execute(f'DELETE FROM "{table}" WHERE rowid IN ({placeholders})', rowids)
    return {"deleted": 0 if null_columns else len(rowids), "nulled": nulled}


def _delete_files(files: list[dict[str, Any]], data_dir: Path) -> dict[str, Any]:
    deleted = 0
    bytes_deleted = 0
    skipped: list[dict[str, str]] = []
    for record in files:
        path = Path(record["path"])
        safe, why = _safe_path(path, Path(record.get("root", data_dir)))
        if safe is None or not path.exists():
            skipped.append({"path": str(path), "reason": why or "파일 없음"})
            continue
        try:
            if path.is_dir():
                # 실패 clone은 폴더 단위이지만 내부 심볼릭 링크가 있으면 전체를
                # 지우지 않아 외부 경로를 따라가지 않게 한다.
                if any(child.is_symlink() for child in path.rglob("*")):
                    skipped.append({"path": str(path), "reason": "내부 심볼릭 링크"})
                    continue
                import shutil

                shutil.rmtree(path)
            else:
                path.unlink()
            deleted += 1
            bytes_deleted += int(record.get("bytes", 0))
        except OSError as exc:
            skipped.append({"path": str(path), "reason": f"삭제 실패: {type(exc).__name__}"})
    return {"count": deleted, "bytes": bytes_deleted, "skipped": skipped}


def _record_run(
    db: Path,
    started_at: str,
    finished_at: str,
    applied: bool,
    plan_value: dict[str, Any],
    result: dict[str, Any],
    error: str | None,
) -> None:
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO retention_runs "
            "(run_id,started_at,finished_at,applied,plan_json,result_json,error) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                uuid.uuid4().hex,
                started_at,
                finished_at,
                int(applied),
                json.dumps(_public_plan(plan_value), ensure_ascii=False, sort_keys=True),
                json.dumps(result, ensure_ascii=False, sort_keys=True),
                error,
            ),
        )


def run(
    db: Path,
    apply: bool = False,
    now: datetime | None = None,
    data_dir: Path | None = None,
    backup_dir: Path | None = None,
) -> dict[str, Any]:
    """계획을 계산하고 apply일 때만 백업 뒤 DB·파일 정리를 실행한다."""
    started_clock = time.monotonic()
    started_at = _iso(datetime.now(timezone.utc))
    db = Path(db)
    data_dir = Path(data_dir) if data_dir is not None else Path(storage.DATA_DIR)
    backup_dir = Path(backup_dir) if backup_dir is not None else data_dir / "backups"
    planned = _plan_impl(db, now=now, data_dir=data_dir)
    if not apply:
        planned["applied"] = False
        return _public_plan(planned)

    backup_path: Path | None = None
    try:
        # 되돌리기 어려운 DB 삭제보다 백업을 먼저 끝내야 실패 시 원자료가 그대로 남는다.
        backup_path = _backup_database(db, backup_dir)
        init_db(db)
        internal = planned.get("_internal", {}).get("internal", {})
        tables = planned.get("_internal", {}).get("tables", planned["tables"])
        table_result: dict[str, dict[str, int]] = {}
        with sqlite3.connect(db) as con:
            con.execute("BEGIN")
            for table, info in tables.items():
                if table in _ADVISOR_POLICIES:
                    raw_columns = tuple(info.get("raw_columns", ())) if info.get("status") == "planned" else ()
                    table_result[table] = {
                        "status": info.get("status"),
                        **_execute_targets(con, table, info, null_columns=raw_columns),
                    }
                elif table in (*_TABLE_TIME_POLICIES, *_PAPER_TABLES, _REPRO_TABLE):
                    table_result[table] = {
                        "status": info.get("status"),
                        **_execute_targets(con, table, info),
                    }
            con.commit()

        file_result = _delete_files(planned["files"]["delete"], data_dir)
        file_result["skipped"] = [*planned["files"].get("skipped", []), *file_result["skipped"]]
        # 백업 직후 다시 계산해 새 백업을 최신 4개 보존 집합에 포함한다.
        backup_cleanup = _backup_plan(backup_dir)
        backup_result = {"keep": backup_cleanup["keep"], "deleted": [], "count": 0, "bytes": 0}
        for record in backup_cleanup["delete"]:
            path = Path(record["path"])
            safe, why = _safe_path(path, backup_dir)
            if safe is None or not path.exists():
                file_result["skipped"].append({"path": str(path), "reason": why or "파일 없음"})
                continue
            try:
                path.unlink()
                backup_result["deleted"].append(record)
                backup_result["count"] += 1
                backup_result["bytes"] += int(record["bytes"])
            except OSError as exc:
                file_result["skipped"].append({"path": str(path), "reason": f"백업 삭제 실패: {type(exc).__name__}"})

        checkpoint_vacuum_error: str | None = None
        try:
            with sqlite3.connect(db) as con:
                con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                con.execute("VACUUM")
        except sqlite3.Error as exc:
            checkpoint_vacuum_error = f"{type(exc).__name__}: {exc}"

        result: dict[str, Any] = {
            "applied": True,
            "backup_path": str(backup_path),
            "tables": table_result,
            "files": file_result,
            "backups": backup_result,
            "checkpoint_vacuum_error": checkpoint_vacuum_error,
            "elapsed_seconds": round(time.monotonic() - started_clock, 6),
            "error": None,
        }
        finished_at = _iso(datetime.now(timezone.utc))
        _record_run(db, started_at, finished_at, True, planned, result, None)
        return result
    except Exception as exc:  # noqa: BLE001 — 백업·스키마·트랜잭션 실패를 결과에 남긴다.
        result = {
            "applied": False,
            "backup_path": str(backup_path) if backup_path else None,
            "tables": {
                table: {"status": info.get("status"), "deleted": 0, "nulled": 0}
                for table, info in planned.get("tables", {}).items()
            },
            "files": {"count": 0, "bytes": 0, "skipped": []},
            "backups": {"count": 0, "bytes": 0, "deleted": []},
            "elapsed_seconds": round(time.monotonic() - started_clock, 6),
            "error": f"{type(exc).__name__}: {exc}",
        }
        return {**_public_plan(planned), **result}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="주 1회 DB 보존 정리. 기본은 계획만 출력한다.")
    parser.add_argument("--db", type=Path, default=storage.DB_PATH)
    parser.add_argument("--data-dir", type=Path, default=storage.DATA_DIR)
    parser.add_argument("--backup-dir", type=Path, default=None)
    parser.add_argument("--apply", action="store_true", help="migrate.py 방식 백업 뒤 정리한다")
    args = parser.parse_args(argv)
    result = run(args.db, apply=args.apply, data_dir=args.data_dir, backup_dir=args.backup_dir)
    print(json.dumps(_public_plan(result), ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not result.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
