"""④⑤ 합성 표로 이름·지표·표 참조의 보수적 경계를 고정한다."""
import pytest

import arxiv_tables as at
import performance_results as pr
import research_frontier as rf


def figure(body: str, caption: str = "Table 8: Scores on Toy-bench.") -> str:
    return f'<figure class="ltx_table" id="T8"><figcaption>{caption}</figcaption><table>{body}</table></figure>'


def stacked(*lines: str) -> str:
    return '<span class="ltx_tabular">' + ''.join(
        f'<span class="ltx_tr"><span class="ltx_td">{line}</span></span>' for line in lines) + '</span>'


def test_direct_title_acronym_only():
    """무엇을 망가뜨리면 실패하는가: 직접 제안·제목 일치·유일 약어·머리글자 확인을 없애는 것."""
    title = 'Synthetic Cycle Planner: A Study'
    assert pr.method_name(title, 'We introduce Synthetic Cycle Planner (SCP), a system.') == 'SCP'
    for opening in ('Others introduce Synthetic Cycle Planner (SCP).',
                    'We introduce Synthetic Cycle Planner (XYZ).',
                    'We introduce Another Cycle Planner (ACP).',
                    'We introduce Synthetic Cycle Planner (scp).',
                    'We introduce Synthetic Cycle Planner (SCP). We propose Synthetic Cycle Planner (XYZ).'):
        assert pr.method_name(title, opening) == 'Synthetic Cycle Planner'
    html = figure('<tr><th>Method</th><th>accuracy</th></tr><tr><td>SCP</td><td>43.2</td></tr>')
    method = pr.method_name(title, 'We present Synthetic Cycle Planner (SCP).')
    assert pr.table_results(at.parse_tables(html), method=method, benchmarks=['Toy-bench'])[0]['own']


def test_bench_token_and_case_deduplication():
    """무엇을 망가뜨리면 실패하는가: -bench 후보 보충 또는 대소문자 중복 제거를 빼는 것."""
    benches = pr.benchmark_candidates('Experiments use Toy-bench. A toy-bench is mentioned.',
                                      [{'benchmarks': ['TOY-Bench']}])
    assert benches == ['TOY-Bench']
    assert pr.benchmark_candidates('Experiments use Toy-bench.') == ['Toy-bench']
    assert pr.benchmark_candidates('We sit on a work-bench.') == []
    html = figure('<tr><th>Method</th><th>Toy-bench / accuracy</th></tr><tr><td>Ours</td><td>43.2</td></tr>')
    assert len(pr.table_results(at.parse_tables(html), method='', benchmarks=benches)) == 1


def test_pass_shorthand_needs_matching_definition():
    """무엇을 망가뜨리면 실패하는가: p@N 직접 정의·N 일치 검사를 빼거나 모든 p 열을 읽는 것."""
    html = figure('<tr><th>Method</th><th>Toy-bench / p@2</th></tr><tr><td>Ours</td><td>43.2</td></tr>')
    # 정의의 대상은 마지막 머리칸 자체다. 합쳐진 임의 구절에 약어를 덧씌우지 않는다.
    assert not pr.table_results(at.parse_tables(html), method='', benchmarks=['Toy-bench'], context='p@2 denotes Pass@2.')
    html = figure('<tr><th>Method</th><th>p@2</th><th>avg pk</th></tr><tr><td>Ours</td><td>43.2</td><td>13.4</td></tr>')
    for context in ('', 'p@2 is precision@2.', 'p@2 is the mean Pass@3.', 'p@20 is Pass@20.',
                    'p@2 is Pass@2. Another p@2 denotes precision@2.'):
        assert not pr.table_results(at.parse_tables(html), method='', benchmarks=['Toy-bench'], context=context)
    rows = pr.table_results(at.parse_tables(html), method='', benchmarks=['Toy-bench'], context='p@2 is the mean Pass@2.')
    assert [(r['metric'], r['value'], r['direction'], r['locator']) for r in rows] == [('pass@2 / p@2', 43.2, 'higher', 'T8#0:r1c1')]


def test_pass_outcome_counts_are_not_scores():
    """무엇을 망가뜨리면 실패하는가: Pass@N=성공수/시행수 머리의 과제 개수를 점수로 읽는 것."""
    html = figure('<tr><th>Category</th><th>Pass@2 =0/2</th><th>Pass@2 =1/2</th></tr>'
                  '<tr><td>Ours</td><td>9</td><td>6</td></tr>')
    assert not pr.table_results(at.parse_tables(html), method='', benchmarks=['Toy-bench'])


