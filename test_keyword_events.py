"""⑨ 준비(2026-09-12, §8-102) — 세대 provenance · 기각 기억 · 게이트 판정 감사.
각 테스트: 무엇을 망가뜨리면 실패하는가."""
import json
import sqlite3
from datetime import datetime, timezone

import profile_advisor as adv
import profile_impact as pi
import research_profile as rp


def _events(db, kw=None):
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        q = "SELECT * FROM profile_keyword_events WHERE profile_id='p'" + (" AND keyword=?" if kw else "") + " ORDER BY event_id"
        return [dict(r) for r in con.execute(q, (kw,) if kw else ())]


def _create(db, core, weights=None, origin="user", seeds=("seed",), **kw):
    return rp.create_profile(db, "p", "이름", core_topics=list(core), core_weights=weights or {c: 1.0 for c in core},
                             exclude=["banned"], max_items=2, s2_seeds=list(seeds), origin=origin, **kw)


def test_이벤트는_논리_diff_만_남기고_바뀌지_않은_키워드는_안_적는다(tmp_path):
    """이 테스트가 잡는 것: DELETE/INSERT 를 그대로 이벤트로 옮겨 저장할 때마다 전부 removed/added 가
    되는 것, kind 가 식별자에 안 들어가 core 와 s2_seed 의 같은 문자열이 섞이는 것."""
    db = tmp_path / "t.db"
    _create(db, ["A", "B"], seeds=["A"])
    ev = _events(db)
    assert {(e["keyword"], e["kind"], e["change"]) for e in ev} == {("a", "core", "added"), ("b", "core", "added"),
                                                                    ("a", "s2_seed", "added"), ("banned", "exclude", "added")}
    _create(db, ["A", "B"], weights={"A": 1.0, "B": 0.6}, seeds=["A"])   # 가중치만 — 집합은 그대로
    assert len(_events(db)) == 4, "바뀌지 않은 키워드에는 이벤트가 없다"
    _create(db, ["A", "C"], seeds=["A"])
    new = _events(db)[4:]
    assert {(e["keyword"], e["change"]) for e in new} == {("b", "removed"), ("c", "added")}
    assert all(e["actor_origin"] == "user" for e in new)


def test_제거_후_사람이_재추가하면_새_user_세대이고_advisor_세대는_advisor_로_남는다(tmp_path):
    """이 테스트가 잡는 것: '처음 출현 origin' 을 영구 provenance 로 써서 사람이 다시 넣은 키워드가
    영원히 auto 인 것, 세대 번호가 안 오르는 것, 사람 저장으로 advisor 키워드가 anchor 가 되는 것."""
    db = tmp_path / "t.db"
    _create(db, ["A"])
    _create(db, ["A", "X"], origin="advisor")          # 제안기가 X 추가
    assert rp.keyword_provenance(db, "p")["x"]["origin"] == "advisor"
    _create(db, ["A", "X"], weights={"A": 1.0, "X": 0.6})   # 사람이 설정만 저장 — X 는 여전히 advisor
    assert rp.keyword_provenance(db, "p")["x"]["origin"] == "advisor"
    _create(db, ["A"])                                  # 사람이 X 제거
    assert "x" not in rp.keyword_provenance(db, "p")
    _create(db, ["A", "X"])                             # 몇 달 뒤 사람이 X 를 직접 추가
    prov = rp.keyword_provenance(db, "p")
    assert prov["x"]["origin"] == "user" and prov["x"]["source"] == "generation:2"
    xs = _events(db, "x")
    assert [(e["change"], e["actor_origin"], e.get("provenance_origin"), e.get("generation")) for e in xs] == \
        [("added", "advisor", "advisor", 1), ("removed", "user", None, None), ("added", "user", "user", 2)]


def test_rollback_은_actor_가_rollback_이지만_되살린_세대의_provenance_를_복원한다(tmp_path):
    """이 테스트가 잡는 것: rollback 으로 돌아온 키워드가 'rollback' origin 이 되어 anchor 도 auto 도
    아니게 되는 것, advisor 키워드가 rollback 으로 user 가 되는 것."""
    db = tmp_path / "t.db"
    r1 = _create(db, ["A", "U"])                         # 사람: A, U
    r2 = _create(db, ["A", "U", "X"], origin="advisor")   # 제안기: X
    r3 = _create(db, ["A"])                               # 사람이 U, X 제거
    out = adv.rollback(db, "p", r2, reason="test")
    assert out["rolled_back"]
    prov = rp.keyword_provenance(db, "p")
    assert prov["u"]["origin"] == "user" and prov["x"]["origin"] == "advisor"
    last = [e for e in _events(db) if e["revision"] == out["revision"] and e["change"] == "added"]
    assert {e["keyword"]: (e["actor_origin"], e["provenance_origin"]) for e in last} == {"u": ("rollback", "user"), "x": ("rollback", "advisor")}
    import profile_health as ph
    anchors, basis = ph.anchor_keywords(db, "p")
    assert anchors == {"a", "u"} and basis == "provenance"


