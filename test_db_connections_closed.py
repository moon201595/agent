"""⑨ 배달·⑧ 축적 — GC 시점과 무관하게 SQLite 연결을 닫는 계약을 지킨다(2026-11-02)."""
from __future__ import annotations

import ast
import gc
import os
from contextlib import closing
from pathlib import Path

import pytest


# 연결을 반환하는 팩토리는 소비자가 닫는다. 초기 설정 실패 때에는 팩토리가 닫아야 한다.
_CONNECTION_FACTORIES = {
    ("storage.py", "db"): "row_factory를 설정해 반환하며 소비자가 소유한다",
    ("server.py", "_db"): "server.DB_PATH를 전달하는 storage.db의 위임 팩토리다",
    ("daily_links.py", "_ro"): "query_only를 설정해 반환하며 소비자가 소유한다",
    ("db_retention.py", "_read_only"): "읽기 전용 URI와 row_factory를 설정해 반환한다",
    ("ops_dashboard.py", "_ro"): "읽기 전용 연결을 반환하며 소비자가 소유한다",
}


def _unclosed_connections(root: Path) -> list[str]:
    """생성 지점뿐 아니라 연결 팩토리 소비자도 검사해 별칭 뒤에 종료 누락이 숨지 않게 한다."""
    failures = []
    for path in sorted([*root.glob("*.py"), *root.glob("scripts/*.py")]):
        if path.name.startswith("test_") or path.name == "conftest.py":
            continue
        relative = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        sqlite_names, storage_names = {"sqlite3"}, {"storage"}
        direct_names = {name for file, name in _CONNECTION_FACTORIES if file == relative}
        closing_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "sqlite3": sqlite_names.add(alias.asname or alias.name)
                    if alias.name == "storage": storage_names.add(alias.asname or alias.name)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if (node.module, alias.name) in (("sqlite3", "connect"), ("storage", "db")):
                        direct_names.add(alias.asname or alias.name)
                    if node.module == "contextlib" and alias.name == "closing":
                        closing_names.add(alias.asname or alias.name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            opens = isinstance(function, ast.Name) and function.id in direct_names
            if isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name):
                opens |= ((function.value.id in sqlite_names and function.attr == "connect")
                          or (function.value.id in storage_names and function.attr == "db"))
            if not opens:
                continue
            parent = parents.get(node)
            # closing을 호출하기만 하면 닫히지 않으므로 with의 자원 관리에 넘겼는지 본다.
            if (isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name)
                    and parent.func.id in closing_names and isinstance(parents.get(parent), ast.withitem)):
                continue
            if (isinstance(parent, ast.Attribute) and parent.attr == "close"
                    and isinstance(parents.get(parent), ast.Call)):
                continue
            scope = parent
            while scope is not None and not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope = parents.get(scope)
            if scope is not None and (relative, scope.name) in _CONNECTION_FACTORIES:
                continue
            if isinstance(parent, ast.Assign) and len(parent.targets) == 1 and isinstance(parent.targets[0], ast.Name):
                name = parent.targets[0].id
                container = parents.get(parent)
                # 생성 직후의 try가 모든 이용을 감싸고 finally가 닫는 형태만 인정한다.
                for field in ("body", "orelse", "finalbody"):
                    statements = getattr(container, field, [])
                    if parent not in statements:
                        continue
                    index = statements.index(parent)
                    following = statements[index + 1] if index + 1 < len(statements) else None
                    if isinstance(following, ast.Try) and any(
                        isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
                        and isinstance(stmt.value.func, ast.Attribute) and stmt.value.func.attr == "close"
                        and isinstance(stmt.value.func.value, ast.Name) and stmt.value.func.value.id == name
                        for stmt in following.finalbody
                    ):
                        break
                else:
                    failures.append(f"{relative}:{node.lineno}: {ast.unparse(node)}")
                continue
            failures.append(f"{relative}:{node.lineno}: {ast.unparse(node)}")
    return failures


def test_operational_connections_have_explicit_owners():
    """closing 한 곳을 맨 with 연결로 되돌리거나 대입 연결의 finally를 지우면 실패한다. 운영 연결 처리는 재구현하지 않는다."""
    assert _unclosed_connections(Path(__file__).resolve().parent) == []


