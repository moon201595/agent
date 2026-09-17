"""2026-09-17 분할(§8-155) 뒤 단계 경계를 지키는 테스트.

망가뜨리면 실패하는 것: (1) 검색 모듈이 요약·다이제스트·발송을 다시 import 해 한 파일로 되돌아가는 것 (2) 발송 모듈이 검색을 아는 것
(3) `run_profile_scan` 이 옛 이름(`_deliver`·`scan_profile`·`_summary_exists` …)을 더는 내보내지 않아 cron 진입점·화면·테스트 monkeypatch 가 깨지는 것."""
from __future__ import annotations

import ast
from pathlib import Path

import run_profile_scan as rps
import scan_deliver
import scan_search


def _imports(path: str) -> set[str]:
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            out.add(n.module.split(".")[0])
    return out


def test_search_stage_does_not_know_delivery_or_summary():
    assert _imports("scan_search.py").isdisjoint({"batch_summarize", "digest", "email_delivery", "mail_ledger", "scan_deliver", "run_profile_scan", "trend_report"})


def test_delivery_stage_does_not_know_search():
    assert _imports("scan_deliver.py").isdisjoint({"find_new_papers", "s2_delta", "batch_summarize", "scan_search", "run_profile_scan", "profile_scoring"})


def test_orchestrator_reexports_the_names_tests_and_callers_use():
    assert rps.scan_profile is scan_search.scan_profile
    assert rps._summary_exists is scan_search._summary_exists
    assert rps._deliver is scan_deliver._deliver
    assert rps.delivery_failed is scan_deliver.delivery_failed
    assert rps.mail_subject is scan_deliver.mail_subject
    assert rps.DELIVERY_NO_RECIPIENT == scan_deliver.DELIVERY_NO_RECIPIENT
    for name in ("scan_and_digest", "scan_all_profiles", "is_weekly_review_day", "_exit_code", "_exit_message", "main", "DEEP_LAYER_BUDGET_SECONDS"):
        assert hasattr(rps, name), name
