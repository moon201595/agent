"""summary_parser.py 단위 테스트 — 네트워크 불필요.

verify.py가 이미 검증된 로직이라 여기서는 섹션 파싱 자체(마크다운 구조를
정확히 뽑는지)와 verify.verify_numbers 와의 결합만 검증한다.
"""

import summary_parser as sp

_SAMPLE_MD = """### 기본정보
- 제목 : Test Paper Title
- 저자/소속 : A. Author / Test University
- 발표 시점·게재처 : arXiv 프리프린트 · 게재 여부 미확인
- 링크 : 명시되지 않음

### 연구 개요
※ 이 절에는 수치를 쓰지 않는다. 각 항목 2~3문장.
- 무엇을 하려 했는가 : 문제를 풀려고 했다.
- 어떻게 했는가 : 방법을 썼다.

### 결과
※ 수치는 여기에만 쓴다.
- 정확도 91.0% (테스트 조건 / 베이스라인 대비 / 정확도) — 본문 3절 [S0002] ★★★
- 처리 속도 2.5배 향상 (동일 조건 / 기존 방법 대비 / 속도) — 본문 4절 [S0004] ★★

### 논문의 한계점
- 저자가 밝힌 한계 : 일반화가 제한적이다.
- 요약자가 판단한 한계 : 비교 대상이 적다.

### 결론
① 한 줄 요약 : 방법을 제안하고 검증했다.
② 문제의식 : 기존 방법에 한계가 있었다.
"""

_SAMPLE_SOURCE = (
    "First sentence sets up the problem for this study. "
    "The proposed method achieves 91.0 percent accuracy compared to the baseline here. "
    "Additional context sentence follows here for padding purposes. "
    "It also runs 2.5 times faster than the existing method under identical conditions."
)


def test_parse_sections_splits_by_heading():
    sections = sp.parse_sections(_SAMPLE_MD)
    assert set(sections.keys()) == {"기본정보", "연구 개요", "결과", "논문의 한계점", "결론"}


def test_parse_sections_extracts_bullets_flat():
    sections = sp.parse_sections(_SAMPLE_MD)
    assert len(sections["기본정보"]) == 4
    assert sections["기본정보"][0].startswith("제목")


def test_parse_sections_excludes_template_instruction_lines():
    sections = sp.parse_sections(_SAMPLE_MD)
    joined = " ".join(sections["결과"])
    assert "※" not in joined
    assert "수치는 여기에만" not in joined


def test_parse_sections_handles_circled_number_bullets():
    sections = sp.parse_sections(_SAMPLE_MD)
    assert len(sections["결론"]) == 2
    assert sections["결론"][0].startswith("한 줄 요약")


def test_parse_sections_empty_markdown_returns_empty_dict():
    assert sp.parse_sections("") == {}
    assert sp.parse_sections("그냥 평문, 헤딩 없음") == {}


def test_parse_summary_includes_sections_and_verification():
    result = sp.parse_summary(_SAMPLE_MD, _SAMPLE_SOURCE)
    assert "sections" in result
    assert "verification" in result
    assert result["verification"]["total"] > 0


def test_parse_summary_grounded_claims_reflect_sentence_citations():
    result = sp.parse_summary(_SAMPLE_MD, _SAMPLE_SOURCE)
    claims = {c["token"]: c for c in result["verification"]["claims"]}
    assert claims["91.0"]["grounded"] is True
    assert claims["91.0"]["found"] is True
    assert claims["2.5"]["grounded"] is True
    assert claims["2.5"]["found"] is True


def test_parse_summary_merges_provided_meta():
    result = sp.parse_summary(_SAMPLE_MD, _SAMPLE_SOURCE, meta={"arxiv_id": "9999.99999"})
    assert result["meta"]["arxiv_id"] == "9999.99999"


def test_parse_summary_without_meta_omits_meta_key():
    result = sp.parse_summary(_SAMPLE_MD, _SAMPLE_SOURCE)
    assert "meta" not in result


def test_parse_summary_catches_fabricated_citation():
    """이 파서가 그라운딩 기능과 실제로 맞물려 있는지 확인 — 조작된 인용은
    구조화 결과의 claims 안에서도 found=False 로 나와야 한다."""
    tampered = _SAMPLE_MD.replace("[S0002]", "[S0099]")  # 존재하지 않는 문장 번호
    result = sp.parse_summary(tampered, _SAMPLE_SOURCE)
    claims = {c["token"]: c for c in result["verification"]["claims"]}
    assert claims["91.0"]["found"] is False
    assert claims["91.0"]["grounded"] is True
