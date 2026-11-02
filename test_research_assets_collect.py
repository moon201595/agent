"""참고 자료 수집기 계약 — 모델이 쓴 것과 Python 이 붙인 것을 가르는 경계를 지킨다(2026-11-02)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

import research_assets
import research_assets_collect as rac


DAY = date(2026, 11, 2)
README = (
    "# Dex Retargeting\n\nVarious retargeting optimizers to translate human hand motion to robot hand motion.\n\n"
    "## Installation\n\n```shell\npip install dex_retargeting\n```\n\n"
    "To use dex-retargeting results with other libraries, handle joint ordering explicitly using joint names, "
    "which are unique within a URDF file.\n\n"
    "This repository is derived from the AnyTeleop Project https://arxiv.org/abs/2307.04577 and is subject to ongoing enhancements. "
    + "Filler text about examples and rendering. " * 10
)


class FakeFetcher:
    def __init__(self, items=None, readme=README, pypi_urls=None):
        self.items = items if items is not None else [
            {"full_name": "dexsuite/dex-retargeting", "html_url": "https://github.com/dexsuite/dex-retargeting",
             "description": "Various retargeting optimizers", "license": {"spdx_id": "MIT"},
             "pushed_at": "2025-08-21T17:51:55Z", "archived": False, "fork": False, "stargazers_count": 900},
            {"full_name": "someone/awesome-dexterous", "html_url": "https://github.com/someone/awesome-dexterous",
             "license": None, "pushed_at": "2025-01-01T00:00:00Z", "fork": False, "stargazers_count": 5000},
            {"full_name": "other/fork-of-thing", "html_url": "https://github.com/other/fork-of-thing",
             "license": None, "pushed_at": "2025-01-01T00:00:00Z", "fork": True, "stargazers_count": 1},
        ]
        self.readme = readme
        self.pypi_urls = pypi_urls if pypi_urls is not None else {"Source": "https://github.com/dexsuite/dex-retargeting"}
        self.calls: list[str] = []

    def github_search(self, query):
        self.calls.append(f"search:{query}")
        return self.items

    def github_readme(self, full_name):
        self.calls.append(f"readme:{full_name}")
        return self.readme

    def hf_datasets(self, query):
        return []

    def hf_dataset_card(self, dataset_id):
        raise AssertionError("HF 를 부르지 않는 과업이다")

    def pypi(self, package):
        self.calls.append(f"pypi:{package}")
        return {"info": {"name": "dex_retargeting", "version": "0.5.0", "requires_python": "<3.13,>=3.7",
                         "project_urls": self.pypi_urls},
                "releases": {"0.5.0": [{"upload_time": "2025-04-05T00:17:29"}]}}

    def arxiv(self, arxiv_id):
        return {"id": arxiv_id, "title": "AnyTeleop", "summary": "A unified and general teleoperation system to support multiple different arms, hands, realities, and camera configurations within a single system."}


GOOD_PICK = {
    "cid": "gh:dexsuite/dex-retargeting", "topic": "손 원격조작 · 리타게팅",
    "name": "dex-retargeting — 사람 손 동작을 로봇 손 관절값으로 바꾸는 라이브러리",
    "what": "사람 손 동작을 로봇 손 관절값으로 바꾸는 최적화기 모음입니다.",
    "helps": "리타게팅 결과의 관절 순서를 관절 이름으로 맞추는 방법을 이해하는 데 도움이 됩니다.",
    "evidence": ["Various retargeting optimizers to translate human hand motion to robot hand motion.",
                 "handle joint ordering explicitly using joint names"],
    "doc_facts": [{"text": "관절 순서는 URDF 관절 이름으로 맞추라고 안내", "quote": "joint names, which are unique within a URDF file"}],
}


class FakeRunner:
    def __init__(self, picks=None, verdict="pass", review_error=False, select_error=False):
        self.picks = [GOOD_PICK] if picks is None else picks
        self.verdict = verdict
        self.review_error = review_error
        self.select_error = select_error
        self.select_payloads: list[dict] = []
        self.review_payloads: list[dict] = []

    def select(self, payload_json):
        if self.select_error:
            raise rac.CollectError("claude_exit")
        self.select_payloads.append(json.loads(payload_json))
        return {"picks": self.picks, "note": ""}

    def review(self, payload_json):
        self.review_payloads.append(json.loads(payload_json))
        if self.review_error:
            raise rac.CollectError("codex_exit")
        return {"verdict": self.verdict, "problems": [] if self.verdict == "pass" else ["원문에 없는 기능"]}


def _setup(base: Path, *, tasks=None, config=None) -> None:
    base.mkdir(parents=True, exist_ok=True)
    (base / "config.json").write_text(json.dumps(config or {
        "enabled": True, "audience": "operator", "weekdays": [1, 3], "max_items": 2, "profiles": ["team_robot"]}), encoding="utf-8")
    (base / "tasks.json").write_text(json.dumps(tasks or {"team_robot": [
        {"task_id": "RB-05", "title": "장갑 원격조작", "needs": "리타게팅", "queries": ["hand retargeting"], "priority": "high"}]}),
        encoding="utf-8")


def _items(base: Path) -> list[dict]:
    return research_assets.load_items(base)


def test_good_pick_becomes_ready_with_python_facts_and_links(tmp_path):
    """Python 이 붙이는 라이선스·설치·링크 대신 모델 값을 쓰거나, Codex 통과 항목을 ready 로 올리지 않으면 실패한다."""
    _setup(tmp_path)
    summary = rac.run(tmp_path, today=DAY, fetcher=FakeFetcher(), runner=FakeRunner())
    items = _items(tmp_path)
    assert summary["status"] == "ok" and [i["item_id"] for i in items] == ["gh:dexsuite/dex-retargeting"]
    item = items[0]
    assert item["status"] == "ready" and item["task_id"] == "RB-05" and item["source"] == "collector"
    assert "설치 pip install dex_retargeting (PyPI 0.5.0, 2025-04 · Python <3.13,>=3.7)" in item["facts"]
    assert any(f.startswith("라이선스 MIT · 저장소 마지막 갱신 2025-08") for f in item["facts"])
    assert item["facts"][0] == "관절 순서는 URDF 관절 이름으로 맞추라고 안내"
    assert [r["url"] for r in item["refs"]] == ["https://github.com/dexsuite/dex-retargeting", "https://arxiv.org/abs/2307.04577"]


def test_forks_awesome_lists_and_known_items_are_not_candidates(tmp_path):
    """포크·awesome 목록 제외(v5-5)나 이미 아는 자료 제외를 빼면 실패한다 — 모델 입력 후보로 확인한다."""
    _setup(tmp_path)
    runner = FakeRunner(picks=[])
    rac.run(tmp_path, today=DAY, fetcher=FakeFetcher(), runner=runner)
    cids = [c["cid"] for c in runner.select_payloads[0]["candidates"]]
    assert cids == ["gh:dexsuite/dex-retargeting"]
    # 이미 저장소에 있으면(어느 상태든) 다시 후보로 올리지 않는다.
    _setup(tmp_path)
    research_assets.upsert_items([{**rac.build_item(rac.Task("team_robot", "RB-05", "t", "n", ["q"]),
                                                    {"cid": "gh:dexsuite/dex-retargeting", "kind": "github", "title": "dexsuite/dex-retargeting",
                                                     "url": "https://github.com/dexsuite/dex-retargeting", "license": "MIT", "updated": "2025-08",
                                                     "source_text": README, "pypi": None, "arxiv": None},
                                                    {"topic": "t", "name": "dex-retargeting — x", "what": "w", "helps": "h", "doc_facts": [], "evidence": []},
                                                    DAY), "status": "rejected"}], tmp_path)
    runner2 = FakeRunner(picks=[])
    rac.run(tmp_path, today=DAY, fetcher=FakeFetcher(), runner=runner2, force=True)
    assert runner2.select_payloads == []      # 후보가 없으면 모델을 부르지 않는다


def test_fabricated_quote_rejects_pick(tmp_path):
    """근거 구절 원문 대조를 빼거나 최소 개수를 낮추면 실패한다 — 원문에 없는 구절로는 실리지 않는다."""
    _setup(tmp_path)
    bad = {**GOOD_PICK, "evidence": ["Various retargeting optimizers to translate human hand motion to robot hand motion.",
                                     "supports Manus and Rokoko gloves out of the box"]}
    summary = rac.run(tmp_path, today=DAY, fetcher=FakeFetcher(), runner=FakeRunner(picks=[bad]))
    assert _items(tmp_path) == []
    assert any("근거 구절 1개" in r for r in summary["profiles"]["team_robot"]["rejected"])


def test_judgment_phrase_rejects_pick(tmp_path):
    """정보만 준다는 경계(v5-7 — 팀 환경 적합성·도입 판단 금지) 검사를 빼면 실패한다."""
    _setup(tmp_path)
    bad = {**GOOD_PICK, "helps": "우리 장갑 장비에 바로 쓸 수 있어 도입하기 좋습니다."}
    rac.run(tmp_path, today=DAY, fetcher=FakeFetcher(), runner=FakeRunner(picks=[bad]))
    assert _items(tmp_path) == []


def test_pick_outside_candidates_or_wrong_name_is_rejected(tmp_path):
    """후보 목록 밖 cid 를 받아 주거나 이름에 자료 이름이 없는 소개를 받아 주면 실패한다."""
    _setup(tmp_path)
    outside = {**GOOD_PICK, "cid": "gh:evil/elsewhere"}
    wrong_name = {**GOOD_PICK, "name": "손 리타게팅 도구"}
    rac.run(tmp_path, today=DAY, fetcher=FakeFetcher(), runner=FakeRunner(picks=[outside, wrong_name]))
    assert _items(tmp_path) == []


def test_pypi_package_not_linked_to_repo_gives_no_install_fact(tmp_path):
    """PyPI 메타데이터가 저장소를 가리키는지 확인을 빼면 실패한다 — 이름만 같은 남의 패키지 버전을 싣지 않는다.
    github.com/dexsuite/dex-retargeting2 같은 접두 일치도 같은 저장소로 보면 실패한다."""
    _setup(tmp_path)
    fetcher = FakeFetcher(pypi_urls={"Source": "https://github.com/dexsuite/dex-retargeting2"})
    rac.run(tmp_path, today=DAY, fetcher=fetcher, runner=FakeRunner())
    facts = _items(tmp_path)[0]["facts"]
    assert not any(f.startswith("설치") for f in facts)


def test_codex_fail_rejects_and_codex_error_keeps_pending_then_retries(tmp_path):
    """Codex 반대를 무시하고 ready 로 올리거나, Codex 실행 실패를 통과로 치거나, 다음 실행에서 보류 항목을 다시 보지 않으면 실패한다."""
    _setup(tmp_path)
    rac.run(tmp_path, today=DAY, fetcher=FakeFetcher(), runner=FakeRunner(verdict="fail"))
    item = _items(tmp_path)[0]
    assert item["status"] == "rejected" and item["review"]["problems"] == ["원문에 없는 기능"]

    other = tmp_path / "second"
    _setup(other)
    rac.run(other, today=DAY, fetcher=FakeFetcher(), runner=FakeRunner(review_error=True))
    assert _items(other)[0]["status"] == "pending_review"
    # 그 주는 "끝남"으로 기록됐어도(검색·선정은 성공) 다음 근무일 실행이 보류 항목을 다시 대조한다 — 검색은 다시 안 한다.
    assert rac.load_state(other)["done_week"] == rac.week_key(DAY)
    retry, idle = FakeRunner(picks=[]), FakeFetcher()
    summary = rac.run(other, today=date(2026, 11, 3), fetcher=idle, runner=retry)
    assert summary["status"] == "already_done" and idle.calls == []
    assert _items(other)[0]["status"] == "ready" and len(retry.review_payloads) == 1


def test_review_payload_carries_source_and_mail_pick_drops_it(tmp_path):
    """Codex 에 원문을 안 넘기거나(대조 불가), 메일 항목에 원문 발췌(review)가 섞여 나가면 실패한다."""
    _setup(tmp_path)
    runner = FakeRunner()
    rac.run(tmp_path, today=DAY, fetcher=FakeFetcher(), runner=runner)
    assert "translate human hand motion" in runner.review_payloads[0]["source"]
    picked = research_assets.pick_for_mail("team_robot", date(2026, 11, 3), tmp_path)
    assert picked and "review" not in picked[0]


def test_weekly_guard_and_failure_marks_partial(tmp_path):
    """같은 주 재실행 막기를 빼거나, 선정 실패를 '끝난 주'로 기록해 그 주 재시도를 막으면 실패한다."""
    _setup(tmp_path)
    failing = rac.run(tmp_path, today=DAY, fetcher=FakeFetcher(), runner=FakeRunner(select_error=True))
    assert failing["status"] == "partial"
    again = rac.run(tmp_path, today=DAY, fetcher=FakeFetcher(), runner=FakeRunner())
    assert again["status"] == "ok" and _items(tmp_path)
    assert rac.run(tmp_path, today=DAY, fetcher=FakeFetcher(), runner=FakeRunner())["status"] == "already_done"


def test_choose_tasks_prefers_oldest_then_high_priority():
    """가장 오래 안 모은 과업 우선·같으면 '높음' 우선 규칙을 바꾸면 실패한다."""
    tasks = [rac.Task("p", "A", "a", "", ["q"]), rac.Task("p", "B", "b", "", ["q"], priority="high"),
             rac.Task("p", "C", "c", "", ["q"]), rac.Task("other", "Z", "z", "", ["q"], priority="high")]
    assert [t.task_id for t in rac.choose_tasks(tasks, "p", {})] == ["B", "A"]
    state = {"last_collected": {"p:B": "2026-10-26", "p:A": "2026-10-26"}}
    assert [t.task_id for t in rac.choose_tasks(tasks, "p", state)] == ["C", "B"]


def test_disabled_config_skips_without_network(tmp_path):
    """설정이 꺼졌는데 검색·모델을 부르면 실패한다."""
    _setup(tmp_path, config={"enabled": False})
    fetcher = FakeFetcher()
    assert rac.run(tmp_path, today=DAY, fetcher=fetcher, runner=FakeRunner())["status"] == "disabled"
    assert fetcher.calls == []


def test_enough_unsent_ready_items_skip_collection(tmp_path):
    """안 보낸 ready 가 충분할 때 수집을 건너뛰는 상한을 빼면(쌓이기만 한다) 실패한다."""
    _setup(tmp_path)
    for n in range(rac.READY_ENOUGH):
        cand = {"cid": f"gh:o/r{n}", "kind": "github", "title": f"o/r{n}", "url": f"https://github.com/o/r{n}",
                "license": "MIT", "updated": "2025-01", "source_text": README, "pypi": None, "arxiv": None}
        body = {"topic": "t", "name": f"r{n} — x", "what": "w", "helps": "h", "doc_facts": [], "evidence": []}
        research_assets.upsert_items([{**rac.build_item(rac.Task("team_robot", "RB-05", "t", "n", ["q"]), cand, body, DAY),
                                       "status": "ready"}], tmp_path)
    fetcher = FakeFetcher()
    summary = rac.run(tmp_path, today=DAY, fetcher=fetcher, runner=FakeRunner())
    assert summary["profiles"]["team_robot"]["skipped"] and fetcher.calls == []


def test_quote_normalization_ignores_markdown_and_case():
    """정규화가 마크다운 기호·공백·대소문자를 접지 않으면 진짜 인용을 놓쳐 실패한다. 15자 미만 인용을 받아 주면 실패한다."""
    norm = rac.normalize(rac.clean_markdown("**Handle   joint ordering** explicitly\nusing `joint names`."))
    assert rac.quote_found("handle joint ordering explicitly using joint names", norm)
    assert not rac.quote_found("joint names", norm)


def test_budget_exhaustion_stops_and_reports_partial(tmp_path):
    """요청 예산이 바닥났는데 계속 돌거나, 그 주를 끝난 것으로 기록하면 실패한다."""
    _setup(tmp_path)

    class Broke(FakeFetcher):
        def github_search(self, query):
            raise rac.CollectError("budget_requests")

    summary = rac.run(tmp_path, today=DAY, fetcher=Broke(), runner=FakeRunner())
    assert summary["status"] == "partial"
    assert rac.load_state(tmp_path).get("done_week") is None


def test_daily_chain_runs_collection_after_mail_and_ignores_failure():
    """수집을 발송 앞으로 옮기거나(메일 지연), 실패 무시(|| echo)·시간 상한(timeout)을 빼거나, 로그 블록 밖으로 빼면 실패한다."""
    sh = (Path(rac.__file__).resolve().parent / "run_daily_scan.sh").read_text(encoding="utf-8")
    scan = sh.index(".venv/bin/python run_profile_scan.py --all")
    end = sh.index('echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) 종료 (exit $status) ==="')
    collect = sh.index(".venv/bin/python research_assets_collect.py")
    block_end = sh.index("} >> logs/daily_scan.log 2>&1")
    assert scan < end < collect < block_end
    line = sh[sh.rindex("\n", 0, collect):sh.index("\n", collect)]
    assert line.strip().startswith("timeout ") and '|| echo "  [참고 자료]' in line
    # 셸 상한은 수집기 자체 마감(+ 마지막 CLI 한 번) 뒤에 와야 하고, 한 시간을 넘지 않으며, 안 죽으면 강제 종료한다.
    words = line.split()
    assert any(w.startswith("--kill-after=") for w in words)
    limit = int(next(w for w in words[1:] if w.isdigit()))
    assert rac.RUN_DEADLINE_S + rac.CLI_TIMEOUT_S < limit <= 3600



# ── Codex 검토(2026-11-02) 뒤 붙인 경계 ─────────────────────────────────────────

def test_api_shaped_doc_facts_are_dropped_and_review_sees_only_model_facts(tmp_path):
    """모델이 '설치 pip install …'·'라이선스 …' 모양 줄을 쓰면 버리는 검사를 빼거나(근거 구절은 줄의 존재만 보증한다),
    Python 이 API 로 붙인 줄을 Codex 입력에서 빼지 않거나(그러면 줄 모양 면제가 다시 필요해진다) 하면 실패한다."""
    _setup(tmp_path)
    quote = "handle joint ordering explicitly using joint names"
    pick = {**GOOD_PICK, "doc_facts": GOOD_PICK["doc_facts"] + [
        {"text": "설치 pip install bogus_tool (PyPI 99.0)", "quote": quote},
        {"text": "라이선스 Apache-2.0 으로 공개", "quote": quote}]}
    runner = FakeRunner(picks=[pick])
    rac.run(tmp_path, today=DAY, fetcher=FakeFetcher(), runner=runner)
    item = _items(tmp_path)[0]
    assert not any("bogus_tool" in f or "Apache" in f for f in item["facts"])
    assert any(f.startswith("설치 pip install dex_retargeting") for f in item["facts"])      # Python 줄은 메일에 남는다
    assert runner.review_payloads[0]["item"]["facts"] == ["관절 순서는 URDF 관절 이름으로 맞추라고 안내"]
    assert set(item["review"]["api_facts"]) == {f for f in item["facts"] if f != "관절 순서는 URDF 관절 이름으로 맞추라고 안내"}


def test_review_prompt_does_not_exempt_lines_by_shape():
    """사실 대조 지시문에 줄 모양(라이선스·설치 줄) 면제를 되살리면 실패한다 — 출처는 입력 칸이 가른다."""
    prompt = (rac.PROMPTS / "research_assets_review_v1.md").read_text(encoding="utf-8")
    assert "원문에 없어도 문제로 보지 않는다" not in prompt and "면제하지 말고" in prompt


def test_links_and_english_sentences_reject_pick(tmp_path):
    """본문 칸의 URL 차단을 빼거나(링크는 Python 만 붙인다), 한글 비율 검사를 빼서 통째 영어 소개문을 받으면 실패한다."""
    _setup(tmp_path)
    with_link = {**GOOD_PICK, "what": "사람 손 동작을 로봇 손 관절값으로 바꿉니다. 자세한 내용은 https://evil.example/x 를 봅니다."}
    english = {**GOOD_PICK, "what": "Various retargeting optimizers that translate human hand motion into robot joint values 입니다."}
    summary = rac.run(tmp_path, today=DAY, fetcher=FakeFetcher(), runner=FakeRunner(picks=[with_link, english]))
    assert _items(tmp_path) == []
    reasons = " ".join(summary["profiles"]["team_robot"]["rejected"])
    assert "본문에 링크" in reasons and "한국어 문장이 아님" in reasons
    assert rac.korean_enough("LLM 출력을 Pydantic 모델로 받아 검증하고 다시 묻는 라이브러리입니다.")


def test_search_outage_and_broken_tasks_do_not_close_week(tmp_path):
    """검색이 하나도 안 된 것(gh 없음·장애)이나 깨진 과업 파일을 '후보 0건'·'과업 없음'으로 보고 그 주를 끝남으로 적으면 실패한다."""
    _setup(tmp_path)

    class Down(FakeFetcher):
        def github_search(self, query):
            raise rac.CollectError("gh_missing")

    summary = rac.run(tmp_path, today=DAY, fetcher=Down(), runner=FakeRunner())
    state = rac.load_state(tmp_path)
    assert summary["status"] == "partial" and state.get("done_week") is None
    assert "team_robot:RB-05" not in (state.get("last_collected") or {})

    broken = tmp_path / "broken"
    _setup(broken)
    (broken / "tasks.json").write_text("{not json", encoding="utf-8")
    assert rac.run(broken, today=DAY, fetcher=FakeFetcher(), runner=FakeRunner())["status"] == "partial"
    assert rac.load_state(broken).get("done_week") is None


def test_store_failure_does_not_close_week(tmp_path, monkeypatch):
    """항목 저장이 실패했는데 과업·주를 끝남으로 적으면 실패한다 — 그 자료는 보류 재검토에도 안 잡혀 다음 주까지 사라진다."""
    _setup(tmp_path)
    monkeypatch.setattr(research_assets, "upsert_items", lambda items, base: ["참고 자료 저장 실패"] if items else [])
    summary = rac.run(tmp_path, today=DAY, fetcher=FakeFetcher(), runner=FakeRunner())
    state = rac.load_state(tmp_path)
    assert summary["status"] == "partial" and state.get("done_week") is None
    assert "team_robot:RB-05" not in (state.get("last_collected") or {})


def test_one_deadline_covers_pending_review_and_selection(tmp_path):
    """실행 마감 검사를 보류 대조·선정 앞에서 빼면(마감을 넘겨 셸 timeout 에 잘린다) 실패한다 — 모델을 한 번도 부르지 않아야 한다."""
    _setup(tmp_path)
    rac.run(tmp_path, today=DAY, fetcher=FakeFetcher(), runner=FakeRunner(review_error=True))
    assert _items(tmp_path)[0]["status"] == "pending_review"
    now = [0.0]
    late = rac.Budget(deadline_s=rac.CLI_TIMEOUT_S - 1, clock=lambda: now[0])
    runner = FakeRunner()
    summary = rac.run(tmp_path, today=DAY, fetcher=FakeFetcher(), runner=runner, force=True, budget=late)
    assert runner.review_payloads == [] and runner.select_payloads == []
    assert _items(tmp_path)[0]["status"] == "pending_review" and summary["status"] == "partial"


def test_budget_counts_requests_and_wall_clock():
    """요청 수 상한이나 시간 상한 중 하나를 빼면 실패한다."""
    now = [0.0]
    budget = rac.Budget(requests=2, deadline_s=10, clock=lambda: now[0])
    budget.take()
    budget.take()
    with pytest.raises(rac.CollectError, match="budget_requests"):
        budget.take()
    timed = rac.Budget(requests=5, deadline_s=10, clock=lambda: now[0])
    now[0] = 11
    with pytest.raises(rac.CollectError, match="budget_time"):
        timed.take()
    with pytest.raises(rac.CollectError, match="budget_time"):
        timed.check_time()      # 응답 조각 사이에서 부르는 검사
    assert timed.remaining() < 0


def test_hf_card_budget_exhaustion_is_not_swallowed(tmp_path):
    """HF 데이터 카드 조회의 예산 소진을 '카드 없음'으로 삼켜 그 주를 끝남으로 적으면 실패한다."""
    _setup(tmp_path, tasks={"team_robot": [{"task_id": "RB-06", "title": "시연 데이터", "needs": "데이터셋",
                                            "queries": ["hand demo dataset"], "hf": True}]})

    class HfBroke(FakeFetcher):
        def hf_datasets(self, query):
            return [{"id": "someone/demo-data", "tags": ["license:mit"]}]

        def hf_dataset_card(self, dataset_id):
            raise rac.CollectError("budget_time")

    summary = rac.run(tmp_path, today=DAY, fetcher=HfBroke(), runner=FakeRunner(picks=[]))
    assert summary["status"] == "partial" and rac.load_state(tmp_path).get("done_week") is None


def test_concurrent_collector_is_refused(tmp_path):
    """수집기 락을 빼면 실패한다 — 두 실행이 같은 보류 항목을 읽으면 늦게 끝난 쪽의 옛 상태가 ready 를 덮는다."""
    import fcntl
    _setup(tmp_path)
    fetcher, runner = FakeFetcher(), FakeRunner()
    with (tmp_path / ".collect.lock").open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        summary = rac.run(tmp_path, today=DAY, fetcher=fetcher, runner=runner)
    assert summary["status"] == "busy" and fetcher.calls == [] and runner.select_payloads == []


def test_main_treats_busy_as_success_and_failure_as_nonzero(monkeypatch, tmp_path):
    """다른 수집이 도는 중(busy)을 실패로 돌려 셸이 '수집 실패'를 찍게 하거나, partial 을 성공으로 숨기면 실패한다.
    실제 run 은 부르지 않는다 — 진짜 gh 검색·모델 CLI 를 타기 때문이다."""
    for status, code in (("busy", 0), ("ok", 0), ("already_done", 0), ("partial", 1)):
        monkeypatch.setattr(rac, "run", lambda base, today, force=False, _s=status: {"status": _s})
        assert rac.main(["--base", str(tmp_path)]) == code


def test_related_arxiv_comes_from_head_bibtex_or_metadata_only():
    """README 아무 데나 있는 arXiv 링크(비교·관련 연구)를 그 저장소의 논문으로 붙이거나, 머리의 마크다운 링크
    `[Paper](https://arxiv.org/abs/…)` 를 놓치면(정리한 README 에서 찾던 옛 동작) 실패한다."""
    filler = "Benchmark notes and usage examples. " * 120          # 머리(README_HEAD) 밖으로 민다
    head = "# Tool\n\n[Paper](https://arxiv.org/abs/2307.04577) | [Project](https://x.github.io)\n\n" + filler
    assert rac.related_arxiv_id(head, {}) == "2307.04577"
    assert "2307.04577" not in rac.clean_markdown(head)
    compared = "# Tool\n\n" + filler + "| Method | Score |\n| Other [12] https://arxiv.org/abs/2401.00001 | 3.1 |\n"
    assert rac.related_arxiv_id(compared, {}) is None
    cite = compared + "\n```bibtex\n@inproceedings{wang2024dexcap,\n  title={DexCap},\n  journal={arXiv preprint arXiv:2403.07788},\n}\n```\n"
    assert rac.related_arxiv_id(cite, {}) == "2403.07788"
    assert rac.related_arxiv_id(compared, {"description": "Code for https://arxiv.org/abs/2402.12345"}) == "2402.12345"


def test_review_source_keeps_abstract_when_readme_is_long():
    """README 가 길 때 초록이 검토 원문 뒤에서 잘려 나가게 되돌리면 실패한다 — Python 이 초록으로 확인한 근거를 Codex 가 못 받는다."""
    long_readme = "x" * rac.README_KEEP
    cand = {"cid": "gh:o/r", "kind": "github", "title": "o/r", "url": "https://github.com/o/r", "license": "MIT", "updated": "2025-01",
            "source_text": long_readme, "pypi": None, "arxiv": {"id": "2307.04577", "title": "AnyTeleop", "summary": "A unified teleoperation system."}}
    body = {"topic": "t", "name": "r — x", "what": "w", "helps": "h", "doc_facts": [], "evidence": []}
    item = rac.build_item(rac.Task("team_robot", "RB-05", "t", "n", ["q"]), cand, body, DAY)
    assert "A unified teleoperation system." in json.loads(rac.review_payload(item))["source"]


def test_headless_runner_keeps_isolation_flags(monkeypatch):
    """실제 CLI 호출의 격리 플래그(도구 없음·MCP 없음·설정 무시·읽기 전용·웹 검색 끔)나 시간 상한·임시 작업 폴더를 빼면 실패한다.
    FakeRunner 는 이 경로를 안 탄다."""
    import shutil
    import agent_maintenance as am
    calls: list[tuple[list[str], str, int, str]] = []

    def fake_run(argv, stdin_text, timeout, cwd):
        calls.append((argv, stdin_text, timeout, cwd))
        if "-o" in argv:
            Path(argv[argv.index("-o") + 1]).write_text(json.dumps({"verdict": "pass", "problems": []}), encoding="utf-8")
            return 0, "", ""
        return 0, json.dumps({"structured_output": {"picks": [], "note": "없음"}}), ""

    monkeypatch.setattr(shutil, "which", lambda name, path=None: f"/fake/{name}")
    monkeypatch.setattr(rac, "_cli", rac._real_cli)      # conftest 가 막은 CLI 지점을 되살리고 그 아래 `_run` 을 가짜로 — `_run` 을 거치는지도 본다
    monkeypatch.setattr(am, "_run", fake_run)
    runner = rac.HeadlessRunner()
    assert runner.select('{"task": {}}') == {"picks": [], "note": "없음"}
    assert runner.review('{"item": {}}') == {"verdict": "pass", "problems": []}
    (claude, c_in, c_to, c_cwd), (codex, x_in, x_to, x_cwd) = calls
    for flag in ("--tools", "--strict-mcp-config", "--setting-sources", "--no-session-persistence", "--json-schema"):
        assert flag in claude
    assert claude[claude.index("--tools") + 1] == "" and claude[claude.index("--setting-sources") + 1] == ""
    for flag in ("--ephemeral", "--ignore-user-config", "--ignore-rules", "--output-schema", 'web_search="disabled"', "mcp_servers={}"):
        assert flag in codex
    assert codex[codex.index("-s") + 1] == "read-only" and codex[codex.index("--disable") + 1] == "shell_tool"
    assert (c_in, x_in) == ('{"task": {}}', '{"item": {}}') and c_to == x_to == rac.CLI_TIMEOUT_S
    root = str(Path(rac.__file__).resolve().parent)
    assert not c_cwd.startswith(root) and not x_cwd.startswith(root)


def test_conftest_blocks_real_collection_io():
    """conftest 의 수집기 차단을 빼면 실패한다 — 실제 조회기·CLI 가 테스트에서 돌면 gh 검색·모델 사용량을 쓴다."""
    fetcher = rac.NetFetcher(rac.Budget())
    with pytest.raises(ConnectionError):
        fetcher.github_search("hand retargeting")
    with pytest.raises(ConnectionError):
        fetcher.hf_datasets("hand demo")      # pypi() 는 404 를 '정보 없음'으로 삼키므로 예외를 올리는 받기로 본다
    assert fetcher.pypi("dex_retargeting") is None
    with pytest.raises(ConnectionError):
        rac.HeadlessRunner().select("{}")


# ── Codex 재검토(2026-11-02) 뒤 붙인 경계 ───────────────────────────────────────

def test_net_get_follows_redirects_by_hand_within_budget_and_deadline(monkeypatch):
    """httpx 자동 넘겨주기로 되돌리거나(중간 응답 본문을 검사 밖에서 다 읽는다·홉을 예산에 안 센다), http 로 넘어가게 하거나,
    응답 조각마다 보는 마감 검사를 빼면 실패한다. 실제 `_get` 을 가짜 전송 계층으로 부른다 — 네트워크는 안 탄다."""
    import httpx
    monkeypatch.setattr(rac.NetFetcher, "_get", rac._real_net_get)
    hops: list[str] = []
    now = [0.0]

    def drip():
        for _ in range(5):
            now[0] += 100
            yield b"ab"

    def handler(request):
        hops.append(request.url.path)
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/final"}, content=b"x" * 50)
        if request.url.path == "/plain":
            return httpx.Response(302, headers={"location": "http://example.org/final"})
        if request.url.path == "/loop":
            return httpx.Response(302, headers={"location": "/loop"})
        if request.url.path == "/slow":
            return httpx.Response(200, content=drip())
        return httpx.Response(200, content=b"hello")

    budget = rac.Budget(requests=20, deadline_s=1000, clock=lambda: now[0])
    fetcher = rac.NetFetcher(budget, transport=httpx.MockTransport(handler))
    assert fetcher._get("https://example.org/start") == "hello"
    assert hops == ["/start", "/final"] and budget.left == 18          # 홉마다 하나씩
    with pytest.raises(rac.CollectError, match="http_redirect"):
        fetcher._get("https://example.org/plain")
    with pytest.raises(rac.CollectError, match="http_redirects"):
        fetcher._get("https://example.org/loop")
    assert hops.count("/loop") == rac.HTTP_MAX_REDIRECTS + 1
    late = rac.NetFetcher(rac.Budget(requests=5, deadline_s=250, clock=lambda: now[0]), transport=httpx.MockTransport(handler))
    now[0] = 0.0
    with pytest.raises(rac.CollectError, match="budget_time"):
        late._get("https://example.org/slow")


def test_time_spent_in_selection_stops_review_and_next_task(tmp_path):
    """마감 검사가 실행 시작에만 있고 단계 사이에 없으면 실패한다 — 선정이 시간을 다 쓰면 대조·다음 과업을 시작하지 않는다."""
    _setup(tmp_path, tasks={"team_robot": [
        {"task_id": "RB-05", "title": "장갑 원격조작", "needs": "리타게팅", "queries": ["hand retargeting"], "priority": "high"},
        {"task_id": "RB-06", "title": "양팔", "needs": "양팔", "queries": ["bimanual"]}]})
    now = [0.0]

    class Slow(FakeRunner):
        def select(self, payload_json):
            now[0] += rac.CLI_TIMEOUT_S + 20
            return super().select(payload_json)

    runner = Slow()
    fetcher = FakeFetcher()
    budget = rac.Budget(deadline_s=2 * rac.CLI_TIMEOUT_S + 10, clock=lambda: now[0])
    summary = rac.run(tmp_path, today=DAY, fetcher=fetcher, runner=runner, budget=budget)
    assert len(runner.select_payloads) == 1 and runner.review_payloads == []
    assert _items(tmp_path)[0]["status"] == "pending_review"          # 다음 실행이 대조한다
    assert summary["status"] == "partial" and summary["profiles"]["team_robot"]["tasks"] == ["RB-05"]


@pytest.mark.parametrize("tasks_text", [
    "[]",                                                                  # 최상위가 목록
    '{"team_robot": {"task_id": "RB-05"}}',                                # 프로필 값이 객체
    '{"team_robot": [{"task_id": "RB-05", "title": "t"}]}',                # 검색어 없음
    '{"team_robot": [{"task_id": "RB-05", "title": "t", "queries": [" "]}]}',
    '{"team_robot": ["RB-05"]}',                                           # 줄이 문자열
])
def test_malformed_tasks_file_does_not_close_week(tmp_path, tasks_text):
    """과업 파일의 구조 오류를 '과업 없음'으로 읽어 그 주를 끝남으로 적으면 실패한다."""
    _setup(tmp_path)
    (tmp_path / "tasks.json").write_text(tasks_text, encoding="utf-8")
    assert rac.load_tasks(tmp_path) is None
    assert rac.run(tmp_path, today=DAY, fetcher=FakeFetcher(), runner=FakeRunner())["status"] == "partial"
    assert rac.load_state(tmp_path).get("done_week") is None


def test_hf_outage_with_no_github_candidates_is_not_zero_results(tmp_path):
    """GitHub 0건에 HF 검색 장애를 '후보 없음'으로 보고 과업을 끝남으로 적으면 실패한다."""
    _setup(tmp_path, tasks={"team_robot": [{"task_id": "RB-06", "title": "시연 데이터", "needs": "데이터셋",
                                            "queries": ["hand demo dataset"], "hf": True}]})

    class HfDown(FakeFetcher):
        def hf_datasets(self, query):
            raise ConnectionError("down")

    summary = rac.run(tmp_path, today=DAY, fetcher=HfDown(items=[]), runner=FakeRunner(picks=[]))
    state = rac.load_state(tmp_path)
    assert summary["status"] == "partial" and "team_robot:RB-06" not in (state.get("last_collected") or {})


def test_bibtex_scope_ends_at_matching_brace():
    """첫 BibTeX 항목을 짝 맞는 중괄호에서 끊지 않으면(뒤의 관련 연구 링크까지 읽으면) 실패한다."""
    filler = "Benchmark notes and usage examples. " * 120
    readme = ("# Tool\n\n" + filler + "```bibtex\n@article{own2024,\n  title={Our {Nested} Title},\n  year={2024}\n}\n```\n\n"
              "## Related work\nSee also https://arxiv.org/abs/2401.00001 for a baseline.\n")
    assert rac.related_arxiv_id(readme, {}) is None
    assert rac._first_bibtex(readme).endswith("year={2024}\n}")


def test_korean_ratio_ignores_code_identifiers_and_api_filter_has_word_bounds(tmp_path):
    """식별자를 비율에 넣어 정상 한국어 문장을 버리거나, API 모양 검사의 낱말 경계를 빼서 'Aggregated' 같은 정상 사실 줄을 지우면 실패한다."""
    assert rac.korean_enough("torch.nn.Module과 torch.utils.data.DataLoader를 연결합니다. torch.utils.data.Dataset 구현 예제입니다.")
    assert not rac.korean_enough("Various retargeting optimizers that translate human hand motion into robot joint values 입니다.")
    assert not rac._API_LIKE_RE.search("Aggregated 데이터 예제를 제공합니다")
    assert not rac._API_LIKE_RE.search("Gated Recurrent Unit 예제를 제공합니다")
    assert rac._API_LIKE_RE.search("접근이 gated 로 막혀 있음") and rac._API_LIKE_RE.search("Licensed under MIT")
