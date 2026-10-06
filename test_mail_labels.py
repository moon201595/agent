"""⑨ 항목명 — 설명 문장은 보존하고 질문형 제목만 명사형으로 표시한다."""
import pytest

import digest
import mail_document
import saved_digest
from test_mail_detail import Regions


BRIEF = ('- 무엇을 하려 했는가 : 목적 <unsafe> [S0001]\n'
         '- 어떻게 했는가 : 모델 SFT → RL\n'
         '- 무엇을 보였는가 : 76.3% / 기준 3.7% [S0018]')


@pytest.mark.parametrize('saved', [True, False])
def test_abstract_rows_have_noun_labels_and_keep_original_explanations(saved):
    """저장/새 초록 중 하나라도 질문형 항목을 남기거나 설명·근거·초록 안내를 바꾸면 실패한다."""
    if saved:
        text = ('연구 동향 브리핑 · 2026-10-02\n■ 오늘의 핵심 논문 1편 (전체 후보 9건 중)\n'
                '1. Paper\n   본문 비공개 — 초록만 보고 정리한 것이다\n'
                + '\n'.join('   ' + line for line in BRIEF.splitlines()))
        html = saved_digest.render_html(text, '팀', [{'title': 'Paper'}])
    else:
        html = digest._summary_block_html('s2:fake', {'abstract_brief': BRIEF}, 'abstract_only')
    regions = Regions(html)
    assert regions.section_titles == ['연구 목적', '방법론', '주요 결과']
    body = ''.join(regions.inside + regions.outside)
    for original in ['목적 <unsafe> [S0001]', '모델 SFT → RL', '76.3% / 기준 3.7% [S0018]',
                     '초록만 보고 정리한 것이다']:
        assert original in body
    for question in ['무엇을 하려 했는가', '어떻게 했는가', '무엇을 보였는가']:
        assert question not in body
    assert '<unsafe>' not in html and '&lt;unsafe&gt;' in html


def test_full_text_overview_and_its_inner_labels_are_nouns_without_extra_rows(monkeypatch):
    """원문 개요의 바깥/안쪽 질문을 남기거나 하위 항목을 새 행으로 쪼개면 실패한다."""
    lines = ['무엇을·어떻게 :', *BRIEF.splitlines(), '방법 상세 :', '- 입력 : 원래 이미지 [S0020]']
    old = mail_document.from_lines(lines, saved_digest._line)
    monkeypatch.setattr(digest, 'summary_sections', lambda aid: {
        'overview': BRIEF.splitlines(), 'method': ['입력 : 원래 이미지 [S0020]'], 'limits': ''})
    new = digest._summary_block_html('2609.00001', {'title': 'Paper'}, 'ok')
    for html in [old, new]:
        regions = Regions(html)
        assert regions.section_titles == ['연구 개요', '방법론']
        body = ''.join(regions.inside + regions.outside)
        for phrase in ['연구 목적 : 목적 <unsafe> [S0001]', '방법론 : 모델 SFT → RL',
                       '주요 결과 : 76.3% / 기준 3.7% [S0018]', '입력 : 원래 이미지 [S0020]']:
            assert phrase in body
        assert '무엇을·어떻게' not in body
        assert '어떻게 했는가' not in body


def test_new_plain_text_mail_uses_the_same_noun_labels(monkeypatch):
    """HTML만 바꾸고 앞으로 발송할 평문 초록/원문을 질문형으로 남기면 실패한다."""
    monkeypatch.setattr(digest, 'injection_label', lambda aid: '')
    paper = {'arxiv_id': 's2:fake', 'title': 'Paper', 'deep_status': 'abstract_only', 'abstract_brief': BRIEF}
    abstract = digest._paper_entry(1, paper)
    monkeypatch.setattr(digest, 'summary_sections', lambda aid: {'overview': BRIEF.splitlines(), 'limits': ''})
    full = digest._paper_entry(1, {**paper, 'deep_status': 'ok'})
    for text in [abstract, full]:
        for phrase in ['연구 목적 : 목적 <unsafe> [S0001]', '방법론 : 모델 SFT → RL',
                       '주요 결과 : 76.3% / 기준 3.7% [S0018]']:
            assert phrase in text
        assert '무엇을 하려 했는가' not in text and '어떻게 했는가' not in text
    assert '연구 개요 :' in full


def test_unknown_labels_and_question_words_inside_explanations_are_unchanged():
    """문장 안의 같은 표현이나 모르는 항목명까지 전역 치환하여 원문을 바꾸면 실패한다."""
    original = '- 분석 메모 : 어떻게 했는가를 후속 실험에서 확인한다 [S0099]'
    html = saved_digest._line(original)
    assert original in ''.join(Regions(html).outside)
    original = '원문 표현 무엇을 하려 했는가 : 이 표현은 제목이 아니다.'
    assert original in ''.join(Regions(saved_digest._line(original)).outside)


def test_saved_noun_labels_can_be_read_back_as_closed_three_and_two_row_tables():
    """앞으로 생성된 명사형 메일을 저장해 다시 그릴 때 표/토글이 사라지면 실패한다."""
    text = ('연구 동향 브리핑 · 2026-10-02\n■ 오늘의 핵심 논문 2편 (전체 후보 9건 중)\n'
            '1. Abstract\n   본문 비공개 — 초록만 보고 정리한 것이다\n'
            '   - 연구 목적 : 목적 [S0001]\n   - 방법론 : 모델 SFT → RL\n   - 주요 결과 : 76.3% [S0018]\n'
            '2. Full\n   연구 개요 :\n   - 연구 목적 : 목적 [S0001]\n'
            '   방법 상세 :\n   - 입력 : 이미지 [S0020]\n   [원문 분석 완료]')
    html = saved_digest.render_html(text, '팀', [{'title': 'Abstract'}, {'title': 'Full'}])
    regions = Regions(html)
    assert regions.section_titles == ['연구 목적', '방법론', '주요 결과', '연구 개요', '방법론']
    assert regions.toggles == 2 and regions.open_toggles == 0
    assert [c['toggles'] for c in regions.cards] == [1, 1]
    assert '76.3% [S0018]' in ''.join(regions.cards[0]['body'])
    assert '입력 : 이미지 [S0020]' in ''.join(regions.cards[1]['body'])


@pytest.mark.parametrize('label', ['무엇을 하려 했는가', '무엇을 하려고 했는가', '무엇을 하려 했다는가'])
def test_legacy_purpose_variants_keep_values_and_fullwidth_colon(label):
    """과거 목적 라벨의 변형이나 전각 콜론을 놓치거나 반복된 목록 기호/근거를 잃으면 실패한다."""
    original = '  - - ' + label + '： 원 수치 42 [S0002]'
    html = mail_document.from_lines(['- ' + label + '： 원 수치 42 [S0002]'], saved_digest._line, brief=True)
    regions = Regions(html)
    assert regions.section_titles == ['연구 목적']
    assert '원 수치 42 [S0002]' in ''.join(regions.outside)
    assert mail_document.nominal_line(original) == '  - - 연구 목적： 원 수치 42 [S0002]'
