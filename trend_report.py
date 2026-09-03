"""trend_report.py — 주간 동향 리뷰 (2026-09-02).

"논문들을 쭉 넣은 다음 마지막에 동향 보고가 필요하다"는 요구에 대한 답이다.

**LLM 을 쓰지 않는다.** 전부 셈과 문자열 대조라 위조가 불가능하다
(CLAUDE.md 7). 서술형 리뷰는 검증할 수 없는 산출물이라 판정 경로에 두지
않고, 대신 "무엇이 몇 번 나왔나"를 정확히 센다 — 그게 동향의 실체다.

다이제스트의 "이번 창의 동향 신호" 한 줄이 그날치만 보여주는 것을, 여기서
주 단위로 넓히고 세 가지를 더한다:

  1. 키워드 추이 — 이번 주 vs 지난 주. 늘었나 줄었나가 한 줄로 보인다.
  2. venue·소스 분포 — 어디에 실리는 분야인가(arXiv 인가 저널인가).
  3. **공통 인용** — 이번 주 논문들이 함께 인용한 논문. 이 분야가 무엇을
     딛고 서 있는지를 보여주는 신호이고, 개별 논문 요약으로는 절대 안 나온다.

3번이 이 모듈의 핵심이다. s2_get_references 도구가 있는데 파이프라인에서
한 번도 안 쓰이고 있었다.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

import server

# 공통 인용을 볼 때 논문 하나당 받아올 참고문헌 수. S2 는 초당 1회라
# 논문 수만큼 요청이 나간다 — 주 1회 실행이라 감당할 만하다.
REFERENCES_PER_PAPER = 50

# 공통 인용으로 보고할 최소 횟수. 1이면 그냥 참고문헌 목록이지 "공통"이 아니다.
MIN_SHARED_CITATIONS = 2

# 인용망 조회에 쓸 벽시계 예산(초). 넘으면 **거기까지 모은 것으로** 보고한다.
#
# 실측(2026-09-02): 논문 8편으로 돌렸더니 7분을 넘겼다. S2 가 429 를 뱉으면
# 재시도 예산(server.RATE_LIMIT_*, 최대 450초)이 논문마다 붙기 때문이다.
# 동향 리뷰는 **있으면 좋은 부가 정보**지 다이제스트의 전제가 아니다 —
# 이것 때문에 메일이 늦어지면 주객이 전도된다.
#
# 부분 결과를 그대로 쓰는 게 정직한가: 그렇다. "N편 중 M편까지 봤다"를 같이
# 보고하므로 읽는 사람이 표본 크기를 안다. 조용히 전체인 척하지 않는다.
REFERENCE_BUDGET_SECONDS = 120.0


# ---------------------------------------------------------------- 미등록 용어 (2026-09-03)
#
# **닫힌 고리를 끊는다.** 지금까지 하네스는 core_topics 로 검색하고,
# core_topics 로 점수 매기고, core_topics 별 편수를 세서 "이게 동향"이라고
# 말했다. 새로 뜨는 것은 새 이름을 달고 오므로 모든 단계에서 구조적으로
# 안 보인다 — 우리가 이름 붙인 것만 되돌려주는 거울이었다.
#
# 실측(2026-09-03, 저장된 83편 초록): core_topics 에 없는데 자주 나오는 말이
# 이만큼 있었다.
#
#   5편 anomaly detection      ← 표면검사의 형제어
#   5편 vla models             ← vision-language-action 은 있는데 약어가 없다
#   5편 edge devices
#   4편 jetson orin / orin nano ← 팀이 실제로 쓸 하드웨어
#   4편 retrieval-augmented generation · agentic systems
#
# **LLM 을 안 쓴다**(규칙 7). 기계는 n-gram 을 세기만 하고, 이걸 core_topics 에
# 넣을지는 사람이 정한다. "무엇이 뜨고 있는가"를 모델에게 물으면 검증할 수
# 없는 답이 오지만, "이 말이 몇 편에 나왔나"는 위조가 불가능하다.
#
# 편수로 센다(출현 횟수가 아니라). 한 논문이 같은 말을 20번 해도 1편이다 —
# 아니면 장황한 논문 하나가 동향을 만들어낸다.

# 두 목록을 나눈다.
#
# _BOILERPLATE 는 **조합 어디에도 오면 안 되는 말** — 주제어가 아니라 논문의
# 형식이다("we propose", "code available at https://github.com").
#
# _EDGE_STOP 은 **양 끝에만 못 오는 말** — 불용어와 연구 동사(train·evaluate·
# leverage 등). 동사는 주제가 아니라 형식이라 머리에 오면 `train vla models`
# 같은 잘린 조합이 진짜 용어 `vla models` 를 밀어낸다. 처음에 model·learning 을 상투어로
# 넣었더니 `vla models`(실측 5편)와 `reinforcement learning` 이 통째로
# 죽었다 — 이것들은 합성어의 머리로 쓰일 때 정확히 우리가 찾는 신호다.
# "the model" 은 반대편 끝이 불용어라 어차피 걸리므로, model·learning 자체는
# 어느 목록에도 안 넣는다. 넣었더니 `large language models` 가 통째로 죽고
# 잘린 `large language` 만 남았다 — 꼬리 자리가 바로 우리가 찾는 자리다.
_BOILERPLATE = frozenset("""
https http www github com org net arxiv doi io gitlab huggingface
available publicly release released open source repository
paper study experiments experiment evaluation results result
propose proposed proposes present presents introduce introduces
show shows shown demonstrate demonstrates achieve achieves achieved
state art sota baseline baselines outperform outperforms
compared comparison extensive comprehensive significantly substantially
first second third recent recently novel
approach approaches method methods framework frameworks technique techniques
""".split())

_EDGE_STOP = frozenset("""
a an the of for and or with in on to from by via using use uses used based
toward towards we our this that these those is are be was were can it its as
at into over under between within without more most less than however such
also each other both same many while when where which who what have has had
do does did been being not only but if then there their them they you your
his her will would could should may might must per across new various several
different multiple time times data code analysis here thus hence therefore
train trains trained training evaluate evaluates evaluated apply applies applied
leverage leverages leveraged employ employs employed utilize utilizes utilized
develop develops developed design designs designed build builds built enable
enables enabled allow allows allowed require requires required obtain obtains
""".split()) | _BOILERPLATE

_WORD_RE = re.compile(r"[a-z][a-z0-9\-]+")


def _ngrams(text: str, n: int):
    """상투어·불용어로 시작하거나 끝나는 조합은 버린다 — 'the defect' 같은 것. model·learning 은 머리로는 쓰이므로 양끝만 막는다."""
    words = [w for w in _WORD_RE.findall(text.lower()) if len(w) > 2]
    for i in range(len(words) - n + 1):
        gram = words[i:i + n]
        if gram[0] in _EDGE_STOP or gram[-1] in _EDGE_STOP:
            continue
        if any(w in _BOILERPLATE for w in gram):
            continue
        yield " ".join(gram)


def _subsumed(term: str, others: set[str]) -> bool:
    """더 긴 조합에 그대로 들어 있으면 짧은 쪽은 버린다 —
    'language models' 와 'large language models' 를 둘 다 보고하지 않는다."""
    return any(term != o and term in o for o in others)


def emerging_terms(rows: list[sqlite3.Row], profile: dict,
                   prev_rows: list[sqlite3.Row] | None = None,
                   min_papers: int = 3, top_n: int = 12) -> list[tuple[str, int, int]]:
    """core_topics 에 없는데 자주 나오는 말. (용어, 이번 주 편수, 지난 주 편수).

    이번 주에 min_papers 편 이상 나온 것만 본다 — 한두 편은 우연이다.
    지난 주 편수를 같이 주므로 "새로 생긴 것"과 "원래 있던 것"이 구분된다.
    """
    known = {k.lower() for k in profile.get("core_topics", [])}
    known |= {d.lower() for d in (profile.get("domain_hints") or [])}

    def count(rs) -> Counter:
        c: Counter = Counter()
        for row in rs or []:
            text = f"{row['title'] or ''}. {row['abstract'] or ''}"
            seen = set()
            for n in (2, 3):
                seen.update(_ngrams(text, n))
            c.update(seen)          # 편수 — 한 논문이 반복해도 1
        return c

    now, prev = count(rows), count(prev_rows)
    cand = {g for g, n in now.items()
            if n >= min_papers and not any(k in g or g in k for k in known)}
    cand = {g for g in cand if not _subsumed(g, cand)}
    ranked = sorted(cand, key=lambda g: (-now[g], g))
    return [(g, now[g], prev.get(g, 0)) for g in ranked[:top_n]]


# ---------------------------------------------------------------- 서술 (2026-09-03)
#
# "규칙 기반으로 어떻게 동향을 보고하나"는 지적을 받고 넣었다. 맞는 말이다 —
# 편수 표는 무엇이 몇 편인지는 알려주지만 **무엇과 무엇이 이어지는지**는 못
# 말한다. 그건 글이어야 하고, 글은 LLM 이 쓴다.
#
# **규칙 7 위반이 아니다.** 규칙 7 이 막는 건 *판정*이다 — 검증 통과 여부,
# 재현 성공 여부처럼 파이프라인이 그걸 근거로 다음 행동을 정하는 자리.
# 여기서 나오는 글은 아무것도 정하지 않는다. 논문을 고르지도, 점수를 바꾸지도,
# 다이제스트를 막지도 않는다. 사람이 읽는 글이고, ④ 요약이 이미 같은 범주다.
#
# **진짜 제약은 규칙 4다: LLM 에는 공개 논문 텍스트만 보낸다.** 그래서
# 프롬프트에 다음을 절대 넣지 않는다 — core_topics(팀의 연구 방향 그 자체다),
# 우리 집계, 프로필 이름, 팀 이름, emerging_terms 결과(core_topics 를 빼서
# 만든 것이라 여집합이 새어 나간다). 모델이 보는 건 **이번 주 논문의 제목과
# 초록뿐**이고, 그것도 공개 출처(arXiv·오픈액세스)로 한정한다. 수동 업로드
# PDF 는 출처를 우리가 보증할 수 없어 뺀다.
#
# **규칙 8은 두 겹으로 지킨다.** 프롬프트에서 숫자를 못 쓰게 하고, 그래도
# 나오면 원문 대조로 걸러 표시한다. 편수는 위 셈 절이 이미 정확히 갖고 있고,
# 그 숫자와 모델이 지어낸 숫자가 한 화면에서 섞이는 게 제일 나쁘다.

def _field(row, name: str, default: str = "") -> str:
    """sqlite3.Row 는 .get() 이 없다 — 없는 컬럼에 죽지 않게 감싼다.
    주간 리뷰는 부가 정보라 필드 하나 때문에 다이제스트를 막으면 안 된다."""
    try:
        value = row[name]
    except (KeyError, IndexError):
        return default
    return default if value is None else str(value)


# 줄머리 목차 번호("1." "2)" "- 3.")는 숫자 주장이 아니다.
_LIST_MARKER_RE = re.compile(r"^[\s\-*·]*\d+[.)]\s", re.MULTILINE)

NARRATIVE_MAX_PAPERS = 20      # 한 번의 프롬프트에 넣을 논문 수 상한
NARRATIVE_ABSTRACT_CHARS = 900  # 논문당 초록 길이 상한
NARRATIVE_PUBLIC_SOURCES = ("arxiv", "open-access")

_NARRATIVE_PROMPT = """아래는 최근 발표된 논문들의 제목과 초록이다.
이것만 보고 한국어 평서체로 짧게 쓴다.

