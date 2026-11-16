"""⑨ 설명용 구조도(docs/system_map.html) 어긋남 감시 — 상자의 문구는 사람이 적으므로 코드가 바뀌면 조용히 틀린다(2026-11-15).

모듈을 더하고 구조도에 안 넣거나, 파일·대표 함수를 지우거나 옮기거나, LLM 을 부르는 모듈을 결정적 상자에 두거나,
정의를 고치고 다시 뽑지 않으면 실패한다. 다시 뽑기: .venv/bin/python scripts/build_system_map.py
상자의 상태(자동 운영·쟁점·손으로·예정)와 설명 문구의 정확성은 사람이 판단한 값이라 이 시험이 보증하지 않는다.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("build_system_map", ROOT / "scripts" / "build_system_map.py")
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)
HTML = (ROOT / "docs" / "system_map.html").read_text(encoding="utf-8")


def test_every_production_module_is_in_some_block():
    """새 운영 모듈을 만들고 구조도의 어느 상자에도 넣지 않으면 실패한다."""
    listed = {f for b in m.BLOCKS for f in b["files"]}
    missing = [f for f in m.production_modules() if f not in listed]
    assert not missing, f"구조도에 없는 모듈: {missing} — scripts/build_system_map.py 의 BLOCKS 에 넣어라"


def test_blocks_point_to_existing_files_functions_and_known_ids():
    """지운 파일·바꾼 함수 이름을 가리키거나, 없는 계층·상태·상자를 쓰거나, 흐름이 없는 상자를 부르면 실패한다."""
    layers = {l["id"] for l in m.LAYERS}
    ids = [b["id"] for b in m.BLOCKS]
    assert len(ids) == len(set(ids))
    for b in m.BLOCKS:
        assert b["layer"] in layers and b["status"] in m.STATUS, b["id"]
        assert all((ROOT / f).is_file() for f in b["files"]), b["id"]
        # 바깥 연결과 예정 상자만 파일이 없어도 된다.
        assert b["files"] or b["layer"] == "ext" or b["status"] == "planned", b["id"]
        for ref in b["funcs"]:
            f, _, name = ref.partition(":")
            assert f in b["files"] and m._func_lines(f).get(name), f"{b['id']}: {ref} 가 없다"
    for s in m.SCENARIOS:
        for name, blocks, _ in s["steps"]:
            assert blocks and set(blocks) <= set(ids), f"{s['id']}: {name}"


def test_pipeline_steps_are_all_on_the_map():
    """①~⑨ 단계 중 하나라도 어느 상자에도 안 붙으면 실패한다 — 문서·주석 전반의 단계 표기와 그림이 같은 말을 해야 한다."""
    steps = {b["step"] for b in m.BLOCKS}
    assert set("①②③④⑤⑥⑦⑧⑨") <= steps


def test_llm_markers_match_code():
    """모델을 부르는 모듈이 결정적 상자에 있거나, LLM 표시 상자가 모델을 부르는 모듈을 하나도 담지 않으면 실패한다.
    검증·대조·링크 감사처럼 결정적이어야 하는 상자에 표시가 붙어도 실패한다."""
    called = set(m.llm_modules())
    marked = {f for b in m.BLOCKS if b["llm"] for f in b["files"]}
    assert called and called <= marked, f"LLM 표시 밖에서 모델을 부른다: {sorted(called - marked)}"
    for b in m.BLOCKS:
        if b["llm"] and b["files"]:
            assert set(b["files"]) & called, f"{b['id']} 는 LLM 표시인데 모델을 부르는 모듈이 없다"
    deterministic = {b["id"]: b for b in m.BLOCKS if b["id"] in {"verify", "select", "audit", "weight", "send", "store"}}
    assert not [i for i, b in deterministic.items() if b["llm"]]


def test_llm_detector_sees_call_sites():
    """LLM 호출 모양 검색이 무뎌져(예: 정규식을 바꿔) 알려진 호출 지점을 못 보면 실패한다 — 그러면 위 표시 시험이 헛돈다."""
    called = set(m.llm_modules())
    assert {"summarize_engine.py", "agent_maintenance.py", "research_assets_collect.py", "trend_report.py",
            "scripts/probe_gemini.py", "narrative_engine.py"} <= called  # 뒤 둘은 별칭 호출(se._post_gemini)이다
    assert not called & {"verify.py", "profile_scoring.py", "link_policy.py", "feedback_weights.py"}
    # 주석 속 이름은 호출이 아니다.
    assert not m.calls_llm("x = 1  # _run_agent(...) 는 여기서 안 부른다\n")
    assert m.calls_llm("text = await se._post_gemini(client, prompt)\n")


def test_related_tests_reads_package_and_file_imports():
    """scripts 아래 모듈을 패키지 경로·여러 이름 한 줄·파일 이름으로 여는 시험을 못 찾으면 실패한다."""
    tests = {"test_a.py": "import scripts.morning_report as mr\n", "test_b.py": "from scripts import probe_gemini, count_arxiv\n",
             "test_c.py": "spec = load(ROOT / 'scripts' / 'build_system_map.py')\n", "test_d.py": "import os, verify\n",
             "test_e.py": "# import scripts.morning_report\nx = 'morning_report'\n", "test_f.py": "names = {'verify.py', 'eval.py'}\n",
             "test_g.py": "run(['python', 'scripts/count_arxiv.py'])\n"}
    assert m._related_tests("scripts/morning_report.py", tests) == ["test_a.py"]
    assert m._related_tests("scripts/count_arxiv.py", tests) == ["test_b.py", "test_g.py"]
    assert m._related_tests("scripts/build_system_map.py", tests) == ["test_c.py"]
    assert m._related_tests("verify.py", tests) == ["test_d.py"]


def test_saved_map_matches_generator():
    """정의·화면 코드(BODY·STYLE)를 고치고 다시 뽑지 않으면 실패한다 — 저장본에 실린 생성 시점 값(FACTS)을 그대로 넣어 다시 그려 견준다."""
    saved = json.loads(re.search(r"const DEF=(\{.*?\}), FACTS=", HTML, re.S).group(1).replace("<\\/", "</"))
    assert saved == json.loads(json.dumps(m.definition(), ensure_ascii=False)), "docs/system_map.html 이 낡았다 — scripts/build_system_map.py 를 다시 돌려라"
    facts = json.loads(re.search(r", FACTS=(\{.*\});\nconst ST=", HTML, re.S).group(1).replace("<\\/", "</"))
    assert m.render(facts) == HTML, "docs/system_map.html 이 낡았다 — scripts/build_system_map.py 를 다시 돌려라"


_URL_RE = re.compile(r"""https?://[^\s"'`<>)]+""")
_LOAD_RE = re.compile(r"""url\(\s*['"]?https?:""" r"|@import|fetch\(|XMLHttpRequest|new\s+WebSocket|sendBeacon|<iframe|<img\s", re.I)


