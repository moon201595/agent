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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

import http_client

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
# **규칙 4(2026-09-03 개정)를 따른다: 밖에 나가도 되는 것만 보낸다.**
#
# 처음엔 관심 분야까지 뺐다. 그랬더니 서술이 "VLA 연구가 이어진다" 같은 일반
# 요약만 하고 **"그게 우리 분야와 어디서 만나는가"를 못 썼다** — 그게 이
# 시스템의 목적 그 자체인데. 규칙을 지키느라 목적을 놓친 경우라 규칙을 고쳤다.
#
# 보내는 것: 논문 제목·초록(출처 무관 — ④ 요약이 이미 직접 올린 PDF 를 보내고
# 있어 여기만 엄격한 건 앞뒤가 안 맞았다), 그리고 관심 분야 키워드
# (core_topics·domain_hints). "defect detection" 같은 일반 기술 용어다.
#
# 안 보내는 것은 그대로다: 우리 집계·편수·별점, emerging_terms 결과
# (core_topics 를 빼서 만든 것이라 "우리가 아직 안 보는 것"이 드러난다),
# 재현 성공률, 사내 문서. 가르는 기준은 **"무엇에 관심 있나"는 나가도 되지만
# "무엇을 하고 있나"는 안 된다**.
#
# **저자 이름은 안 보낸다**(2026-09-03 결정). 규칙 4 의 새 경계선으로는
# "공개된 논문 메타데이터니까 허용"으로 읽히지만, 그건 보내도 되느냐의 답이지
# 보내야 하느냐의 답이 아니다. "이 그룹이 이 주제를 밀고 있다"를 LLM 에 쓰게
# 하면 개인·기관 프로파일링을 외부 모델에 시키는 게 되는데, 저자 빈도 집계는
# 로컬 셈으로 똑같이 나온다(§8-40). **얻는 게 같고 성격만 나쁘면 안 보낸다.**
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

_NARRATIVE_PROMPT = """아래는 최근 발표된 논문들의 제목과 초록이다.
읽는 사람이 관심 있는 분야는 다음과 같다: {topics}

이것만 보고 한국어 평서체로 짧게 쓴다.

1. 여러 논문에 공통으로 나타나는 기술적 흐름 (최대 3개)
2. 그 흐름이 위 관심 분야와 만나는 지점 — 우리가 왜 봐야 하는가
3. 관심 분야 밖이지만 곧 넘어올 것 같은 움직임이 있으면 그것

지킬 것:
- 초록에 없는 내용을 쓰지 않는다. 근거가 없으면 "이 표본으로는 알 수 없다"고 쓴다.
- **숫자·통계·비율·증감을 쓰지 않는다.** 편수는 따로 집계돼 있다.
- 논문을 가리킬 때는 제목 앞부분을 그대로 인용한다.
- 관심 분야 목록을 그대로 나열하지 않는다. 논문과 이어질 때만 언급한다.
- 전체 500자 이내. 항목마다 한두 문장.

논문 목록:
{papers}
"""


def _narrative_corpus(rows: list) -> tuple[str, int]:
    """프롬프트에 넣을 논문 텍스트. (본문, 넣은 편수).

    출처로 거르지 않는다 — 규칙 4(2026-09-03 개정)는 논문 텍스트를 arXiv·
    오픈액세스·직접 올린 PDF 모두 허용한다. 여기만 걸렀더니 ④ 요약이 이미
    직접 올린 PDF 를 LLM 에 보내고 있는 것과 앞뒤가 안 맞았다(실측:
    `pdf-5bd2ec925e` 는 요약이 이미 있다).
    """
    parts, used = [], 0
    for row in rows:
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


