"""review_core 의 UI 없는 로직 — Streamlit 없이 돈다 (§8-31, 2026-09-02).

배경: review_app.py 는 1,902줄에 커버리지 **0%** 였고 그 안에 ⑦ 전이 지점이
3곳 있다. 함수 33개 중 **20개(382줄)에는 `st.` 호출이 하나도 없다** — UI 와
코어 로직이 한 파일에 섞여 있어서 커버리지를 올릴 방법이 없던 것이다.

**파일을 쪼개기 전에 테스트부터 쓴다.** 0% 커버 파일을 그물 없이 옮기는 건
§8-30 에서 "커버리지 39% 로는 리팩토링 못 한다"고 미뤘던 것보다 더 위험하다.
여기서 그물을 짜 두면 분리는 그 뒤에 안전하게 할 수 있고, 분리를 안 하더라도
회귀 감지라는 실익은 이미 얻는다.

2026-09-02: 처음엔 review_app 에서 그대로 임포트해 테스트했고(그물 먼저),
그 뒤에 review_core.py 로 분리했다. 이 파일은 **Streamlit 을 아예 임포트하지
않는다** — 그게 분리가 실제로 된 증거다.
"""

import sqlite3
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest

import review_core as app
import server


# ---------------------------------------------------------------- 시간 표시


def _iso(**kw):
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()


@pytest.mark.parametrize("kw,want", [
    ({"seconds": 5}, "방금 전"),
    ({"minutes": 7}, "7분 전"),
    ({"hours": 3}, "3시간 전"),
    ({"days": 2}, "2일 전"),
])
def test_relative_time_buckets(kw, want):
    assert app._relative_time(_iso(**kw)) == want


def test_relative_time_treats_naive_timestamp_as_utc():
    """DB 에 타임존 없이 저장된 값이 섞여 있다 — 로컬로 해석하면 9시간이
    어긋나 "9시간 전"이 "방금 전"으로 보인다."""
    naive = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    assert app._relative_time(naive) == "방금 전"


def test_relative_time_passes_through_unparseable():
    """못 읽는 값을 빈칸으로 만들면 화면에서 정보가 사라진다 — 원문을 그대로 둔다."""
    assert app._relative_time("언제인지 모름") == "언제인지 모름"
    assert app._relative_time("") == ""


# ---------------------------------------------------------------- 요약 마크다운 꾸미기


def test_field_label_is_bolded():
    out = app._prettify_summary_markdown("- 입력과 출력 : 이미지를 받는다.")
    assert "**입력과 출력**" in out


def test_grounding_tag_becomes_code_span():
    """[S0142] ★★★ 같은 근거 표기가 본문에 섞이면 읽기 어렵다 — 코드 스팬으로
    묶어 눈에 띄게 하되 내용은 안 바꾼다."""
    out = app._prettify_summary_markdown("정확도 0.99 였다(본문 V절 [S0142] ★★★).")
    assert "`[S0142] ★★★`" in out


def test_prettify_does_not_change_the_numbers():
    """⑤ 검증이 대조하는 건 숫자다. 표시용 가공이 숫자를 건드리면 검증과
    화면이 어긋난다."""
    src = "ROC-AUC 0.990 과 재현율 0.953 을 달성했다 [S0153]."
    out = app._prettify_summary_markdown(src)
    for n in ("0.990", "0.953"):
        assert n in out


# 멱등성은 테스트하지 않는다. 이 함수는 항상 **저장된 원본 마크다운**에
# 적용되지 자기 출력에 다시 적용되지 않는다 — 요구되지 않는 성질을 계약으로
# 굳히면 나중에 정당한 변경을 막는다. (처음에 이걸 테스트로 넣었다가 뺐다.)


# ---------------------------------------------------------------- 프로세스 확인


def test_pid_alive_for_self():
    import os
    assert app._pid_alive(os.getpid()) is True


def test_pid_alive_for_impossible_pid():
    assert app._pid_alive(9_999_999) is False


