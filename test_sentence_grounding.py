"""sentence_grounding.py 단위 테스트 — 네트워크 불필요.

⑤ 검증기를 문장 단위 그라운딩으로 격상하는 기반이 되는 함수라 특히
"학술 논문 텍스트에서 흔히 문장 경계를 착각하는 경우"를 중점적으로 본다
(약어, 소수점, 이니셜) — 여기서 틀리면 인용 번호가 엉뚱한 문장을 가리키게
되어 grounding 자체의 신뢰가 무너진다.
"""

import sentence_grounding as sg


def test_basic_two_sentences():
    text = "The model achieves 92.4% accuracy. This is a new state of the art."
    sentences = sg.segment_sentences(text)
    assert sentences == [
        "The model achieves 92.4% accuracy.",
        "This is a new state of the art.",
    ]


def test_decimal_number_not_split():
    # "92.4%" 의 마침표 뒤에 공백이 없으므로 경계 후보 자체가 안 된다.
    text = "The score was 92.4 which beats the baseline of 88.1 by a wide margin."
    sentences = sg.segment_sentences(text)
    assert len(sentences) == 1
    assert "92.4" in sentences[0] and "88.1" in sentences[0]


def test_figure_abbreviation_not_split():
    text = "As shown in Fig. 3 the loss decreases steadily. Table 4 lists all results."
    sentences = sg.segment_sentences(text)
    assert len(sentences) == 2
    assert sentences[0].startswith("As shown in Fig. 3")
    assert sentences[1].startswith("Table 4")


def test_et_al_abbreviation_not_split():
    text = "This extends the approach of Smith et al. to larger models. Results improve."
    sentences = sg.segment_sentences(text)
    assert len(sentences) == 2
    assert "Smith et al." in sentences[0]


def test_initial_not_split():
    text = "The method was proposed by J. Smith and R. Jones. It works well in practice."
    sentences = sg.segment_sentences(text)
    assert len(sentences) == 2
    assert "J. Smith and R. Jones" in sentences[0]


def test_eg_ie_abbreviations_not_split():
    text = "We use standard metrics, e.g. accuracy and F1, to evaluate. Both improve."
    sentences = sg.segment_sentences(text)
    assert len(sentences) == 2


def test_question_and_exclamation_marks_split():
    text = "Does this approach generalize? We test on three benchmarks. It works!"
    sentences = sg.segment_sentences(text)
    assert len(sentences) == 3


def test_whitespace_and_newlines_normalized():
    text = "This is\nsplit across\nlines. Another sentence here."
    sentences = sg.segment_sentences(text)
    assert len(sentences) == 2
    assert "\n" not in sentences[0]


def test_empty_text():
    assert sg.segment_sentences("") == []
    assert sg.segment_sentences("   ") == []


def test_long_sentence_is_hard_split():
    # 마침표가 아예 없는 비정상적으로 긴 텍스트 — 청크 경계가 문장 중간에서
    # 안 끊기게 하려면 어쨌든 한계는 둬야 한다.
    text = "word " * 200  # 공백 포함 1000자
    sentences = sg.segment_sentences(text.strip())
    assert len(sentences) > 1
    assert all(len(s) <= sg._MAX_SENTENCE_CHARS for s in sentences)


def test_deterministic_same_input_same_output():
    text = "First result: 42%. Second result: 17.3. Third: see Fig. 2 for details."
    assert sg.segment_sentences(text) == sg.segment_sentences(text)


def test_tag_sentences_format():
    sentences = ["First sentence.", "Second sentence.", "Third sentence."]
    tagged = sg.tag_sentences(sentences)
    assert tagged == [
        "[S0001] First sentence.",
        "[S0002] Second sentence.",
        "[S0003] Third sentence.",
    ]


def test_tag_sentences_custom_start_id():
    tagged = sg.tag_sentences(["A.", "B."], start_id=101)
    assert tagged[0].startswith("[S0101]")
    assert tagged[1].startswith("[S0102]")


