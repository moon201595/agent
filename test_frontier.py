"""⑤⑧⑨ 성능 동향(2026-09-30) — 관측 DB·외부 조사 검증·메일 문구. 네트워크·헤드리스 에이전트 없이 픽스처와 가짜로 돈다."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

import external_evidence as ee
import frontier_store as fs
import research_frontier as rf

FIX = Path(__file__).parent / "fixtures" / "arxiv_tables"
NOW = datetime(2026, 9, 30, 1, tzinfo=timezone.utc)


def _r(value, *, model="M", own=False, locator="T1:r1c1", bench="Real3D-AD", metric="O-AUROC", direction="higher"):
    import performance_results as pr
    return {"benchmark": bench, "metric": metric, "bench_key": pr.norm_key(bench), "metric_key": pr.norm_key(metric),
            "direction": direction, "value": value, "text": f"{value}", "model": model, "own": own, "locator": locator, "caption": ""}


# ── 관측 DB ─────────────────────────────────────────────────────────────────────────────────────

def test_compare_uses_other_papers_only_and_respects_direction(tmp_path):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 자기 논문 표의 기준선과 견줘 "관측 최고 초과"라고 하거나(그건 논문 자체 비교다),
    낮을수록 좋은 지표의 방향을 뒤집거나, 방향을 모르는 지표로 대소를 말하면 실패한다."""
    db = tmp_path / "f.db"
    fs.record(db, "A", [_r(85.9, model="B-net"), _r(83.2, model="C-net", locator="T1:r2c1")], "2026-07-01", NOW)
    fs.record(db, "X", [_r(87.2, model="Ours", own=True), _r(99.0, model="self-baseline", locator="T1:r9c1")], "2026-09-28", NOW)
    got = fs.compare(db, "X", _r(87.2, own=True))
    assert got["status"] == "above_observed" and got["best"]["value"] == 85.9 and got["best"]["reported_in"] == "A"
    assert fs.compare(db, "X", _r(80.0, own=True))["status"] == "below_observed"
    fs.record(db, "B", [_r(0.30, metric="FID", direction="lower")], None, NOW)
    assert fs.compare(db, "X", _r(0.25, metric="FID", direction="lower"))["status"] == "above_observed"
    assert fs.compare(db, "X", _r(1.0, metric="Params", direction=None))["status"] == "no_direction"
    assert fs.compare(db, "X", _r(50.0, bench="NewBench"))["status"] == "first"


# ── 외부 조사: 제안은 에이전트, 수치는 Python 이 표에서 ──────────────────────────────────────────

TARGET = {"paper_id": "2609.99999", "title": "T", "method": "", "benchmark": "Bench2Drive", "metric": "DS",
          "metric_key": "ds", "value": "90.95", "direction": "higher",
          "source_text": "We evaluate on the Bench2Drive v0.0.3 base set with 220 routes using the official CARLA leaderboard protocol.",
          "repo_mentions": {"o/r", "o/bench2drive"}}


SYNTH = "2601.00001"   # 합성 픽스처(synthetic_bench2drive.html)를 가리키는 가짜 arXiv 번호 — 원문 픽스처는 재배포 권한 때문에 커밋하지 않는다


def _fetch_fixture(url, timeout, headers=None):
    aid = url.rsplit("/", 1)[-1]
    name = "synthetic_bench2drive" if aid == SYNTH else aid
    return (FIX / f"{name}.html").read_text(encoding="utf-8")


def _comp(**kw):
    c = {"source_url": f"https://arxiv.org/abs/{SYNTH}", "model": "SimLingo", "value": 85.07, "same_conditions": False, "same_task": True, "task_note": "같은 과제 지표",
         "differences": [{"what": "multi-view vs single-view", "quote": "M: Multi-view, S: Single view, L: LiDAR."},
                         {"what": "made-up difference", "quote": "this sentence is not in the source page at all"}]}
    c.update(kw)
    return c


