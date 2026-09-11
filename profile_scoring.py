"""profile_scoring.py — Fast Layer 2차 스코어링 (신설, selection.py 확장 아님).

2026-08-24 설계 리뷰에서 확인한 것들을 전제로 짰다:

1. selection.py의 rank()는 citation_count·year만 본다 — 이 모듈이 제안하는
   relevance/domain_hit/venue/recency 가중합은 코드에 전혀 없던 것이라
   "확장"이 아니라 옆에 새로 얹는 레이어다. selection.py는 그대로 두고,
   이 모듈은 selection.dedupe()가 이미 병합해 준 논문 목록을 입력으로 받는다
   (같은 결로: 이 모듈도 dedupe는 하지 않는다).

2. relevance 판단에 임베딩(Gemini API 호출)을 쓰지 않는다. Fast Layer는
   매일 신규 후보 수십~수백 편을 스코어링해야 하는데, 임베딩도 API
   호출이라 ④ 요약과 같은 무료 티어 한도를 나눠 쓰게 된다("임베딩이라
   LLM 호출이 아니다"는 리뷰에서 스스로 정정한 오류). 대신 프로필의
   core_topics/target_domain/exclude 키워드를 제목+초록에 대해 단어
   경계 정규식으로 매칭한다 — selection.py와 같은 이유(판단이 매번
   달라지면 사후 설명이 안 된다)로 결정론적 쪽을 택했다. 나중에 GPU
   임베딩이 자리 잡으면(§9 Compute Router 확인 후) relevance 항목만
   교체하면 되게 인터페이스를 분리해뒀다(score_paper의 반환값에 매칭된
   키워드 목록을 그대로 남겨 "왜 이 점수인지"가 항상 설명 가능하게 함).

3. venue_score는 기본 0/None이다 — s2_search_papers가 지금 요청하는
   fields에 venue가 빠져 있어(server.py 확인) 이 데이터 자체가 없다.
   paper dict에 "venue" 키가 있으면만 매칭을 시도한다 — 문자열 완전일치가
   아니라 프로필의 venue 이름이 S2가 주는 venue 문자열에 부분 포함되는지로
   본다(예: 프로필 "IEEE TII" vs S2 "IEEE Transactions on Industrial
   Informatics"는 지금 방식으론 안 걸린다 — venue 매칭 자체가 아직 미해결
   문제라는 걸 반환값의 venue_hit=None으로 구분해 남긴다).

exclude 매칭은 다른 항목보다 먼저 본다 — 하나라도 걸리면 나머지 계산 없이
바로 제외한다(우선순위 설계, selection.py의 "판단 기준이 고정돼야 사후
설명이 된다" 원칙과 같은 이유로 exclude가 core_topics를 항상 이긴다).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

_WORD_RE_CACHE: dict[str, re.Pattern] = {}


def _keyword_pattern(keyword: str) -> re.Pattern:
    """다단어 키워드("digital twin")도 통째로 단어 경계 매칭한다. 순수
    substring 매칭을 안 쓰는 이유: "AI"가 "domain" 안에 우연히 들어있는
    것 같은 거짓 양성을 막기 위해서다.

    마지막 낱말의 복수형(-s/-es)은 같은 키워드로 본다. 이게 없으면 논문이
    "event cameras", "robot manipulators", "wearable biosensors"처럼 복수로
    쓸 때 통째로 놓친다 — 초록은 대부분 복수형으로 쓰기 때문에 이 누락이
    드물지 않다. 단어 경계는 그대로 유지되므로 오탐은 늘지 않는다."""
    pat = _WORD_RE_CACHE.get(keyword)
    if pat is None:
        pat = re.compile(r"\b" + re.escape(keyword) + r"(?:es|s)?\b", re.IGNORECASE)
        _WORD_RE_CACHE[keyword] = pat
    return pat


def _find_hits(text: str, keywords: list[str]) -> list[str]:
    return [kw for kw in keywords if _keyword_pattern(kw).search(text)]


def _paper_text(paper: dict) -> str:
    return f"{paper.get('title') or ''} {paper.get('abstract') or ''}"


def recency_score(published: str | None, half_life_days: float) -> float | None:
    """published(ISO 8601, 'Z' 종료)를 오늘 기준 지수 감쇠 점수(0~1)로.
    파싱 실패(형식이 다르거나 S2처럼 연도만 있는 경우)는 None을 돌려준다 —
    호출부가 "모른다"를 "오래됐다(0점)"로 오인해 불이익을 주지 않도록,
    None은 가중합에서 그냥 빠진다(0을 더하는 게 아니라 항 자체가 없어짐).
    """
    if not published:
        return None
    try:
        dt = datetime.strptime(published, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
    if age_days < 0:
        age_days = 0
    return 0.5 ** (age_days / half_life_days)


def venue_hit(paper: dict, profile_venues: list[str]) -> bool | None:
    """venue 매칭 — 데이터가 아예 없으면(None) "안 맞음(False)"과 구분한다.
    지금 s2_search_papers는 venue 필드를 안 주므로(server.py 확인,
    2026-08-24) 실전에서는 대부분 None이 나온다 — fields 확장이 먼저
    필요하다는 걸 이 반환값이 드러낸다."""
    venue = paper.get("venue")
    if not venue or not profile_venues:
        return None
    venue_lower = venue.lower()
    return any(v.lower() in venue_lower for v in profile_venues)


# 핵심 키워드의 **가중치 합**이 이 값에 닿으면 relevance 만점(1.0)으로 본다.
#
# 2026-08-31 이전에는 "적중 개수 / 3"이었다. 키워드를 12개로 좁힌 뒤 실측하니
# 채점 대상 120편이 **전부 정확히 1개만 적중**했다(예외 0건). 좁고 서로 배타적인
# 키워드에서는 한 논문이 2개 이상을 건드릴 일이 구조적으로 없기 때문이다. 그
# 결과 relevance 가 모든 후보에서 0.333 상수가 되어 순위 정보를 전혀 못 주고,
# 최신성과 도메인 가점만으로 순위가 정해졌다 — 무관한 무선통신 논문이 표적인
# PCB 검사 논문을 눌렀다.
#
# 그래서 "몇 개 맞혔나"가 아니라 "얼마나 무거운 걸 맞혔나"로 바꿨다. 적중 1개가
# 기본값이 되는 세계에서는 **어떤 1개인지**가 유일하게 남은 정보다.
CORE_WEIGHT_FOR_FULL_SCORE = 2.0

# ── 2026-09-06: 순위도 별점과 같은 것을 본다
#
# 위 상한(가중치 **합** 2.0)에는 구멍이 있었다. 합으로 재면 **동향어 두 개
# (0.6+0.6=1.2)가 표적어 하나(1.0)를 이긴다.** digest._stars 는 이미 이걸
# 알고 있어서 별점만은 최댓값(top_core_weight)으로 매기는데, 순위는 여전히
# 합이 정했다 — 같은 질문에 두 자가 서로 다른 답을 하고 있었다.
#
# 실측(2026-09-06, 후보 478편). 포함관계 이중계수를 고치자 이 결함이 그대로
# 드러났다. 상위 6칸이 **전부 ★** 이 됐다 — 로보틱스 VLA 논문들이 동향어를
# 두 개씩 맞혀 0.72~0.74 를 받고, 표적어 defect detection 논문(0.637)이
# 8위로 밀렸다. 팀 표적 분야가 메일에서 통째로 사라진 것이다.
# (2026-08-31 에 도메인 가점을 0.4→0.1 로 낮춘 것도 같은 증상이었다:
#  "표적어인 defect detection 논문이 동향어 논문보다 아래에 있었다".
#  그때는 도메인 가점이 범인이었고, 이번엔 합 자체가 범인이다.)
#
# 그래서 relevance 를 **계층(최댓값) × 폭(2적중이냐)** 으로 나눈다:
#
#     relevance = top_core_weight × (BASE + (1-BASE) × min(적중수-1, 1))
#
#   표적어 1개  1.00 × 0.8 = 0.80      동향어 1개  0.60 × 0.8 = 0.48
#   표적어 2개  1.00 × 1.0 = 1.00      동향어 2개  0.60 × 1.0 = 0.60
#
# BASE=0.8 은 임의값이 아니다. "표적어 1개"와 "동향어 2개"의 격차가
# 0.80-0.60 = **0.20** 이 되도록 고른 값이고, 이건 옛 공식의 계층 격차
# (1.0-0.6)/2.0 = 0.20 과 정확히 같다 — 그래서 그 격차 위에 세워진
# _DOMAIN_MUST_BE_SMALLER_THAN_TIER_GAP(0.1)이 손대지 않고 그대로 유효하다.
#
# 폭을 2에서 자르는 것은 옛 상한과 같다. 3개 맞혀도 2개와 같게 본다.
_BREADTH_BASE = 0.8

# 도메인 가점은 몇 건까지만 센다. 상한이 없으면 도메인 낱말을 여럿 스치는 논문이
# 핵심 적중 없이도 위로 올라온다(핵심 상한과 같은 이유).
DOMAIN_HITS_CAP = 2


# ── 선별 순서 계약 (2026-09-11, A단계 — docs/ASTRA_PLAN_2026-09-10.md §5)
#
# **순위는 가중합이 아니라 튜플로 정한다.** 그전에는 위 `priority` 하나로
# 정렬했는데, 그러면 도메인 가점 0.2 가 최신성 5일을 이긴다 — 실측
# (2026-09-11, 저장 후보 1143편 고정 스냅샷): 최신 날짜 논문이 최상위
# 계층에 35편 있는데 상위 6칸에 3편만 들어갔고, 9월 4일 논문 둘이 도메인
# 가점으로 2·3위를 차지했다. 사용자의 요구는 "관련성 계층 우선, 같은
# 계층이면 최신 우선"이고 합산은 그 계약을 지킬 수 없다.
#
# 튜플 (오름차순 정렬):
#   tier_rank        활성 프로필의 서로 다른 가중치를 내림차순으로 세운 순위.
#                    1.0→0, 0.6→1, … 낮을수록 먼저. 적중 core 중 최고 계층.
#                    부동소수 값 대신 순위를 쓴다 — 0.35 와 0.4 의 차이는
#                    "계층이 다르다"이지 "0.05 만큼 덜 관련"이 아니다.
#   -day_ordinal     공개일을 **일 단위**로 정규화. 시·분 차이로 한 출처를
#                    우대하지 않는다. 날짜를 모르면 0 — ordinal 은 항상 양수라
#                    같은 계층 안에서 날짜 있는 것 뒤로 간다(§5.4). 계층은
#                    넘지 않는다. (처음엔 별도 플래그를 뒀는데 이 항과 동작이
#                    같아 돌연변이가 안 잡혔다 — 중복이라 뺐다.)
#   -core_breadth    같은 날이면 서로 다른 core 적중 수(상한 2). **날짜 아래**에
#                    둔다 — 계획서 §5.3 은 결합을 최신성보다 앞세우지 말라고
#                    했지 동률 안에서 무시하라고 하지 않았다. 없으면 같은 날
#                    core 둘 맞힌 논문이 core 하나+도메인 하나 논문에 밀린다
#                    (09-04 메일의 PhyHGNet 회귀가 그 모양이었다).
#   -domain_hits     그 다음 도메인 적중 수(상한 2). 동의어 묶음은 확인된 것이
#                    없어 아직 안 한다 — 문자열 적중 수 그대로다.
#   paper_key        동률 안정화. 입력 순서에 의존하지 않는다.
#
# `priority` 는 계속 계산해 **설명 필드로만** 남긴다. 정렬·자격 판정에는
# 안 쓴다. 결합축(서로 다른 연구축 동시 적중)은 **최신성 위에** 놓지
# 않는다(§5.3) — 지금 `core_hits` 는 연구축 수가 아니라 문자열 적중 목록이라,
# 그걸 결합 개수로 쓰면 동의어 둘이 결합축으로 승격된다. 날짜 아래 동률
# 가르개로만 쓰는 이유가 그것이다 — 피해가 같은 날 안으로 갇힌다.

DATE_FORMATS = ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d")


def publication_day(published: str | None) -> tuple[int | None, str]:
    """returns (일 단위 ordinal 또는 None, 정밀도) — 정밀도는
    'day' | 'year' | 'missing' | 'invalid'.

    연도만 있는 값은 ordinal 을 **만들지 않는다**. 1월 1일로 채우면 없는
    최신성을 지어내는 것이다. 대신 'year' 로 표시해 같은 계층 뒤로 보낸다."""
    if not published:
        return None, "missing"
    text = str(published).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().toordinal(), "day"
        except ValueError:
            continue
    if re.fullmatch(r"\d{4}", text):
        return None, "year"
    return None, "invalid"


def tier_table(profile: dict) -> list[float]:
    """활성 core_topics 의 유효 가중치(누락은 1.0)를 서로 다른 값만 내림차순으로.
    `core_weights.values()` 가 아니라 core_topics 기준이다 — 가중치 표에만
    남은 죽은 키워드가 계층을 만들면 안 된다."""
    weights = profile.get("core_weights") or {}
    return sorted({float(weights.get(kw, 1.0)) for kw in profile.get("core_topics", [])},
                  reverse=True)


def tier_rank(profile: dict, core_hits: list[str]) -> int | None:
    """적중 core 중 최고 계층의 순위(0 이 최상위). 적중 없으면 None.
    `_score["top_core_weight"]` 를 역조회하지 않는다 — 그 값은 소수 넷째
    자리로 반올림돼 원래 가중치와 안 맞을 수 있다. 적중 키워드와 원 프로필
    값으로 다시 구한다."""
    if not core_hits:
        return None
    table = tier_table(profile)
    weights = profile.get("core_weights") or {}
    best = max(float(weights.get(kw, 1.0)) for kw in core_hits)
    return table.index(best)


def rank_key(paper: dict, result: dict, profile: dict) -> tuple:
    """선별 순서 계약(위 주석). 오름차순 정렬에 그대로 쓴다.
    적중이 없는 논문은 애초에 순위 대상이 아니므로 호출부가 먼저 거른다."""
    from research_profile import paper_key  # 순환 없음 — research_profile 은 이 모듈을 안 부른다
    hits = result.get("core_hits") or []
    tr = tier_rank(profile, hits)
    day, _precision = publication_day(paper.get("published"))
    breadth = min(len(hits), 2)
    domain = min(len(result.get("domain_hits") or []), DOMAIN_HITS_CAP)
    return (tr if tr is not None else 10 ** 6,
            -(day or 0),
            -breadth,
            -domain,
            paper_key(paper))

# 다의어 가드 — 그 낱말이 **우리가 뜻하는 의미로** 쓰였는지 확인할 동반어.
# 하나도 없으면 적중으로 치지 않는다.
#
# 근거를 실측한 것만 넣는다(2026-08-31): CSymPlan(arXiv 2608.22983)이 핵심
# 키워드 "quantization"으로 걸렸는데, 실제로는 모델 경량화가 아니라 제어
# 상태공간의 이산화를 가리키는 말이었다. 짐작만으로 가드를 늘리면 조용히
# 놓치는 논문이 생기므로, 실제로 오탐이 관측된 낱말에만 건다.
# 동반어는 **단어 경계 정규식**으로 본다. 처음에 부분문자열로 짰더니 너무
# 헐거웠다 — 실측: CSymPlan 이 "modeling inaccuracies" 의 "model" 하나로
# 가드를 통과했다. "bit" 를 넣었다면 "arbitrary" 에도 걸렸을 것이다.
# 그래서 제어·로보틱스 논문에서는 안 나오고 ML 경량화 논문에서만 나오는
# 표현으로 좁혔다(단독 "model"/"network"/"precision" 같은 범용어 제거).
_POLYSEMY_GUARDS: dict[str, tuple[str, ...]] = {
    "quantization": (
        r"bits?", r"int8", r"int4", r"\d+-bit", r"bit-?width",
        r"low-precision", r"mixed-precision", r"post-training",
        r"quantization-aware", r"quantized", r"model compression",
        r"weight quantization", r"activation quantization",
    ),
    # "world model" (2026-09-11). 사용자가 물었다 — "넌 뭐때문에 그걸 넣었어? 로봇
    # 아니야? 아니면 디지털 트윈 시뮬레이션?" 맞다. 9/9 v2 개정에서 VLA 와 결합해
    # 자기교정·정책 학습을 하는 흐름을 잡으려고 넣었다. 그런데 지구 시스템·방사선
    # 임상·광양자 계산·비디오 LLM 도 같은 말을 쓴다 — 2026-09-11 메일 4위를 광양자
    # 논문이 먹었다.
    #
    # **문자열을 좁히는 길은 막혀 있다**(실측, 후보 1477편): "world foundation
    # model" 0편 · "embodied world model" 0편 · "robot world model" 1편. 논문이
    # 그렇게 안 쓴다. 그래서 낱말은 두고 동반어로 가른다 — 로봇·정책·시뮬레이션·
    # 디지털 트윈 계열. 실측: 적중 67편 중 61편 통과, 6편 거름(광양자 GBS, 성도
    # MRI, 언어모델 Dutch Book, 비디오 LLM 탐침 + 경계선 둘). "physics"·"video"·
    # "action" 은 넣지 않았다 — 거의 모든 초록에 있어 가드가 안 된다.
    # 검색(S2 씨앗)은 그대로다 — 이건 채점만 거른다.
    "world model": (
        r"robots?", r"robotics?", r"polic(?:y|ies)", r"embodied", r"agents?", r"agentic",
        r"manipulat\w*", r"vision-language-action", r"vla", r"navigation", r"driving",
        r"vehicles?", r"locomotion", r"control(?:ler|lers)?", r"reinforcement learning",
        r"planning", r"imitation", r"sim-to-real", r"simulat\w*", r"digital twin",
    ),
}

_GUARD_RE_CACHE: dict[str, re.Pattern] = {}


def _passes_polysemy_guard(keyword: str, text: str) -> bool:
    guards = _POLYSEMY_GUARDS.get(keyword.lower())
    if not guards:
        return True
    pat = _GUARD_RE_CACHE.get(keyword)
    if pat is None:
        pat = re.compile(r"\b(?:" + "|".join(guards) + r")\b", re.IGNORECASE)
        _GUARD_RE_CACHE[keyword] = pat
    return bool(pat.search(text))


# 도메인 가점은 **계층 격차보다 작아야 한다**. 안 그러면 낮은 계층 키워드 +
# 도메인이 높은 계층 키워드를 항상 따라잡아, 계층을 둔 의미가 사라진다.
#
# 2026-09-02 실측에서 정확히 그 일이 벌어졌다. 도메인 가점이 0.2 였는데
# 계층 격차도 (1.0-0.6)/2.0 = 0.20 이라 **완전히 상쇄**됐다:
#
#   표적어(w1.0) 1개, 도메인 0개  → 0.500
#   동향어(w0.6) 1개, 도메인 1개  → 0.500   ← 같다
#
# 그날 상위 6편 점수가 0.6191~0.6422(폭 0.023)로 뭉쳐 순위가 사실상
# 무작위였고, 표적어인 defect detection 논문이 동향어 논문보다 아래에
# 있었다. 별점도 6편 전부 ★ 였다.
#
# 0.1 로 낮춰 격차의 절반만 되게 한다 — 도메인은 **동점을 가르는** 신호이지
# 계층을 뛰어넘는 신호가 아니다.
_DOMAIN_MUST_BE_SMALLER_THAN_TIER_GAP = 0.1

@dataclass
class Weights:
    core_topic: float = 1.0     # relevance(0~1)에 곱함
    domain_hit: float = _DOMAIN_MUST_BE_SMALLER_THAN_TIER_GAP
    venue_hit: float = 0.3      # venue 매칭 시 고정 가점
    recency: float = 0.15       # recency_score(0~1)에 곱함
    recency_half_life_days: float = 30.0


def has_full_text_route(paper: dict) -> bool:
    """본문을 받을 길이 있나 — arXiv ID 또는 오픈액세스 PDF 링크.

    **점수에는 안 들어간다.** 잠깐 동점 가르개(+0.05)로 넣었다가 뺐다
    (2026-09-06). 자리 배분을 `run_profile_scan._full_text_slots` 가 아예
    문지기로 처리하게 되면서, 내용 자리에 오는 논문은 전부 이 조건을
    만족한다 — 가르개가 가를 게 없어졌다. 아무것도 안 하는 항을 남기지
    않는다. 판정 결과는 score_paper 출력의 `full_text` 키로 그대로 보인다.

    `pdf-<해시>` 는 사람이 직접 올린 파일의 합성 ID 라 검색 후보에는 안
    나오지만, 나와도 본문이 이미 있다는 뜻이므로 True 가 맞다.
    """
    return bool((paper.get("arxiv_id") or "").strip()
                or (paper.get("open_access_pdf") or "").strip())


def _drop_subsumed(hits: list[str]) -> list[str]:
    """긴 적중에 그대로 들어 있는 짧은 적중을 뺀다.

    'micro defect detection' 이 걸렸으면 'defect detection' 은 같은 문구를
    다시 센 것이다. 순서는 유지한다 — 호출부가 "왜 걸렸나" 줄을 이 순서로
    찍는다. 흡수 판정은 **키워드 문자열끼리** 본다(본문이 아니라). 짧은 쪽이
    본문의 **다른 자리에도** 따로 나왔을 수는 있지만, 그걸 구분하려면
    매칭 위치를 추적해야 하고 그 복잡도가 얻는 것보다 크다 — 그리고 안전한
    쪽으로 틀린다(점수를 부풀리지 않는 쪽).
    """
    return [h for h in hits
            if not any(h != o and _keyword_pattern(h).search(o) for o in hits)]


def core_hits_with_weight(paper: dict, profile: dict) -> tuple[list[str], float, float]:
    """핵심 키워드 적중 목록, 가중치 **합**, 그리고 가장 무거운 적중의 가중치.

    합과 최댓값이 둘 다 필요하다. 합만으로는 "표적어를 맞혔나"를 알 수 없다 —
    동향어 두 개(0.6+0.6=1.2)가 표적어 하나(1.0)보다 크기 때문이다.
    합은 순위(총점)에, 최댓값은 별점(분류)에 쓴다.

    점수 계산과 분리해 둔 이유: 다이제스트의 동향 집계(어떤 키워드가 이번
    창에서 몇 편 걸렸나)가 순위와 무관하게 같은 판정을 써야 하기 때문이다.

    **한 문구가 두 키워드에 걸리면 한 번만 센다**(2026-09-06). core_topics
    안에 포함관계가 있으면 — 실제로 'defect detection' ⊂ 'micro defect
    detection' — 논문이 "micro defect detection" 한 번만 써도 적중이 2개가
    된다. 개념은 하나인데 적중은 둘이고, 그 차이가 총점에서 **+0.500** 이다
    (relevance 0.5 → 1.0). 계층 차이(+0.200)나 도메인 가점(+0.200)보다 크고,
    7일 창 안에서 최신성이 낼 수 있는 최대 차이(+0.022)의 23배다.

    실측(2026-09-06, 후보 478편): 2적중 이상 13편 중 1편이 이 부풀림이었고,
    그 1편이 **랭킹 1위**였다(PhyHGNet, priority 1.133 ★★★). 저장된 논문
    108편에서는 0건이라 안 보였는데 — 복합어를 쓰는 쪽이 본문을 못 받는
    저널이라 애초에 저장 테이블에 안 들어오기 때문이다.

    trend_report._subsumed 가 'large language' 를 'large language models' 에
    흡수시키는 것과 **같은 규칙**이다. 같은 판정을 두 곳에서 다르게 하고
    있었다.
    """
    text = _paper_text(paper)
    weights = profile.get("core_weights") or {}
    matched = [kw for kw in profile.get("core_topics", [])
               if _keyword_pattern(kw).search(text) and _passes_polysemy_guard(kw, text)]
    hits = _drop_subsumed(matched)
    total = sum(float(weights.get(kw, 1.0)) for kw in hits)
    top = max((float(weights.get(kw, 1.0)) for kw in hits), default=0.0)
    return hits, total, top


def score_paper(paper: dict, profile: dict, weights: Weights = Weights()) -> dict:
    """returns 점수 breakdown — priority 숫자 하나만이 아니라 core_hits 등
    매칭된 키워드 목록을 항상 같이 돌려준다. "왜 이 논문이 위에 있는지"를
    사후에 설명할 수 있어야 한다는 이 프로젝트의 반복된 설계 원칙(②
    selection.py, ⑤ 검증기 grounding과 같은 결)을 여기서도 지킨다.
    """
    text = _paper_text(paper)

    exclude_hits = _find_hits(text, profile.get("exclude", []))
    if exclude_hits:
        return {"priority": 0.0, "excluded": True, "exclude_hits": exclude_hits,
                "core_hits": [], "core_weight": 0.0, "top_core_weight": 0.0,
                "domain_hits": [], "venue_hit": None, "recency": None,
                "full_text": has_full_text_route(paper), "primary_hit": ""}

    core_topics = profile.get("core_topics", [])
    core_hits, core_weight, top_core_weight = core_hits_with_weight(paper, profile)
    if core_topics and not core_hits:
        # core_topics는 OR 조건 — 프로필 설명(설계 문서 §1)과 같다. 하나도
        # 안 걸리면 이 프로필과 무관한 논문으로 보고 0점 처리(제외는 아님 —
        # exclude와 구분해서, 호출부가 "그냥 순위가 낮다"와 "명시적으로
        # 걸러졌다"를 구분할 수 있게 excluded=False로 둔다).
        return {"priority": 0.0, "excluded": False, "exclude_hits": [],
                "core_hits": [], "core_weight": 0.0, "top_core_weight": 0.0,
                "domain_hits": [], "venue_hit": None, "recency": None,
                "full_text": has_full_text_route(paper), "primary_hit": ""}

    domain_hits = _find_hits(text, profile.get("target_domain", []))
    full_text = has_full_text_route(paper)

    # 이 논문을 **대표하는** 키워드 — 가장 무거운 적중. 동률이면 core_hits 의
    # 첫 번째(=core_topics 순서라 프로필 안에서 안정적이다).
    #
    # 여기서 정하는 이유: 이걸 쓰는 쪽(run_profile_scan._spread_keywords 의
    # 자리 상한)은 프로필 가중치를 안 갖고 있다. 처음엔 거기서 `hits[0]` 로
    # 때웠는데 그러면 **표적어를 맞힌 논문이 동향어 이름으로 세어진다** —
    # `["embodied AI", "defect detection"]` 이면 embodied AI 로 잡혀
    # defect detection 의 자리 상한이 안 깎인다. 가중치를 아는 곳은 여기뿐이라
    # 여기서 정해서 내려보낸다.
    core_weights = profile.get("core_weights") or {}
    primary_hit = max(core_hits, key=lambda k: float(core_weights.get(k, 1.0)),
                      default="")
    v_hit = venue_hit(paper, profile.get("venues", []))
    recency = recency_score(paper.get("published"), weights.recency_half_life_days)

    relevance = (top_core_weight * (_BREADTH_BASE
                                    + (1.0 - _BREADTH_BASE) * min(len(core_hits) - 1, 1))
                 if core_topics else 0.0)
    priority = relevance * weights.core_topic
    priority += min(len(domain_hits), DOMAIN_HITS_CAP) * weights.domain_hit
    if v_hit:
        priority += weights.venue_hit
    if recency is not None:
        # 최신성 가중치를 0.4 에서 0.15 로 낮췄다(2026-08-31). 검색은 이미
        # 델타 창(최근 7~10일)으로 잘려 들어오므로 후보는 **전부** 최신이다.
        # 그 위에 최신성을 다시 크게 매기면 같은 정보를 두 번 세는 셈이고,
        # 실제로 사흘 차이가 주제 적합도를 뒤집는 일이 벌어졌다. 최신성은
        # 이제 동점을 가르는 역할만 한다 — "최신 동향 반영"은 창과 키워드가
        # 담당하지, 사흘의 나이 차가 담당하는 게 아니다.
        priority += recency * weights.recency

    day, precision = publication_day(paper.get("published"))
    return {
        "priority": round(priority, 4), "excluded": False,
        "exclude_hits": [], "core_hits": core_hits,
        "core_weight": round(core_weight, 4),
        "top_core_weight": round(top_core_weight, 4), "domain_hits": domain_hits,
        "venue_hit": v_hit, "recency": recency, "full_text": full_text,
        "primary_hit": primary_hit,
        # 선별 순서 계약의 설명 필드(2026-09-11). 정렬은 rank_key 가 한다.
        "tier_rank": tier_rank(profile, core_hits),
        "pub_day": day, "date_precision": precision,
    }


def score_and_rank(
    papers: list[dict], profile: dict, weights: Weights = Weights(), top_k: int | None = None,
) -> dict:
    """selection.dedupe_and_rank()와 같은 모양의 출력 — 각 단계 건수를 같이
    돌려줘서 무엇이 걸러졌는지 보이게 한다(같은 설계 원칙).

    core_hit_counts 는 **top_k 로 자르기 전** 후보 전체에서 각 핵심 키워드가
    몇 편에 걸렸는지다. 다이제스트의 동향 집계가 이걸 쓴다 — 상위 5편만 보면
    "이번 주에 어느 주제가 많았나"를 알 수 없고, 그 질문에는 LLM 없이 셈만으로
    답할 수 있다(CLAUDE.md 7: 기계가 위조 불가능하게 판정할 수 있는 것).
    """
    scored = []
    excluded_count = 0
    unmatched_count = 0
    core_hit_counts: dict[str, int] = {}
    for p in papers:
        result = score_paper(p, profile, weights)
        if result["excluded"]:
            excluded_count += 1
            continue
        # 자격은 **적중 유무**로 본다. 그전엔 `priority == 0.0` 을 같이 봤는데,
        # 점수는 설명 필드가 됐으므로(2026-09-11) 자격 판정이 점수에 기대면 안 된다.
        if not result["core_hits"]:
            unmatched_count += 1
            continue
        for kw in result["core_hits"]:
            core_hit_counts[kw] = core_hit_counts.get(kw, 0) + 1
        scored.append({**p, "_score": result})

    # 가중합이 아니라 튜플 계약으로 정렬한다(2026-09-11, 위 주석). 오름차순.
    scored.sort(key=lambda p: rank_key(p, p["_score"], profile))
    if top_k is not None:
        scored = scored[:top_k]

    return {
        "input_count": len(papers),
        "excluded_count": excluded_count,
        "unmatched_count": unmatched_count,
        "scored_count": len(scored),
        "core_hit_counts": dict(sorted(core_hit_counts.items(),
                                       key=lambda kv: (-kv[1], kv[0]))),
        "papers": scored,
    }
