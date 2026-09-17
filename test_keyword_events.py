"""⑨ 준비(2026-09-12, §8-102) — 세대 provenance · 되돌리기 · append-only 이벤트.
옛 주간 개선기(profile_advisor)의 기각 기억·게이트 판정 테스트는 2026-09-17 모듈 삭제와 함께 뺐다. 각 테스트: 무엇을 망가뜨리면 실패하는가."""
import json
import sqlite3
from datetime import datetime, timezone

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
    out = rp.rollback_to_revision(db, "p", r2, reason="test")
    assert out["rolled_back"]
    prov = rp.keyword_provenance(db, "p")
    assert prov["u"]["origin"] == "user" and prov["x"]["origin"] == "advisor"
    last = [e for e in _events(db) if e["revision"] == out["revision"] and e["change"] == "added"]
    assert {e["keyword"]: (e["actor_origin"], e["provenance_origin"]) for e in last} == {"u": ("rollback", "user"), "x": ("rollback", "advisor")}
    import profile_health as ph
    anchors, basis = ph.anchor_keywords(db, "p")
    assert anchors == {"a", "u"} and basis == "provenance"


def test_기대_revision이_다르면_프로필_쓰기와_이벤트를_함께_롤백한다(tmp_path):
    """이 테스트가 잡는 것: 검사한 revision이 바뀐 뒤 profile_keywords·revision·이벤트를 부분적으로
    쓰는 것(외부 검토 2026-09-14)."""
    import pytest
    db = tmp_path / "t.db"
    first = _create(db, ["A"])
    _create(db, ["A", "B"])
    before = rp.get_profile(db, "p")
    event_count = len(_events(db))
    with pytest.raises(ValueError, match="expected_revision"):
        rp.create_profile(db, "p", "이름", core_topics=["C"], expected_revision=first)
    assert rp.get_profile(db, "p")["core_topics"] == before["core_topics"]
    assert rp.current_revision(db, "p") == 2
    assert len(_events(db)) == event_count


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
    out = rp.rollback_to_revision(db, "p", r2, reason="legacy")
    assert out["rolled_back"]
    prov = rp.keyword_provenance(db, "p")
    assert prov["x"]["origin"] == "advisor" and prov["a"]["origin"] == "user"


def test_expected_revision_check_holds_the_write_lock(tmp_path):
    """대조와 쓰기가 한 잠금 안이어야 한다. 이 테스트가 잡는 것: expected_revision 대조를 잠금 없이 해서
    다른 연결이 그 사이에 쓸 수 있는 것 — 잠금을 잡았으면 다른 연결의 즉시 쓰기가 막힌다."""
    import sqlite3, threading
    import research_profile as rp
    db = tmp_path / "t.db"
    rp.create_profile(db, "p", "P", ["alpha"])
    rev = rp.current_revision(db, "p")
    blocked = {}
    real_connect = sqlite3.connect

    def spying_connect(*a, **k):
        con = real_connect(*a, **k)
        class Wrap:
            def __getattr__(self, n): return getattr(con, n)
            def __enter__(self): con.__enter__(); return self
            def __exit__(self, *e): return con.__exit__(*e)
            def execute(self, sql, *args):
                out = con.execute(sql, *args)
                if sql.startswith("SELECT COALESCE(MAX(revision)") and "probe" not in blocked:
                    other = real_connect(str(db), timeout=0)
                    try:
                        other.execute("BEGIN IMMEDIATE"); blocked["probe"] = False; other.rollback()
                    except sqlite3.OperationalError:
                        blocked["probe"] = True
                    finally:
                        other.close()
                return out
        return Wrap()

    import unittest.mock as um
    with um.patch.object(rp.sqlite3, "connect", spying_connect):
        rp.create_profile(db, "p", "P", ["alpha", "beta"], expected_revision=rev)
    assert blocked.get("probe") is True