def test_competitor_value_comes_from_the_table_cell_not_the_agent():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 에이전트가 적은 숫자를 표에서 확인하지 않고 싣거나, 틀린 숫자를 통과시키거나,
    출처에 없는 "조건 차이" 인용을 사실처럼 싣으면 실패한다."""
    ok = ee.verify_competitor(_comp(), TARGET, _fetch_fixture, 5)
    assert ok["status"] == "verified" and ok["value"] == 85.07 and "SimLingo" in ok["locator"]
    assert ok["differences"] == ["multi-view vs single-view"] and ok["unverified_differences"] == 1
    wrong = ee.verify_competitor(_comp(value=99.9), TARGET, _fetch_fixture, 5)      # 제시값이 없으면 표 값으로 대체하지 않는다
    assert wrong["status"] == "not_found" and wrong["reason"] == "출처 표의 그 행에 제시값 없음" and "value" not in wrong
    assert ee.verify_competitor(_comp(model="NoSuchModel"), TARGET, _fetch_fixture, 5)["status"] == "not_found"


def test_row_identity_is_decided_by_name_never_by_the_number():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: SimLingo 를 묻는데 값(80)이 맞는다고 SimLingo-base 행을 고르거나(Codex 재현),
    이름이 여러 행에 걸리는데 아무 행이나 고르면 실패한다."""
    md = "# Bench2Drive\n\n| Method | DS |\n|---|---|\n| SimLingo [18] | 85.07 |\n| SimLingo-base | 80.00 |\n"
    url = "https://github.com/o/Bench2Drive"
    got = ee.verify_competitor(_comp(source_url=url, differences=[], value=80.0), TARGET, lambda *a, **k: md, 5)
    assert got["status"] == "not_found" and "value" not in got
    amb = ee.verify_competitor(_comp(source_url=url, differences=[], model="Sim", value=80.0), TARGET, lambda *a, **k: md, 5)
    assert amb["status"] == "not_found"


def test_metric_from_caption_and_long_metric_names():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 지표가 캡션에만 있는 표(`O-AUROC score on Real3D-AD`, 열은 Mean)를 못 읽거나,
    README 의 "Driving Score" 를 논문 표의 DS 와 같은 지표로 못 보거나, 0~1 척도를 %와 못 맞추면 실패한다."""
    html = ('<figure class="ltx_table"><figcaption>Table 1. O-AUROC score on Real3D-AD.</figcaption><table>'
            '<tr><th>Method</th><th>Airplane</th><th>Mean</th></tr><tr><td>Group3AD</td><td>0.744</td><td>0.751</td></tr></table></figure>')
    target = {**TARGET, "benchmark": "Real3D-AD", "metric": "O-AUROC / Mean", "metric_key": "oaurocmean"}
    got = ee.verify_competitor(_comp(model="Group3AD", value="75.1", differences=[]), target, lambda *a, **k: html, 5)
    assert got["status"] == "verified" and got["value"] == 0.751 and got["note"] == ""
    import performance_results as pr
    assert pr.metric_key("O-AUROC / Average") == pr.metric_key("O-AUROC / Mean")
    assert pr.metric_key("Driving Score ↑") == pr.metric_key("DS") and pr.metric_token("O-AUROC score (\\uparrow)") == "O-AUROC"


@pytest.mark.parametrize("url, status", [
    ("https://someblog.example/post", "unverified_source"),
    ("https://pmc.ncbi.nlm.nih.gov/articles/PMC1/", "unverified_source"),     # 실측: 2차 게재처에서 가져왔다
    ("http://arxiv.org/abs/2609.18623", "unverified_source"),
    ("https://openreview.net/forum?id=x", "no_structure"),
])
def test_sources_outside_priority_are_not_fetched_or_used(url, status):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 블로그·2차 게재처·http 출처를 받아 보거나 근거로 쓰거나, 구조 없는 출처의 숫자를 싣으면 실패한다."""
    def never(*a, **k):
        raise AssertionError("받으면 안 되는 출처를 받았다")
    assert ee.verify_competitor(_comp(source_url=url), TARGET, never, 5)["status"] == status


def test_markdown_leaderboard_is_read_as_a_table():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: GitHub README 의 마크다운 리더보드를 표로 못 읽거나 머리·구분 줄을 값으로 읽으면 실패한다."""
    md = "# Bench2Drive Leaderboard\n\n| Method | DS | SR(%) |\n|---|:-:|---|\n| SimLingo | 85.07 | 67.27 |\n| UniAD | 45.81 | 16.36 |\n"
    t = ee.markdown_tables(md)
    assert len(t) == 1 and t[0]["caption"] == "Bench2Drive Leaderboard" and t[0]["grid"][1][:2] == ["SimLingo", "85.07"]
    got = ee.verify_competitor(_comp(source_url="https://github.com/o/r", differences=[]), TARGET, lambda *a, **k: md, 5)
    assert got["status"] == "verified" and got["value"] == 85.07


def test_check_is_fail_open_and_schema_bound():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 에이전트 실패·잡글·스키마 밖 필드를 예외로 올리거나 "완료"로 적거나, 묻지 않은 논문을 받아들이면 실패한다."""
    def boom(prompt, timeout):
        raise TimeoutError("slow")
    assert ee.check([TARGET], run_agent=boom)["2609.99999"]["status"] == "incomplete"
    bad = json.dumps({"papers": [{"paper_id": "2609.99999", "competitors": [], "extra": 1}]})
    assert ee.check([TARGET], run_agent=lambda p, t: bad)["2609.99999"]["status"] == "incomplete"
    stray = json.dumps({"papers": [{"paper_id": "0000.00000", "competitors": []}]})
    assert ee.check([TARGET], run_agent=lambda p, t: stray)["2609.99999"]["status"] == "incomplete"
    good = "noise ```json\n" + json.dumps({"papers": [{"paper_id": "2609.99999", "competitors": [_comp()]}]}) + "\n```"
    res = ee.check([TARGET], run_agent=lambda p, t: good, fetch=_fetch_fixture)["2609.99999"]
    assert res["status"] == "done" and res["competitors"][0]["status"] == "verified"


