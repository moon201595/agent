"""E1(2026-09-11) — 평가 지표 회귀. docs/ASTRA_PLAN_2026-09-10.md §11.
운영 함수를 부르고, 각 결함을 주입하면 실패하는지 돌연변이로 확인했다."""
from datetime import datetime, timedelta, timezone

import evaluation as ev
import research_profile as rp

T0 = datetime(2026, 9, 10, tzinfo=timezone.utc)


def _db(tmp_path):
    db = tmp_path / "e.db"
    rp.create_profile(db, "p", "이름", core_topics=["target term"], core_weights={"target term": 1.0})
    return db


def _cand(db, key, published, first_seen, shown=None, title="target term"):
    import sqlite3
    rp.init_db(db)
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO search_candidates (profile_id, paper_key, title, abstract, source, arxiv_id, "
                    "published, score, outcome, first_seen, last_seen) VALUES ('p',?,?,?,'arxiv',?,?,1.0,'content',?,?) "
                    "ON CONFLICT(profile_id, paper_key) DO UPDATE SET last_seen=excluded.last_seen",
                    (key, title, "", key, published, first_seen, first_seen))
    if shown:
        with sqlite3.connect(db) as con:
            con.execute("INSERT INTO profile_shown (profile_id, paper_key, title, shown_at) VALUES ('p', ?, ?, ?)",
                        (key, title, shown))


def test_미포착은_지우지_않고_관측_시작_전_공개는_분모에서_뺀다(tmp_path):
    """이 테스트가 잡는 것: 미전달 논문을 지연 계산에서 삭제만 하고 안 세는 것,
    테이블 생성일에 묶인 first_seen 으로 지연을 부풀리는 것, 행 수로 세는 것."""
    db = _db(tmp_path)
    _cand(db, "a", "2026-09-08T00:00:00Z", "2026-09-09T05:00:00+00:00", shown="2026-09-09T05:10:00+00:00")
    _cand(db, "b", "2026-09-08T00:00:00Z", "2026-09-10T05:00:00+00:00")           # 미포착
    _cand(db, "old", "2026-08-01T00:00:00Z", "2026-09-09T05:00:00+00:00")         # 관측 시작 전 공개
    _cand(db, "a", "2026-09-08T00:00:00Z", "2026-09-11T05:00:00+00:00")           # 같은 논문 재관측 → 한 편
    lat = ev.latency(db, "p", T0 - timedelta(days=30), T0 + timedelta(days=30),
                     observation_start=datetime(2026, 9, 7, tzinfo=timezone.utc))
    assert lat["papers"] == 3, "고유 논문 단위"
    assert lat["collection"]["all"]["n"] == 2 and lat["collection"]["all"]["median"] == 1.5
    assert lat["not_delivered"] == 1 and lat["censored_before_observation"] == 1
    assert lat["delivery"]["n"] == 1 and abs(lat["delivery"]["median"] - 10 / 1440) < 1e-6


def test_라벨이_없으면_의미상_지표는_None_이고_있으면_미판정을_분모에서_뺀다():
    assert ev.semantic_metrics(None, ["a"], 3)["status"] == ev.LABELS_UNAVAILABLE
    assert ev.semantic_metrics({}, ["a"], 3)["precision_at_k"] is None
    m = ev.semantic_metrics({"a": "direct", "b": "irrelevant", "c": "unknown", "d": "indirect"},
                            ["a", "b", "c", "x"], 3)
    assert m["precision_at_k"] == 0.5, "상위 3 중 판정된 a·b 만 분모 — c 는 미판정"
    assert m["candidate_recall"] == 0.5, "관련 a·d 중 후보에 있는 것은 a"
    assert m["noise_rate"] == 1 / 3 and m["judged"] == 3 and m["unjudged"] == 1