def test_deliver_closes_connections_with_gc_disabled(tmp_path, monkeypatch):
    """2026-11-02: digest의 storage.db 소비자 한 곳을 closing 없이 되돌리면 FD가 늘어 실패한다. 실제 그리기·서명·기록을 통과한다."""
    if not Path("/proc/self/fd").is_dir():
        pytest.skip("Linux의 파일 기술자 관측이 필요하다")
    import sqlite3
    import storage
    import research_profile as rp
    import code_ladder
    import feedback_links
    import mail_ledger
    import evidence_state
    import scan_deliver
    import email_delivery

    db = tmp_path / "delivery.db"
    storage.init_storage(db)
    rp.create_profile(db, "fd_fixture", "연결 검사", core_topics=["robot"], max_items=6)
    for module in (code_ladder, feedback_links, mail_ledger, evidence_state):
        module.init_db(db)
    recipients = [f"reader{i}@example.invalid" for i in range(4)]
    for recipient in recipients:
        rp.add_recipient(db, "fd_fixture", recipient)
    summary = tmp_path / "summary.md"
    summary.write_text("### 연구 개요\n- 연결 검사에 쓰는 설명이다.\n### 결론\n연결 검사에 쓰는 결론이다.\n", encoding="utf-8")
    papers = [{"arxiv_id": f"9999.{i:05}", "title": f"Fixture {i}", "abstract": "Only a fixture.",
               "deep_status": "ok", "repro_outcome": {"status": "unconfirmed"},
               "_delivered_state": {"retraction": None}, "_score": {"core_hits": ["robot"]}}
              for i in range(6)]
    with closing(sqlite3.connect(db)) as con, con:
        con.executemany("INSERT INTO papers(arxiv_id,title) VALUES(?,?)", [(p["arxiv_id"], p["title"]) for p in papers])
        con.executemany("INSERT INTO summaries(arxiv_id,path,numbers_total,numbers_matched,coverage_ratio,coverage_kind) "
                        "VALUES(?,?,0,0,1.0,'measured')", [(p["arxiv_id"], str(summary)) for p in papers])
    monkeypatch.setattr(storage, "DB_PATH", db)
    monkeypatch.setattr(feedback_links, "config", lambda: ("https://example.invalid/exec", "fixture-only-secret"))
    monkeypatch.setattr(feedback_links, "page_url", lambda: "")
    # 실제 그리기·DB 조회 경로를 남기기 위해 SMTP만 가짜로 바꾼다.
    sent = []
    monkeypatch.setattr(email_delivery, "send_digest_email", lambda *args: sent.append(args))
    result = {"papers": papers, "title_only_papers": [], "candidates_found": 6, "scored_count": 6,
              "run_status": "done", "s2_status": "done",
              "_evidence_states": [{"paper_key": rp.paper_key(p), "arxiv_id": p["arxiv_id"],
                                    "title": p["title"], "state": p["_delivered_state"]} for p in papers]}
    gc.collect()
    was_enabled = gc.isenabled()
    gc.disable()
    before = len(os.listdir("/proc/self/fd"))
    try:
        status = scan_deliver._deliver(db, "fd_fixture", result, "")
        delta = len(os.listdir("/proc/self/fd")) - before
        print(f"GC 비활성 · 카드 6장 · 수신자 4명 · FD 증가 {delta:+d}")
        assert status == "발송 완료 → 4명"
        assert [args[2] for args in sent] == [[recipient] for recipient in recipients]
        assert all("Fixture 0" in args[0] and "Fixture 5" in args[3] for args in sent)
        assert delta <= 4
    finally:
        gc.collect()
        if was_enabled:
            gc.enable()


@pytest.mark.parametrize("fail", [False, True])
def test_feedback_write_keeps_commit_and_rollback(tmp_path, monkeypatch, fail):
    """2026-11-02: closing만 남겨 commit을 잃거나 오류 때 commit하도록 바꾸면 재접속 후 토큰 수가 달라져 실패한다."""
    import sqlite3
    import feedback_links

    db = tmp_path / "transaction.db"
    feedback_links.init_db(db)
    monkeypatch.setattr(feedback_links, "config", lambda: ("https://example.invalid/exec", "fixture-only-secret"))
    monkeypatch.setattr(feedback_links, "page_url", lambda: "")
    if fail:
        # 두 번째 INSERT에서 실제 UNIQUE 오류를 내 첫 번째 INSERT까지 롤백되는지 본다.
        monkeypatch.setattr(feedback_links.secrets, "token_urlsafe", lambda *args: "duplicate-tid")
    papers = [{"arxiv_id": "9999.00001"}, {"arxiv_id": "9999.00002"}]
    if fail:
        with pytest.raises(sqlite3.IntegrityError):
            feedback_links.issue_links(db, "fixture", "issue", "reader@example.invalid", papers)
    else:
        assert len(feedback_links.issue_links(db, "fixture", "issue", "reader@example.invalid", papers)) == 2
    with closing(sqlite3.connect(db)) as con:
        assert con.execute("SELECT count(*) FROM feedback_tokens").fetchone()[0] == (0 if fail else 2)
