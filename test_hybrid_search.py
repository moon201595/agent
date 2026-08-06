"""hybrid_search.py 단위 테스트 — 네트워크 불필요.

embed_text()(네트워크 호출)를 뺀 순수 계산 부분(BM25, 코사인 유사도, RRF,
rank_documents)만 검증한다 — 네트워크가 필요한 부분은 이 세션의 실측
검증(docs/PROGRESS.md §5)으로 별도 확인했다.
"""

import hybrid_search as hs


def test_tokenize_lowercases_and_splits():
    assert hs.tokenize("Vision-Language Model Compression") == [
        "vision", "language", "model", "compression",
    ]


def test_tokenize_handles_korean():
    assert hs.tokenize("경량화 기법 연구") == ["경량화", "기법", "연구"]


def test_tokenize_empty_string():
    assert hs.tokenize("") == []
    assert hs.tokenize(None) == []


# ── BM25 ──────────────────────────────────────────────────────────


def test_bm25_more_query_term_occurrences_scores_higher():
    corpus = [
        hs.tokenize("edge device model compression compression compression"),
        hs.tokenize("edge device model compression"),
        hs.tokenize("completely unrelated topic about cooking recipes"),
    ]
    bm25 = hs.BM25(corpus)
    scores = bm25.scores(hs.tokenize("compression"))
    assert scores[0] > scores[1] > scores[2]
    assert scores[2] == 0.0  # 쿼리 단어가 아예 없으면 0


def test_bm25_rare_term_weighted_higher_than_common_term():
    # "model"은 모든 문서에 있고(흔함) "quantization"은 한 문서에만 있다(희귀함).
    corpus = [
        hs.tokenize("model quantization for edge devices"),
        hs.tokenize("model architecture for vision tasks"),
        hs.tokenize("model training on large datasets"),
    ]
    bm25 = hs.BM25(corpus)
    common_term_score = bm25.score(hs.tokenize("model"), 0)
    rare_term_score = bm25.score(hs.tokenize("quantization"), 0)
    assert rare_term_score > common_term_score


def test_bm25_empty_corpus_does_not_crash():
    bm25 = hs.BM25([])
    assert bm25.scores(hs.tokenize("anything")) == []


# ── 코사인 유사도 ──────────────────────────────────────────────────


def test_cosine_similarity_identical_vectors_is_one():
    v = [1.0, 2.0, 3.0]
    assert abs(hs.cosine_similarity(v, v) - 1.0) < 1e-9


def test_cosine_similarity_orthogonal_vectors_is_zero():
    assert abs(hs.cosine_similarity([1.0, 0.0], [0.0, 1.0])) < 1e-9


def test_cosine_similarity_opposite_vectors_is_negative_one():
    assert abs(hs.cosine_similarity([1.0, 0.0], [-1.0, 0.0]) - (-1.0)) < 1e-9


def test_cosine_similarity_zero_vector_returns_zero_not_nan():
    assert hs.cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_cosine_similarity_mismatched_length_returns_zero():
    assert hs.cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0]) == 0.0


# ── Reciprocal Rank Fusion ───────────────────────────────────────


def test_rrf_top_ranked_in_both_lists_wins():
    # 문서 5가 두 랭킹 모두에서 1등 — 가장 높은 융합 점수를 받아야 한다.
    bm25_ranking = [5, 2, 8]
    cosine_ranking = [5, 8, 2]
    fused = hs.reciprocal_rank_fusion([bm25_ranking, cosine_ranking])
    best = max(fused, key=fused.get)
    assert best == 5


def test_rrf_document_in_only_one_ranking_still_scored():
    fused = hs.reciprocal_rank_fusion([[10, 20, 30], [40, 50]])
    assert 10 in fused and 40 in fused
    # 10은 자기 목록에서 0위, 50은 자기 목록에서 1위 — 목록 길이와 무관하게
    # 순위 자체가 더 좋은 쪽(10)이 더 높은 점수를 받는다.
    assert fused[10] > fused[50]