def narrative_topics(profile: dict, rows: list | None = None, limit: int = 12) -> str:
    """프롬프트에 넣을 관심 분야.

    **이번 주 논문에 실제로 걸린 키워드만 보낸다**(2026-09-03).
    서술의 목적이 "이번 흐름이 우리와 어디서 만나나"이므로 안 걸린 키워드는
    프롬프트에 있을 이유가 없다. 기능은 그대로고 노출만 준다.

    왜 노출을 줄이나: 키워드 하나하나는 일반 기술 용어라 규칙 4 로 나가도
    되지만, **27개를 한 줄로 늘어놓으면 조합이 정보가 된다** — "검사·온센서·
    로봇·비접촉 생체신호를 동시에 한다"는 과제 구성에 가깝고, 그건 규칙 4 가
    그은 선의 반대쪽이다. 걸린 것만 보내면 매주 나가는 조합이 달라져 전체
    구성이 한 번에 드러나지 않는다.

    **가중치 값은 여전히 안 보낸다** — 순서에만 쓴다. "무엇에 관심 있나"는
    나가도 되지만 "무엇을 얼마나 중요하게 보나"는 우선순위라 "무엇을 하고
    있나"에 가깝다.
    """
    weights = profile.get("core_weights") or {}
    topics = profile.get("core_topics", [])
    if rows is not None:
        hit = set(keyword_counts(rows, profile))
        topics = [t for t in topics if t in hit] or topics
    ranked = sorted(topics, key=lambda k: (-float(weights.get(k, 1.0)), k))
    return ", ".join(ranked[:limit])


async def narrative(client: httpx.AsyncClient, rows: list,
                    profile: dict | None = None) -> tuple[str, list[str]] | None:
    """이번 주 논문과 관심 분야로 쓴 서술. (글, 검증 안 된 숫자들).

    실패하면 None — 셈 절은 그대로 나간다. 서술은 부가 정보다.
    """
    import summarize_engine as se

    corpus, used = _narrative_corpus(rows)
    if used < 3:
        return None      # 표본이 이보다 적으면 "흐름"이라 부를 게 없다

    topics = narrative_topics(profile or {}, rows) or "(지정 없음)"
    prompt = _NARRATIVE_PROMPT.format(papers=corpus, topics=topics)
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
            "SELECT p.arxiv_id, p.title, p.abstract, p.authors, p.published, p.source, "
            "s.engine, s.coverage_ratio "
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


def author_counts(rows: list[sqlite3.Row], min_papers: int = 2,
                  top_n: int = 10) -> list[tuple[str, int]]:
    """이 기간에 여러 편을 낸 저자. (이름, 편수).

    "누가 이 분야를 밀고 있나"는 조사 요약의 핵심인데 `papers.authors` 를
    90편 전부 갖고 있으면서 어디서도 안 읽고 있었다(§8-40). **추가 API 호출이
    0회다** — 이미 저장된 값을 세기만 한다.

    **셈 절에만 쓴다. 프롬프트에는 안 보낸다**(2026-09-03 결정) — 저자 빈도는
    로컬 셈으로 똑같이 나오는데 LLM 에 넘기면 개인·기관 프로파일링을 외부
    모델에 시키는 게 된다. `_narrative_corpus` 주석 참고.

    편수로 센다. 공저자가 많은 논문 한 편이 저자 전원을 1편씩 올리므로
    min_papers=2 가 실질적인 하한이다 — 1편짜리를 세면 그냥 저자 목록이다.
    """
    counts: Counter = Counter()
    for row in rows:
        raw = _field(row, "authors")
        if not raw:
            continue
        try:
            names = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(names, list):
            continue
        # 한 논문 안에서 같은 이름이 두 번 나와도 1편이다
        counts.update({n.strip() for n in names if isinstance(n, str) and n.strip()})
    return [(n, k) for n, k in counts.most_common(top_n) if k >= min_papers]


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


@dataclass
class ReferenceScan:
    """공통 인용 조회 한 번에서 나오는 것 전부.

    처음엔 (공통인용, 조회수, 대상수) 3-튜플이었는데 계보 묶기와 인용수 정렬,
    최전선 조회가 **같은 응답에서** 나오면서 6개가 됐다. 튜플로 6개를 돌려주면
    호출부에서 순서를 세게 되므로 이름을 붙인다 — 값이 늘어난 건 추가 호출이
    생겨서가 아니라 **버리던 걸 안 버리게 됐기 때문**이다(§8-40).
    """
    shared: list[tuple[str, int]]        # (참고문헌 제목, 함께 인용한 편수)
    examined: int                        # 실제로 조회한 우리 논문 수
    targets: int                         # 조회 대상이던 우리 논문 수
    by_paper: dict[str, set[str]]        # 우리 논문 → 참고문헌 제목 집합 (계보용)
    cites: dict[str, int]                # 참고문헌 제목 → 그 논문의 총 인용수
    ref_ids: dict[str, str]              # 참고문헌 제목 → arXiv ID (최전선 조회용)


