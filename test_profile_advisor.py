"""D단계(2026-09-11) — 제한된 LLM 프로필 개선기 회귀. docs/ASTRA_PLAN_2026-09-10.md §8·§9.

네트워크는 가짜 클라이언트로 대체하고 **운영 함수를 그대로 부른다.** 각 결함을
주입하면 실패하는지 돌연변이로 확인했다.
"""
import asyncio
import json
import sqlite3
from datetime import datetime, timezone

import pytest

import profile_advisor as adv
import profile_impact as pi
import research_profile as rp
import summarize_engine as engine

START, END = datetime(2026, 9, 1, tzinfo=timezone.utc), datetime(2026, 9, 30, tzinfo=timezone.utc)
NOW = datetime(2026, 9, 11, 3, 0, tzinfo=timezone.utc)


class FakeResp:
    def __init__(self, status: int, payload: dict | None = None, text: str | None = None):
        self.status_code = status
        self._payload = payload
        self.text = text if text is not None else (json.dumps(payload) if payload else "")

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def gemini_ok(text: str, model: str = "gemini-flash-latest-001") -> FakeResp:
    return FakeResp(200, {"candidates": [{"content": {"parts": [{"text": text}]}}],
                          "modelVersion": model, "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}})


class FakeClient:
    """post 마다 미리 정한 응답을 순서대로 돌려주고, 실제 payload 를 기록한다."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        if not self.responses:
            raise AssertionError("예상보다 많은 HTTP 요청")
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "d.db"
    rp.create_profile(path, "p", "이름", core_topics=["target term", "trend term"],
                      core_weights={"target term": 1.0, "trend term": 0.6},
                      target_domain=["factory"], exclude=["banned"], s2_seeds=["target term"], max_items=2)
    monkeypatch.setattr(engine, "ENV", {"GOOGLE_API_KEY": "k1", "GOOGLE_API_KEY2": "k2"})
    monkeypatch.setattr(engine, "_gemini_key_cursor", 0)
    monkeypatch.setattr(engine, "_gemini_model_cursor", 0)
    return path


def _observe(db, key, title, abstract, published="2026-09-09T00:00:00Z"):
    prof = rp.get_profile(db, "p")
    scan = rp.begin_scan(db, "p", prof, started_at="2026-09-09T05:00:00+00:00")
    p = {"arxiv_id": key, "title": title, "abstract": abstract, "published": published, "source": "arxiv",
         "_score": {"core_hits": ["x"], "domain_hits": [], "excluded": False, "tier_rank": 0, "date_precision": "day"}}
    rp.record_observations(db, scan, "p", [p], core_signature="c", seed_signature="s",
                           observed_at="2026-09-09T05:00:00+00:00")
    rp.finish_scan(db, scan, observations=1)


def _seed_corpus(db):
    _observe(db, "a1", "target term with world model", "A world model for target term MARKER_COUNT_7")
    _observe(db, "a2", "target term again", "world model appears here too")
    _observe(db, "a3", "trend term paper", "nothing new")


PROPOSAL = json.dumps({"decision": "propose", "proposals": [
    {"action": "add_core_term", "term": "world model", "proposed_tier": 0.6,
     "evidence_paper_keys": ["a1", "a2"], "reason": "이어진다", "ambiguity_risks": []}]})


def _run(db, client, **kw):
    return asyncio.run(adv.run_weekly(db, "p", client, START, END, now=NOW, **kw))


# ── 외부 입력 경계 ────────────────────────────────────────────────────────
def test_금지_필드는_HTTP_payload_에_없고_허용_필드만_나간다(db):
    """이 테스트가 잡는 것: whitelist 를 빼고 스냅샷·집계를 통째로 보내는 것."""
    _seed_corpus(db)
    # 관측 테이블에 내부 집계 표식이 있어도(탈락 사유·순위) payload 에 안 나가야 한다
    with sqlite3.connect(db) as con:
        con.execute("UPDATE candidate_observations SET filter_reason='SECRET_REASON', rank_pos=777")
    client = FakeClient([gemini_ok(PROPOSAL)])
    out = _run(db, client)
    assert out["status"] == "proposed"
    sent = client.calls[0]["json"]["contents"][0]["parts"][0]["text"]
    assert "SECRET_REASON" not in sent and "777" not in sent and "rank_pos" not in sent
    assert "target term" in sent and "world model" in sent, "관심사와 공개 논문 텍스트는 나간다"
    for forbidden in ("returned", "unique", "eligible", "filter_reason", "already_shown", "outcome", "scan_id"):
        assert forbidden not in sent, forbidden
    with sqlite3.connect(db) as con:
        row = con.execute("SELECT sent_input_json, prompt_text, prompt_sha256 FROM advisor_runs").fetchone()
    sent_obj = json.loads(row[0])
    assert set(sent_obj) == {"core_by_tier", "allowed_tiers", "s2_seeds", "papers", "sent_paper_keys"}
    assert all(set(pp) == adv.SENT_FIELDS for pp in sent_obj["papers"]), "논문마다 key·title·abstract 만 나간다"
    assert row[1] == sent, "실행 시 사용한 정확한 프롬프트 문자열을 보존한다"


def test_근거_키는_전송_부분집합에_있어야_하고_용어는_문자열로_실재해야_한다(db):
    """이 테스트가 잡는 것: 전체 스냅샷 존재만으로 근거를 인정하는 것, R7 생략."""
    _seed_corpus(db)
    for i in range(10):    # 대표 8편을 넘기는 논문 — 전송되지 않는다
        _observe(db, f"x{i}", "target term filler", "filler only", published=f"2026-09-0{(i % 8) + 1}T00:00:00Z")
    prof = rp.get_profile(db, "p")
    snap = pi.snapshot(db, "p", START, END)
    papers = adv.select_papers(snap, prof)
    sent = adv.build_input(snap, prof, papers)
    assert len(sent["papers"]) == adv.MAX_PAPERS
    not_sent = next(k for k in {p["_paper_key"] for p in snap["papers"]} if k not in sent["sent_paper_keys"])
    v = adv.validate_proposals({"decision": "propose", "proposals": [
        {"action": "add_core_term", "term": "world model", "proposed_tier": 0.6,
         "evidence_paper_keys": [not_sent, "a1"]},
        {"action": "add_core_term", "term": "phantom phrase", "proposed_tier": 0.6,
         "evidence_paper_keys": ["a1", "a2"]},
    ]}, sent, prof)
    assert any(e.startswith("evidence_not_sent") for e in v[0]["errors"])
    assert "term_absent_in_evidence" in v[1]["errors"]


# ── JSON·액션 계약 ───────────────────────────────────────────────────────
def test_미지_액션_새_계층_NaN_중복_초과는_거부되고_guard_는_보류된다(db):
    _seed_corpus(db)
    prof = rp.get_profile(db, "p")
    snap = pi.snapshot(db, "p", START, END)
    sent = adv.build_input(snap, prof, adv.select_papers(snap, prof))
    data = {"decision": "propose", "proposals": [
        {"action": "delete_core", "term": "target term"},
        {"action": "add_core_term", "term": "world model", "proposed_tier": 0.7, "evidence_paper_keys": ["a1", "a2"]},
        {"action": "add_core_term", "term": "world model", "proposed_tier": float("nan"), "evidence_paper_keys": ["a1", "a2"]},
        {"action": "propose_term_guard", "term": "world model", "guard": ["robot"]},
        {"action": "add_s2_seed", "term": "target term"},
    ]}
    v = adv.validate_proposals(data, sent, prof)
    assert len(v) == adv.MAX_PROPOSALS, "3건 초과는 잘린다"
    assert "unknown_action" in v[0]["errors"]
    assert "tier_not_allowed" in v[1]["errors"], "0.7 은 기존 계층이 아니다 — 모델이 계층을 만들 수 없다"
    assert "tier_not_allowed" in v[2]["errors"] and "duplicate_term" in v[2]["errors"]
    v2 = adv.validate_proposals({"decision": "propose", "proposals": [data["proposals"][3]]}, sent, prof)
    assert v2[0]["deferred"] and v2[0]["errors"] == ["unsupported_guard_application"]
    after = adv.apply_actions(prof, v2)
    assert after["core_topics"] == prof["core_topics"], "보류된 guard 를 변경 없음으로 삼키지 않는다 — 프로필 불변"
    assert adv.validate_proposals({"decision": "no_change", "proposals": []}, sent, prof) == []
    assert adv.validate_proposals({"decision": "whatever"}, sent, prof)[0]["errors"] == ["bad_decision"]


def test_no_change_는_정상이고_revision_을_만들지_않는다(db):
    _seed_corpus(db)
    before = rp.current_revision(db, "p")
    out = _run(db, FakeClient([gemini_ok('{"decision":"no_change","proposals":[]}')]))
    assert out["status"] == "no_change" and out["applied"] is False
    assert rp.current_revision(db, "p") == before


# ── 예산 ─────────────────────────────────────────────────────────────────
def test_키회전_모델전환_형식재시도가_모두_같은_2회_예산을_쓴다(db):
    """이 테스트가 잡는 것: 내부 재시도가 예산을 우회하는 것(3번째 요청)."""
    _seed_corpus(db)
    client = FakeClient([FakeResp(429, text="quota"), FakeResp(503, text="busy"), gemini_ok(PROPOSAL)])
    out = _run(db, client)
    assert len(client.calls) == 2, "429 → 다른 키, 그걸로 끝. 세 번째는 없다"
    assert out["status"] == "failed" and out["reason"] == "no_valid_response"
    assert client.calls[0]["headers"]["x-goog-api-key"] == "k1" and client.calls[1]["headers"]["x-goog-api-key"] == "k2"
    with sqlite3.connect(db) as con:
        rows = con.execute("SELECT attempt, outcome, http_status, requested_model FROM advisor_attempts ORDER BY attempt").fetchall()
        budget = con.execute("SELECT requests, runs FROM advisor_budget").fetchone()
    assert [r[1] for r in rows] == ["http_429", "http_503"] and rows[1][3] == "gemini-flash-latest"
    assert budget == (2, 1)

    # 형식 오류 두 번도 2회로 끝난다 — 별도 주로 잰다
    client2 = FakeClient([gemini_ok("not json"), gemini_ok("still not json"), gemini_ok(PROPOSAL)])
    out2 = asyncio.run(adv.run_weekly(db, "p", client2, START, END, now=datetime(2026, 9, 18, 3, 0, tzinfo=timezone.utc)))
    assert len(client2.calls) == 2 and out2["status"] == "failed"


def test_같은_주에_두_번_돌지_않고_예산_소진이면_요청_0(db):
    _seed_corpus(db)
    _run(db, FakeClient([gemini_ok(PROPOSAL)]))
    client = FakeClient([gemini_ok(PROPOSAL)])
    out = _run(db, client)
    assert out["status"] == "skipped" and out["reason"] == "already_ran_this_week" and client.calls == []
    # 장부를 직접 소진 상태로 두면(재시작 뒤 같은 주) 새 실행도 요청을 못 보낸다
    with sqlite3.connect(db) as con:
        con.execute("UPDATE advisor_budget SET runs=0, requests=?", (adv.MAX_REQUESTS_PER_BATCH,))
    client = FakeClient([gemini_ok(PROPOSAL)])
    out = _run(db, client)
    assert out["reason"] == "request_budget_exhausted" and client.calls == []


def test_provider_잔여를_모르면_보내지_않는다(db):
    _seed_corpus(db)
    client = FakeClient([gemini_ok(PROPOSAL)])
    assert _run(db, None)["reason"] == "budget_unknown"
    engine.ENV.clear()
    out = asyncio.run(adv.run_weekly(db, "p", client, START, END, now=datetime(2026, 9, 25, 3, 0, tzinfo=timezone.utc)))
    assert out["reason"] == "budget_unknown" and client.calls == []


# ── 모델·입출력 보존 ──────────────────────────────────────────────────────
def test_시도별_요청_모델과_응답_모델을_보존한다(db, monkeypatch):
    """이 테스트가 잡는 것: 완료 후 전역 커서를 다시 읽어 모델을 기록하는 것,
    첫 실패 응답을 덮어쓰는 것."""
    _seed_corpus(db)
    client = FakeClient([FakeResp(503, text="pool busy"), gemini_ok(PROPOSAL, model="gemini-flash-lite-latest-002")])
    out = _run(db, client)
    assert out["status"] == "proposed"
    with sqlite3.connect(db) as con:
        rows = con.execute("SELECT attempt, requested_model, response_model, raw_response, usage_kind "
                           "FROM advisor_attempts ORDER BY attempt").fetchall()
    assert rows[0][1] == "gemini-flash-latest" and rows[0][3] == "pool busy" and rows[0][2] is None
    assert rows[1][1] == "gemini-flash-lite-latest" and rows[1][2] == "gemini-flash-lite-latest-002"
    assert rows[1][4] == "provider_reported"


# ── C 연결·게이트·적용 ───────────────────────────────────────────────────
def test_제안은_C로_넘어가고_기본_모드에서는_적용되지_않는다(db):
    _seed_corpus(db)
    out = _run(db, FakeClient([gemini_ok(PROPOSAL)]))
    assert out["status"] == "proposed" and out["valid"] == 1
    assert out["gate"] == pi.NEEDS_SHADOW, "core 추가는 arXiv 질의를 바꾸므로 shadow 필요"
    assert out["mode"] == adv.MODE_PROPOSAL_ONLY and out["applied"] is False
    assert rp.get_profile(db, "p")["core_topics"] == ["target term", "trend term"], "프로필 불변"
    with sqlite3.connect(db) as con:
        pr = con.execute("SELECT action, term, status, bundle_analysis_id FROM advisor_proposals").fetchone()
        an = con.execute("SELECT gate_status FROM impact_analyses").fetchone()
    assert pr[:3] == ("add_core_term", "world model", "analyzed") and pr[3] and an[0] == pi.NEEDS_SHADOW


def test_적용은_게이트_모드_stale_을_스스로_확인하고_복구는_새_revision_이다(db):
    """이 테스트가 잡는 것: 호출자의 eligible 을 믿는 것, proposal_only 에서 적용,
    stale 분석 적용, 같은 분석 중복 적용, 복구가 이력을 지우는 것."""
    _seed_corpus(db)
    prof = rp.get_profile(db, "p")
    snap = pi.snapshot(db, "p", START, END)
    # 검색 영향 없는 순수 계층 변경 + 테스트용 규칙 → eligible 을 만들 수 있다
    after = dict(prof, core_weights={"target term": 1.0, "trend term": 1.0})
    rules = {"max_core_changes": 5, "max_topk_left_ratio": 1.0, "max_exclude_risk": 1.0}
    # 씨앗이 명시돼 있어 실효 질의는 불변이어야 한다
    assert not pi.diff_profiles(prof, after)["seed_changed"]
    an = pi.analyze_and_store(db, snap, prof, after, 2, rules=rules)
    assert an["gate_status"] == pi.ELIGIBLE
    # 1) 기본 모드 → 거부
    r = adv.apply_analysis(db, "p", an["analysis_id"])
    assert r["applied"] is False and r["reason"].startswith("mode:")
    # 2) 모드를 열어도 게이트 미통과 분석은 거부
    adv.init_db(db)
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO advisor_settings (profile_id, mode, rules_json) VALUES ('p', ?, ?)",
                    (adv.MODE_AUTO_APPLY, json.dumps(rules)))
    held = pi.analyze_and_store(db, snap, prof, after, 2, rules={**rules, "max_core_changes": 0})
    assert held["gate_status"] == pi.HELD
    assert adv.apply_analysis(db, "p", held["analysis_id"])["reason"].startswith("gate:")
    # 3) 통과 + 모드 열림 → 적용. revision 이 오르고 이벤트가 남는다
    before_rev = rp.current_revision(db, "p")
    r = adv.apply_analysis(db, "p", an["analysis_id"])
    assert r["applied"] and r["revision"] == before_rev + 1
    assert rp.get_profile(db, "p")["core_weights"]["trend term"] == 1.0
    # 4) 같은 분석 재실행 → 중복 적용 없음; 이제 프로필이 바뀌어 stale 이기도 하다
    assert adv.apply_analysis(db, "p", an["analysis_id"])["reason"] in ("already_applied", "stale_before")
    assert rp.current_revision(db, "p") == before_rev + 1
    # 5) 복구는 직전 revision 내용을 **새 revision** 으로
    r = adv.rollback(db, "p", before_rev, reason="test")
    assert r["rolled_back"] and r["revision"] == before_rev + 2
    assert rp.get_profile(db, "p")["core_weights"]["trend term"] == 0.6
    with sqlite3.connect(db) as con:
        kinds = [k for (k,) in con.execute("SELECT kind FROM advisor_events ORDER BY at, event_id")]
        n_rev = con.execute("SELECT count(*) FROM profile_revisions").fetchone()[0]
    assert "applied" in kinds and "rolled_back" in kinds and n_rev == before_rev + 2


def test_이전_revision_에서_만든_분석은_stale_로_거부된다(db):
    _seed_corpus(db)
    prof = rp.get_profile(db, "p")
    snap = pi.snapshot(db, "p", START, END)
    rules = {"max_core_changes": 5, "max_topk_left_ratio": 1.0, "max_exclude_risk": 1.0}
    adv.init_db(db)
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO advisor_settings (profile_id, mode, rules_json) VALUES ('p', ?, ?)",
                    (adv.MODE_AUTO_APPLY, json.dumps(rules)))
    an = pi.analyze_and_store(db, snap, prof, dict(prof, core_weights={"target term": 1.0, "trend term": 1.0}), 2, rules=rules)
    # 사용자가 그 사이 프로필을 바꿨다(A→B). 분석의 before 는 더 이상 현재가 아니다
    rp.create_profile(db, "p", "이름", core_topics=["target term", "trend term", "extra"],
                      core_weights={"target term": 1.0, "trend term": 0.6, "extra": 0.6},
                      target_domain=["factory"], exclude=["banned"], s2_seeds=["target term"], max_items=2)
    assert adv.apply_analysis(db, "p", an["analysis_id"])["reason"] == "stale_before"
    # A→B→A: 내용이 정확히 같아지면 before 해시가 맞아 적용된다 — 분석은 그 내용에 대한 것이다.
    # revision 은 다르지만(3), "내용 동일"이 적용 조건이다. revision 은 이력·복구용이다.
    rp.create_profile(db, "p", "이름", core_topics=["target term", "trend term"],
                      core_weights={"target term": 1.0, "trend term": 0.6},
                      target_domain=["factory"], exclude=["banned"], s2_seeds=["target term"], max_items=2)
    assert rp.current_revision(db, "p") == 3
    assert adv.apply_analysis(db, "p", an["analysis_id"])["applied"] is True
    # 적용 뒤 다시 A 로 복구하면 before 해시가 또 맞는다 — 그래도 같은 분석은 두 번 적용하지 않는다
    r = adv.rollback(db, "p", 3, reason="back to A")
    assert r["rolled_back"]
    assert adv.apply_analysis(db, "p", an["analysis_id"])["reason"] == "already_applied"
    with sqlite3.connect(db) as con:
        origins = [o for (o,) in con.execute("SELECT origin FROM profile_revisions ORDER BY revision")]
    assert origins[-1] == "rollback" and origins[-2] == "advisor", "복구는 origin 이 다른 새 revision 이다"


# ── 장애 격리·우선순위 (scan_all_profiles 연결)

def test_제안기_장애는_배달을_바꾸지_않고_모든_프로필_뒤에_돈다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 제안기 예외가 summary 를 error 로 만드는 것, 제안기가
    프로필 사이에 끼어 도는 것, 주간이 아닌 날에 도는 것."""
    import run_profile_scan as rps
    db = tmp_path / "s.db"
    for pid in ("p1", "p2"):
        rp.create_profile(db, pid, pid, core_topics=["robot"], s2_seeds=["robot"])
    order: list[str] = []

    async def fake_scan(db_path, profile_id, client, max_pages=10):
        order.append(f"scan:{profile_id}")
        return {"run_status": "done", "candidates_found": 0, "scored_count": 0, "papers": []}, "digest"
    monkeypatch.setattr(rps, "scan_and_digest", fake_scan)
    monkeypatch.setattr(rps, "_deliver", lambda *a, **k: (order.append("deliver"), "발송 완료 → 1명")[1])
    monkeypatch.setattr(rps, "is_weekly_review_day", lambda now=None: True)

    async def boom(db_path, profile_id, client, start, end, now=None, k=None):
        order.append(f"advisor:{profile_id}")
        raise RuntimeError("advisor down")
    monkeypatch.setattr(adv, "run_weekly", boom)

    summary = asyncio.run(rps.scan_all_profiles(db, None, send=True))
    assert all(v["status"] == "ok" and v["delivery"].startswith("발송 완료") for v in summary.values())
    assert all(v["advisor"]["status"] == "error" for v in summary.values())
    assert order == ["scan:p1", "deliver", "scan:p2", "deliver", "advisor:p1", "advisor:p2"], (
        "모든 프로필의 처리·전달이 끝난 뒤에야 제안기가 돈다")

    # 주간이 아닌 날엔 안 돈다
    order.clear()
    monkeypatch.setattr(rps, "is_weekly_review_day", lambda now=None: False)
    summary = asyncio.run(rps.scan_all_profiles(db, None, send=True))
    assert not any("advisor" in v for v in summary.values()) and "advisor:p1" not in order


def test_제안기가_멈춰도_deadline_뒤_배달_결과는_그대로다(tmp_path, monkeypatch):
    import run_profile_scan as rps
    db = tmp_path / "s.db"
    rp.create_profile(db, "p1", "p1", core_topics=["robot"], s2_seeds=["robot"])

    async def fake_scan(db_path, profile_id, client, max_pages=10):
        return {"run_status": "done", "candidates_found": 0, "scored_count": 0, "papers": []}, "digest"
    monkeypatch.setattr(rps, "scan_and_digest", fake_scan)
    monkeypatch.setattr(rps, "_deliver", lambda *a, **k: "발송 완료 → 1명")
    monkeypatch.setattr(rps, "is_weekly_review_day", lambda now=None: True)
    monkeypatch.setattr(adv, "BATCH_DEADLINE_S", 0.05)
    monkeypatch.setattr(rps, "ADVISOR_TIMEOUT_GRACE_S", 0)

    async def hang(*a, **k):
        await asyncio.sleep(3600)
    monkeypatch.setattr(adv, "run_weekly", hang)

    async def bounded():   # 운영 deadline 이 빠지면 여기서 5초 안에 끊겨 실패한다 — 매달리지 않는다
        return await asyncio.wait_for(rps.scan_all_profiles(db, None, send=True), timeout=5)
    summary = asyncio.run(bounded())
    assert summary["p1"]["delivery"] == "발송 완료 → 1명"
    assert summary["p1"]["advisor"]["status"] == "error" and "TimeoutError" in summary["p1"]["advisor"]["reason"]
