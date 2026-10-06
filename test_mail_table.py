"""⑨ 상세 표 — 스타일이 없어도 항목명/설명이 실제 한 행의 두 칸이다."""
import re

import digest
import mail_document
import saved_digest
from test_mail_detail import DETAIL_LINES, HEADINGS, Regions
from test_mail_ui import Elements


def test_detail_is_one_real_table_with_two_cells_and_centered_labels_without_css():
    """style 태그가 없어도 한 표의 각 행이 중앙 항목명/왼쪽 설명 두 칸이어야 하며 중첩 표 배치는 실패한다."""
    html = mail_document.from_lines(DETAIL_LINES, saved_digest._line)
    html = re.sub(r'<style>.*?</style>', '', html, flags=re.S)
    parsed = Elements(html)
    tables = [node for node in parsed.nodes if node['tag'] == 'table']
    assert len(tables) == 1
    assert tables[0]['attrs']['class'] == 'ph-document'
    rows = parsed.by_class('ph-section')
    assert len(rows) == 6 and all(row['tag'] == 'tr' for row in rows)
    for row in rows:
        cells = [node for node in parsed.nodes if node['tag'] == 'td' and node['parent'] is row]
        assert len(cells) == 2
        label, body = cells
        assert label['attrs']['class'] == 'ph-label'
        assert label['attrs']['align'] == 'center' and label['attrs']['valign'] == 'middle'
        assert 'text-align:center' in label['attrs']['style'] and 'vertical-align:middle' in label['attrs']['style']
        assert label['attrs']['bgcolor'] == '#EDF5FA'
        assert body['attrs']['class'] == 'ph-detail-body'
        assert body['attrs']['align'] == 'left' and body['attrs']['valign'] == 'top'
        assert body['attrs']['bgcolor'] == '#FFFFFF'
    regions = Regions(html)
    assert regions.section_titles == HEADINGS
    text = ''.join(regions.outside)
    for phrase in ['모델 : Qwen 2B', '학습 : SFT → RL', '성공 76.3% / 기준 3.7% [S0018]', '알 수 없는 메모 : 그대로 보존']:
        assert phrase in text
    assert 'display:inline-table' not in html


def test_abstract_analysis_uses_three_table_rows_in_saved_and_new_mail():
    """초록만 원래 문단으로 남기거나 세 항목·초록 안내·원 수치를 누락하면 실패한다."""
    brief = ('- 무엇을 하려 했는가 : 목적 <unsafe>\n'
             '- 어떻게 했는가 : 프로토콜 비교 [S0002]\n'
             '- 무엇을 보였는가 : 원래 초록의 42개 사례')
    text = ('연구 동향 브리핑 · 2026-10-02\n■ 오늘의 핵심 논문 1편 (전체 후보 9건 중)\n'
            '1. Abstract paper\n   [초록 기반 · 미검증]\n   본문 비공개 — 초록만 보고 정리한 것이다\n'
            + '\n'.join('   ' + line for line in brief.splitlines()))
    old = saved_digest.render_html(text, '팀', [{'title': 'Abstract paper'}])
    new = digest._summary_block_html('s2:fake', {'abstract_brief': brief}, 'abstract_only')
    for html in [old, new]:
        parsed = Elements(html)
        rows = parsed.by_class('ph-section')
        assert len(rows) == 3 and all(row['tag'] == 'tr' for row in rows)
        regions = Regions(html)
        assert regions.section_titles == ['연구 목적', '방법론', '주요 결과']
        body = ''.join(regions.inside + regions.outside)
        for phrase in ['초록만 보고 정리한 것이다', '목적 <unsafe>', '프로토콜 비교 [S0002]', '원래 초록의 42개 사례']:
            assert phrase in body
        assert '<unsafe>' not in html and '&lt;unsafe&gt;' in html
    assert Regions(old).toggles == 1 and Regions(old).open_toggles == 0


def test_full_text_keeps_abstract_like_subheadings_inside_its_overview_row():
    """초록의 세 항목을 원문 분석에도 적용해 무엇을·어떻게 절을 쪼개면 실패한다."""
    lines = ['무엇을·어떻게 :', '- 무엇을 하려 했는가: 검색 역할 분석',
             '- 어떻게 했는가: 모델 SFT → RL', '- 무엇을 보였는가: 76.3% [S0018]',
             '방법 상세 :', '입력 : 이미지 → 임베딩']
    html = mail_document.from_lines(lines, saved_digest._line)
    regions = Regions(html)
    assert regions.section_titles == ['연구 개요', '방법론']
    assert len(Elements(html).by_class('ph-section')) == 2
    body = ''.join(regions.outside)
    for phrase in ['연구 목적: 검색 역할 분석', '방법론: 모델 SFT → RL',
                   '주요 결과: 76.3% [S0018]', '입력 : 이미지 → 임베딩']:
        assert phrase in body