def test_check_respects_the_total_budget():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 에이전트에 전체 예산을 다 주거나(검증할 시간이 없다), 예산이 끝났는데 검증 내려받기를 계속하면 실패한다."""
    now = [0.0]
    seen = {}

    def agent(prompt, timeout):
        seen["timeout"] = timeout
        now[0] += 299.5
        return json.dumps({"papers": [{"paper_id": "2609.99999", "competitors": [_comp()]}]})

    def fetch(*a, **k):
        raise AssertionError("예산이 끝났는데 받았다")
    res = ee.check([TARGET], run_agent=agent, fetch=fetch, clock=lambda: now[0])["2609.99999"]
    assert seen["timeout"] <= ee.BUDGET_S - ee.VERIFY_RESERVE_S and res["status"] == "incomplete"


# ── 메일 문구: 판정이 아니라 근거 수준 ────────────────────────────────────────────────────────────

def _fr(ext=None, cmp_status="first", best=None, claims=None):
    main = _r(87.2, model="GRIM (Ours)", own=True)
    main["compare"] = {"status": cmp_status, "best": best, "n": 1 if best else 0}
    return {"main": [main], "claims": claims or [], "external": ext, "sentences": []}


def _verified(value, same=False, diffs=None):
    return {"status": "verified", "value": value, "text": f"{value}", "model": "R3D-AD", "source_url": "https://arxiv.org/abs/2407.10862",
            "same_task": True, "task_note": "같은 과제 지표", "column_path": ["O-AUROC"], "table_label": "Table 1",
            "same_conditions": same, "differences": diffs if diffs is not None else ["범주별 학습"], "unverified_differences": 0,
            "locator": "R3D-AD × Real3D-AD / O-AUROC"}


def test_different_conditions_show_numbers_but_never_a_winner():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 조건이 다른데 87.2 > 73.4 로 우열을 말하거나, 경쟁 수치·출처·다른 조건을 숨기면 실패한다."""
    fr = _fr(ext={"status": "done", "competitors": [_verified(73.4)]})
    lines = rf.block_lines(fr)
    text = "\n".join(t for t, _ in lines)
    assert lines[0][0] == "외부 경쟁 결과 확인 · 조건 차이로 직접 비교 안 함"
    assert "73.4" in text and "조건 차이: 범주별 학습" in text and "우열 판정은 하지 않음" in text
    assert any(u == "https://arxiv.org/abs/2407.10862" for _, u in lines)
    for banned in ("SOTA", "더 좋", "우수", "최고 성능 후보"):
        assert banned not in text


def test_best_label_only_with_same_conditions_and_says_observed_range():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 조건이 확인되지 않았는데 "관측 범위 내 최고"를 붙이거나, 조건이 같아도 숫자로 최고·SOTA를 판정하면 실패한다."""
    same = _fr(ext={"status": "done", "competitors": [_verified(80.1, same=True, diffs=[])]})
    assert rf.status_label(same) == "동일 조건 외부 비교 가능"
    below = _fr(ext={"status": "done", "competitors": [_verified(90.0, same=True, diffs=[])]})
    assert rf.status_label(below) == "동일 조건 외부 비교 가능"
    unchecked = _verified(80.1, same=True, diffs=[])
    unchecked["unverified_differences"] = 1
    assert rf.status_label(_fr(ext={"status": "done", "competitors": [unchecked]})) != "관측 범위 내 최고 성능 후보"
    assert "SOTA" not in "".join(t for t, _ in rf.block_lines(same))


def test_labels_for_incomplete_and_observed_frontier_and_claim_prefix():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 외부 조사 실패를 "경쟁 없음"으로 적거나, 관측 최고 초과를 외부 검증 대상으로 안 알리거나,
    명시적 주장 표시를 잃으면 실패한다."""
    assert rf.status_label(_fr(ext={"status": "incomplete", "competitors": []})) == "외부 비교 미완료"
    best = {"value": 85.9, "model": "B-net", "reported_in": "2607.00001", "own": False}
    fr = _fr(cmp_status="above_observed", best=best, claims=[{"benchmarks": ["Real3D-AD"]}])
    assert rf.status_label(fr) == "성능 선도 주장 · 성능 결과 보고 · 외부 검증 대상"
    assert not any("보다 높음" in t for t, _ in rf.block_lines(fr))