def test_이벤트_표_도입_전_프로필은_현재_활성_키워드를_첫_세대로_bootstrap_한다(tmp_path):
    """이 테스트가 잡는 것: 이벤트가 없는 옛 프로필의 키워드를 전부 user 로 가정하는 것(처음 출현
    이력이 advisor 인 것은 advisor 여야 한다), bootstrap 을 두 번 하는 것."""
    db = tmp_path / "t.db"
    _create(db, ["A"]); _create(db, ["A", "X"], origin="advisor")
    with sqlite3.connect(db) as con:                      # 도입 전 상태를 흉내: 트리거를 걷고 이벤트 표를 비운다
        con.execute("DROP TRIGGER keyword_events_immutable_d")
        con.execute("DELETE FROM profile_keyword_events")
        con.execute("CREATE TRIGGER keyword_events_immutable_d BEFORE DELETE ON profile_keyword_events "
                    "BEGIN SELECT RAISE(ABORT, 'profile_keyword_events is append-only'); END")
    assert rp.keyword_provenance(db, "p")["x"]["origin"] == "advisor", "이벤트 없으면 처음 출현 이력으로"
    n = rp.bootstrap_keyword_events(db, "p")
    assert n == 4 and rp.bootstrap_keyword_events(db, "p") == 0
    ev = {(e["keyword"], e["kind"]): e for e in _events(db)}
    assert ev[("x", "core")]["provenance_origin"] == "advisor" and ev[("a", "core")]["provenance_origin"] == "user"
    assert all(e["actor_origin"] == "bootstrap" and e["generation"] == 1 for e in ev.values())


def _sent(keys):
    return {"papers": [{"key": k, "title": f"title {k}", "abstract": f"abstract {k}"} for k in keys],
            "exploration": [], "sent_paper_keys": list(keys), "allowed_tiers": [1.0]}


def test_insufficient_는_기각이_아니고_held_만_기억한다(tmp_path, monkeypatch):
    """이 테스트가 잡는 것: 규칙 미설정(insufficient)으로 막힌 정상 제안을 기각으로 기억해 9/27 전
    제안이 전부 억제되는 것(외부 검토 2026-09-12), held 를 기억하지 않는 것, 규칙이 바뀌었는데도
    억제하는 것, 전부 억제됐는데 status 가 proposed 인 것."""
    import asyncio
    import test_profile_advisor as T
    from datetime import timedelta
    db = tmp_path / "d.db"
    rp.create_profile(db, "p", "이름", core_topics=["target term", "trend term"],
                      core_weights={"target term": 1.0, "trend term": 0.6}, exclude=["banned"], max_items=2, s2_seeds=["target term"])
    monkeypatch.setenv("GOOGLE_API_KEY", "k1")
    T._seed_corpus(db)
    tier = json.dumps({"decision": "propose", "proposals": [
        {"action": "change_core_tier", "term": "trend term", "proposed_tier": 1.0,
         "evidence_paper_keys": ["a1", "a3"], "reason": "r", "ambiguity_risks": []}]})
    # 1) 규칙 미설정 → insufficient → 기각 아님. 다음 주 같은 제안은 다시 분석된다.
    out1 = T._run(db, T.FakeClient([T.gemini_ok(tier)]))
    assert out1["status"] == "proposed" and out1["gate"] == pi.INSUFFICIENT
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM advisor_events WHERE kind='rejected'").fetchone()[0] == 0
    out2 = asyncio.run(adv.run_weekly(db, "p", T.FakeClient([T.gemini_ok(tier)]), T.START, T.END, now=T.NOW + timedelta(days=7)))
    assert out2["status"] == "proposed" and out2["valid"] == 1 and out2["suppressed"] == 0
    # 2) 규칙을 설정해 held 가 나면 기각 기억 → 다음 주 억제 → status 는 suppressed, valid 0
    adv.init_db(db)
    rules = {"max_core_changes": 0, "max_topk_left_ratio": 1.0, "max_exclude_risk": 1.0}
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO advisor_settings (profile_id, mode, rules_json) VALUES ('p', ?, ?)", (adv.MODE_PROPOSAL_ONLY, json.dumps(rules)))
    out3 = asyncio.run(adv.run_weekly(db, "p", T.FakeClient([T.gemini_ok(tier)]), T.START, T.END, now=T.NOW + timedelta(days=14)))
    assert out3["gate"] == pi.HELD
    with sqlite3.connect(db) as con:
        rej = [json.loads(r[0]) for r in con.execute("SELECT detail_json FROM advisor_events WHERE kind='rejected'")]
    assert len(rej) == 1 and rej[0]["reason"].startswith("gate:held") and rej[0]["rules_hash"]
    out4 = asyncio.run(adv.run_weekly(db, "p", T.FakeClient([T.gemini_ok(tier)]), T.START, T.END, now=T.NOW + timedelta(days=21)))
    assert out4["status"] == "suppressed" and out4["valid"] == 0 and out4["suppressed"] == 1
    with sqlite3.connect(db) as con:
        n_an = con.execute("SELECT count(*) FROM impact_analyses").fetchone()[0]
    assert n_an == 2, "4주차엔 분석하지 않았다(1·2주차는 같은 입력이라 분석 1건 재사용 + 3주차 held 1건)"
    # 3) 규칙이 바뀌면(한도 완화) 같은 증거라도 다시 평가한다
    with sqlite3.connect(db) as con:
        con.execute("UPDATE advisor_settings SET rules_json=? WHERE profile_id='p'", (json.dumps({**rules, "max_core_changes": 5}),))
    out5 = asyncio.run(adv.run_weekly(db, "p", T.FakeClient([T.gemini_ok(tier)]), T.START, T.END, now=T.NOW + timedelta(days=28)))
    assert out5["status"] == "proposed" and out5["suppressed"] == 0