@pytest.mark.parametrize("bad", [None, 0, -1, "abc", 3.5, True])
def test_pid_alive_rejects_non_positive_and_garbage(bad):
    """**실측으로 잡은 버그(2026-09-02)**: 호출부가 `job.get("pid", -1)` 로
    기본값 -1 을 넘기는데, POSIX 에서 -1 은 "아무 자식이나"라서
    `waitpid(-1)` 이 무관한 자식을 회수하고 `kill(-1, 0)` 은 성공한다 —
    **pid 가 없으면 작업이 영원히 "진행 중"으로 남았다.** 이 함수가 막으려고
    쓰인 바로 그 증상이 기본값으로 다시 들어와 있었다.

    문자열·None 은 예외를 냈다. progress 파일이 깨졌을 때 여기서 예외가 나면
    화면 전체가 죽는다."""
    assert app._pid_alive(bad) is False


# ---------------------------------------------------------------- DB 조회


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    monkeypatch.setattr(server, "DB_PATH", path)
    server._init_storage()
    return path


def _add_summary(db, aid, status="pending"):
    with sqlite3.connect(db) as con:
        con.execute("INSERT OR REPLACE INTO papers (arxiv_id, title, fetched_at) "
                    "VALUES (?,?,?)", (aid, f"제목 {aid}", server._now()))
        con.execute("INSERT OR REPLACE INTO summaries "
                    "(arxiv_id, path, numbers_total, numbers_matched, created_at, review_status) "
                    "VALUES (?,?,?,?,?,?)", (aid, "", 10, 9, server._now(), status))


def test_fetch_review_rows_returns_saved_summaries(db):
    _add_summary(db, "2608.1")
    rows = app._fetch_review_rows()
    assert [r["arxiv_id"] for r in rows] == ["2608.1"]


def test_fetch_review_rows_on_empty_db(db):
    assert app._fetch_review_rows() == []


def test_reproduce_running_reads_the_marker(db, tmp_path, monkeypatch):
    """재현이 도는 중에는 repro_results 에 행이 없어서 DB 만 보면 "기록없음"과
    구분이 안 된다 — 마커 파일이 그 구분이다(digest.repro_label 과 같은 규칙)."""
    repro = tmp_path / "repro"
    repro.mkdir()
    monkeypatch.setattr(server, "REPRO_DIR", repro)
    assert app._reproduce_running("2608.1") is False
    (repro / "2608.1.running").write_text("t")
    assert app._reproduce_running("2608.1") is True


def test_reproduce_running_escapes_slash_in_id(db, tmp_path, monkeypatch):
    """구형 arXiv ID(cs.AI/0601001)에는 '/' 가 들어간다 — 파일명으로 쓰면
    디렉터리로 해석된다."""
    repro = tmp_path / "repro"
    repro.mkdir()
    monkeypatch.setattr(server, "REPRO_DIR", repro)
    (repro / "cs.AI_0601001.running").write_text("t")
    assert app._reproduce_running("cs.AI/0601001") is True


def test_review_core_does_not_import_streamlit():
    """분리가 실제로 됐는지 확인하는 유일한 기계적 증거다. `st.` 을 쓰는
    함수가 하나라도 다시 들어오면 이 파일 전체가 Streamlit 없이는 못 돌고,
    커버리지가 다시 0 으로 돌아간다.

    데코레이터도 포함이다 — `@st.cache_data` 가 붙은 `_translate_cached` 를
    처음에 옮기려다 문법 오류로 드러났다(AST 의 lineno 가 def 줄을 가리켜
    데코레이터를 빠뜨렸다)."""
    import ast
    import review_core
    src = Path(review_core.__file__).read_text(encoding="utf-8")
    assert "import streamlit" not in src
    # 주석·docstring 은 봐준다(이 파일이 왜 나뉘었는지 설명하며 `st.` 을
    # 언급한다). **실제 코드에서** st 를 참조하는지만 본다 — 데코레이터도
    # ast.walk 에 잡힌다.
    tree = ast.parse(src)
    referenced = {n.value.id for n in ast.walk(tree)
                  if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
    referenced |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "st" not in referenced


def test_transition_point_lives_here_now():
    """CLAUDE.md 5 는 ⑦ 자동 전이 지점을 이름으로 못박는다. 그 규칙의 목적은
    "전이 지점이 흩어지지 않는 것"이고, 함수가 통째로 옮겨왔으므로 목적은
    그대로다 — 다만 규칙 문구도 같이 갱신했다.

    이 테스트는 전이 지점이 **여전히 한 곳**임을 확인한다."""
    import review_core
    src = Path(review_core.__file__).read_text(encoding="utf-8")
    assert src.count("docker_runner.launch_background(") == 1