def test_reading_point_has_no_praise_and_states_limits():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 읽을 포인트에 칭찬·추천·점수가 들어가거나, 관심축·확인 한계를 빼면 실패한다."""
    paper = {"_score": {"core_hits": ["defect detection"]}, "_frontier": _fr(ext={"status": "done", "competitors": [_verified(73.4)]})}
    point = rf.reading_point(paper)
    assert point.startswith("`defect detection` 관심축과 직접 연관.") and "직접 우열 비교는 어려움" in point
    for banned in ("추천", "주목", "뛰어", "우수", "훌륭", "점"):
        assert banned not in point.replace("관심축", "")
    assert "본문을 확보하지 못해" in rf.reading_point({"_score": {"core_hits": []}, "deep_status": "abstract_only"})


def test_analyze_batches_five_and_calls_once_per_profile_day(tmp_path):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 프로필당 5편을 넘겨 조사하거나, 두 번째 프로필의 새 결과를 조사하지 않거나,
    같은 논문의 오늘 결과를 재사용하지 않거나, 관측 최고 초과 논문보다 뒤 순위를 먼저 고르면 실패한다."""
    db = tmp_path / "p.db"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE papers (arxiv_id TEXT PRIMARY KEY, text_path TEXT, abstract TEXT, title TEXT, published TEXT)")
        for i in range(4):
            con.execute("INSERT INTO papers VALUES (?,?,?,?,?)", (f"2609.0000{i}", None, "We evaluate on Real3D-AD.", f"P{i}", "2026-09-2{i}"))
    fs.record(db, "old", [_r(86.0, model="Old")], "2026-06-01", NOW)
    values = {"2609.00000": 80.0, "2609.00001": 81.0, "2609.00002": 90.0, "2609.00003": 82.0}

    def tables_of(aid):
        return [{"id": "T1", "caption": "Results on Real3D-AD", "header_rows": 1,
                 "grid": [["Method", "O-AUROC"], ["Ours", str(values[aid])], ["Old", "86.0"]]}]
    calls = []

    def run_external(targets):
        calls.append([t["paper_id"] for t in targets])
        return {t["paper_id"]: {"status": "done", "competitors": []} for t in targets}
    papers = [{"arxiv_id": a, "title": f"P{i}", "_score": {"core_hits": ["x"]}} for i, a in enumerate(values)]
    rf.analyze(db, papers, tables_of=tables_of, run_external=run_external, signals_of=lambda a: {}, now=NOW, profile_id="first")
    assert len(calls) == 1 and len(calls[0]) == 4 and calls[0][0] == "2609.00002"      # 관측 최고(86.0) 초과가 먼저
    # 두 번째 프로필은 오늘 확인한 논문을 재사용하고 자기 예산으로 처음 보는 벤치마크를 조사한다.
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO papers VALUES ('2609.00005', NULL, 'We evaluate on MVTec AD.', 'P5', '2026-09-29')")

    def tables_of2(aid):
        if aid == "2609.00005":
            return [{"id": "T1", "caption": "Results on MVTec AD", "header_rows": 1, "grid": [["Method", "AUROC"], ["Ours", "99.1"]]}]
        return tables_of(aid)
    again = [{"arxiv_id": a, "title": "", "_score": {}} for a in list(values) + ["2609.00005"]]
    rf.analyze(db, again, tables_of=tables_of2, run_external=run_external, signals_of=lambda a: {}, now=NOW, profile_id="second")
    assert calls == [["2609.00002", "2609.00000", "2609.00001", "2609.00003"], ["2609.00005"]]
    assert again[2]["_frontier"]["external"]["status"] == "done"                        # 오늘 결과 재사용
    assert again[4]["_frontier"]["external"]["status"] == "done"