def test_기각_기억은_같은_제안_같은_증거_같은_프로필일_때만_억제한다(tmp_path):
    """이 테스트가 잡는 것: 기각을 영구 blacklist 로 쓰는 것(새 증거·바뀐 프로필에도 억제), 근거 키
    순서·표기 변형·계층 표기가 다르다고 다른 제안으로 보는 것, revision 만 달라도(A→B→A) 다시 올리는 것."""
    db = tmp_path / "t.db"
    _create(db, ["A"]); profile = rp.get_profile(db, "p")
    p = {"action": "add_core_term", "term": "spiking-sensor", "proposed_tier": 1.0, "evidence_paper_keys": ["k2", "k1"]}
    sent = _sent(["k1", "k2", "k3"])
    rh = adv.current_rules_hash(db, "p", None)
    adv.record_rejection(db, "p", p, sent, profile, reason="gate:held", rules_hash=rh)
    same = {"action": "add_core_term", "term": "spiking sensors", "proposed_tier": 1.00, "evidence_paper_keys": ["k1", "k2"]}
    out = adv.suppress_rejected(db, "p", [same], sent, profile)
    assert out[0].get("suppressed") is True, "표기 변형·키 순서·계층 표기가 달라도 같은 제안이다"
    new_ev = {**same, "evidence_paper_keys": ["k1", "k3"]}
    assert not adv.suppress_rejected(db, "p", [new_ev], sent, profile)[0].get("suppressed"), "새 증거면 다시 올린다"
    changed_text = {**sent, "papers": [{**pp, "abstract": "updated"} if pp["key"] == "k1" else pp for pp in sent["papers"]]}
    assert not adv.suppress_rejected(db, "p", [same], changed_text, profile)[0].get("suppressed"), "같은 키라도 텍스트가 바뀌면 다른 증거"
    _create(db, ["A", "B"]); other = rp.get_profile(db, "p")
    assert not adv.suppress_rejected(db, "p", [same], sent, other)[0].get("suppressed"), "프로필이 바뀌면 다시 올린다"
    _create(db, ["A"]); back = rp.get_profile(db, "p")     # A→B→A: revision 은 올랐지만 내용은 같다
    assert adv.suppress_rejected(db, "p", [same], sent, back)[0].get("suppressed") is True, "revision 이 아니라 내용 해시"
    assert not adv.suppress_rejected(db, "p", [same], sent, back, rules={"max_core_changes": 3})[0].get("suppressed"), "규칙이 바뀌면 다시 평가"
    with sqlite3.connect(db) as con:
        d = json.loads(con.execute("SELECT detail_json FROM advisor_events WHERE kind='rejected'").fetchone()[0])
    assert d["base_revision"] == 1 and "reason" in d
    # 억제된 제안은 변경안에 들어가지 않는다
    after = adv.apply_actions(profile, [{**same, "suppressed": True}])
    assert "spiking sensors" not in after["core_topics"]