def test_parse_tag_valid():
    assert sg.parse_tag("[S0142]") == 142
    assert sg.parse_tag(" [S0001] ") == 1


def test_parse_tag_invalid():
    assert sg.parse_tag("S0142") is None
    assert sg.parse_tag("[Section 3]") is None
    assert sg.parse_tag("") is None


def test_pack_into_chunks_respects_size():
    sentences = [f"[S{i:04d}] sentence number {i}." for i in range(1, 21)]
    chunks = sg.pack_into_chunks(sentences, chunk_size=200)
    assert len(chunks) > 1
    # 각 청크가 대략 상한 근처거나 그 이하 — 문장 하나가 상한을 넘지 않는 한.
    for c in chunks:
        assert len(c) <= 200 + max(len(s) for s in sentences)


def test_pack_into_chunks_never_splits_a_sentence():
    sentences = [f"[S{i:04d}] " + "x" * 50 for i in range(1, 6)]
    chunks = sg.pack_into_chunks(sentences, chunk_size=60)
    rejoined = "\n".join(chunks)
    for s in sentences:
        assert s in rejoined  # 온전한 문장 그대로 어딘가의 청크에 들어있다


def test_pack_into_chunks_oversized_sentence_gets_own_chunk():
    huge = "[S0001] " + "x" * 500
    small = "[S0002] short."
    chunks = sg.pack_into_chunks([huge, small], chunk_size=100)
    assert huge in chunks
    assert small in chunks


def test_build_tagged_chunks_respects_max_chunks_cap():
    paper = " ".join(f"Sentence number {i} has value {i}.0 percent." for i in range(1, 100))
    chunks, sentences = sg.build_tagged_chunks(paper, chunk_size=200, max_chunks=2)
    assert len(chunks) <= 2
    assert len(sentences) > 0


def test_build_tagged_chunks_sentence_list_matches_segmentation():
    paper = "First sentence here. Second sentence here. Third sentence here."
    chunks, sentences = sg.build_tagged_chunks(paper, chunk_size=1000, max_chunks=4)
    assert sentences == sg.segment_sentences(paper)
    assert len(chunks) == 1
    assert "[S0001]" in chunks[0] and "[S0003]" in chunks[0]


def test_sentence_lookup_returns_window():
    paper = "Alpha sentence. Beta sentence has 42%. Gamma sentence."
    # Beta 는 2번째 문장(1-based id=2)
    result = sg.sentence_lookup(paper, sentence_id=2, window=1)
    assert "42%" in result
    assert "Alpha" in result and "Gamma" in result  # window=1 이라 앞뒤 다 포함


def test_sentence_lookup_out_of_range_returns_none():
    paper = "Only one sentence here."
    assert sg.sentence_lookup(paper, sentence_id=99) is None
    assert sg.sentence_lookup(paper, sentence_id=0) is None


def test_sentence_lookup_zero_window_is_exact_sentence_only():
    paper = "Alpha sentence. Beta sentence has 42%. Gamma sentence."
    result = sg.sentence_lookup(paper, sentence_id=2, window=0)
    assert result == "Beta sentence has 42%."


def test_segmentation_matches_between_generation_and_verification():
    """④가 태그를 매길 때와 ⑤가 검증할 때 같은 원문이면 문장 번호가
    정확히 일치해야 한다 — grounding 의 핵심 전제."""
    paper = "Result A is 10%. Result B is 20% as shown in Table 1. Result C is 30%."
    chunks, sentences = sg.build_tagged_chunks(paper, chunk_size=1000, max_chunks=4)
    # 생성 시점: 청크 안의 [S0002] 문장에 "20%" 가 있다.
    assert "[S0002]" in chunks[0]
    assert "20%" in sentences[1]  # 0-based index 1 == sentence_id 2
    # 검증 시점: sentence_lookup 이 같은 문장을 되찾아야 한다.
    looked_up = sg.sentence_lookup(paper, sentence_id=2, window=0)
    assert "20%" in looked_up