def test_재생은_cutoff_이후_관측을_쓰지_않고_당시_프로필로_채점한다(tmp_path):
    """이 테스트가 잡는 것: 시점 누수(cutoff 뒤 관측 포함), 현재 프로필로 재생하는 것,
    관측 없는 과거를 개체 테이블로 억지 재생하는 것."""
    db = _db(tmp_path)
    assert ev.replay(db, "p", T0, 3)["status"] == ev.NOT_REPLAYABLE
    prof = rp.get_profile(db, "p")
    s1 = rp.begin_scan(db, "p", prof, started_at="2026-09-09T05:00:00+00:00")
    sc = {"core_hits": ["x"], "domain_hits": [], "excluded": False, "tier_rank": 0, "date_precision": "day"}
    rp.record_observations(db, s1, "p", [
        {"arxiv_id": "early", "title": "target term", "abstract": "", "published": "2026-09-08", "source": "arxiv", "_score": sc},
        # 당시 프로필엔 안 걸리고 **현재** 프로필(late term 추가 후)에만 걸리는 논문 — 당시 재생이면 안 나와야 한다
        {"arxiv_id": "future-core", "title": "late term paper", "abstract": "", "published": "2026-09-09", "source": "arxiv", "_score": sc}],
        core_signature="c", seed_signature="s", observed_at="2026-09-09T05:00:00+00:00")
    # 프로필을 바꾸고(새 키워드) 그 뒤 관측
    rp.create_profile(db, "p", "이름", core_topics=["target term", "late term"],
                      core_weights={"target term": 1.0, "late term": 1.0})
    prof2 = rp.get_profile(db, "p")
    s2 = rp.begin_scan(db, "p", prof2, started_at="2026-09-12T05:00:00+00:00")
    rp.record_observations(db, s2, "p", [
        # cutoff 뒤 관측인데 당시 프로필에도 걸리는 논문 — 누수되면 재생 결과에 나타난다
        {"arxiv_id": "late", "title": "target term newest", "abstract": "", "published": "2026-09-11", "source": "arxiv", "_score": sc}],
        core_signature="c", seed_signature="s", observed_at="2026-09-12T05:00:00+00:00")
    cutoff = datetime(2026, 9, 10, tzinfo=timezone.utc)
    r = ev.replay(db, "p", cutoff, 3)
    assert r["status"] == "replayed" and r["topk"] == ["early"], "cutoff 뒤 관측(late)도, 당시 프로필 밖(future-core)도 안 보인다"
    r2 = ev.replay(db, "p", datetime(2026, 9, 13, tzinfo=timezone.utc), 3)
    assert set(r2["topk"]) == {"early", "late", "future-core"}, "9/13 시점 프로필엔 late term 이 있다"
    # 현재 정책 재채점은 이름부터 다르고 결과도 다르다 — future-core 가 현재 core 에 걸린다
    assert set(ev.rescore_current(db, "p", cutoff, 3)["topk"]) == {"early", "future-core"}


def test_보고서는_미측정_목록을_내고_성과를_지어내지_않는다(tmp_path):
    db = _db(tmp_path)
    r = ev.report(db, "p", T0 - timedelta(days=7), T0)
    assert "precision_at_k" in r["unmeasured"] and r["semantic"]["precision_at_k"] is None
    assert r["term_adoption_latency"]["n"] == 0
    assert r["latency"]["papers"] == 0 and r["latency"]["collection"] == {}


# ── R 제안기·실험 manifest (E1)

