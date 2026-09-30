"""④⑤ 표 구조 — 실제 HTML 교차 셀의 고정값으로 행·열 이동 회귀를 잡는다."""
from pathlib import Path

import pytest

import arxiv_tables as at


# 공용 autouse가 무관한 운영 모듈과 .env를 읽으므로 이 파일 안에서만 대체한다.
def _pure_parser_only() -> None:
    """독립 파서 검증이 운영 설정·DB·네트워크에 의존하지 않게 한다."""


for _name in (
    '_no_real_feedback_config', '_no_real_shadow_search', '_no_real_link_audit_network',
    '_no_real_sota_lookups', '_no_real_github_search', '_no_real_codex_cli',
):
    globals()[_name] = pytest.fixture(name=_name, autouse=True)(_pure_parser_only)


FIXTURES = Path(__file__).parent / 'fixtures' / 'arxiv_tables'


def _fixture(paper: str, table_id: str) -> dict:
    return next(t for t in at.parse_tables((FIXTURES / f'{paper}.html').read_text(encoding='utf-8'))
                if t['id'] == table_id)


def _html(rows: str, caption: str = '', attrs: str = '') -> str:
    return f'<figure class="ltx_table" {attrs}><figcaption>{caption}</figcaption><table>{rows}</table></figure>'


def test_real_grim() -> None:
    """병합된 방법 분류나 벤치마크 열을 밀어 GRIM의 네 수치를 바꾸면 실패한다."""
    table = _fixture('2609.35059', 'S3.T1')
    assert table['header_rows'] == 2
    assert len(table['grid']) == 18
    assert {len(row) for row in table['grid']} == {6}
    cells = at.find_cells(table, lambda s: s == 'Unified GRIM (Ours)',
                          lambda p: any('AUROC' in s for s in p))
    assert [(c['row'], c['col'], c['column_path'], c['value']) for c in cells] == [
        (17, 2, ['Anomaly-ShapeNet', r'O-AUROC(\uparrow)'], 97.4),
        (17, 3, ['Anomaly-ShapeNet', r'P-AUROC(\uparrow)'], 94.5),
        (17, 4, ['Real3D-AD', r'O-AUROC(\uparrow)'], 87.2),
        (17, 5, ['Real3D-AD', r'P-AUROC(\uparrow)'], 91.3),
    ]
    assert at.column_path(table, 0) == ['Method']
    assert at.find_cells(table, lambda s: 'IMRNet' in s,
                         lambda p: p == ['Real3D-AD', r'P-AUROC(\uparrow)']) == []


@pytest.mark.skipif(not (Path(__file__).parent / 'fixtures' / 'arxiv_tables' / '2609.21424.html').exists(), reason='원문 픽스처는 로컬 전용(재배포 권한)')
def test_real_surface_ours() -> None:
    """데이터셋·백본 rowspan을 잃거나 1-shot의 MEAN을 Fold/5-shot과 섞으면 실패한다."""
    table = _fixture('2609.21424', 'S3.T1')
    assert table['header_rows'] == 2
    assert {len(row) for row in table['grid']} == {13}
    assert not any('P³-SAM' in s for row in table['grid'] for s in row)
    cells = at.find_cells(table, lambda s: s == 'Surface Defects-4i Ours ResNet50',
                          lambda p: p == ['mIoU(1-shot)', 'MEAN'])
    assert cells == [{'row': 15, 'col': 6, 'row_label': 'Surface Defects-4i Ours ResNet50',
                      'column_path': ['mIoU(1-shot)', 'MEAN'], 'text': '55.41', 'value': 55.41}]
    baseline = at.find_cells(table, lambda s: s == 'Surface Defects-4i VRP-SAM ResNet50',
                             lambda p: p == ['mIoU(1-shot)', 'MEAN'])
    assert len(baseline) == 1
    assert baseline[0]['value'] == 43.41


@pytest.mark.skipif(not (Path(__file__).parent / 'fixtures' / 'arxiv_tables' / '2609.18623.html').exists(), reason='원문 픽스처는 로컬 전용(재배포 권한)')
def test_real_simlingo() -> None:
    """thead 없는 표의 그룹명을 머리에 넣거나 DS·SR·다른 데이터셋을 섞으면 실패한다."""
    table = _fixture('2609.18623', 'S5.T1')
    assert table['header_rows'] == 2
    assert {len(row) for row in table['grid']} == {13}
    cells = at.find_cells(table, lambda s: s == 'SimLingo [18] S ✓',
                          lambda p: p == ['Bench2Drive', r'DS \uparrow'])
    assert cells == [{'row': 17, 'col': 3, 'row_label': 'SimLingo [18] S ✓',
                      'column_path': ['Bench2Drive', r'DS \uparrow'], 'text': '85.07', 'value': 85.07}]
    cells = at.find_cells(table, lambda s: s.startswith('SimLingo '),
                          lambda p: p == ['Fail2Drive Generalisation', r'DS \uparrow'])
    assert len(cells) == 1
    assert cells[0]['text'] == '71.7 (-13.2%)'
    assert cells[0]['value'] == 71.7


