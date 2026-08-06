"""hybrid_search.py — 로컬 저장소 논문에 대한 BM25 + 임베딩 하이브리드 검색.

외부 "심층 조사와 발전 설계" 문서가 제안한 Hybrid Search를 구현한다.
`arxiv_search_papers`/`s2_search_papers`(①)는 외부 API 자체의 검색·랭킹을
그대로 쓴다 — 우리가 랭킹 알고리즘을 바꿀 수 없다. 이 모듈은 그와 다른
자리다: **이미 로컬에 저장한 논문들**(`fetch_paper`로 수집한 것) 안에서
"이 논문들 중 어떤 게 이 질문과 관련 있나"를 찾는 용도다 — 평가셋이 커질수록
"저장한 논문 중에 이미 관련된 게 있었나"를 다시 찾기 어려워지는 문제를 푼다.

두 신호를 합친다:
- BM25(어휘 일치) — 질의어와 같은 단어를 쓴 논문을 잘 찾는다. 동의어·의역은 못 잡는다.
- 임베딩 코사인 유사도(의미 일치) — "경량화"와 "compression"처럼 다른 단어라도
  의미가 비슷하면 찾는다. 반대로 흔한 단어를 정확히 맞혀야 하는 경우엔 약하다.

합치는 방식은 Reciprocal Rank Fusion(RRF) — 두 점수의 스케일이 완전히 달라서
(BM25는 상한 없는 양수, 코사인 유사도는 대략 [-1,1]) 직접 가중합하면 스케일이
큰 쪽이 사실상 지배해버린다. RRF는 점수가 아니라 **순위**만 쓰므로 스케일
문제가 아예 없다 — 정보검색에서 흔히 쓰는 표준 기법이다.

임베딩은 Gemini 무료 API(`gemini-embedding-001`)를 쓴다 — summarize_engine.py
와 같은 키(GOOGLE_API_KEY)를 재사용한다. 이 모듈 자체는 캐싱하지 않는다 —
캐싱(어떤 논문의 임베딩을 이미 계산해뒀는지)은 DB를 아는 server.py 쪽 책임이고,
여기는 순수 계산만 한다(테스트하기 쉽게, 그리고 이 프로젝트의 다른 모듈들
— verify.py, selection.py, sentence_grounding.py — 과 같은 경계 원칙).
"""

from __future__ import annotations

import logging
import math
import os
import re
from collections import Counter
from pathlib import Path

import httpx

# summarize_engine.py 와 같은 이유로 httpx 기본 요청 로깅을 꺼둔다 — 키를
# 헤더로 보내 URL엔 안 실리지만, 매 호출마다 INFO 로그가 찍히는 걸 막아
# 터미널 출력을 깔끔하게 유지한다.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"


def _load_env() -> dict:
    env = dict(os.environ)
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip())
    return env


ENV = _load_env()

# 영문/숫자 단어 또는 한글 음절 연속을 토큰으로 본다 — 이 저장소의 논문은
# 거의 전부 영문 제목·초록이라 한글 처리는 부수적이지만, 사용자 질의가
# 한글일 수 있어 최소한은 다룬다. 형태소 분석기 같은 무거운 의존성은 안 쓴다
# — BM25 자체가 "정확히 같은 토큰"을 요구하는 성긴(sparse) 신호라 완벽한
# 토큰화가 필요 없다(그건 임베딩 쪽이 보완한다).
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[가-힣]+")


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