def test_R_은_A_와_같은_입력에서_같은_계약으로_제안하고_D_검증을_통과한다(tmp_path):
    """이 테스트가 잡는 것: R 이 전체 집계를 쓰는 것, 허용 계층 밖 값, 기존 core 재제안."""
    import profile_advisor as adv, rule_advisor as R
    prof = {"core_topics": ["target term"], "core_weights": {"target term": 1.0}, "target_domain": [],
            "exclude": [], "s2_seeds": []}
    papers = [{"_paper_key": f"p{i}", "title": "target term with graph neural network",
               "abstract": "graph neural network results", "published": "2026-09-09"} for i in range(3)]
    papers.append({"_paper_key": "q", "title": "unrelated stuff", "abstract": "graph neural network only", "published": "2026-09-09"})
    snap = {"papers": papers, "paper_count": 4, "title_only": 0, "abstract_corrupt": [], "scans": {"total": 1}}
    sent = adv.build_input(snap, prof, adv.select_papers(snap, prof))
    out = R.propose(sent, prof)
    assert out["decision"] == "propose" and out["proposals"][0]["term"] == "graph neural network"
    assert out["proposals"][0]["proposed_tier"] == 1.0, "허용 계층 중 가장 낮은 것(여기선 1.0 하나)"
    v = adv.validate_proposals(out, sent, prof)
    assert v and not v[0]["errors"], f"D 검증기를 그대로 통과해야 한다: {v[0]['errors']}"
    # 기존 core 는 재제안하지 않는다 — core 가 가장 빈번한 gram 인 상황에서도
    core_heavy = [{"_paper_key": f"c{i}", "title": "target term", "abstract": "target term everywhere", "published": "2026-09-09"} for i in range(5)]
    snap2 = {"papers": core_heavy + papers[:2], "paper_count": 7, "title_only": 0, "abstract_corrupt": [], "scans": {"total": 1}}
    sent2 = adv.build_input(snap2, prof, adv.select_papers(snap2, prof, limit=7))
    out3 = R.propose(sent2, prof, rules={"max_proposals": 10, "min_support": 1})
    assert all("target term" not in p["term"] for p in out3["proposals"]), out3["proposals"]
    # 동시 출현 비율 미달이면 no_change — 'unrelated' 논문만 있는 용어
    out2 = R.propose(sent, prof, rules={"min_cooccurrence_ratio": 1.01})
    assert out2["decision"] == "no_change"


def test_실험은_기준선_뒤에_완전한_설정으로만_얼리고_얼린_뒤엔_불변이다(tmp_path):
    """이 테스트가 잡는 것: 임계값 미설정 실험을 얼리는 것, 기준선 전 freeze, frozen 행 수정."""
    import sqlite3
    edb = tmp_path / "eval.db"
    m = {k: "x" for k in ev.REQUIRED_MANIFEST}
    m.update({"k": 6, "gate_rules": {"max_core_changes": None}, "arms": ["F", "R", "A"]})
    eid = ev.experiment_draft(edb, m)
    base_end = datetime(2026, 9, 25, tzinfo=timezone.utc)
    assert ev.experiment_freeze(edb, eid, base_end, now=datetime(2026, 9, 20, tzinfo=timezone.utc))["reason"] == "baseline_not_finished"
    assert ev.experiment_freeze(edb, eid, base_end, now=datetime(2026, 9, 26, tzinfo=timezone.utc))["reason"] == "gate_rules_unconfigured"
    with sqlite3.connect(edb) as con:
        m["gate_rules"] = {"max_core_changes": 3}
        con.execute("UPDATE experiments SET manifest_json=? WHERE experiment_id=?", (__import__("json").dumps(m, sort_keys=True), eid))
    r = ev.experiment_freeze(edb, eid, base_end, now=datetime(2026, 9, 26, tzinfo=timezone.utc))
    assert r["frozen"] and r["manifest_sha256"]
    with sqlite3.connect(edb) as con, __import__("pytest").raises(sqlite3.IntegrityError):
        con.execute("UPDATE experiments SET manifest_json='{}' WHERE experiment_id=?", (eid,))
    with sqlite3.connect(edb) as con, __import__("pytest").raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM experiments WHERE experiment_id=?", (eid,))
    rid = ev.experiment_record_run(edb, eid, "R", "abc", 1, {"topk": []})
    with sqlite3.connect(edb) as con:
        assert con.execute("SELECT manifest_sha256 FROM experiment_runs WHERE run_id=?", (rid,)).fetchone()[0] == r["manifest_sha256"]
    eid2 = ev.experiment_draft(edb, m)
    with __import__("pytest").raises(ValueError):
        ev.experiment_record_run(edb, eid2, "R", None, None, {})


def test_피드백은_관련성_라벨이_아니라_별도_지표다(tmp_path):
    db = _db(tmp_path)
    r = ev.feedback_usefulness(db, "p", T0 - timedelta(days=7), T0)
    assert "precision" not in __import__("json").dumps(r)
    assert r["papers_with_feedback"] == 0