def test_spans_math_caption_and_missing() -> None:
    """빈 병합 셀을 덮거나 math 내부를 중복 수집하거나 서식의 글자를 버리면 실패한다."""
    html = _html('''<thead>
    <tr><th rowspan="2">Method</th><th colspan="2">Bench</th></tr>
    <tr><th>DS <math alttext="↑"><mo>WRONG</mo></math></th><th>SR</th></tr></thead>
    <tbody><tr><td rowspan="2">P<sup>3</sup>-<b>SAM</b></td><td><u>87.2</u>±0.3</td><td>N/A</td></tr>
    <tr><td></td><td>−3.1%</td></tr><tr><td>Other<br>model</td><td colspan="2"></td></tr></tbody>''',
                 'Table <b>1</b>: A&nbsp;&amp; B <math alttext="x"><mi>ignored</mi></math>')
    table, = at.parse_tables(html)
    assert table == {'id': 'T1', 'caption': 'Table 1: A & B x', 'header_rows': 2,
                     'grid': [['Method', 'Bench', 'Bench'], ['Method', 'DS ↑', 'SR'],
                              ['P3-SAM', '87.2±0.3', 'N/A'], ['P3-SAM', '', '−3.1%'],
                              ['Other model', '', '']]}
    assert at.column_path(table, 1) == ['Bench', 'DS ↑']
    cells = at.find_cells(table, lambda s: s == 'P3-SAM', lambda p: 'Bench' in p)
    assert [(c['row'], c['col'], c['value']) for c in cells] == [(2, 1, 87.2), (3, 2, -3.1)]


@pytest.mark.parametrize(('text', 'value'), [
    ('87.2', 87.2), ('87.2±0.3', 87.2), ('87.2 (+1.3)', 87.2), ('87.2%', 87.2),
    ('−3.1', -3.1), ('N/A', None), ('-', None), ('', None), ('—', None),
    ('accuracy: 12.5', 12.5), ('ResNet50', None), ('1e-3', .001), ('.5', .5), ('1e999', None),
])
def test_number(text: str, value: float | None) -> None:
    """첫 수치 대신 오차·개선량을 택하거나 결측·모델명·무한대를 수치화하면 실패한다."""
    assert at.cell_number(text) == value


@pytest.mark.parametrize(('rows', 'headers'), [
    ('<thead><tr><td>2025</td><td>2026</td></tr></thead><tr><td>A</td><td>1</td></tr>', 1),
    ('<tr><td class="ltx_th ltx_th_column">2025</td><th>2026</th></tr><tr><th scope="row">A</th><td>1</td></tr>', 1),
    ('<tr><td>Method</td><td>Score</td></tr><tr><th class="ltx_th_row">A</th><td>1</td></tr>', 1),
    ('<tr><td>Method</td><td>Score</td></tr><tr><td class="ltx_border_t" colspan="2">Group</td></tr><tr><td>A</td><td>1</td></tr>', 1),
    ('<tr><td>Method</td><td>Score</td></tr><tr><td>A</td><td>N/A</td></tr><tr><td>B</td><td>1</td></tr>', 1),
    ('<tr><th scope="row">A</th><td>1</td></tr>', 0),
])
def test_headers(rows: str, headers: int) -> None:
    """명시 머리·행 th·전폭 그룹·결측 행을 구분하는 경계를 바꾸면 실패한다."""
    table, = at.parse_tables(_html(rows))
    assert table['header_rows'] == headers


def test_rowspan_zero_and_padding() -> None:
    """rowspan=0을 다음 tbody까지 늘리거나 짧은 행의 빈 칸을 채우지 않으면 실패한다."""
    table, = at.parse_tables(_html('''<tbody><tr><td rowspan="0">A</td><td>1</td></tr>
    <tr><td>2</td></tr></tbody><tbody><tr><td>B</td></tr></tbody>'''))
    assert table['grid'] == [['A', '1'], ['A', '2'], ['B', '']]
    assert at.row_label(table, 1) == 'A'
    assert at.row_label(table, -1) == ''
    assert at.column_path(table, -1) == []
    assert at.column_path(table, 2) == []


def test_scope_and_ids() -> None:
    """일반 표를 수집하거나 중첩 figure의 표를 중복 반환하거나 figure id를 잃으면 실패한다."""
    html = '<table><tr><td>outside</td></tr></table>'
    html += _html('<tr><td>A</td><td>1</td></tr>', attrs='id="S1.T1"')
    html += '<figure class="ltx_table">' + _html('<tr><td>B</td><td>2</td></tr>') + '</figure>'
    tables = at.parse_tables(html)
    assert [t['id'] for t in tables] == ['S1.T1', 'T2']
    assert [t['grid'] for t in tables] == [[['A', '1']], [['B', '2']]]