def test_analyze_never_raises_and_mail_still_renders(tmp_path):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 표 받기·DB·외부 조사 중 하나가 죽을 때 예외가 스캔까지 올라가 메일이 멈추면 실패한다."""
    import digest

    def broken(aid):
        raise RuntimeError("html down")
    papers = [{"arxiv_id": "2609.00009", "title": "T", "abstract": "a", "deep_status": "abstract_only", "abstract_brief": "- 요지 : x",
               "_score": {"core_hits": ["defect detection"]}}]
    rf.analyze(tmp_path / "missing.db", papers, tables_of=broken, run_external=lambda t: 1 / 0, signals_of=lambda a: 1 / 0, now=NOW)
    html = digest._paper_entry_html(1, papers[0])
    assert "읽을 포인트" in html and "defect detection" in html


def test_agent_is_called_once_per_day_even_without_the_db_table(tmp_path, monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: DB 기록 표가 없을 때 같은 프로필의 반복 호출로 프로필·날짜 예산을 넘기면 실패한다."""
    db = tmp_path / "p.db"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE papers (arxiv_id TEXT PRIMARY KEY, text_path TEXT, abstract TEXT, title TEXT, published TEXT)")
        con.execute("INSERT INTO papers VALUES ('2609.1', NULL, 'We evaluate on Real3D-AD.', 'P', '2026-09-29')")
    monkeypatch.setattr(fs, "save_external", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("no such table")))
    monkeypatch.setattr(fs, "record", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("no such table")))
    tables = lambda aid: [{"id": "T1", "caption": "Results on Real3D-AD", "header_rows": 1, "grid": [["Method", "O-AUROC"], ["Ours", "88.0"]]}]
    calls = []
    for _profile in range(4):
        rf.analyze(db, [{"arxiv_id": "2609.1", "title": "P", "_score": {}}], tables_of=tables,
                   run_external=lambda t: calls.append(t) or {x["paper_id"]: {"status": "done", "competitors": []} for x in t},
                   signals_of=lambda a: {}, now=NOW)
    assert len(calls) == 1
    again = [{"arxiv_id": "2609.1", "title": "P", "_score": {}}]
    rf.analyze(db, again, tables_of=tables, run_external=lambda t: calls.append(t), signals_of=lambda a: {}, now=NOW)
    assert again[0]["_frontier"]["external"]["status"] == "done"          # 표가 없어도 같은 날 다른 프로필은 결과를 재사용



def test_github_source_must_be_named_in_the_target_paper():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 대상 논문이 가리키지 않은 임의 GitHub 저장소(`unrelated/Bench2Drive-copy`)의 README 표를
    공식 리더보드처럼 받으면 실패한다(Codex 재현)."""
    def never(*a, **k):
        raise AssertionError("받으면 안 된다")
    got = ee.verify_competitor(_comp(source_url="https://github.com/unrelated/Bench2Drive-copy", differences=[]), TARGET, never, 5)
    assert got["status"] == "unofficial_repo"


def test_same_conditions_need_all_four_conditions_with_distinct_quotes_from_both_papers():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 에이전트의 same_conditions=true 만으로, 네 조건 중 일부만으로, 같은 인용문을 되풀이해서,
    또는 한쪽 원문에만 있는 인용으로 동일 조건을 인정하면 실패한다(Codex 최종 검토 재현: 같은 문장 두 번으로 "관측 범위 내 최고")."""
    md = ("# Bench2Drive\n\nWe report on Bench2Drive v0.0.3 base set with 220 routes using the official CARLA leaderboard protocol "
          "and single-camera training data only.\n\n| Method | DS |\n|---|---|\n| SimLingo | 85.07 |\n")
    target = {**TARGET, "source_text": "We evaluate on the Bench2Drive v0.0.3 base set with 220 routes using the official CARLA "
                                        "leaderboard protocol, trained on single-camera training data only."}
    url = "https://github.com/o/Bench2Drive"

    def run(ev, same=True):
        return ee.verify_competitor(_comp(source_url=url, differences=[], same_conditions=same, match_evidence=ev), target,
                                    lambda *a, **k: md, 5)
    full = [{"condition": "dataset_version", "what": "판", "source_quote": "Bench2Drive v0.0.3 base set", "target_quote": "Bench2Drive v0.0.3 base set"},
            {"condition": "split", "what": "경로", "source_quote": "with 220 routes", "target_quote": "with 220 routes using"},
            {"condition": "protocol", "what": "프로토콜", "source_quote": "official CARLA leaderboard protocol", "target_quote": "official CARLA leaderboard protocol,"},
            {"condition": "training_setting", "what": "학습", "source_quote": "single-camera training data only", "target_quote": "trained on single-camera training data"}]
    assert run([], same=True)["same_conditions"] is False
    assert run(full)["same_conditions"] is True
    assert run(full[:3])["same_conditions"] is False                                   # 학습 설정 근거 없음
    dup = [dict(full[0], condition=c) for c in ("dataset_version", "split", "protocol", "training_setting")]
    assert run(dup)["same_conditions"] is False                                        # 같은 인용 되풀이
    one_side = full[:3] + [dict(full[3], target_quote="trained with 999 cameras nowhere in target")]
    assert run(one_side)["same_conditions"] is False