def test_explicit_table_reference_benchmark_and_conflicts():
    """무엇을 망가뜨리면 실패하는가: 표 번호·인접 평가 대상·충돌 검사를 없애 문서 후보를 모든 표에 배정하는 것."""
    html = figure('<tr><th>Method</th><th>Medal Rate (%)</th></tr><tr><td>Ours</td><td>43.2</td></tr>', 'Table 8: Comparison.')
    ts = at.parse_tables(html)
    context = 'Our main evaluation uses Toy-bench. We report medal rates in Table 8.'
    rows = pr.table_results(ts, method='', benchmarks=['Toy-bench', 'Other-bench'], context=context)
    assert [(r['benchmark'], r['value'], r['direction']) for r in rows] == [('Toy-bench', 43.2, 'higher')]
    for bad in (context.replace('Table 8', 'Table 80'), context.replace('evaluation uses', 'training uses'),
                'Toy-bench is useful. We report medal rates in Table 8.',
                context + ' We report accuracy on Other-bench in Table 8.',
                'Our evaluation uses Toy-bench and Other-bench. We report medal rates in Table 8.'):
        assert not pr.table_results(ts, method='', benchmarks=['Toy-bench', 'Other-bench'], context=bad)


@pytest.mark.parametrize('count', ['(17.5)', '(17%)', '17', '(0)', '(17/20)'])
def test_nested_header_rejects_non_count_numbers(count):
    """무엇을 망가뜨리면 실패하는가: 괄호 양의 정수만 받는 제한을 소수·비율·단독 값까지 넓히는 것."""
    html = figure('<tr><td class="ltx_td">Method</td><td class="ltx_td">'
                  + stacked('Subset', count) + '</td></tr><tr><td>Ours</td><td>43.2</td></tr>')
    assert at.parse_tables(html) == []


def test_nested_header_counts_and_label_tables_preserve_cells():
    """무엇을 망가뜨리면 실패하는가: 표본 수 머리·문자 방법명 중첩 허용을 빼거나 점수 열 중첩까지 허용하는 것."""
    body = ('<tr><td class="ltx_td">Method</td><td class="ltx_td">' + stacked('Medal Rate', '(17)') + '</td></tr>'
            '<tr><td class="ltx_td">' + stacked('Base Model 2.0', 'Backbone-9') + '</td><td>31.4</td></tr>'
            '<tr><td>Ours</td><td>43.2 +11.8</td></tr>')
    ts = at.parse_tables(figure(body))
    assert len(ts) == 1 and ts[0]['header_rows'] == 1
    assert at.column_path(ts[0], 1) == ['Medal Rate (17)']
    rows = pr.table_results(ts, method='', benchmarks=['Toy-bench'])
    assert [(r['model'], r['value']) for r in rows] == [('Base Model 2.0 Backbone-9', 31.4), ('Ours', 43.2)]
    assert not at.parse_tables(figure(body.replace('<td>31.4</td>', '<td>' + stacked('Score', '31.4') + '</td>')))
    assert not at.parse_tables(figure(body.replace(stacked('Base Model 2.0', 'Backbone-9'), stacked('31.4', 'Backbone-9'))))


def test_subgroup_metric_and_same_block_comparison_only():
    """무엇을 망가뜨리면 실패하는가: 미정의 Overall 열을 메달률로 읽거나 다른 구획 값을 대조하는 것."""
    html = figure('<tr><th>Method</th><th>Medal Rate / Small (17)</th><th>Overall Metrics / All (29)</th></tr>'
                  '<tr><td colspan="3">Other setting</td></tr><tr><td>Unrelated</td><td>98.7</td><td>96.5</td></tr>'
                  '<tr><td colspan="3">Matched setting</td></tr><tr><td>Pair average</td><td>31.4</td><td>24.6</td></tr>'
                  '<tr><td>Ours</td><td>43.2 +11.8</td><td>35.7 +11.1</td></tr>')
    rows = pr.table_results(at.parse_tables(html), method='', benchmarks=['Toy-bench'])
    main = rf._main_results([r for r in rows if r['own']], [])[0]
    assert (main['metric'], main['value']) == ('Medal Rate / Small (17)', 43.2)
    assert [(r['model'], r['value']) for r in rf._baselines(rows, main)] == [('Pair average', 31.4)]
    assert not any(r['locator'].endswith('c2') for r in rows)


def test_assessment_does_not_invent_own_rows():
    """무엇을 망가뜨리면 실패하는가: 평가 보고서의 기존 조합 결과를 자기 방법의 결과로 만드는 것."""
    html = figure('<tr><th>Stack</th><th>accuracy</th></tr><tr><td>Model-A Protocol-B</td><td>43.2</td></tr>')
    method = pr.method_name('An Assessment of Combined Systems', 'We evaluate existing systems.')
    rows = pr.table_results(at.parse_tables(html), method=method, benchmarks=['Toy-bench'])
    assert len(rows) == 1 and not rows[0]['own']
    assert rf._main_results([r for r in rows if r['own']], []) == []