async def shared_references(
    client: httpx.AsyncClient, rows: list[sqlite3.Row],
    limit: int = REFERENCES_PER_PAPER, budget_s: float = REFERENCE_BUDGET_SECONDS,
) -> ReferenceScan:
    """이번 주 논문들이 **함께 인용한** 논문. 이 분야가 뭘 딛고 서 있는지.

    returns (공통 인용 목록, 조회한 논문 수, 조회 대상 수, 논문별 참고문헌
    집합, 참고문헌별 인용수) — 표본 크기를 같이 돌려준다. 예산에 걸려 일부만
    봤을 때 그걸 숨기면 "전체를 본 결과"로 오해된다.
    뒤의 둘은 계보 묶기와 정렬용이고, **같은 응답에서 나오므로 추가 호출이 없다.**

    arXiv ID 가 있는 논문만 조회한다 — S2 인용망은 arXiv ID 로 찾는다.
    실패는 조용히 건너뛴다: 동향 보고가 못 나온다고 다이제스트를 막으면 안 된다.
    """
    counter: Counter = Counter()
    cites: dict[str, int] = {}
    ref_ids: dict[str, str] = {}
    by_paper: dict[str, set[str]] = {}
    targets = [r for r in rows
               if (r["arxiv_id"] or "") and not (r["arxiv_id"] or "").startswith("pdf-")]
    started = time.monotonic()
    examined = 0
    for row in targets:
        if time.monotonic() - started > budget_s:
            break
        try:
            raw = await http_client.s2_citation_graph(row["arxiv_id"], limit, "references")
            refs = json.loads(raw).get("papers") or []
        except Exception:  # noqa: BLE001
            continue
        examined += 1
        # 같은 논문 안에서 같은 참고문헌이 두 번 세지지 않게 제목으로 유일화
        titles = {(r.get("title") or "").strip() for r in refs if r.get("title")}
        counter.update(titles)
        # 논문별 집합을 **버리지 않고 남긴다** — 계보 묶기가 이걸 쓴다.
        # 추가 호출이 0회인 이유가 이것이다(§8-40).
        by_paper[row["arxiv_id"]] = titles
        # citationCount 는 이미 응답에 온다(http_client.s2_citation_graph 의 fields).
        # 필드를 더 요청할 필요도 없다.
        for r in refs:
            t = (r.get("title") or "").strip()
            if not t:
                continue
            n = r.get("citationCount")
            if isinstance(n, int):
                cites[t] = max(cites.get(t, 0), n)
            # externalIds 도 이미 응답에 온다 — 최전선 조회의 씨앗이 된다.
            aid = ((r.get("externalIds") or {}).get("ArXiv") or "").strip()
            if aid:
                ref_ids.setdefault(t, aid)

    # 몇 편이 함께 인용했나가 1순위, 그 논문 자체의 인용수가 2순위.
    # **2순위를 넣는 이유**: 지금은 "이 분야의 토대라서 다들 인용한다"와
    # "이 3편이 우연히 같은 무명 논문을 인용했다"가 같은 줄에 섞여 있다.
    # delta 논문은 인용수가 0이라 신호가 없지만(§8-40 실측), **공통 인용으로
    # 올라오는 논문은 오래된 논문이라 인용수가 실제로 크다** — 같은 필드를
    # 값이 있는 곳에 쓰는 것이다.
    ranked = sorted(counter.items(), key=lambda kv: (-kv[1], -cites.get(kv[0], 0), kv[0]))
    shared = [(t, n) for t, n in ranked[:20] if n >= MIN_SHARED_CITATIONS]
    return ReferenceScan(shared, examined, len(targets), by_paper, cites, ref_ids)


# 두 논문이 "같은 토대 위에 있다"고 부를 참고문헌 겹침 비율(Jaccard).
# 0.15 는 참고문헌 20편 기준 대략 3편이 겹치는 수준이다 — 우연이라기엔 많고
# 같은 주제라기엔 느슨한, 계보를 말하기 시작할 만한 선.
LINEAGE_MIN_JACCARD = 0.15