def _facts(**over) -> dict:
    f = {"files": {}, "funcs": {}, "llm_modules": [], "modules": 0, "test_files": 0, "test_functions": 0, "commit": "x",
         "commit_full": "x", "commit_date": "", "dirty": [], "github": "https://github.com/o/r",
         "open_prefix": "vscode://vscode-remote/wsl+X/home/", "generated": "", "ops": {}}
    f.update(over)
    return f


def test_local_map_loads_nothing_external_and_artifact_only_fonts():
    """로컬 판이 바깥 자원을 끌어오거나(태그·CSS url()·fetch 등), 게시용 조각이 Google 글꼴 밖의 주소를 싣거나 문서 틀·VS Code 주소를 실으면 실패한다.
    주소는 GitHub 링크(눌러야 가는 것)와 SVG 이름공간만 허용한다."""
    gh = json.loads(re.search(r", FACTS=(\{.*\});\nconst ST=", HTML, re.S).group(1).replace("<\\/", "</"))["github"]
    local_ok = ("http://www.w3.org/2000/svg",) + ((gh,) if gh else ())
    assert not _LOAD_RE.search(HTML)
    assert all(u.startswith(local_ok) for u in _URL_RE.findall(HTML)), [u for u in _URL_RE.findall(HTML) if not u.startswith(local_ok)]
    art = m.render_artifact(_facts())
    assert not art.lstrip().lower().startswith(("<!doctype", "<html")) and "<title>" in art[:300]
    assert "vscode://" not in art and not _LOAD_RE.search(art)
    hosts = {urlsplit(u).hostname for u in _URL_RE.findall(art) if not u.startswith(("http://www.w3.org/2000/svg", "https://github.com/o/r"))}
    assert hosts <= {"fonts.googleapis.com", "fonts.gstatic.com"}, hosts