@pytest.mark.parametrize('span', ['9999999999999999999', '-1', 'x', '257'])
def test_bad_colspan(span: str) -> None:
    """비정상·거대 colspan을 축소하거나 배치를 추측해 결과로 내놓으면 실패한다."""
    assert at.parse_tables(_html(f'<tr><td colspan="{span}">1</td></tr>')) == []


def test_bad_geometry_and_unclosed() -> None:
    """겹친 셀·행 밖 병합·미완성·중첩 표를 정상 교차점으로 반환하면 실패한다."""
    assert at.parse_tables(_html('<tr><td>A</td><td rowspan="2">B</td></tr><tr><td colspan="2">9</td></tr>')) == []
    assert at.parse_tables(_html('<tr><td rowspan="2">9</td></tr>')) == []
    assert at.parse_tables('<figure class="ltx_table"><table><tr><td>1</td>') == []
    assert at.parse_tables(_html('<tr><td><table><tr><td>9</td></tr></table></td></tr>')) == []


def test_input_byte_limit() -> None:
    """5MiB 상한을 문자 수로만 검사하거나 상한 초과 접두부 표를 반환하면 실패한다."""
    html = _html('<tr><td>A</td><td>1</td></tr>')
    assert len(at.parse_tables(html + ' ' * (at.MAX_INPUT_BYTES - len(html)))) == 1
    assert at.parse_tables(html + ' ' * at.MAX_INPUT_BYTES) == []
    assert at.parse_tables(html + '가' * (at.MAX_INPUT_BYTES // 3 + 1)) == []


def test_table_limit() -> None:
    """표 수 상한을 넘긴 문서를 부분 성공으로 반환하면 실패한다."""
    html = _html('<tr><td>1</td></tr>')
    assert len(at.parse_tables(html * at.MAX_TABLES)) == at.MAX_TABLES
    assert at.parse_tables(html * (at.MAX_TABLES + 1)) == []


def test_expanded_cell_limit(monkeypatch) -> None:
    """원본 셀만 세어 병합·직사각형 패딩으로 증가한 셀을 제한하지 않으면 실패한다."""
    monkeypatch.setattr(at, 'MAX_CELLS', 6)
    small = _html('<tr><td colspan="3">H</td></tr><tr><td>A</td><td>1</td></tr>')
    assert at.parse_tables(small)[0]['grid'] == [['H', 'H', 'H'], ['A', '1', '']]
    assert at.parse_tables(_html('<tr><td colspan="4">H</td></tr><tr><td>A</td></tr>')) == []
    monkeypatch.setattr(at, 'MAX_TOTAL_CELLS', 11)
    assert at.parse_tables(small * 2) == []


def test_depth_node_and_row_limits(monkeypatch) -> None:
    """셀 외부의 과도한 중첩·노드·빈 행도 자원 상한으로 제한하지 않으면 실패한다."""
    assert at.parse_tables('<div>' * (at.MAX_DEPTH + 1)) == []
    monkeypatch.setattr(at, 'MAX_NODES', 3)
    assert at.parse_tables(_html('<tr><td>1</td></tr>')) == []
    monkeypatch.setattr(at, 'MAX_NODES', 100)
    monkeypatch.setattr(at, 'MAX_ROWS', 2)
    assert at.parse_tables(_html('<tr></tr>' * 3)) == []


def test_math_fallback_and_ignored_content() -> None:
    """alttext 수치를 MathML과 합치거나 alttext 없는 annotation을 중복해 넣으면 실패한다."""
    table, = at.parse_tables(_html('''<tr><td>Model</td>
    <td><math alttext="87.2"><mn>99</mn><annotation>99</annotation></math></td>
    <td><math><semantics><mn>3.5</mn><annotation>3.5</annotation></semantics></math></td>
    <td>4<script>999</script><style>999</style></td></tr>'''))
    assert table['grid'] == [['Model', '87.2', '3.5', '4']]
    assert table['header_rows'] == 0
    cells = at.find_cells(table, lambda s: s == 'Model', lambda p: True)
    assert [c['value'] for c in cells] == [87.2, 3.5, 4.0]


def test_column_path_only_adjacent_duplicates() -> None:
    """빈 머리를 경로에 넣거나 떨어져 재등장한 머리까지 제거하면 실패한다."""
    table, = at.parse_tables(_html('''<thead>
    <tr><th>A</th></tr><tr><th></th></tr><tr><th>A</th></tr>
    <tr><th>B</th></tr><tr><th>A</th></tr></thead><tr><td>7</td></tr>'''))
    assert table['header_rows'] == 5
    assert at.column_path(table, 0) == ['A', 'B', 'A']
    assert at.find_cells(table, lambda s: True, lambda p: p == ['A', 'B', 'A']) == [
        {'row': 5, 'col': 0, 'row_label': '', 'column_path': ['A', 'B', 'A'],
         'text': '7', 'value': 7.0}]