def test_variant_row_is_never_accepted_for_the_requested_model():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 요청한 모델(SimLingo) 행이 없을 때 이름이 비슷한 변형(SimLingo-base) 행 하나를 대신 승인하면
    실패한다(Codex 최종 검토 재현)."""
    md = "# Bench2Drive\n\n| Method | DS |\n|---|---|\n| SimLingo-base | 85.07 |\n"
    got = ee.verify_competitor(_comp(source_url="https://github.com/o/Bench2Drive", differences=[]), TARGET, lambda *a, **k: md, 5)
    assert got["status"] == "not_found"


def test_fetch_deadline_stops_slow_redirect_chains(monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 요청마다 원래 timeout 을 줘서 리다이렉트가 이어지면 15초 상한이 42초까지 늘어나거나(Codex 재현),
    마지막 빈 응답에서 마감을 안 보면 실패한다."""
    import httpx
    now = [0.0]
    asked = []

    class Resp:
        def __init__(self, code, loc=None):
            self.status_code, self.headers, self.encoding = code, ({"location": loc} if loc else {}), "utf-8"
        def __enter__(self):
            now[0] += 14
            return self
        def __exit__(self, *a): return False
        def raise_for_status(self): pass
        def iter_bytes(self): return iter([])

    class Client:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def stream(self, method, url, timeout=None):
            asked.append(timeout.read if hasattr(timeout, "read") else timeout)
            return Resp(302, url + "x") if len(asked) < 3 else Resp(200)

    class Clock:
        @staticmethod
        def monotonic():
            return now[0]
    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setattr(ee, "time", Clock)
    with pytest.raises(TimeoutError):
        ee._real_fetch("https://arxiv.org/html/2601.00001", 15)
    assert len(asked) <= 2 and asked[1] <= 1.0 + 1e-9                                    # 둘째 요청엔 남은 1초만


