"""② 주간 관리 에이전트(2026-09-15) — 브리프 · 검증 · Codex 판정 우선 · 적용 · 실패 건너뛰기 · 메일 보고 · 헤드리스 보안 플래그.

각 테스트의 docstring 에 "무엇을 망가뜨리면 실패하는가"를 적었다. 실제 CLI 는 부르지 않는다 — 가짜 runner 가 같은 두 메서드를 흉내 낸다.
"""
import asyncio
import json
import sqlite3
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

import agent_maintenance as am
import digest
import feedback_links as fl
import http_client
import research_profile as rp
import run_profile_scan as rps
import scan_search          # 2026-09-17 분할: 검색 소스 모듈은 여기서 patch 한다


def _day(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%SZ")


HITS = [
    {"arxiv_id": "h1", "title": "robot manipulation with tactile skin", "abstract": "tactile skin sensing helps robot manipulation.", "published": _day(1)},
    {"arxiv_id": "h2", "title": "robot manipulation for grasping", "abstract": "a tactile skin glove for robot manipulation.", "published": _day(1)},
    {"arxiv_id": "h3", "title": "robot manipulation in medical imaging", "abstract": "medical imaging segmentation with robot manipulation.", "published": _day(1)},
]
DROPPED = [{"arxiv_id": f"d{i}", "title": f"spiking sensor study {i}", "abstract": "We build a spiking sensor for edge devices.",
            "published": _day(1)} for i in range(4)]


def _profile(db, freq="manual"):
    import storage
    storage.init_storage(db)
    rp.create_profile(db, "p", "로봇", core_topics=["robot manipulation"], core_weights={"robot manipulation": 1.0},
                      target_domain=["factory"], exclude=["banned"], max_items=5, s2_seeds=["robot manipulation"],
                      schedule_frequency=freq)


def _scan(db, monkeypatch, papers):
    async def fake_get(client, params):
        class R: text = "<x/>"
        return R()

    async def fake_s2(client, keywords, since, until, limit=100):
        return {"papers": [], "status": "done", "query": "S2", "keywords_failed": 0}
    monkeypatch.setattr(http_client, "throttled_arxiv_get", fake_get)
    monkeypatch.setattr(http_client, "parse_arxiv_feed", lambda _: papers)
    monkeypatch.setattr(scan_search.s2_delta, "find_new_papers_since", fake_s2)
    asyncio.run(rps.scan_profile(db, "p", None))


def _react(db, paper_key, action, recipient="r1", minutes_ago=60, issue="i1"):
    fl.init_db(db)
    now = datetime.now(timezone.utc)
    tid = f"{issue}-{recipient}-{paper_key}-{action}-{minutes_ago}"
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO feedback_tokens (tid, issue_id, profile_id, item_no, paper_key, recipient_hash, position, "
                    "created_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (tid, issue, "p", "P1", paper_key, recipient, 1, now.isoformat(), 2_000_000_000))
        con.execute("INSERT INTO feedback_events (event_id, tid, action, received_at, status, imported_at) VALUES (?,?,?,?,?,?)",
                    (tid, tid, action, (now - timedelta(minutes=minutes_ago)).isoformat(), fl.STATUS_VALID, now.isoformat()))


@pytest.fixture
def world(tmp_path, monkeypatch):
    db = tmp_path / "a.db"
    _profile(db)
    _scan(db, monkeypatch, HITS + DROPPED)
    _react(db, "h1", "more")
    _react(db, "h2", "useful")
    _react(db, "h3", "out")
    return db


def _rid(brief, title_word):
    return next(r["id"] for r in brief.data["reactions"] if title_word in r["title"])


class FakeRunner:
    def __init__(self, propose=None, judge=None):
        self._propose, self._judge = propose, judge
        self.calls = []

    def propose(self, brief_json):
        self.calls.append(("propose", json.loads(brief_json)))
        return self._propose(json.loads(brief_json)) if callable(self._propose) else (self._propose or {"actions": []})

    def judge(self, payload_json):
        payload = json.loads(payload_json)
        self.calls.append(("judge", payload))
        return self._judge(payload) if callable(self._judge) else self._judge


# ---------------------------------------------------------------- 브리프
def test_brief_carries_reactions_hits_and_missed_terms(world):
    """이 테스트가 잡는 것: 반응 논문 텍스트·반응 수가 브리프에 안 실리는 것, 핵심 키워드 적중 편수를 안 세는 것,
    탈락 논문 반복 용어(missed_terms)의 증거 id 가 브리프 논문과 이어지지 않는 것, 사용자 키워드 origin 을 잃는 것."""
    b = am.build_brief(world, "p")
    prof = b.data["profile"]
    assert prof["core"] == [{"id": "K1", "term": "robot manipulation", "weight": 1.0, "origin": "user", "hits_28d": 3}]
    assert prof["seeds"] == [{"term": "robot manipulation", "origin": "user"}]
    by_title = {r["title"]: (r["more"], r["useful"], r["out"]) for r in b.data["reactions"]}
    assert by_title == {HITS[0]["title"]: (1, 0, 0), HITS[1]["title"]: (0, 1, 0), HITS[2]["title"]: (0, 0, 1)}
    missed = {t["term"]: t for t in b.data["missed_terms"]}
    assert "spiking sensor" in missed
    assert all(e in b.texts and "spiking sensor" in b.texts[e] for e in missed["spiking sensor"]["evidence"])
    assert {t["term"] for t in b.data["liked_terms"]} == {"tactile skin"}
    assert b.has_signal()


def test_latest_reaction_per_recipient_wins(tmp_path):
    """이 테스트가 잡는 것: 같은 수신자가 마음을 바꾼 경우(out → more) 옛 반응까지 세는 것, 창 밖 반응을 세는 것."""
    rows = [{"issue_id": "i", "recipient_hash": "r", "paper_key": "k", "action": "out", "received_at": "2026-09-10T00:00:00+00:00"},
            {"issue_id": "i", "recipient_hash": "r", "paper_key": "k", "action": "more", "received_at": "2026-09-11T00:00:00+00:00"},
            {"issue_id": "i", "recipient_hash": "r2", "paper_key": "old", "action": "more", "received_at": "2026-07-01T00:00:00+00:00"}]
    assert am._latest_reactions(rows, "2026-09-01T00:00:00+00:00") == {"k": Counter({"more": 1})}


# ---------------------------------------------------------------- 검증
def _act(op, term, evidence, weight=None, reason="근거"):
    return {"op": op, "term": term, "weight": weight, "evidence": evidence, "reason": reason}


def test_validate_requires_real_evidence_containing_the_term(world):
    """이 테스트가 잡는 것: 증거 논문에 없는 용어를 키워드로 넣는 것, 지어낸 증거 id 를 받는 것, 가중치 범위·bool 을 안 보는 것,
    기존 키워드의 표기 변형·우산어를 새 키워드로 받는 것."""
    b = am.build_brief(world, "p")
    r1 = _rid(b, "tactile")
    ok, bad = am.validate([
        _act("add_keyword", "tactile skin", [r1], 0.8),
        _act("add_keyword", "quantum gravity", [r1], 0.8),
        _act("add_keyword", "made up", ["R99"], 0.8),
        _act("set_weight", "robot manipulation", ["K1"], 2.5),
        _act("set_weight", "robot manipulation", ["K1"], True),
        _act("add_keyword", "robot-manipulation", [r1], 0.8),
        _act("add_keyword", "deep learning", [r1], 0.8),
    ], b)
    assert [a["term"] for a in ok] == ["tactile skin"]
    assert [x["reason"] for x in bad] == ["term_not_in_evidence", "unknown_evidence", "bad_weight", "bad_weight",
                                          "already_known", "umbrella_term"]


def test_keywords_grow_and_rise_only_on_liked_reactions(world):
    """규칙 1(2026-09-15 실측 반영). 이 테스트가 잡는 것: 반응 없이 탈락 논문 반복어만으로 키워드를 늘리는 것(운영 DB 복사본에서
    'communication overhead' 가 그렇게 들어갔다), 좋다고 한 논문 없이 가중치를 올리는 것, 관심 밖 논문만 근거로 올리는 것."""
    b = am.build_brief(world, "p")
    x = b.data["missed_terms"][0]["evidence"]
    r3 = _rid(b, "medical")
    ok, bad = am.validate([_act("add_keyword", "spiking sensor", x, 0.5),
                           _act("set_weight", "robot manipulation", ["K1"], 1.3),
                           _act("set_weight", "robot manipulation", [r3], 1.3),
                           _act("set_weight", "robot manipulation", [_rid(b, "tactile")], 1.2)], b)
    assert [x["reason"] for x in bad] == ["no_liked_evidence", "no_liked_evidence", "no_liked_evidence"]
    assert [(a["op"], a["weight"]) for a in ok] == [("set_weight", 1.2)]


def test_mail_reason_drops_internal_evidence_ids():
    """이 테스트가 잡는 것: 'X3·X4의 제목·초록에…' 처럼 받는 사람에게 뜻 없는 브리프 id 가 메일에 실리는 것, 반대로 R2D2·X-ray 같은
    진짜 낱말을 지우는 것."""
    assert am.reader_reason("X3·X4의 제목·초록에 실제로 등장") == "제목·초록에 실제로 등장"
    assert am.reader_reason("관심 밖 논문(R12)과 K2 적중 0편") == "관심 밖 논문과 적중 0편"
    assert am.reader_reason("RGB-D 와 X-ray, R2D2") == "RGB-D 와 X-ray, R2D2"


def test_user_keywords_and_seeds_are_never_removed(world):
    """규칙 1. 이 테스트가 잡는 것: 사용자가 정한 키워드·검색어를 에이전트가 지우는 것. 하향은 되어야 한다."""
    b = am.build_brief(world, "p")
    ok, bad = am.validate([_act("remove_keyword", "robot manipulation", ["K1"]),
                           _act("remove_seed", "robot manipulation", ["K1"]),
                           _act("set_weight", "robot manipulation", ["K1"], 0.6)], b)
    assert [x["reason"] for x in bad] == ["user_keyword_protected", "user_seed_protected"]
    assert [(a["op"], a["weight"]) for a in ok] == [("set_weight", 0.6)]


def test_agent_keywords_can_be_removed_and_seeds_follow_order(world):
    """이 테스트가 잡는 것: 에이전트가 넣은 키워드를 못 지우는 것, 같은 목록 앞에서 추가한 키워드를 뒤의 add_seed 가 못 보는 것,
    핵심 키워드가 아닌 말을 검색어로 받는 것."""
    b = am.build_brief(world, "p")
    b.data["profile"]["core"].append({"id": "K2", "term": "old auto", "weight": 0.5, "origin": "agent", "hits_28d": 0})
    b.keyword_ids["K2"] = "old auto"
    r1 = _rid(b, "tactile")
    ok, bad = am.validate([_act("add_seed", "tactile skin", [r1]),
                           _act("add_keyword", "tactile skin", [r1], 0.7),
                           _act("add_seed", "tactile skin", [r1]),
                           _act("remove_keyword", "old auto", ["K2"])], b)
    assert [x["reason"] for x in bad] == ["seed_not_core"]
    assert [(a["op"], a["term"]) for a in ok] == [("add_keyword", "tactile skin"), ("add_seed", "tactile skin"),
                                                  ("remove_keyword", "old auto")]


def test_exclude_needs_out_evidence_and_must_not_kill_liked_or_core(world):
    """이 테스트가 잡는 것: 관심 밖 반응 없이 제외어를 넣는 것, 관심 밖 논문 한 편만으로 제외어를 넣는 것(제외된 논문은 다시 안
    보여 되돌릴 반응이 안 생긴다), 좋다고 한 논문에도 있는 말을 제외하는 것, 핵심 키워드와 겹치는 제외어를 받는 것."""
    b = am.build_brief(world, "p")
    r1, r3 = _rid(b, "tactile"), _rid(b, "medical")
    assert [x["reason"] for x in am.validate([_act("add_exclude", "medical imaging", [r3])], b)[1]] == ["single_out_evidence"]
    b.reactions["R9"], b.texts["R9"] = Counter({"out": 1}), "another medical imaging paper"   # 관심 밖 두 번째 논문
    ok, bad = am.validate([_act("add_exclude", "medical imaging", [r3]),
                           _act("add_exclude", "tactile skin", [r1]),
                           _act("add_exclude", "robot", [r3]),
                           _act("add_exclude", "segmentation", [r1])], b)
    assert [a["term"] for a in ok] == ["medical imaging"]
    assert [x["reason"] for x in bad] == ["no_out_evidence", "overlaps_core", "no_out_evidence"]
    b.reactions[r1] = Counter({"out": 1})          # tactile skin: out 근거는 생겼지만 h2(useful)에도 있다
    b.reactions["R8"], b.texts["R8"] = Counter({"out": 1}), "tactile skin again"
    assert [x["reason"] for x in am.validate([_act("add_exclude", "tactile skin", [r1])], b)[1]] == ["hits_liked_paper"]


# ---------------------------------------------------------------- 한 주 실행
def test_codex_final_list_wins_and_change_is_an_agent_revision(world):
    """사용자 결정: 의견이 갈리면 Codex. 이 테스트가 잡는 것: Claude 제안을 그대로 적용하는 것(Codex 가 뺀 것까지),
    revision origin 이 agent 가 아닌 것, 추가 키워드 provenance 가 user 로 기록되는 것, manual 프로필이 daily 로 바뀌는 것."""
    b = am.build_brief(world, "p")
    r1, r3 = _rid(b, "tactile"), _rid(b, "medical")
    claude = {"actions": [_act("add_keyword", "tactile skin", [r1], 1.2), _act("add_exclude", "medical imaging", [r3])]}
    codex = {"reviews": [{"index": 0, "verdict": "modify", "reason": "한 반응으로 1.2 는 크다"},
                         {"index": 1, "verdict": "reject", "reason": "한 편뿐"}],
             "actions": [_act("add_keyword", "tactile skin", [r1], 0.7, "좋다고 한 두 편에 반복")]}
    runner = FakeRunner(claude, codex)
    res = am.run_profile(world, "p", runner)
    assert res["status"] == "applied"
    prof = rp.get_profile(world, "p")
    assert prof["core_weights"] == {"robot manipulation": 1.0, "tactile skin": 0.7}
    assert prof["exclude"] == ["banned"]
    assert [c[0] for c in runner.calls] == ["propose", "judge"]
    assert runner.calls[1][1]["proposal"] == claude          # Codex 는 Claude 제안을 보고 판정한다
    with sqlite3.connect(world) as con:
        origin, note = con.execute("SELECT origin, note FROM profile_revisions WHERE profile_id='p' ORDER BY revision DESC").fetchone()
        freq = con.execute("SELECT schedule_frequency FROM profiles WHERE profile_id='p'").fetchone()[0]
    assert origin == "agent" and note.startswith("weekly-agent ") and freq == "manual"
    assert rp.keyword_provenance(world, "p")["tactile skin"]["origin"] == "agent"


def test_report_is_shown_once_after_delivery(world):
    """이 테스트가 잡는 것: 바뀐 내용이 메일에 안 실리는 것, 같은 보고가 매일 반복되는 것."""
    b = am.build_brief(world, "p")
    r1 = _rid(b, "tactile")
    am.run_profile(world, "p", FakeRunner(None, {"reviews": [], "actions": [_act("add_keyword", "tactile skin", [r1], 0.7, "반복 용어")]}))
    lines, keys = am.pending_report(world, "p")
    assert any("키워드 추가: tactile skin 0.7 — 반복 용어" in ln for ln in lines)
    am.mark_reported(world, keys)
    assert am.pending_report(world, "p") == ([], [])


def test_failure_skips_the_week_and_leaves_profile_untouched(world):
    """규칙 6. 이 테스트가 잡는 것: Codex 실패 때 Claude 제안만으로 적용하는 것, 실패를 예외로 올려 다른 프로필을 막는 것,
    실패 사유가 메일에 안 실리는 것."""
    before = rp.current_revision(world, "p")

    def boom(_payload):
        raise am.StepError("timeout", "codex 600s")
    b = am.build_brief(world, "p")
    claude = {"actions": [_act("add_keyword", "tactile skin", [_rid(b, "tactile")], 0.7)]}
    res = am.run_profile(world, "p", FakeRunner(claude, boom))
    assert res == {"profile_id": "p", "week": am.week_of(datetime.now(timezone.utc)), "status": "failed", "error": "timeout"}
    assert rp.current_revision(world, "p") == before
    lines, _ = am.pending_report(world, "p")
    assert lines and "건너뛰었습니다 — 시간 초과" in lines[0]


def test_secret_like_output_discards_the_run(world):
    """규칙 5. 이 테스트가 잡는 것: 시크릿처럼 생긴 모델 출력을 DB 에 저장하거나 적용하는 것."""
    b = am.build_brief(world, "p")
    leak = {"actions": [_act("add_keyword", "tactile skin", [_rid(b, "tactile")], 0.7, "GOOGLE_API_KEY=AIzaSyA1234567890abcdefghijk")]}
    res = am.run_profile(world, "p", FakeRunner(leak, {"reviews": [], "actions": []}))
    assert res["error"] == "secret_like_output"
    with sqlite3.connect(world) as con:
        assert con.execute("SELECT proposal_json FROM agent_runs").fetchone()[0] is None
    assert not am.secret_like("vision-language-action model for robot manipulation")


def test_no_signal_skips_models_and_week_runs_once(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 근거가 없는 주에도 구독 한도를 쓰는 것, 같은 주에 두 번 도는 것, --force 가 안 먹는 것."""
    db = tmp_path / "e.db"
    _profile(db)
    runner = FakeRunner({"actions": []}, {"reviews": [], "actions": []})
    assert am.run_profile(db, "p", runner)["status"] == "skipped_no_signal"
    assert runner.calls == []
    assert am.run_profile(db, "p", runner)["status"] == "already_ran"
    assert am.run_profile(db, "p", runner, force=True)["status"] == "skipped_no_signal"


def test_revision_change_during_the_run_is_not_overwritten(world):
    """이 테스트가 잡는 것: 모델이 도는 사이 사람이 프로필을 고쳤는데 옛 상태 기준 변경으로 덮어쓰는 것."""
    b = am.build_brief(world, "p")
    r1 = _rid(b, "tactile")

    def judge(_payload):
        rp.create_profile(world, "p", "로봇", core_topics=["robot manipulation", "human edit"], schedule_frequency="manual")
        return {"reviews": [], "actions": [_act("add_keyword", "tactile skin", [r1], 0.7)]}
    res = am.run_profile(world, "p", FakeRunner(None, judge))
    assert res["error"] == "revision_changed"
    assert sorted(rp.get_profile(world, "p")["core_topics"]) == ["human edit", "robot manipulation"]


# ---------------------------------------------------------------- 헤드리스 보안
def test_cli_children_get_no_secrets_and_no_tools(monkeypatch):
    """규칙 4·5. 이 테스트가 잡는 것: CLI 자식 프로세스에 시크릿 환경변수가 넘어가는 것, Claude 의 도구 차단(--tools "")이나
    Codex 의 셸 도구 차단(--disable shell_tool)이 빠지는 것, 브리프를 stdin 이 아닌 곳으로 넘기는 것."""
    monkeypatch.setenv("GOOGLE_API_KEY", "x")
    monkeypatch.setenv("FEEDBACK_HMAC_SECRET", "y")
    env = am._cli_env()
    assert "GOOGLE_API_KEY" not in env and "FEEDBACK_HMAC_SECRET" not in env and "HOME" in env
    seen = []

    def fake_run(argv, stdin_text, timeout, cwd):
        seen.append((argv, stdin_text))
        if "exec" in argv:
            from pathlib import Path
            Path(argv[argv.index("-o") + 1]).write_text('{"reviews": [], "actions": []}')
            return 0, "", ""
        return 0, json.dumps({"is_error": False, "structured_output": {"actions": []}}), ""
    monkeypatch.setattr(am, "_run", fake_run)
    monkeypatch.setattr(am.shutil, "which", lambda name, path=None: f"/bin/{name}")
    runner = am.HeadlessRunner()
    assert runner.propose('{"brief": 1}') == {"actions": []}
    assert runner.judge('{"payload": 1}') == {"reviews": [], "actions": []}
    claude_argv, claude_in = seen[0]
    i = claude_argv.index("--tools")
    assert claude_argv[i + 1] == "" and "--strict-mcp-config" in claude_argv and claude_in == '{"brief": 1}'
    codex_argv, codex_in = seen[1]
    pairs = list(zip(codex_argv, codex_argv[1:]))
    assert ("--disable", "shell_tool") in pairs and ("-s", "read-only") in pairs and codex_in == '{"payload": 1}'
    assert "--ignore-user-config" in codex_argv and "--ignore-rules" in codex_argv


def test_timeout_kills_the_whole_process_group(tmp_path):
    """이 테스트가 잡는 것: 시간 상한이 없거나, 상한 뒤에도 CLI 가 띄운 자식이 남아 cron 이 끝나지 않는 것."""
    marker = tmp_path / "child_alive"
    start = time.monotonic()
    with pytest.raises(am.StepError) as e:
        am._run(["bash", "-c", f"(sleep 3; touch {marker}) & sleep 30"], "", 1, str(tmp_path))
    assert e.value.code == "timeout" and time.monotonic() - start < 10
    time.sleep(3.5)
    assert not marker.exists()


# ---------------------------------------------------------------- 메일
def test_the_old_agent_report_never_renders_again(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 옛 `agent_report` 절을 메일에 다시 붙이는 것(2026-09-20).

    주간 관리가 월요일 체인으로 옮겨 오면서(§8-163) 같은 변경이 `지난 7일 검색 기준 변화` 절과
    **한 메일에 두 번** 실리게 됐다. 게다가 옛 경로는 `reported_at` 기준이라 월요일 발송이 한 명에게라도
    실패하면 화요일 메일에 다시 붙었다. 에이전트 변경을 메일에 싣는 곳은 이제 한 곳뿐이다."""
    import server, storage
    store = tmp_path / "s.db"
    storage.init_storage(store)
    for mod in (server, storage):
        monkeypatch.setattr(mod, "DB_PATH", store)
    result = {"papers": [], "candidates_found": 0,
              "agent_report": ["· 2026-W38 변경 (revision 4 → 5, 되돌릴 수 있음)"]}
    text = digest.generate_digest(result, "P")
    html = digest.generate_digest_html(result, "P")
    assert digest.AGENT_REPORT_TITLE not in text
    assert digest.AGENT_REPORT_TITLE not in html
    assert "2026-W38" not in text and "2026-W38" not in html


def test_agent_reason_is_escaped_in_the_change_section(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 모델이 쓴 사유를 HTML 이스케이프 없이 싣는 것. 사유는 LLM 출력이라
    비신뢰 문자열로 다룬다 — 옛 `agent_report` 절이 지키던 계약을 새 절이 이어받는다."""
    import server, storage
    store = tmp_path / "s.db"
    storage.init_storage(store)
    for mod in (server, storage):
        monkeypatch.setattr(mod, "DB_PATH", store)
    changes = {"window": ("2026-09-14T00:00:00+00:00", "2026-09-21T00:00:00+00:00"), "days": 7,
               "weights": [], "added": [], "removed": [], "by_actor": {}, "reactions_used": 0,
               "agent": {"applied": [{"op": "add_keyword", "term": "tactile skin", "weight": 0.7,
                                      "basis": "feedback", "reason": "<b>반복</b>"}],
                         "impact": None, "shadow": None, "failed": None}}
    result = {"papers": [], "candidates_found": 0, "profile_changes": changes}
    html = digest.generate_digest_html(result, "P")
    assert "&lt;b&gt;반복&lt;/b&gt;" in html and "<b>반복</b>" not in html
    assert "[반응 근거]" in digest.generate_digest(result, "P")   # 근거 표시도 새 절이 이어받는다


def test_deliver_no_longer_attaches_the_old_report(world, monkeypatch, tmp_path):
    """이 테스트가 잡는 것: 발송 루프가 `pending_report` 를 다시 읽어 view 에 붙이는 것.
    `pending_report` 자체는 남아 있지만(agent_runs 상태 해석의 기준 구현) **메일 경로에는 없다**."""
    import email_delivery, server, storage
    store = tmp_path / "s.db"
    storage.init_storage(store)
    for mod in (server, storage):
        monkeypatch.setattr(mod, "DB_PATH", store)
    b = am.build_brief(world, "p")
    am.run_profile(world, "p", FakeRunner(None, {"reviews": [], "actions": [
        _act("add_keyword", "tactile skin", [_rid(b, "tactile")], 0.7, "반복")]}))
    rp.add_recipient(world, "p", "alice@x.com")
    sent = []
    monkeypatch.setattr(email_delivery, "send_digest_email", lambda text, subject, to, html: sent.append(text))
    rps._deliver(world, "p", {"papers": [], "candidates_found": 0}, "")
    assert sent and "tactile skin" not in sent[0]
    assert am.pending_report(world, "p")[1]      # 표시도 안 한다 — 메일이 읽지 않으니 전진시킬 이유가 없다


def test_agent_actor_keeps_agent_provenance(tmp_path):
    """이 테스트가 잡는 것: PROVENANCE_ORIGINS 에 agent 가 빠져 자동 키워드가 user 로 기록되는 것(삭제 보호·anchor 오염)."""
    db = tmp_path / "v.db"
    rp.create_profile(db, "p", "P", ["alpha"])
    rp.create_profile(db, "p", "P", ["alpha", "beta"], origin="agent")
    prov = rp.keyword_provenance(db, "p")
    assert prov["alpha"]["origin"] == "user" and prov["beta"]["origin"] == "agent"


# ---------------------------------------------------------------- 외부 검토(2026-09-15) 반영분
def test_underscore_and_hyphen_variants_are_the_same_keyword(world):
    """이 테스트가 잡는 것: 채점기는 `_`·`/`·하이픈을 같은 구분자로 보는데 검증은 하이픈만 풀어, `robot_manipulation` 을 새 키워드로
    넣거나 핵심 키워드와 같은 말을 제외어로 받는 것."""
    assert am._same("robot_manipulation", "Robot-Manipulations") and am._same("vision/language model", "vision language models")
    b = am.build_brief(world, "p")
    b.texts[_rid(b, "medical")] += " robot_manipulation"
    ok, bad = am.validate([_act("add_keyword", "robot_manipulation", [_rid(b, "tactile")], 0.8),
                           _act("add_exclude", "robot_manipulation", [_rid(b, "medical")]),
                           _act("add_exclude", "manipulation", [_rid(b, "medical")])], b)
    assert ok == [] and [x["reason"] for x in bad] == ["already_known", "already_known", "overlaps_core"]


def test_exclude_protects_liked_papers_outside_the_brief_cap(world, monkeypatch):
    """이 테스트가 잡는 것: 모델에 보낸 반응 목록(상한 40편)만 보고 제외어를 검사해, 목록 밖의 좋아요 논문을 제외어로 죽이는 것."""
    _react(world, "h3", "out", recipient="r2")                 # h3 반응 2개 → 상한 1 이면 h3 만 브리프에 들어간다
    monkeypatch.setattr(am, "MAX_REACTED_PAPERS", 1)
    b = am.build_brief(world, "p")
    assert [r["title"] for r in b.data["reactions"]] == [HITS[2]["title"]] and len(b.liked_texts) == 2
    r3 = b.data["reactions"][0]["id"]
    b.texts[r3] += " tactile skin"
    b.reactions["R8"], b.texts["R8"] = Counter({"out": 1}), "tactile skin again"
    ok, bad = am.validate([_act("add_exclude", "tactile skin", [r3])], b)
    assert ok == [] and [x["reason"] for x in bad] == ["hits_liked_paper"]


def test_unknown_or_missing_provenance_is_protected(world, monkeypatch):
    """규칙 1 fail-closed. 이 테스트가 잡는 것: provenance 가 NULL·대문자·이벤트 없음일 때 사용자 키워드를 자동 키워드로 보고 지우는 것.
    (이벤트 표는 append-only 트리거라 값을 바꿀 수 없어 active_generations 를 대신 흉내 낸다.)"""
    assert am._origin(None) == am._origin("USER") == am._origin("weird") == "user" and am._origin("agent") == "agent"
    for odd in (None, "USER"):
        monkeypatch.setattr(rp, "active_generations", lambda db, pid, odd=odd: {
            ("robot manipulation", "core"): {"provenance_origin": odd}, ("robot manipulation", "s2_seed"): {"provenance_origin": odd}})
        b = am.build_brief(world, "p")
        assert b.data["profile"]["core"][0]["origin"] == "user"
        bad = am.validate([_act("remove_keyword", "robot manipulation", ["K1"]), _act("remove_seed", "robot manipulation", ["K1"])], b)[1]
        assert [x["reason"] for x in bad] == ["user_keyword_protected", "user_seed_protected"]
    monkeypatch.setattr(rp, "active_generations", lambda db, pid: {})
    monkeypatch.setattr(rp, "keyword_provenance", lambda db, pid: {"robot manipulation": {"origin": "agent"}})
    assert am.build_brief(world, "p").data["profile"]["core"][0]["origin"] == "user"   # 이벤트 없는 옛 프로필은 추정하지 않는다


def test_no_reactions_with_trend_calls_both_models(tmp_path, monkeypatch):
    """동향 자료가 있는 반응 없는 프로필에서 모델 호출을 막으면 실패한다."""
    db = tmp_path / "m.db"
    _profile(db)
    _scan(db, monkeypatch, HITS + DROPPED)
    b = am.build_brief(db, "p")
    assert b.data["missed_terms"] and not b.data["reactions"] and b.has_signal()
    runner = FakeRunner({"actions": []}, {"reviews": [], "actions": []})
    assert am.run_profile(db, "p", runner)["status"] == "no_change"
    assert [c[0] for c in runner.calls] == ["propose", "judge"]


def test_record_failure_after_apply_is_still_reported_as_a_change(world, monkeypatch):
    """이 테스트가 잡는 것: revision 을 쓴 뒤 실행 기록이 실패하면 run 이 failed 로 남아 메일에 "키워드는 그대로"라고 거짓 보고하는 것."""
    b = am.build_brief(world, "p")
    real_finish, boom = am._finish, {"left": 1}

    def flaky(db, pid, week, **cols):
        if cols.get("status") == "applied" and "error" not in cols and boom["left"]:
            boom["left"] -= 1
            raise sqlite3.OperationalError("database is locked")
        return real_finish(db, pid, week, **cols)
    monkeypatch.setattr(am, "_finish", flaky)
    res = am.run_profile(world, "p", FakeRunner(None, {"reviews": [], "actions": [_act("add_keyword", "tactile skin", [_rid(b, "tactile")], 0.7, "반복")]}))
    assert res["status"] == "applied" and res["revision"] == rp.current_revision(world, "p")
    lines, _ = am.pending_report(world, "p")
    assert any("키워드 추가: tactile skin" in ln for ln in lines) and not any("그대로" in ln for ln in lines)


def test_interrupted_applying_row_is_resolved_by_run_tag(world):
    """이 테스트가 잡는 것: 적용 도중 프로세스가 죽어 'applying' 으로 남은 행을 보고에서 빠뜨리거나, 적용되지 않았는데 변경으로 싣는 것."""
    week = am.week_of(datetime.now(timezone.utc))
    am.init_db(world)
    applied = json.dumps([{"op": "add_keyword", "term": "x", "weight": 0.5, "evidence": [], "reason": ""}])
    with sqlite3.connect(world) as con:
        con.execute("INSERT INTO agent_runs (profile_id, week, started_at, status, run_tag, base_revision, applied_json) "
                    "VALUES ('p', ?, ?, 'applying', 'never-written', 1, ?)", (week, datetime.now(timezone.utc).isoformat(), applied))
    lines, keys = am.pending_report(world, "p")
    assert keys == [("p", week)] and "적용 중 중단" in lines[0]


def test_stale_running_row_is_retaken_but_fresh_one_is_not(tmp_path):
    """이 테스트가 잡는 것: 크래시로 남은 'running' 이 그 주 재시도를 영영 막는 것, 반대로 지금 돌고 있는 실행을 겹쳐 시작하는 것."""
    db = tmp_path / "s.db"
    _profile(db)
    am.init_db(db)
    now = datetime.now(timezone.utc)
    week = am.week_of(now)
    runner = FakeRunner()
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO agent_runs (profile_id, week, started_at, status) VALUES ('p', ?, ?, 'running')",
                    (week, (now - timedelta(hours=1)).isoformat()))
    assert am.run_profile(db, "p", runner, now=now)["status"] == "already_ran"
    with sqlite3.connect(db) as con:
        con.execute("UPDATE agent_runs SET started_at=?", ((now - timedelta(hours=4)).isoformat(),))
    assert am.run_profile(db, "p", runner, now=now)["status"] == "skipped_no_signal"


def test_old_unreported_runs_expire(world):
    """이 테스트가 잡는 것: 계속 거절되는 수신자 때문에 한 번도 '실림' 표시가 안 된 보고가 몇 주씩 매일 반복되는 것."""
    b = am.build_brief(world, "p")
    am.run_profile(world, "p", FakeRunner(None, {"reviews": [], "actions": [_act("add_keyword", "tactile skin", [_rid(b, "tactile")], 0.7)]}))
    assert am.pending_report(world, "p")[1]
    assert am.pending_report(world, "p", now=datetime.now(timezone.utc) + timedelta(days=15)) == ([], [])


def test_secret_detector_catches_env_style_names_but_not_long_terms():
    """이 테스트가 잡는 것: AWS 키 이름·키 값 형식을 놓치는 것, 하이픈으로 이은 긴 연구 용어나 한국어 사유를 시크릿으로 오탐해 그 주를 버리는 것."""
    for leak in ("AWS_SECRET_ACCESS_KEY=abc123", "AKIAIOSFODNN7EXAMPLE", "token: abc", "ghp_" + "a" * 36,
                 "d41d8cd98f00b204e9800998ecf8427e", "Zx9Qm2Lp7Rt4Vw8Ys1Nb6Kc3Hd5Jf0Ge"):
        assert am.secret_like(leak), leak
    for fine in ("vision-language-action-v2-for-robot-manipulation", "MVTec-AD 결함 검출 논문 두 편에 반복",
                 "key point detection", "RT-2 와 OpenVLA-7B 비교"):
        assert not am.secret_like(fine), fine


# ── 적용 직전 반사실 재채점 기록(2026-09-17, 옛 profile_advisor 에서 옮겨 연결) ─────────────────────────────────
def test_apply_records_a_counterfactual_impact_but_never_blocks(world, monkeypatch):
    """이 테스트가 잡는 것: 적용 전에 profile_impact 를 부르지 않는 것(특허 A 실시예·논문 섀도 평가 근거가 사라진다), 재채점 요약이
    agent_runs.impact_json·impact_analyses 에 안 남는 것, 재채점이 죽었을 때 적용까지 막는 것(기록 단계가 적용을 막으면 규칙 6 위반),
    보고서에 재채점 줄이 안 붙는 것."""
    import profile_impact
    b = am.build_brief(world, "p")
    r1 = _rid(b, "tactile")
    claude = {"actions": [_act("add_keyword", "tactile skin", [r1], 0.7)]}
    codex = {"reviews": [], "actions": [_act("add_keyword", "tactile skin", [r1], 0.7, "좋다고 한 두 편에 반복")]}
    res = am.run_profile(world, "p", FakeRunner(claude, codex))
    assert res["status"] == "applied"
    with sqlite3.connect(world) as con:
        raw = con.execute("SELECT impact_json FROM agent_runs WHERE profile_id='p' ORDER BY started_at DESC").fetchone()[0]
        n_analyses = con.execute("SELECT count(*) FROM impact_analyses WHERE profile_id='p'").fetchone()[0]
    imp = json.loads(raw)
    assert imp["status"] == "ok" and n_analyses == 1 and imp["snapshot_papers"] > 0
    assert set(imp) >= {"analysis_id", "gained", "lost", "topk_changed", "exclude_risk", "gate_status"}
    lines, _keys = am.pending_report(world, "p")
    assert any("지난 4주 관측" in l and "재채점 기록, 차단 없음" in l for l in lines)
    # 재채점이 죽어도 적용은 된다
    monkeypatch.setattr(profile_impact, "snapshot", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    res2 = am.run_profile(world, "p", FakeRunner(claude, {"reviews": [], "actions": [_act("set_weight", "tactile skin", [r1], 0.9, "더")]}),
                          now=am._now() + timedelta(days=7), force=True)
    assert res2["status"] == "applied" and rp.get_profile(world, "p")["core_weights"]["tactile skin"] == 0.9
    with sqlite3.connect(world) as con:
        raw2 = con.execute("SELECT impact_json FROM agent_runs WHERE profile_id='p' ORDER BY started_at DESC").fetchone()[0]
    assert json.loads(raw2)["status"].startswith("error:")


def test_projected_profile_matches_what_apply_writes(world):
    """이 테스트가 잡는 것: 재채점이 보는 '뒤' 프로필과 실제로 쓰는 프로필이 달라지는 것(둘이 다른 코드를 타면 재채점이 거짓말을 한다)."""
    before = rp.get_profile(world, "p")
    actions = [_act("add_keyword", "tactile skin", ["x"], 0.7), _act("add_exclude", "medical imaging", ["y"]), _act("add_seed", "tactile skin", ["x"])]
    after = am.projected_profile(before, actions)
    rev = am.apply(world, "p", actions, rp.current_revision(world, "p"), "t")
    written = rp.get_profile(world, "p")
    assert rev and written["core_topics"] == after["core_topics"] and written["core_weights"] == after["core_weights"]
    assert written["exclude"] == after["exclude"] and sorted(written["s2_seeds"]) == sorted(after["s2_seeds"])


def test_shadow_search_runs_only_for_search_term_changes_and_is_recorded(world, monkeypatch):
    """2026-09-17 연결. 이 테스트가 잡는 것: 검색어(시드·arXiv 질의)가 바뀌는 변경에서 격리 검색을 안 부르는 것, 결과가 agent_runs.impact_json 의
    shadow 와 shadow_runs 에 안 남는 것, 검색 실패가 적용을 막는 것, 보고서에 격리 검색 줄이 안 붙는 것. conftest 는 shadow_of 를 끄므로 여기서 되살린다."""
    import shadow_search, s2_delta, find_new_papers
    monkeypatch.setattr(am, "shadow_of", am._real_shadow_of)      # conftest 스텁 해제 — 원 함수는 conftest 가 보관해 둔다
    calls = []

    async def fake_s2(client, keywords, since, until, limit=100, budget_s=300.0):
        calls.append(("s2", tuple(keywords)))
        return {"papers": [{"arxiv_id": "s1", "title": "tactile skin robot manipulation", "abstract": "", "published": "2026-09-10T00:00:00Z",
                            "source": "s2", "s2_seeds": list(keywords)}], "status": "done"}

    async def fake_arxiv(client, query, since, **kw):
        calls.append(("arxiv", query))
        return {"papers": [], "status": "done", "until": datetime.now(timezone.utc).isoformat(), "query": query}
    monkeypatch.setattr(s2_delta, "find_new_papers_since", fake_s2)
    monkeypatch.setattr(find_new_papers, "find_new_papers_since", fake_arxiv)
    b = am.build_brief(world, "p")
    r1 = _rid(b, "tactile")
    # 시드는 핵심 키워드여야 받는다(seed_not_core) — 키워드 추가와 시드 추가를 한 주에 같이 낸다
    claude = {"actions": [_act("add_keyword", "tactile skin", [r1], 0.7), _act("add_seed", "tactile skin", [r1])]}
    codex = {"reviews": [], "actions": [_act("add_keyword", "tactile skin", [r1], 0.7, "좋다고 한 두 편에 반복"),
                                         _act("add_seed", "tactile skin", [r1], None, "같은 근거")]}
    res = am.run_profile(world, "p", FakeRunner(claude, codex))
    assert res["status"] == "applied" and any(c[0] == "s2" for c in calls), "시드 추가는 격리 검색을 탄다"
    with sqlite3.connect(world) as con:
        imp = json.loads(con.execute("SELECT impact_json FROM agent_runs WHERE profile_id='p' ORDER BY started_at DESC").fetchone()[0])
        n_shadow = con.execute("SELECT count(*) FROM shadow_runs WHERE profile_id='p'").fetchone()[0]
    assert imp["shadow"]["status"] == "done" and imp["shadow"]["seeds_added"] == ["tactile skin"] and n_shadow == 1
    lines, _ = am.pending_report(world, "p")
    assert any("격리 검색으로 재 보면" in l for l in lines)
    # 검색이 죽어도 적용은 된다
    async def boom(*a, **k):
        raise RuntimeError("S2 down")
    monkeypatch.setattr(s2_delta, "find_new_papers_since", boom)
    res2 = am.run_profile(world, "p", FakeRunner(claude, {"reviews": [], "actions": [_act("add_exclude", "medical imaging", [_rid(b, "medical"), _rid(b, "medical")])]}),
                          now=am._now() + timedelta(days=7), force=True)
    assert res2["status"] in ("applied", "no_change")


def test_trend_only_change_revision_rollback_and_mail(tmp_path, monkeypatch):
    """반응 없는 동향 제안·판정·revision 적용·되돌리기·메일 근거 표시 중 하나라도 끊으면 실패한다."""
    db = tmp_path / 'trend.db'
    _profile(db)
    _scan(db, monkeypatch, HITS + DROPPED)
    base = rp.current_revision(db, 'p')

    def propose(data):
        assert not data['reactions']
        ev = next(r['id'] for r in data['trend']['records'] if 'tactile skin' in r['text'])
        return {'actions': [_act('add_keyword', 'tactile skin', [ev], 0.7, 'related_direction')]}

    runner = FakeRunner(propose, lambda p: {'reviews': [], 'actions': p['proposal']['actions']})
    result = am.run_profile(db, 'p', runner)
    assert result['status'] == 'applied' and result['revision'] > base
    assert rp.get_profile(db, 'p')['core_weights']['tactile skin'] == 0.7
    with sqlite3.connect(db) as con:
        action = json.loads(con.execute('SELECT applied_json FROM agent_runs').fetchone()[0])[0]
        assert con.execute('SELECT origin FROM profile_revisions ORDER BY revision DESC LIMIT 1').fetchone()[0] == 'agent'
    assert action['basis'] == 'trend' and action['source_evidence']
    assert action['reason_code'] == 'related_direction'
    lines, _ = am.pending_report(db, 'p')
    assert any('동향 근거' in line and '연구 방향' in line for line in lines)
    rp.rollback_to_revision(db, 'p', base, '시험 되돌리기')
    assert rp.get_profile(db, 'p')['core_topics'] == ['robot manipulation']


def test_trend_guards_grounding_user_keyword_and_claims(world):
    """동향 경로의 문자열 대조·사용자 삭제 보호·숫자 주장 차단을 빼면 실패한다."""
    b = am.build_brief(world, 'p')
    tid = next(r['id'] for r in b.data['trend']['records'] if 'tactile skin' in r['text'])
    kid = next(r['id'] for r in b.data['trend']['records'] if 'robot manipulation' in r['text'])
    cases = [(_act('add_keyword', 'invented molecule', [tid], 0.7, 'related_direction'), 'term_not_in_evidence'),
             (_act('remove_keyword', 'robot manipulation', [kid], reason='reduce_noise'), 'user_keyword_protected')]
    for reason in ('999편 증가', '지난주 세 배', '2주 연속', 'nine papers', 'related_direction 3', 'related_direction' + ' ' * 300 + '99편'):
        cases.append((_act('add_keyword', 'tactile skin', [tid], 0.7, reason), 'unverified_trend_claim'))
    a = _act('add_keyword', 'tactile skin', [tid], 0.7, 'related_direction')
    cases.append(({**a, 'count': 123}, 'unverified_trend_claim'))
    for action, error in cases:
        ok, bad = am.validate([action], b)
        assert not ok and bad[0]['reason'] == error
    ok, bad = am.validate([_act('set_weight', 'robot manipulation', [kid], 1.2, 'priority_alignment')], b)
    assert not bad and ok[0]['weight'] == 1.2


def test_trend_sources_match_existing_python_collectors(world):
    """기존 집계 값을 바꾸거나 comparable·저장 서술·입력 텍스트를 누락하면 실패한다."""
    import narrative_store
    import observation_signals
    import trend_report
    now = am._now()
    narrative_store.save(world, 'p', 'daily', 'tactile skin은 센서 연구와 연결된다.', moment=now)
    now = am._now()
    b = am.build_brief(world, 'p', now)
    records = b.data['trend']['records']
    by_kind = {r['kind']: r for r in records}
    prof = rp.get_profile(world, 'p')
    assert by_kind['window_movement']['data'] == trend_report.window_movement(world, prof, days=7, end=now)
    assert by_kind['window_movement']['data']['comparable'] is False
    assert '주간 동향 리뷰' in by_kind['weekly_review']['data']['report']
    assert by_kind['narrative']['data']['unverified_interpretation'] is True
    assert all(b.texts[r['id']] == r['text'] for r in records)
    assert by_kind.get('reserve_terms', {}).get('data') == observation_signals.reserve_terms(world, 'p')


def test_prompts_have_no_feedback_only_contract():
    """실제 CLI 프롬프트에 옛 반응 필수 공통 문안을 다시 붙이면 실패한다."""
    for name in ('agent_propose_v1.md', 'agent_judge_v1.md'):
        prompt = am._prompt(name)
        assert '반응이 없어도' in prompt and 'comparable=false' in prompt
        assert '반응이 없는 주에는 키워드를 늘리지 않는다' not in prompt
        assert 'related_direction' in prompt