1. 여러 논문에 공통으로 나타나는 기술적 흐름 (최대 3개)
2. 서로 다른 갈래가 만나는 지점이 있으면 그것
3. 눈에 띄게 새로운 접근이 있으면 그것

지킬 것:
- 초록에 없는 내용을 쓰지 않는다. 근거가 없으면 "이 표본으로는 알 수 없다"고 쓴다.
- **숫자·통계·비율·증감을 쓰지 않는다.** 편수는 따로 집계돼 있다.
- 논문을 가리킬 때는 제목 앞부분을 그대로 인용한다.
- 전체 400자 이내. 항목마다 한두 문장.

논문 목록:
{papers}
"""


def _narrative_corpus(rows: list) -> tuple[str, int]:
    """프롬프트에 넣을 공개 논문 텍스트. (본문, 넣은 편수).

    공개 출처만 넣는다 — 규칙 4. source 가 비어 있으면 arXiv 델타에서 온
    것이라 공개다(papers.source 는 비-arXiv 경로에서만 채워진다).
    """
    parts, used = [], 0
    for row in rows:
        src = (_field(row, "source") or "arxiv").lower()
        if not src.startswith(NARRATIVE_PUBLIC_SOURCES):
            continue
        title = (_field(row, "title") or "").strip()
        abstract = (_field(row, "abstract") or "").strip()[:NARRATIVE_ABSTRACT_CHARS]
        if not title:
            continue
        parts.append(f"- {title}\n  {abstract}" if abstract else f"- {title}")
        used += 1
        if used >= NARRATIVE_MAX_PAPERS:
            break
    return "\n".join(parts), used


def ungrounded_numbers(text: str, corpus: str) -> list[str]:
    """서술에 나왔는데 원문에 없는 숫자. 규칙 8 의 두 번째 겹.

    verify.py 의 추출·정규화를 그대로 빌려 쓴다(읽기만 한다 — 민감 모듈이라
    고치지 않는다). 규칙이 하나면 검증기와 여기가 어긋날 일이 없다.
    """
    import verify
    normalized = corpus.replace(",", "")
    # 줄머리의 "1." "2)" 는 목차 번호지 주장이 아니다. 실측(2026-09-03 첫 라이브
    # 호출)에서 이걸 안 빼니 멀쩡한 서술에 ['1','3'] 경고가 붙었다 — 매번 뜨는
    # 경고는 아무도 안 읽으므로 진짜 조작을 놓치게 만든다.
    body = _LIST_MARKER_RE.sub("", text)
    out = []
    for m in verify._NUMBER_RE.finditer(body):
        norm = verify._normalize(m.group(1))
        if not verify._number_in_text(norm, normalized):
            out.append(m.group(1))
    return sorted(set(out))


async def narrative(client: httpx.AsyncClient, rows: list) -> tuple[str, list[str]] | None:
    """이번 주 논문 제목·초록만으로 쓴 서술. (글, 검증 안 된 숫자들).

    실패하면 None — 셈 절은 그대로 나간다. 서술은 부가 정보다.
    """
    import summarize_engine as se

    corpus, used = _narrative_corpus(rows)
    if used < 3:
        return None      # 표본이 이보다 적으면 "흐름"이라 부를 게 없다

    prompt = _NARRATIVE_PROMPT.format(papers=corpus)
    try:
        text = await se._call_with_rate_limit_retry(
            lambda: se._post_gemini(client, prompt), "Gemini(동향 서술)")
    except Exception:
        try:
            text = await se._call_with_rate_limit_retry(
                lambda: se._post_groq(client, prompt), "Groq(동향 서술)")
        except Exception:
            return None
    text = (text or "").strip()
    if not text:
        return None
    return text, ungrounded_numbers(text, corpus)


def _rows_between(db: Path, start: datetime, end: datetime) -> list[sqlite3.Row]:
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        return con.execute(
            "SELECT p.arxiv_id, p.title, p.abstract, p.published, p.source, s.engine, s.coverage_ratio "
            "FROM papers p JOIN summaries s ON s.arxiv_id = p.arxiv_id "
            "WHERE s.created_at >= ? AND s.created_at < ? ORDER BY s.created_at",
            (start.isoformat(), end.isoformat()),
        ).fetchall()


def keyword_counts(rows: list[sqlite3.Row], profile: dict) -> Counter:
    """저장된 논문 제목에서 핵심 키워드 적중을 센다.

    초록이 아니라 제목만 본다 — 주간 추이는 "무엇에 대한 논문이 나왔나"이지
    "어떤 낱말이 본문 어딘가에 있었나"가 아니다. 제목이 훨씬 정확한 신호다.
    """
    import profile_scoring
    counts: Counter = Counter()
    for row in rows:
        hits, _total, _top = profile_scoring.core_hits_with_weight(
            {"title": row["title"], "abstract": ""}, profile)
        counts.update(hits)
    return counts


def source_mix(rows: list[sqlite3.Row]) -> Counter:
    """arXiv 인가 저널인가. S2 를 붙인 뒤 이 비율이 바뀌는지가 관심사다."""
    mix: Counter = Counter()
    for row in rows:
        src = row["source"] or ""
        mix["저널(오픈액세스)" if src.startswith("open-access") else
            "수동 업로드" if src.startswith("manual-pdf") else "arXiv"] += 1
    return mix


def engine_mix(rows: list[sqlite3.Row]) -> Counter:
    mix: Counter = Counter()
    for row in rows:
        mix[row["engine"] or "(미기록)"] += 1
    return mix


def partial_coverage(rows: list[sqlite3.Row], below: float = 0.98) -> list[tuple[str, float]]:
    """원문을 다 못 본 요약들. Groq 폴백 날에만 생긴다(§8-25)."""
    out = []
    for row in rows:
        ratio = row["coverage_ratio"]
        if ratio is not None and ratio < below:
            out.append((row["title"], float(ratio)))
    return sorted(out, key=lambda x: x[1])


async def shared_references(
    client: httpx.AsyncClient, rows: list[sqlite3.Row],
    limit: int = REFERENCES_PER_PAPER, budget_s: float = REFERENCE_BUDGET_SECONDS,
) -> tuple[list[tuple[str, int]], int, int]:
    """이번 주 논문들이 **함께 인용한** 논문. 이 분야가 뭘 딛고 서 있는지.

    returns (공통 인용 목록, 실제로 조회한 논문 수, 조회 대상이던 논문 수)
    — 표본 크기를 같이 돌려준다. 예산에 걸려 일부만 봤을 때 그걸 숨기면
    "전체를 본 결과"로 오해된다.

    arXiv ID 가 있는 논문만 조회한다 — S2 인용망은 arXiv ID 로 찾는다.
    실패는 조용히 건너뛴다: 동향 보고가 못 나온다고 다이제스트를 막으면 안 된다.
    """
    counter: Counter = Counter()
    targets = [r for r in rows
               if (r["arxiv_id"] or "") and not (r["arxiv_id"] or "").startswith("pdf-")]
    started = time.monotonic()
    examined = 0
    for row in targets:
        if time.monotonic() - started > budget_s:
            break
        try:
            raw = await server._s2_citation_graph(row["arxiv_id"], limit, "references")
            refs = json.loads(raw).get("papers") or []
        except Exception:  # noqa: BLE001
            continue
        examined += 1
        # 같은 논문 안에서 같은 참고문헌이 두 번 세지지 않게 제목으로 유일화
        for title in {(r.get("title") or "").strip() for r in refs if r.get("title")}:
            counter[title] += 1
    shared = [(t, n) for t, n in counter.most_common(20) if n >= MIN_SHARED_CITATIONS]
    return shared, examined, len(targets)


def format_report(this_week: list[sqlite3.Row], last_week: list[sqlite3.Row],
                  profile: dict, shared: list[tuple[str, int]] | None = None,
                  examined: int = 0, targets: int = 0,
                  story: tuple[str, list[str]] | None = None) -> str:
    """사람이 메일에서 바로 읽는 형태.

    **셈과 서술을 섞지 않는다.** 위쪽은 전부 기계가 센 숫자라 위조가 불가능하고,
    맨 아래 서술 절만 LLM 이 쓴 글이다. 둘을 한 문단에 섞으면 읽는 사람이
    어디까지가 측정이고 어디부터가 해석인지 구분할 수 없게 된다 — 그게 이
    프로젝트가 라벨로 계속 막아 온 뭉갬이다(§8-23·24·33).
    """
    now = keyword_counts(this_week, profile)
    prev = keyword_counts(last_week, profile)

    lines = ["■ 주간 동향 리뷰", ""]
    lines.append(f"처리한 논문 {len(this_week)}편 (지난주 {len(last_week)}편)")

    mix = source_mix(this_week)
    if mix:
        lines.append("  출처 : " + " · ".join(f"{k} {v}편" for k, v in mix.most_common()))
    emix = engine_mix(this_week)
    if emix:
        lines.append("  엔진 : " + " · ".join(f"{k} {v}편" for k, v in emix.most_common()))

    partial = partial_coverage(this_week)
    if partial:
        lines.append(f"  ⚠ 원문을 다 못 본 요약 {len(partial)}편 "
                     f"(최저 {partial[0][1] * 100:.0f}%) — Groq 폴백 영향(§8-25)")

    if now:
        lines += ["", "▶ 주제별 편수 (지난주 대비)"]
        for kw, n in now.most_common(12):
            was = prev.get(kw, 0)
            delta = n - was
            arrow = f"  ({was}→{n}, {delta:+d})" if was or delta else ""
            lines.append(f"   {kw} {n}{arrow}")

    gone = [kw for kw in prev if kw not in now]
    if gone:
        lines.append(f"   지난주엔 있었으나 이번주 없음: {', '.join(sorted(gone)[:8])}")

    fresh = emerging_terms(this_week, profile, last_week)
    if fresh:
        lines += ["", "▶ 등록 안 된 말 중 자주 나온 것 (키워드로 넣을지는 사람이 판단)"]
        for term, n, was in fresh:
            if was == 0:
                lines.append(f"   {term} — {n}편 (지난주 없었음)")
            else:
                lines.append(f"   {term} — {n}편 ({was}→{n}, {n - was:+d})")
        lines.append("   ※ core_topics 에 없어서 검색·점수·추이 어디에도 안 잡히는 말들이다.")

    if shared:
        scope = f"{examined}/{targets}편 조회" if targets else ""
        lines += ["", f"▶ 이번 주 논문들이 함께 인용한 논문 ({scope})"]
        if targets and examined < targets:
            lines.append("   (시간 예산으로 일부만 봤다 — 아래는 그 표본 기준이다)")
        for title, n in shared[:10]:
            lines.append(f"   {n}편이 인용 — {title[:70]}")
    elif shared is not None:
        lines += ["", f"▶ 공통 인용: 없음 ({examined}/{targets}편 조회 — "
                      "논문들이 서로 다른 토대를 쓰고 있다)"]

    if story:
        text, ungrounded = story
        lines += ["", "─" * 60,
                  "▶ 서술 (LLM 이 이번 주 논문의 제목·초록만 보고 쓴 것 — 위 숫자와 달리 검증되지 않았다)"]
        lines += [f"   {ln}" for ln in text.strip().splitlines()]
        if ungrounded:
            lines.append(f"   ⚠ 원문에 없는 숫자가 섞여 있다: {', '.join(ungrounded)} — 믿지 말 것")

    return "\n".join(lines) + "\n"


async def build(db: Path, profile: dict, client: httpx.AsyncClient | None = None,
                days: int = 7, with_references: bool = True,
                with_narrative: bool = True) -> str:
    """주간 리뷰 본문. client 를 안 주면 인용망 조회와 서술을 건너뛴다(네트워크 없음)."""
    end = datetime.now(timezone.utc)
    this_week = _rows_between(db, end - timedelta(days=days), end)
    last_week = _rows_between(db, end - timedelta(days=days * 2), end - timedelta(days=days))
    shared, examined, targets = None, 0, 0
    if client is not None and with_references and this_week:
        shared, examined, targets = await shared_references(client, this_week)
    story = None
    if client is not None and with_narrative and this_week:
        story = await narrative(client, this_week)
    return format_report(this_week, last_week, profile, shared, examined, targets, story)
