"""④⑤ 표 구조 — 수치를 행·열 교차점에 묶어 PDF 평문에서 잃는 대응을 보존한다.

표준 라이브러리만 쓰며 파일·네트워크·LLM에 접근하지 않는다.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from html.parser import HTMLParser
import math
import re

MAX_INPUT_BYTES = 5 * 1024 * 1024
MAX_TABLES = 128
MAX_CELLS = 10_000
MAX_TOTAL_CELLS = 100_000
MAX_ROWS = 2_000
MAX_COLUMNS = 256
MAX_NODES = 100_000
MAX_DEPTH = 128
_VOID = frozenset('area base br col embed hr img input link meta param source track wbr'.split())
_MISSING = frozenset({'n/a', 'na', '-', '–', '—', '−'})
_NUMBER = re.compile(r'(?<![\w.])[+\-−]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+\-]?[0-9]+)?(?![\w.])')


class _Limit(ValueError):
    """상한을 넘긴 일부 표를 정상 결과로 오인하지 않게 전체 처리를 중단한다."""


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[_Node | str] = field(default_factory=list)
    closed: bool = False
    source_tag: str = ''

    @property
    def classes(self) -> set[str]:
        return set(self.attrs.get('class', '').split())


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node('root')
        self.stack = [self.root]
        self.nodes = 0
        self.tables = 0
        self.cells = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        source_tag = tag
        classes = set((dict(attrs).get('class') or '').split())
        if tag in {'span', 'div'}:
            roles = {'ltx_tabular': 'table', 'ltx_thead': 'thead', 'ltx_tbody': 'tbody',
                     'ltx_tfoot': 'tfoot', 'ltx_tr': 'tr', 'ltx_td': 'td'}
            tag = next((role for cls, role in roles.items() if cls in classes), tag)
            if tag == 'td' and 'ltx_th' in classes:
                tag = 'th'
        self.nodes += 1
        self.tables += tag == 'table'
        self.cells += tag in {'td', 'th'}
        if (self.nodes > MAX_NODES or len(self.stack) > MAX_DEPTH
                or self.tables > MAX_TABLES or self.cells > MAX_TOTAL_CELLS):
            raise _Limit
        node = _Node(tag, {k: v or '' for k, v in attrs}, source_tag=source_tag)
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)
        else:
            node.closed = True

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, 0, -1):
            if (self.stack[i].source_tag or self.stack[i].tag) == tag:
                self.stack[i].closed = True
                del self.stack[i:]
                break

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def _descendants(node: _Node, tag: str, stop: tuple[str, ...] = ()) -> Iterator[_Node]:
    for child in node.children:
        if isinstance(child, _Node):
            if child.tag == tag:
                yield child
            if child.tag not in stop:
                yield from _descendants(child, tag, stop)


def _plain(node: _Node) -> str:
    """alttext를 원문 그대로 택해 MathML·TeX annotation의 같은 수치 중복을 막는다."""
    if node.tag in {'script', 'style', 'annotation', 'annotation-xml'}:
        return ''
    if node.tag == 'math' and 'alttext' in node.attrs:
        return node.attrs['alttext']
    if node.tag == 'br':
        return ' '
    parts = ''.join(child if isinstance(child, str) else _plain(child) for child in node.children)
    if node.tag in {'p', 'div', 'tr'} or 'ltx_p' in node.classes:
        parts = ' ' + parts + ' '
    return parts


def _text(node: _Node) -> str:
    return ' '.join(_plain(node).split())


def cell_number(text: str) -> float | None:
    """오차·개선량보다 먼저 쓰인 유한 수치를 택하며 방법명 속 숫자는 수치로 보지 않는다."""
    text = text.strip()
    if text.lower() in _MISSING:
        return None
    match = _NUMBER.search(text)
    if match is None:
        return None
    value = float(match.group().replace('−', '-'))
    return value if math.isfinite(value) else None


def _numeric_cell(text: str) -> bool:
    # 인용 연도나 ResNet50을 측정값으로 보면 행 라벨과 머리 경계가 잘린다.
    return _NUMBER.match(text.strip()) is not None


def _span(cell: _Node, key: str, maximum: int) -> int:
    raw = cell.attrs.get(key, '1').strip()
    if not raw.isascii() or not raw.isdigit() or len(raw) > 6:
        raise _Limit
    value = int(raw)
    if value > maximum:
        raise _Limit
    if key == 'colspan' and value == 0:
        raise _Limit
    return value


def _rows(table: _Node) -> list[tuple[_Node, str, int]]:
    """행 그룹을 보존해야 rowspan=0이 다음 tbody까지 침범하지 않는다."""
    result = []
    group_id = 0
    for child in table.children:
        if not isinstance(child, _Node):
            continue
        if child.tag == 'tr':
            result.append((child, '', group_id))
        elif child.tag in {'thead', 'tbody', 'tfoot'}:
            group_id += 1
            result.extend((row, child.tag, group_id) for row in child.children
                          if isinstance(row, _Node) and row.tag == 'tr')
            group_id += 1
    return result


def _grid(table: _Node) -> tuple[list[list[str]], list[tuple[_Node, str, int]], list[list[_Node]]]:
    rows = _rows(table)
    if len(rows) > MAX_ROWS:
        raise _Limit
    occupied: dict[tuple[int, int], str] = {}
    originals = []
    width = 0
    for r, (row, _, group) in enumerate(rows):
        cells = [c for c in row.children if isinstance(c, _Node) and c.tag in {'td', 'th'}]
        originals.append(cells)
        col = 0
        end = r + 1
        while end < len(rows) and rows[end][2] == group:
            end += 1
        for cell in cells:
            # 칸 안에 칸·행이 또 있으면 닫힘이 빠진 HTML 이다(`<td>9<td>1.5</td>`). 글자를 이어 붙이면 없는 수치 "91.5" 가 생긴다
            # (2026-10-07 Codex 독립 검토 P1) — 그 표는 위험한 표로 건너뛴다.
            # 안쪽 table 노드 아래는 보지 않는다 — 머리 칸의 글자뿐인 중첩 표(§229)는 따로 검사한다.
            if any(True for tag in ('td', 'th', 'tr') for _ in _descendants(cell, tag, ('table',))):
                raise _Limit
            while (r, col) in occupied:
                col += 1
            rs = _span(cell, 'rowspan', MAX_ROWS) or (end - r)
            cs = _span(cell, 'colspan', MAX_COLUMNS)
            if r + rs > end or col + cs > MAX_COLUMNS:
                raise _Limit
            width = max(width, col + cs)
            if len(rows) * width > MAX_CELLS:
                raise _Limit
            value = _text(cell)
            for rr in range(r, r + rs):
                for cc in range(col, col + cs):
                    if (rr, cc) in occupied:
                        # 충돌을 옆으로 밀면 다른 벤치마크의 숫자로 둔갑한다.
                        raise _Limit
                    occupied[rr, cc] = value
            col += cs
    return ([[occupied.get((r, c), '') for c in range(width)] for r in range(len(rows))],
            rows, originals)


def _header_rows(grid: list[list[str]], rows: list[tuple[_Node, str, int]],
                 originals: list[list[_Node]]) -> int:
    """thead와 열 th를 우선하고, 나머지는 첫 수치·결측 본문 전까지 머리로 본다.

    ltx_th_row는 본문 방법명에도 붙으므로 단독 근거로 쓰지 않는다.
    숫자만인 연도 머리는 thead/scope=col/ltx_th_column 또는 행 전체 th가 있어야
    보존된다. ltx_border_t/b가 있는 전폭 단일 셀은 머리 이후의 본문 그룹명으로
    본다(구분선이 없어도 전폭 그룹명은 동일). border만으로는 본문에도 붙어 오판한다.
    구조 없는 숫자 머리, 결측 표식 없는 전부 문자 본문, 머리 중 전폭 부제,
    잘못 지정된 thead는 오판할 수 있다. 본문 중 반복 머리는 재분류하지 않는다.
    """
    count = 0
    for r, (_, section, _) in enumerate(rows):
        cells = originals[r]
        texts = [_text(c) for c in cells]
        explicit = bool(cells) and all(
            c.tag == 'th' and 'ltx_th_row' not in c.classes and c.attrs.get('scope') != 'row'
            or 'ltx_th_column' in c.classes or c.attrs.get('scope') in {'col', 'colgroup'}
            for c in cells)
        if section == 'thead' or explicit:
            count += 1
            continue
        if r and len(cells) == 1 and int(cells[0].attrs.get('colspan', '1')) == len(grid[r]):
            break
        if any(_numeric_cell(t) or t.lower() in _MISSING for t in texts):
            break
        count += 1
    return count


def _safe_header_tables(table: _Node) -> bool:
    """LaTeXML 머리글의 한 열 문자 표만 펼쳐 수치가 있는 중첩 표와 구분한다.

    2026-10-07 로컬 3DThinkVLA의 2행×1열 머리글에서 재현했다. 입력·노드·깊이
    상한은 올리지 않으며 최대 4행·1열·수치 없음·추가 중첩 없음으로 제한한다.
    """
    allowed: set[int] = set()
    grid, rows, originals = _grid(table)
    headers = _header_rows(grid, rows, originals)
    for row_index, row in enumerate(originals[:headers]):
        for cell in row:
            if not (rows[row_index][1] == 'thead' or 'ltx_td' in cell.classes
                    or 'ltx_th_column' in cell.classes or cell.attrs.get('scope') == 'col'):
                continue
            for inner in _descendants(cell, 'table', ('table',)):
                if ('ltx_tabular' not in inner.classes or not inner.closed
                        or next(_descendants(inner, 'table'), None) is not None):
                    return False
                inner_grid, _, _ = _grid(inner)
                if not inner_grid or len(inner_grid) > 4 or any(
                        len(line) != 1 or cell_number(line[0]) is not None for line in inner_grid):
                    return False
                allowed.add(id(inner))
    return all(id(inner) in allowed for inner in _descendants(table, 'table'))


def parse_tables(html: str) -> list[dict]:
    """figure.ltx_table 내부 표만 반환해 일반 레이아웃 표와 구별한다.

    UTF-8 5MiB, 표 128개, 표당 펼친 직사각형 1만 셀, 전체 10만 셀을 넘으면
    부분 성공으로 오인하지 않도록 빈 목록을 반환한다. 노드·깊이·행·열도 제한한다.
    잘못된 span, 겹친 셀, 행 그룹 밖 rowspan, 미완성/수치 중첩 table 역시 빈 목록이다.
    span/div의 LaTeXML 표 역할도 같은 상한·격자 규칙으로 읽는다. 문자 머리글 중첩만 제한적으로 허용한다.
    한 figure의 여러 독립 table은 같은 figure id로 각각 반환한다. 없는 id는
    반환 순서 T1부터 부여한다. 머리 판정의 한계는 _header_rows에 명시한다.
    """
    if len(html) > MAX_INPUT_BYTES or len(html.encode('utf-8', errors='replace')) > MAX_INPUT_BYTES:
        return []
    parser = _Parser()
    try:
        parser.feed(html)
        parser.close()
        result = []
        total = 0
        for figure in _descendants(parser.root, 'figure'):
            if 'ltx_table' not in figure.classes:
                continue
            captions = list(_descendants(figure, 'figcaption', ('figure', 'table', 'figcaption')))
            caption = _text(captions[0]) if captions else ''
            for table in _descendants(figure, 'table', ('figure', 'table')):
                # 구조가 위험한 표 **하나**만 건너뛴다. 예전엔 페이지 전체를 버렸다 — 2026-10-07 실측 2608.01265 는 표 13개 중
                # S3.T1 하나의 머리 구조 때문에 0개가 됐다. 그 표를 빼도 다른 표의 셀 값·위치는 그대로라 부분 성공 오인이 아니다.
                # 자원 상한(노드·전체 셀·표 수)은 여전히 페이지 전체를 중단한다 — 그건 잘린 결과일 수 있다.
                try:
                    if not table.closed or not _safe_header_tables(table):
                        continue
                    grid, rows, originals = _grid(table)
                except _Limit:
                    continue
                if not grid or not grid[0]:
                    continue
                total += len(grid) * len(grid[0])
                if total > MAX_TOTAL_CELLS:
                    raise _Limit
                result.append({'id': figure.attrs.get('id') or f'T{len(result) + 1}',
                               'caption': caption, 'grid': grid,
                               'header_rows': _header_rows(grid, rows, originals)})
        return result
    except _Limit:
        return []


def column_path(table: dict, col: int) -> list[str]:
    """병합 머리의 반복은 줄이되 서로 떨어진 같은 이름은 보존한다."""
    grid = table['grid']
    if not grid or not 0 <= col < len(grid[0]):
        return []
    path = []
    for row in grid[:table['header_rows']]:
        text = row[col]
        if text and (not path or path[-1] != text):
            path.append(text)
    return path


def row_label(table: dict, row: int) -> str:
    """첫 수치/결측 전의 왼쪽 문자 셀을 이어 데이터셋·백본·방법을 함께 보존한다.

    숫자가 포함된 이름·인용은 수치 셀과 다르므로 숫자로 시작할 때만 멈춘다.
    colspan 복제로 인접한 같은 라벨은 하나로 줄이고 빈 셀은 건너뛴다.
    숫자로 시작하는 방법명은 별도 의미 지식 없이 구별할 수 없다.
    """
    if not 0 <= row < len(table['grid']):
        return ''
    labels = []
    for text in table['grid'][row]:
        if _numeric_cell(text) or text.lower() in _MISSING:
            break
        if text and (not labels or labels[-1] != text):
            labels.append(text)
    return ' '.join(labels)


def find_cells(table: dict, row_pred: Callable[[str], bool],
               col_pred: Callable[[list[str]], bool]) -> list[dict]:
    """행·열 조건을 동시에 만족한 본문 교차 셀만 반환해 인접 수치 혼동을 막는다."""
    grid = table['grid']
    if not grid:
        return []
    columns = [(c, column_path(table, c)) for c in range(len(grid[0]))]
    columns = [(c, path) for c, path in columns if col_pred(path)]
    found = []
    for r in range(table['header_rows'], len(grid)):
        label = row_label(table, r)
        if row_pred(label):
            for c, path in columns:
                text = grid[r][c]
                value = cell_number(text)
                if value is not None:
                    found.append({'row': r, 'col': c, 'row_label': label,
                                  'column_path': path.copy(), 'text': text, 'value': value})
    return found