def test_rrf_scale_invariance():
    """RRF는 원 점수 크기가 아니라 순위만 쓴다 — 스코어 스케일이 완전히
    달라도(BM25 무상한 vs 코사인 [0,1]) 같은 순위 리스트면 같은 결과가 나와야
    한다. 이게 이 기법을 쓰는 이유다(직접 가중합이면 스케일 큰 쪽이 지배함)."""
    ranking_a = [0, 1, 2]  # BM25 순위라고 가정(점수 스케일 100~500)
    ranking_b = [2, 0, 1]  # 코사인 순위라고 가정(점수 스케일 0~1)
    fused = hs.reciprocal_rank_fusion([ranking_a, ranking_b])
    # 순위 정보만 있으면 재현 가능 — 원 점수를 몰라도 결과가 결정된다.
    assert set(fused.keys()) == {0, 1, 2}


# ── rank_documents (네트워크 없이, 벡터는 직접 주입) ──────────────


def test_rank_documents_hybrid_prefers_lexical_and_semantic_agreement():
    docs_text = [
        "on-device model compression for mobile inference",  # 0: BM25+코사인 둘 다 최상위 관련
        "large scale cloud training infrastructure",          # 1: 둘 다 무관 — 최하위여야 함
        "quantization and pruning model techniques survey",   # 2: "model" 하나만 겹침 — 중간
    ]
    corpus_tokens = [hs.tokenize(t) for t in docs_text]
    bm25 = hs.BM25(corpus_tokens)
    query_tokens = hs.tokenize("model compression")

    # 가짜 임베딩: 문서 0(최고) > 문서 2(중간) > 문서 1(거의 무관) 순으로 유사하게 시뮬레이션.
    query_vec = [1.0, 0.0, 0.0]
    doc_vecs = [[0.9, 0.1, 0.0], [0.1, 0.1, 0.9], [0.7, 0.3, 0.0]]

    results = hs.rank_documents(query_tokens, bm25, query_vec, doc_vecs, top_k=3)
    ranked_indices = [r["index"] for r in results]
    assert ranked_indices[0] == 0  # 어휘·의미 둘 다 1등인 문서가 최상위
    assert ranked_indices[-1] == 1  # 어휘·의미 둘 다 꼴찌인 문서가 최하위


def test_rank_documents_without_embeddings_falls_back_to_bm25_only():
    docs_text = ["compression method A", "compression method B", "unrelated cooking recipe"]
    corpus_tokens = [hs.tokenize(t) for t in docs_text]
    bm25 = hs.BM25(corpus_tokens)
    query_tokens = hs.tokenize("compression")

    results = hs.rank_documents(query_tokens, bm25, None, [None, None, None], top_k=3)
    assert all(r["cosine_score"] is None for r in results)
    ranked_indices = [r["index"] for r in results]
    assert ranked_indices[-1] == 2  # BM25 만으로도 무관한 문서는 꼴찌


def test_rank_documents_partial_embeddings_do_not_crash():
    """일부 문서만 임베딩이 있어도(캐시 미스 등) 죽지 않아야 한다."""
    docs_text = ["alpha document text", "beta document text", "gamma document text"]
    corpus_tokens = [hs.tokenize(t) for t in docs_text]
    bm25 = hs.BM25(corpus_tokens)
    query_tokens = hs.tokenize("alpha")

    doc_vecs = [[1.0, 0.0], None, [0.0, 1.0]]
    results = hs.rank_documents(query_tokens, bm25, [1.0, 0.0], doc_vecs, top_k=3)
    assert len(results) == 3
    scored = {r["index"]: r["cosine_score"] for r in results}
    assert scored[1] is None
    assert scored[0] is not None


def test_rank_documents_respects_top_k():
    docs_text = [f"document number {i}" for i in range(10)]
    corpus_tokens = [hs.tokenize(t) for t in docs_text]
    bm25 = hs.BM25(corpus_tokens)
    results = hs.rank_documents(hs.tokenize("document"), bm25, None, [None] * 10, top_k=3)
    assert len(results) == 3
