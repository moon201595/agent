"""import_local_embeddings.py 단위 테스트 — 임시 SQLite DB로 돈다, 네트워크 없음."""

import json
import sqlite3

import pytest

from import_local_embeddings import import_embeddings, validate_records


def test_validate_records_accepts_well_formed_list():
    records = [{"arxiv_id": "1706.03762", "model": "local:test", "embedding": [0.1, 0.2]}]
    assert validate_records(records) == []


def test_validate_records_reports_every_problem_not_just_the_first():
    records = [
        {"model": "x", "embedding": [0.1]},          # arxiv_id 없음
        {"arxiv_id": "a", "embedding": [0.1]},        # model 없음
        {"arxiv_id": "a", "model": "x", "embedding": []},   # 빈 리스트
        {"arxiv_id": "a", "model": "x", "embedding": "not a list"},
        "not a dict",
    ]
    errors = validate_records(records)
    assert len(errors) == 5  # 다섯 항목 전부 걸림 — 하나만 보고 멈추지 않음


def test_import_embeddings_writes_rows_into_paper_embeddings(tmp_path):
    db_path = tmp_path / "test.db"
    records = [
        {"arxiv_id": "1706.03762", "model": "local:all-MiniLM-L6-v2",
         "embedding": [0.1, -0.2, 0.3]},
        {"arxiv_id": "2506.02153", "model": "local:all-MiniLM-L6-v2",
         "embedding": [0.4, 0.5]},
    ]

    n = import_embeddings(db_path, records)

    assert n == 2
    with sqlite3.connect(db_path) as con:
        rows = con.execute(
            "SELECT arxiv_id, model, embedding FROM paper_embeddings ORDER BY arxiv_id"
        ).fetchall()
    assert rows[0][0] == "1706.03762"
    assert json.loads(rows[0][2]) == [0.1, -0.2, 0.3]


def test_import_embeddings_upserts_same_arxiv_id_and_model():
    """같은 (arxiv_id, model) 조합으로 두 번 가져오면 최신 값으로 덮어써야 한다 —
    server.py의 캐시 갱신(_get_or_compute_embedding)과 같은 INSERT OR REPLACE 전제."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        db_path = Path(d) / "test.db"
        import_embeddings(db_path, [
            {"arxiv_id": "a", "model": "local:x", "embedding": [1.0]},
        ])
        import_embeddings(db_path, [
            {"arxiv_id": "a", "model": "local:x", "embedding": [2.0]},
        ])
        with sqlite3.connect(db_path) as con:
            rows = con.execute("SELECT embedding FROM paper_embeddings").fetchall()
        assert len(rows) == 1  # 새 행이 아니라 덮어쓰기
        assert json.loads(rows[0][0]) == [2.0]


def test_import_embeddings_different_model_same_arxiv_id_creates_conflict_by_design():
    """paper_embeddings의 PK는 arxiv_id 단독이다(server.py 스키마 그대로 재사용) —
    같은 논문을 다른 모델로 다시 넣으면 최신 것이 이전 모델 값을 덮어쓴다는 걸
    스키마 자체가 갖고 있는 제약으로 문서화해둔다(모델별로 따로 갖고 있고 싶다면
    스키마를 바꿔야 하는 지점 — 지금은 hybrid_search.py 쪽도 이 전제로 돈다)."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        db_path = Path(d) / "test.db"
        import_embeddings(db_path, [
            {"arxiv_id": "a", "model": "gemini-embedding-001", "embedding": [1.0]},
        ])
        import_embeddings(db_path, [
            {"arxiv_id": "a", "model": "local:x", "embedding": [2.0]},
        ])
        with sqlite3.connect(db_path) as con:
            rows = con.execute("SELECT model, embedding FROM paper_embeddings").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "local:x"  # 나중 것이 이김


def test_import_embeddings_rejects_whole_batch_on_any_bad_record(tmp_path):
    """부분 반영을 안 한다 — 5건 중 1건이 틀리면 나머지 4건도 안 쓴다(부분
    성공 상태가 "몇 건이 실제로 반영됐는지" 헷갈리게 만드는 걸 막기 위해)."""
    db_path = tmp_path / "test.db"
    records = [
        {"arxiv_id": "ok1", "model": "x", "embedding": [0.1]},
        {"arxiv_id": "bad", "model": "x", "embedding": []},
    ]
    with pytest.raises(ValueError):
        import_embeddings(db_path, records)
    assert not db_path.exists() or _is_empty(db_path)


def _is_empty(db_path) -> bool:
    with sqlite3.connect(db_path) as con:
        tables = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='paper_embeddings'"
        ).fetchall()
        if not tables:
            return True
        return con.execute("SELECT COUNT(*) FROM paper_embeddings").fetchone()[0] == 0