def test_게이트_판정은_실제로_쓴_규칙과_함께_추가만_되는_표에_남고_적용기는_그것을_대조한다(tmp_path):
    """이 테스트가 잡는 것: 재판정이 gate_status 만 바꿔 rules_json(전부 None)과 모순되는 기록을 남기는 것,
    적용기가 gate_status 문자열만 믿는 것, 규칙에 None 이 남은 판정으로 적용하는 것."""
    db = tmp_path / "t.db"
    _create(db, ["target term", "trend term"], weights={"target term": 1.0, "trend term": 0.6}, seeds=["target term"])
    prof = rp.get_profile(db, "p")
    sid = rp.begin_scan(db, "p", prof)
    rp.record_observations(db, sid, "p", [{"arxiv_id": "t1", "title": "target term study", "abstract": "", "published": "2026-09-10T00:00:00Z",
                                            "outcome": "content", "rank_pos": 1, "_hits": {"core_hits": ["target term"], "exclude_hits": [], "domain_hits": []}}],
                           core_signature="c", seed_signature="s")
    rp.finish_scan(db, sid, observations=1)
    snap = pi.snapshot(db, "p", datetime(2020, 1, 1, tzinfo=timezone.utc), datetime(2030, 1, 1, tzinfo=timezone.utc))
    after = dict(prof, core_weights={"target term": 1.0, "trend term": 1.0})
    an = pi.analyze_and_store(db, snap, prof, after, 2)          # 규칙 미설정 → insufficient
    dec = pi.latest_decision(db, an["analysis_id"])
    assert dec and dec["source"] == "analyze" and dec["gate_status"] == pi.INSUFFICIENT
    assert all(v is None for v in json.loads(dec["effective_rules_json"]).values())
    # 사람이 gate_status 만 eligible 로 바꿔 놓아도(모순 기록) 적용기는 판정 기록을 대조해 거부한다
    adv.init_db(db)
    with sqlite3.connect(db) as con:
        con.execute("UPDATE impact_analyses SET gate_status=? WHERE analysis_id=?", (pi.ELIGIBLE, an["analysis_id"]))
        con.execute("INSERT INTO advisor_settings (profile_id, mode, rules_json) VALUES ('p', ?, '{}')", (adv.MODE_AUTO_APPLY,))
    r = adv.apply_analysis(db, "p", an["analysis_id"])
    assert r["applied"] is False and r["reason"] == "no_eligible_decision_record"
    # 규칙을 채운 정식 분석은 판정 기록이 eligible + 규칙 완비 → 적용된다
    rules = {"max_core_changes": 5, "max_topk_left_ratio": 1.0, "max_exclude_risk": 1.0}
    an2 = pi.analyze_and_store(db, snap, prof, after, 2, rules=rules)
    dec2 = pi.latest_decision(db, an2["analysis_id"])
    assert dec2["gate_status"] == pi.ELIGIBLE and json.loads(dec2["effective_rules_json"]) == rules and dec2["rules_hash"]
    assert adv.apply_analysis(db, "p", an2["analysis_id"])["applied"] is True


def test_이벤트와_판정_기록은_DB_가_append_only_를_강제한다(tmp_path):
    """이 테스트가 잡는 것: 코드 실수로 이력을 UPDATE/DELETE 할 수 있는 것."""
    import pytest
    db = tmp_path / "t.db"
    _create(db, ["A"])
    pi.init_db(db)
    with sqlite3.connect(db) as con:
        for sql in ("UPDATE profile_keyword_events SET keyword='z'", "DELETE FROM profile_keyword_events"):
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(sql)
        con.execute("INSERT INTO gate_decisions VALUES ('d','a','p','t','analyze',NULL,'x','[]','{}',NULL,'h')")
        for sql in ("UPDATE gate_decisions SET gate_status='y'", "DELETE FROM gate_decisions"):
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(sql)


def test_이관_전_revision_으로_rollback_해도_legacy_provenance_가_보충된다(tmp_path):
    """이 테스트가 잡는 것: bootstrap 보다 과거 revision 으로 되돌릴 때 이벤트가 없어 되살아난 키워드가
    전부 rollback/user 로 뭉개지는 것 — advisor 가 넣었던 키워드는 advisor 로 와야 한다."""
    db = tmp_path / "t.db"
    r1 = _create(db, ["A"])
    r2 = _create(db, ["A", "X"], origin="advisor")
    r3 = _create(db, ["A"])
    with sqlite3.connect(db) as con:                      # r1~r3 가 이벤트 표 도입 전이었다고 흉내
        con.execute("DROP TRIGGER keyword_events_immutable_d")
        con.execute("DELETE FROM profile_keyword_events")
    rp.bootstrap_keyword_events(db, "p")                  # 이제 활성 = {A} 만 첫 세대
    out = adv.rollback(db, "p", r2, reason="legacy")
    assert out["rolled_back"]
    prov = rp.keyword_provenance(db, "p")
    assert prov["x"]["origin"] == "advisor" and prov["a"]["origin"] == "user"