def test_lookup_budget_is_separate_from_external_and_blocks_new_batches(tmp_path, monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 외부 조사 시간이 조회 예산을 먹거나, 예산이 끝난 뒤에 S2 batch 를 새로 부르면 실패한다(Codex 재현)."""
    import adoption_signals
    db = tmp_path / "p.db"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE papers (arxiv_id TEXT PRIMARY KEY, text_path TEXT, abstract TEXT, title TEXT, published TEXT)")
        con.execute("INSERT INTO papers VALUES ('2609.1', NULL, 'We evaluate on Real3D-AD.', 'P', NULL)")
    now = [0.0]
    batches = []
    monkeypatch.setattr(adoption_signals, "scholarly_batch", lambda ids: batches.append(ids) or {})
    monkeypatch.setattr(adoption_signals, "collect", lambda *a, **k: {"collected": True})

    def slow_external(targets):
        now[0] += 100                                   # 외부 조사가 조회 예산(60초)보다 오래 걸려도
        return {x["paper_id"]: {"status": "done", "competitors": []} for x in targets}
    tables = lambda aid: [{"id": "T1", "caption": "Results on Real3D-AD", "header_rows": 1, "grid": [["Method", "O-AUROC"], ["Ours", "88.0"]]}]
    papers = [{"arxiv_id": "2609.1", "title": "P", "_score": {}}]
    rf.analyze(db, papers, tables_of=tables, run_external=slow_external, now=NOW, clock=lambda: now[0])
    assert batches == [["2609.1"]] and papers[0]["_signals"] == {"collected": True}     # 외부 조사 시간은 조회 예산에서 빠진다
    monkeypatch.setattr(rf, "_SIGNALS", {})
    monkeypatch.setattr(rf, "_CALLED_ON", set())
    now[0] = 0.0

    def eat_budget(aid):
        now[0] += 61
        return tables(aid)
    batches.clear()
    papers = [{"arxiv_id": "2609.1", "title": "P", "_score": {}}]
    rf.analyze(db, papers, tables_of=eat_budget, run_external=lambda t: {}, now=NOW, clock=lambda: now[0])
    assert batches == [] and "_signals" not in papers[0]


def test_empty_reextraction_clears_old_cells_but_fetch_failure_keeps_them(tmp_path):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 정상 재추출에서 사라진 옛 관측이 비교에 남거나(Codex 재현), 받기 실패로 멀쩡한 관측을 지우면 실패한다."""
    db = tmp_path / "p.db"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE papers (arxiv_id TEXT PRIMARY KEY, text_path TEXT, abstract TEXT, title TEXT, published TEXT)")
        con.execute("INSERT INTO papers VALUES ('2609.1', NULL, 'We evaluate on Real3D-AD.', 'P', NULL)")
    fs.record(db, "2609.1", [_r(88.0, own=True)], None, NOW)
    count = lambda: sqlite3.connect(db).execute("SELECT COUNT(*) FROM observed_results WHERE reported_in='2609.1'").fetchone()[0]
    rf.analyze(db, [{"arxiv_id": "2609.1", "title": "P"}], tables_of=lambda a: None, run_external=lambda t: {}, signals_of=lambda a: {}, now=NOW)
    assert count() == 1                                                               # 받기 실패 — 그대로
    rf.analyze(db, [{"arxiv_id": "2609.1", "title": "P"}], tables_of=lambda a: [], run_external=lambda t: {}, signals_of=lambda a: {}, now=NOW)
    assert count() == 0                                                               # 받았는데 표 없음 — 비운다


def test_non_arxiv_paper_gets_an_explicit_unsupported_state(tmp_path):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: arXiv 밖 논문을 조용히 빼 버려 성능 주장이 있어도 아무 상태가 없으면 실패한다(Codex 재현)."""
    paper = {"arxiv_id": None, "doi": "10.1/x", "title": "T", "abstract": "Our method achieves 99.1% AUROC on MVTec AD.",
             "_sota_claims": [{"benchmarks": ["MVTec AD"]}]}
    rf.analyze(tmp_path / "none.db", [paper], run_external=lambda t: {}, signals_of=lambda a: {}, now=NOW)
    lines = [t for t, _ in rf.block_lines(paper["_frontier"])]
    assert paper["_frontier"]["unsupported"] == "non_arxiv" and any("arXiv 밖 논문" in t for t in lines)


def test_signal_failures_survive_the_new_paper_note():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 신규 논문 설명이 HF 다운로드 조회 실패 표시를 덮어 "관측 없음"으로만 나오면 실패한다(Codex 재현)."""
    import adoption_signals
    from datetime import date
    line = adoption_signals.signals_line({"scholarly": {"citations": 0, "publication_date": "2026-09-20"},
                                          "hub": {"page": True, "n_models": 4, "models": [], "models_error": "TimeoutError"}},
                                         date(2026, 9, 30))
    assert "다운로드 조회 실패" in line and "관측 없음" not in line


def test_css_escape_url_in_style_is_dropped():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: CSS 이스케이프(`u\\72l(`)로 쓴 외부 URL 이 style 에 남으면 실패한다(Codex 재현). 우리 style 은 남는다."""
    import link_policy
    a = link_policy.MailLinkAuditor(lookup=lambda h: ["151.101.3.42"], probe=lambda u, t: (200, ""))
    out, _ = link_policy.audit_html('<div style="background-image:u\\72l(https://evil.example/x)">t</div>'
                                    '<div style="color:#000;background-color:#fff;font-family:\'Noto Sans KR\',sans-serif">ok</div>', a.check)
    assert "evil.example" not in out and "72l" not in out and "background-color:#fff" in out


def test_direction_conflict_and_scale_guess_are_refused():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 외부 표의 실제 DS ↓ 헤더를 숨기거나, 별도 수치 비교 함수에서 RMSE를 비율로 변환하면 실패한다(Codex 재현)."""
    md = "# Bench2Drive\n\n| Method | DS ↓ |\n|---|---|\n| SimLingo | 85.07 |\n"
    got = ee.verify_competitor(_comp(source_url="https://github.com/o/Bench2Drive", differences=[]), TARGET, lambda *a, **k: md, 5)
    assert got["status"] == "verified" and got["column_path"] == ["DS ↓"]
    assert ee.comparable_values(0.8, 2.0, "RMSE") is None
    assert ee.comparable_values(0.751, 87.2, "O-AUROC / Mean") == (75.1, 87.2)
    assert rf._better(0.8, 2.0, "lower", "RMSE") is None and rf._better(80.0, 80.0, "higher", "DS") is None


def test_deadline_overrun_and_fetch_failure_are_incomplete_not_done():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 마감(300초)을 넘겨 끝났는데 done 으로 적거나, 출처 받기 실패를 "조사 완료"로 7일 캐시에
    남기면 실패한다(기존 180초 경계 재현을 새 300초 경계로 옮긴다)."""
    now = [0.0]

    def agent(prompt, timeout):
        now[0] += 265
        return json.dumps({"papers": [{"paper_id": "2609.99999", "competitors": [_comp()]}]})

    def slow_fetch(url, timeout, headers=None):
        now[0] += 40
        return _fetch_fixture(url, timeout)
    over = ee.check([TARGET], run_agent=agent, fetch=slow_fetch, clock=lambda: now[0])["2609.99999"]
    assert over["status"] == "incomplete" and "상한" in over["reason"]

    def failing(url, timeout, headers=None):
        raise TimeoutError("x")
    failed = ee.check([TARGET], run_agent=lambda p, t: json.dumps({"papers": [{"paper_id": "2609.99999", "competitors": [_comp()]}]}),
                      fetch=failing)["2609.99999"]
    assert failed["status"] == "incomplete"


def test_cached_competitors_keep_values_but_drop_condition_verdicts(tmp_path):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 다른 논문 기준의 조건 판정(동일 조건)을 캐시로 이 논문에 복사해 "관측 범위 내 최고"를 붙이면 실패한다."""
    db = tmp_path / "p.db"
    fs.save_external(db, "A", "2026-09-29", "real3dad", "oauroc", "done",
                     {"competitors": [_verified(80.1, same=True, diffs=[])], "unverified_sources": 0})
    main = _r(87.2, own=True)
    main["compare"] = {"status": "first", "best": None, "n": 0}
    paper = {"arxiv_id": "B", "_frontier": {"main": [main], "claims": [], "external": None, "sentences": []}}
    rf._external(db, [((False, 0), paper, main)], "2026-09-30", lambda t: pytest.fail("캐시가 있는데 불렀다"))
    fr = paper["_frontier"]
    assert fr["external"]["cached_from"] == "A" and rf.status_label(fr) != "관측 범위 내 최고 성능 후보"
    assert fr["external"]["competitors"][0]["same_task"] is False
    assert not any("외부 논문 표:" in t for t, _ in rf.block_lines(fr))


def test_observed_equal_and_below_are_not_shown_as_ranking(tmp_path):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 같은 값을 "상대가 더 높음"으로 적거나, 조건 모르는 관측값이 더 높다는 우열 문구를 싣으면 실패한다."""
    db = tmp_path / "f.db"
    fs.record(db, "A", [_r(80.0)], None, NOW)
    assert fs.compare(db, "X", _r(80.0, own=True))["status"] == "equal"
    below = _fr(cmp_status="below_observed", best={"value": 90.0, "model": "B", "reported_in": "A", "own": False})
    assert not any("90" in t for t, _ in rf.block_lines(below))


def test_rerecording_replaces_a_papers_old_cells(tmp_path):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 다시 뽑은 결과에 없는 옛 셀이 관측 DB 에 남으면 실패한다."""
    db = tmp_path / "f.db"
    fs.record(db, "A", [_r(80.0, locator="T#0:r1c1"), _r(70.0, locator="T#1:r1c1", metric="SR")], None, NOW)
    fs.record(db, "A", [_r(81.0, locator="T#0:r1c1")], None, NOW)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT value FROM observed_results WHERE reported_in='A'").fetchall() == [(81.0,)]


def test_arxiv_version_is_kept_and_lookup_budget_stops_fetching(tmp_path, monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: `…/html/2609.12345v1` 을 버전 없이 받아 링크와 다른 판을 검증하거나(Codex 재현),
    조회 예산(표 HTML·외부 신호)이 끝났는데도 논문마다 계속 받으면 실패한다."""
    assert ee._ARXIV_ID_RE.search("https://arxiv.org/html/2609.12345v1").group(1) == "2609.12345v1"
    db = tmp_path / "p.db"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE papers (arxiv_id TEXT PRIMARY KEY, text_path TEXT, abstract TEXT, title TEXT, published TEXT)")
        for i in range(3):
            con.execute("INSERT INTO papers VALUES (?,?,?,?,?)", (f"2609.1000{i}", None, "We evaluate on Real3D-AD.", "P", None))
    fetched = []
    monkeypatch.setattr(rf, "_arxiv_tables", lambda aid: fetched.append(aid) or [])
    now = [0.0]

    def clock():
        now[0] += rf.LOOKUP_BUDGET_S          # 첫 논문 뒤로 예산이 끝난다
        return now[0]
    rf.analyze(db, [{"arxiv_id": f"2609.1000{i}", "title": "P"} for i in range(3)], run_external=lambda t: {},
               signals_of=lambda a: {}, now=NOW, clock=clock)
    assert len(fetched) <= 1