def lineage_groups(by_paper: dict[str, set[str]], rows: list[sqlite3.Row],
                   min_jaccard: float = LINEAGE_MIN_JACCARD) -> list[list[str]]:
    """참고문헌이 겹치는 논문끼리 묶는다. (그룹별 논문 제목 목록)

    **추가 API 호출이 0회다** — `shared_references` 가 이미 받아온 참고문헌
    집합을 재사용한다. 지금까지는 그 집합을 세고 나서 버리고 있었다(§8-40).

    공통 인용 절이 "다들 무엇을 딛고 있나"라면 여기는 "누가 누구와 같은 데를
    딛고 있나"다. 평평한 논문 목록이 갈래로 보이기 시작하는 지점이고, 개별
    논문 요약으로는 절대 안 나온다.

    묶는 방법은 단일 연결(하나라도 임계 이상 겹치면 같은 그룹)이다. 계층
    군집이나 그래프 라이브러리를 쓰지 않는다 — 한 주에 논문 수십 편 규모라
    O(n²) 비교로 충분하고, 새 의존성이 주는 이득이 없다.
    """
    ids = [i for i in by_paper if by_paper[i]]
    parent = {i: i for i in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a_idx, a in enumerate(ids):
        for b in ids[a_idx + 1:]:
            sa, sb = by_paper[a], by_paper[b]
            union = len(sa | sb)
            if union and len(sa & sb) / union >= min_jaccard:
                parent[find(a)] = find(b)

    titles = {(_field(r, "arxiv_id")): (_field(r, "title") or "(제목 없음)") for r in rows}
    groups: dict[str, list[str]] = {}
    for i in ids:
        groups.setdefault(find(i), []).append(titles.get(i, i))
    # 혼자인 논문은 "갈래"가 아니다
    return sorted((g for g in groups.values() if len(g) > 1), key=len, reverse=True)


# 최전선을 볼 때 토대 논문 몇 편까지 거슬러 올라갈지. S2 는 초당 1회라
# 여기 넣는 수만큼 호출이 늘어난다 — 주 1회 실행이고 부가 정보라 3편이면 족하다.
FRONTIER_SEED_PAPERS = 3
FRONTIER_RECENT_YEARS = 2


async def frontier_papers(
    client: httpx.AsyncClient, shared: list[tuple[str, int]],
    seed_ids: dict[str, str], limit: int = REFERENCES_PER_PAPER,
    budget_s: float = REFERENCE_BUDGET_SECONDS,
) -> tuple[list[tuple[str, int]], int]:
    """이 분야의 토대 논문을 **최근에 인용한** 논문들. (제목, 인용한 토대 수), 조회 수.

    **순진한 형태는 안 된다.** "우리 논문을 누가 인용하나"를 보려 했는데,
    delta 검색이 물어오는 논문은 30일 이내라 인용수가 0 이다(§8-40 실측:
    저장 논문의 41% 가 30일 이내). 물어볼 대상 자체가 없다.

    **방향을 뒤집는다.** `shared_references` 가 찾아낸 토대 논문은 오래됐고
    인용이 많다 — 그걸 **누가 지금 인용하고 있나**를 보면 이 분야의 최전선이
    나온다. 그리고 그중 상당수는 **우리 키워드에 안 걸린 논문**이다.
    §8-39 가 "닫힌 고리의 절반만 끊었다"고 적은 나머지 절반이 여기다:
    미등록 용어는 *이미 수집한* 논문 안에서 못 보던 말을 찾지만, 이건
    *수집 대상 밖*에서 관련 논문을 데려온다.

    여러 토대를 동시에 인용한 논문일수록 이 분야에 가깝다 — 그걸로 정렬한다.
    실패와 예산 초과는 조용히 부분 결과로 끝낸다(공통 인용과 같은 원칙).
    """
    counter: Counter = Counter()
    started = time.monotonic()
    examined = 0
    cutoff = datetime.now(timezone.utc).year - FRONTIER_RECENT_YEARS
    for title, _n in shared[:FRONTIER_SEED_PAPERS]:
        seed = seed_ids.get(title)
        if not seed:
            continue
        if time.monotonic() - started > budget_s:
            break
        try:
            raw = await http_client.s2_citation_graph(seed, limit, "citations")
            citing = json.loads(raw).get("papers") or []
        except Exception:  # noqa: BLE001
            continue
        examined += 1
        for p in citing:
            t = (p.get("title") or "").strip()
            year = p.get("year")
            # 오래된 인용은 최전선이 아니다 — 지금 누가 쓰고 있나가 관심사다
            if t and isinstance(year, int) and year >= cutoff:
                counter[t] += 1
    return counter.most_common(10), examined


def format_report(this_week: list[sqlite3.Row], last_week: list[sqlite3.Row],
                  profile: dict, shared: list[tuple[str, int]] | None = None,
                  examined: int = 0, targets: int = 0,
                  story: tuple[str, list[str]] | None = None,
                  lineage: list[list[str]] | None = None,
                  cites: dict[str, int] | None = None,
                  frontier: list[tuple[str, int]] | None = None) -> str:
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
            # 그 논문 자체의 인용수를 같이 보여준다 — "분야의 토대라 다들
            # 인용한다"와 "우연히 같은 무명 논문을 인용했다"가 갈린다.
            total = (cites or {}).get(title)
            weight = f" (총 인용 {total:,})" if total else ""
            lines.append(f"   {n}편이 인용{weight} — {title[:70]}")
    elif shared is not None:
        lines += ["", f"▶ 공통 인용: 없음 ({examined}/{targets}편 조회 — "
                      "논문들이 서로 다른 토대를 쓰고 있다)"]

    if lineage:
        lines += ["", "▶ 같은 토대를 쓰는 논문 묶음 (참고문헌이 겹치는 것끼리)"]
        for i, group in enumerate(lineage[:4], start=1):
            lines.append(f"   갈래 {i} — {len(group)}편")
            for title in group[:5]:
                lines.append(f"      · {title[:66]}")
        lines.append("   ※ 공통 인용이 '다들 무엇을 딛고 있나'라면 여기는 "
                     "'누가 누구와 같은 데를 딛고 있나'다.")

    if frontier:
        lines += ["", "▶ 이 분야의 토대를 최근에 인용한 논문 (우리 검색 밖일 수 있다)"]
        for title, n in frontier[:8]:
            lines.append(f"   토대 {n}편을 인용 — {title[:66]}")
        lines.append("   ※ 미등록 용어가 '이미 모은 논문 안에서' 못 보던 말을 찾는다면, "
                     "여기는 '수집 대상 밖에서' 관련 논문을 데려온다.")

    authors = author_counts(this_week)
    if authors:
        lines += ["", "▶ 이번 기간에 여러 편을 낸 저자"]
        lines.append("   " + " · ".join(f"{n} {k}편" for n, k in authors))

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
                with_narrative: bool = True, with_frontier: bool = True) -> str:
    """주간 리뷰 본문. client 를 안 주면 인용망 조회와 서술을 건너뛴다(네트워크 없음)."""
    end = datetime.now(timezone.utc)
    this_week = _rows_between(db, end - timedelta(days=days), end)
    last_week = _rows_between(db, end - timedelta(days=days * 2), end - timedelta(days=days))
    shared, examined, targets = None, 0, 0
    lineage, cites, frontier = None, None, None
    if client is not None and with_references and this_week:
        scan = await shared_references(client, this_week)
        shared, examined, targets = scan.shared, scan.examined, scan.targets
        cites = scan.cites
        # 계보 묶기는 위 응답을 재사용한다 — 추가 호출 0회(§8-40).
        lineage = lineage_groups(scan.by_paper, this_week)
        # 최전선만 호출이 더 든다(토대 논문 3편). 실패해도 나머지는 그대로 나간다.
        if with_frontier and shared:
            frontier, _seen = await frontier_papers(client, shared, scan.ref_ids)
    story = None
    if client is not None and with_narrative and this_week:
        story = await narrative(client, this_week, profile)
    return format_report(this_week, last_week, profile, shared, examined, targets,
                         story, lineage, cites, frontier)