def test_ops_publishes_only_checked_fields(tmp_path):
    """운영 기록이 프로필 ID·상태 객체에 섞인 주소·키·경로를 그대로 싣거나, 스캔이 끝난 뒤의 오류를 스캔 오류로 세거나,
    다른 발송 대상(audience)에게 보낸 자료를 보낸 것으로 치면 실패한다. 운영 데이터 대신 임시 경로를 쓴다."""
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "daily_scan.log").write_text("\n".join([
        "=== 2026-11-10T20:00:00Z 시작 (pid 1) ===", "old Temporary failure in name resolution", "=== 2026-11-10T20:30:00Z 종료 (exit 0) ===",
        "=== 2026-11-11T20:00:00Z 시작 (pid 2) ===", "x Temporary failure in name resolution", "y 다른 프로필 뒤에 한 번 더 돈다",
        "[저하] 발송은 됐지만 검색 소스가 실패한 프로필: team_agent(arXiv failed · S2 done), boss@corp.example(arXiv failed) — 종료코드 2",
        "=== 2026-11-11T20:40:00Z 종료 (exit 2) ===", "[참고 자료] z Temporary failure in name resolution",
    ]), encoding="utf-8")
    base = tmp_path / "data" / "research_assets"
    base.mkdir(parents=True)
    (base / "collect_state.json").write_text(json.dumps({"last_run": {"day": "2026-11-12", "status": "ok", "token": "ghp_secret",
                                                                      "recipients": ["a@b.example"]}}), encoding="utf-8")
    (base / "config.json").write_text(json.dumps({"audience": "all", "profiles": ["team_robot", "x@y.example"]}), encoding="utf-8")
    (base / "items.json").write_text(json.dumps([{"profile_id": "team_robot", "item_id": i, "status": "ready"} for i in ("a", "b")]),
                                     encoding="utf-8")
    (base / "sent.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"profile_id": "team_robot", "item_id": "a", "audience": "all"},
        {"profile_id": "team_robot", "item_id": "b", "audience": "operator"}]), encoding="utf-8")
    ops = m._ops(tmp_path)
    dump = json.dumps(ops, ensure_ascii=False)
    assert "@" not in dump and "ghp_" not in dump and "recipients" not in dump
    assert ops["daily_degraded"] == ["team_agent"] and ops["daily_exit"] == 2 and ops["daily_ended"]
    assert ops["dns_error_lines"] == 1 and ops["deferred"] == 1
    assert ops["assets_run"] == {"day": "2026-11-12", "status": "ok"}
    assert ops["assets_unsent"] == {"team_robot": 1}
    assert "papers" not in ops  # 임시 경로에 DB 가 없다 — 운영 DB 로 새지 않는다


def test_ops_running_scan_has_no_end():
    """종료 줄이 아직 없는 실행을 앞 실행의 종료로 닫으면 실패한다."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "logs").mkdir()
        (root / "logs" / "daily_scan.log").write_text("=== 2026-11-10T20:00:00Z 시작 (pid 1) ===\n=== 2026-11-10T20:30:00Z 종료 (exit 0) ===\n"
                                                    "=== 2026-11-11T20:00:00Z 시작 (pid 2) ===\n작업 중\n", encoding="utf-8")
        ops = m._ops(root)
    assert ops["daily_ended"] is False and "daily_exit" not in ops and ops["daily_start"] == "2026-11-12 05:00 KST"
