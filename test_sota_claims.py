"""⑤⑨ SOTA 주장(2026-09-16) — 논문 자체 주장만, 미검증 표시, 서술 근거 연결. 각 docstring 에 "무엇을 망가뜨리면 실패하는가".
"""
import pytest

import sota_claims as sc
import digest


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """digest 가 읽는 DB 를 임시로 — 이 파일의 첫 판은 격리가 없어 digest → code_ladder.get() 이 **운영 DB** 에 표를 만들었다
    (conftest 가 켠 DDL 플래그 때문, 2026-09-16). 운영 DB 를 건드리는 테스트를 두지 않는다."""
    import server, storage
    store = tmp_path / "s.db"
    storage.init_storage(store)
    monkeypatch.setattr(storage, "DB_PATH", store)
    monkeypatch.setattr(server, "DB_PATH", store)


def test_only_own_performance_claims_count():
    """이 테스트가 잡는 것: 배경 설명("existing SOTA methods")·SOTA 모델을 썼다는 문장·조사 문장을 주장으로 싣는 것, 1인칭 없는 배경 문장을
    주장으로 싣는 것, 부정문("does not reach SOTA")을 주장으로 싣는 것, 반대로 진짜 주장("we achieve state-of-the-art results")을 놓치는 것."""
    yes = ["We achieve state-of-the-art results on MVTec AD and VisA.",
           "Our approach sets a new state of the art on LIBERO-10.",
           "Moreover, our method surpasses existing state-of-the-art models.",
           "Across three benchmarks we outperform all competing approaches.",
           # 외부 검토(2026-09-16)가 실제 원문에서 찾은 미탐 셋
           "Our method outperforms state-of-the-art approaches on all tasks.",
           "Our framework achieves state-of-the-art (SOTA) performance across all metrics compared to existing baselines.",
           "Our model establishes a new single-model state-of-the-art BLEU score of 41.8."]
    no = ["Existing state-of-the-art methods rely on large labeled datasets.",
          "We used DeepSeek-V4-Pro, one of the state-of-the-art open-weight models, to analyze episodes.",
          "We first survey state-of-the-art techniques for each pillar.",
          "Recent advancements in rPPG methods achieve state-of-the-art results.",
          "Compared to state-of-the-art baselines, our model does not reach SOTA accuracy.",
          "In prior work, we achieved state-of-the-art results on MVTec AD.",          # 이 논문의 성과가 아니다
          "OpenVLA achieves state-of-the-art results on Bridge V2."]        # 1인칭·제안 단서 없음 — 놓치더라도 배경 문장을 안 싣는 쪽
    assert all(sc.is_own_claim(t) for t in yes)
    assert not any(sc.is_own_claim(t) for t in no)


def test_benchmark_names_are_parsed_and_junk_is_dropped():
    """이 테스트가 잡는 것: "on MVTec AD and VisA" 를 하나로 붙이는 것, 전치사 뒤("by a large margin")를 이름에 넣는 것, "on Towel"·"on Both"
    같은 보통 낱말을 벤치마크로 내는 것."""
    assert sc._benchmarks("We achieve state-of-the-art results on MVTec AD and VisA, outperforming all prior methods.") == ["MVTec AD", "VisA"]
    assert sc._benchmarks("outperforms prior approaches on the LIBERO benchmark by a large margin") == ["LIBERO"]
    assert sc._benchmarks("sets a new state of the art on Bridge V2 with 97.3% success") == ["Bridge V2"]
    assert sc._benchmarks("may fail on Towel folding and on Both settings") == []
    # 외부 검토(2026-09-16)가 실제 원문에서 찾은 누락 둘
    assert sc._benchmarks("consistent gains on both the CALVIN and LIBERO benchmarks") == ["CALVIN", "LIBERO"]
    assert sc._benchmarks("Across three benchmarks, GenEval, HPSv2, and DPG, we outperform all competing approaches.") == ["GenEval", "HPSv2", "DPG"]


def test_extract_dedupes_and_keeps_sentence_numbers_for_narrative_evidence():
    """이 테스트가 잡는 것: 같은 벤치마크 주장을 여러 번 싣는 것, 주장 수 상한을 안 지키는 것, 서술 근거 S번호가 원문 문장 번호와 어긋나는 것,
    초록에서 뽑은 주장에 가짜 S번호를 붙이는 것."""
    text = ("Intro sentence one. We achieve state-of-the-art results on MVTec AD. Another sentence. "
            "We again achieve state-of-the-art results on MVTec AD. Our approach sets a new state of the art on VisA. "
            "We obtain state-of-the-art accuracy on BTAD. We report state-of-the-art performance on KSDD2.")
    claims = sc.extract(text)
    assert [c["benchmarks"] for c in claims] == [["MVTec AD"], ["VisA"], ["BTAD"]] and claims[0]["index"] == 2
    packets = sc.evidence_packets(claims)
    assert packets[0]["id"] == "S0002" and packets[0]["text"].startswith("(논문 자체의 SOTA 주장 — 미검증)")
    from_abstract = sc.extract("We achieve state-of-the-art results on MVTec AD.")
    for c in from_abstract:
        c["index"] = None
    assert sc.evidence_packets(from_abstract) == []


def test_mail_line_marks_claims_unverified_and_stays_silent_without_claims():
    """규칙 7. 이 테스트가 잡는 것: 주장을 확인된 사실처럼 쓰는 것, 주장이 없는 논문에 SOTA 줄을 억지로 붙이는 것, HTML 이스케이프 누락."""
    claims = [{"index": 3, "sentence": "We achieve state-of-the-art results on MVTec AD & VisA.", "benchmarks": ["MVTec AD", "VisA"]}]
    line = sc.mail_line(claims)
    assert line.startswith("SOTA 주장(논문 자체 주장 · 미검증): MVTec AD, VisA — “We achieve")
    assert sc.mail_line([]) == ""
    paper = {"arxiv_id": "2609.00001", "title": "T", "abstract": "a", "deep_status": "ok", "_sota_claims": claims,
             "_score": {"priority": 1.0, "core_hits": ["alpha"], "domain_hits": [], "venue_hit": None}}
    result = {"papers": [paper], "candidates_found": 1}
    assert line in digest.generate_digest(result, "P")
    html = digest.generate_digest_html(result, "P")
    assert "MVTec AD &amp; VisA" in html and "논문 자체 주장 · 미검증" in html
    paper.pop("_sota_claims")
    assert "SOTA 주장" not in digest.generate_digest(result, "P") and "SOTA 주장" not in digest.generate_digest_html(result, "P")