class BM25:
    """Okapi BM25. corpus_tokens: 문서마다 tokenize() 한 결과의 리스트."""

    def __init__(self, corpus_tokens: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus_tokens = corpus_tokens
        self.doc_freqs = [Counter(doc) for doc in corpus_tokens]
        self.doc_lens = [len(doc) for doc in corpus_tokens]
        self.n_docs = len(corpus_tokens)
        self.avg_doc_len = (sum(self.doc_lens) / self.n_docs) if self.n_docs else 0.0
        self.idf = self._compute_idf()

    def _compute_idf(self) -> dict[str, float]:
        df: Counter[str] = Counter()
        for doc in self.corpus_tokens:
            for term in set(doc):
                df[term] += 1
        # Robertson-Sparck Jones IDF의 +0.5 평활화 변형 — 표준 Okapi BM25 공식.
        return {
            term: math.log(1 + (self.n_docs - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }

    def score(self, query_tokens: list[str], doc_index: int) -> float:
        doc_freq = self.doc_freqs[doc_index]
        doc_len = self.doc_lens[doc_index]
        total = 0.0
        for term in query_tokens:
            freq = doc_freq.get(term)
            if not freq:
                continue
            idf = self.idf.get(term, 0.0)
            denom = freq + self.k1 * (1 - self.b + self.b * doc_len / (self.avg_doc_len or 1))
            total += idf * freq * (self.k1 + 1) / denom
        return total

    def scores(self, query_tokens: list[str]) -> list[float]:
        return [self.score(query_tokens, i) for i in range(self.n_docs)]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def reciprocal_rank_fusion(rankings: list[list[int]], k: int = 60) -> dict[int, float]:
    """rankings: 각각 문서 인덱스를 순위 순으로 나열한 리스트(예: BM25 순위,
    코사인 유사도 순위). RRF 점수 = 각 랭킹에서 1/(k + 순위+1) 를 합산 —
    점수 스케일이 서로 달라도(BM25 무상한 vs 코사인 [0,1]) 순위만 쓰므로
    안전하게 합쳐진다. k=60 은 원 논문(Cormack et al. 2009)의 관례값이다.
    """
    fused: dict[int, float] = {}
    for ranking in rankings:
        for rank, doc_idx in enumerate(ranking):
            fused[doc_idx] = fused.get(doc_idx, 0.0) + 1.0 / (k + rank + 1)
    return fused


def rank_documents(
    query_tokens: list[str],
    bm25: BM25,
    query_vec: list[float] | None,
    doc_vecs: list[list[float] | None],
    top_k: int,
) -> list[dict]:
    """순수 랭킹 계산 — 네트워크 호출 없음(테스트 쉽게 하기 위해 embed_text와
    분리). doc_vecs 에 None 이 섞여 있으면(임베딩 실패·미보유) 그 문서는
    코사인 랭킹에서 빠지고 BM25 랭킹에만 참여한다 — 임베딩이 부분적으로
    없어도 검색 자체가 죽지 않는다.

    query_vec 이 None 이면(GOOGLE_API_KEY 없음 등) BM25 단독으로 동작한다 —
    하이브리드가 안 되면 조용히 실패하는 대신 성긴 검색으로 성능 저하만
    시키고 계속 동작한다.
    """
    n = bm25.n_docs
    bm25_scores = bm25.scores(query_tokens)
    bm25_ranking = sorted(range(n), key=lambda i: -bm25_scores[i])

    cosine_scores: list[float | None] = [None] * n
    rankings = [bm25_ranking]
    if query_vec is not None:
        valid_indices = [i for i in range(n) if doc_vecs[i] is not None]
        for i in valid_indices:
            cosine_scores[i] = cosine_similarity(query_vec, doc_vecs[i])
        cosine_ranking = sorted(valid_indices, key=lambda i: -cosine_scores[i])
        if cosine_ranking:
            rankings.append(cosine_ranking)

    fused = reciprocal_rank_fusion(rankings)
    ranked = sorted(range(n), key=lambda i: -fused.get(i, 0.0))[:top_k]
    return [
        {
            "index": i,
            "bm25_score": round(bm25_scores[i], 4),
            "cosine_score": round(cosine_scores[i], 4) if cosine_scores[i] is not None else None,
            "fused_score": round(fused.get(i, 0.0), 6),
        }
        for i in ranked
    ]


_EMBED_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent"
)
# gemini-embedding-001 문서 기준 권장 입력 상한보다 여유 있게 낮춰 안전하게 자른다
# (제목+초록 용도라 애초에 이 상한을 넘길 일이 거의 없다).
_EMBED_TEXT_CHAR_CAP = 8000


async def embed_text(client: httpx.AsyncClient, text: str, task_type: str) -> list[float]:
    """task_type: 'RETRIEVAL_QUERY'(질의) | 'RETRIEVAL_DOCUMENT'(문서) —
    비대칭 검색에서 둘을 구분해 인코딩하면 관련도가 더 좋아진다(Gemini 임베딩
    API가 공식 지원하는 파라미터, 2026-08-06 실측 확인)."""
    key = ENV.get("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError("GOOGLE_API_KEY 없음")
    resp = await client.post(
        _EMBED_URL,
        json={
            "model": "models/gemini-embedding-001",
            "content": {"parts": [{"text": text[:_EMBED_TEXT_CHAR_CAP]}]},
            "taskType": task_type,
        },
        headers={"x-goog-api-key": key},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]["values"]
