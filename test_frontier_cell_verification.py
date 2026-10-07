"""⑤⑨ 외부 셀 존재 확인과 에이전트 의미 판단의 경계를 고정한다."""
import html
import json

import pytest
import digest
import saved_digest
import paper_observations as po
import external_evidence as ee
from test_frontier_followup import GAUSS, THINK, SAI, TARGET, proposal
from test_frontier import _r


@pytest.mark.parametrize('page,comp,number,path,table', [
    (GAUSS, proposal(), 100, ['Spatial'], 'Table 1'),
    (THINK, proposal('Ours',column='LIBERO-Spatial'), 100, ['LIBERO-Spatial'], 'Table 1'),
    (SAI, proposal('SaiVLA0 (ours)',table='Table 7',value='99.8',aid='2603.08124'), 99.8, ['Spatial'], 'Table 7'),
])
def test_live_excerpts_verify_number_and_python_read_header(page,comp,number,path,table):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 실제 세 출처 표를 못 읽거나 에이전트 헤더로 출처 헤더를 바꾸면 실패한다."""
    got=ee.verify_competitor(comp,TARGET,lambda *a:page,5)
    assert got['status']=='verified' and got['value']==number
    assert got['column_path']==path and got['table_label']==table
    assert got['caption'].startswith(table) and got['same_task'] is True


def test_false_spatial_claim_exposes_actual_object_cell():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 에이전트의 Spatial 주장을 출처 Object 열 이름 대신 싣거나 숫자를 바꿔 싣으면 실패한다."""
    got=ee.verify_competitor(proposal(value='95.8',column='Spatial'),TARGET,lambda *a:GAUSS,5)
    assert got['status']=='verified' and got['value']==95.8 and got['column_path']==['Object']
    line,_=po.external_line(got)
    assert '출처 표 열 "Object" (Table 1)' in line and '출처 표 열 "Spatial"' not in line


def test_equal_values_need_unique_exact_column_path():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 같은 100이 두 열에 있을 때 첫 열을 임의 선택하거나 잘못된 열 요청을 승인하면 실패한다."""
    for column in ('', 'Spatial', 'Mean'):
        got=ee.verify_competitor(proposal('Ours',column=column),TARGET,lambda *a:THINK,5)
        assert got['status']=='not_found' and got['reason']=='같은 값이 여러 열에 있어 하나로 못 정함'
    got=ee.verify_competitor(proposal('Ours',column=' libero - object '),TARGET,lambda *a:THINK,5)
    assert got['status']=='verified' and got['column_path']==['LIBERO-Object']


@pytest.mark.parametrize('model,value', [('Missing','100'),('GaussVLA','99.999'),('GaussVLA','10000'),('GaussVLA','100.02')])
def test_absent_model_or_fabricated_value_never_substitutes_table_value(model,value):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 없는 모델·숫자를 표 값으로 대체하거나 반올림·무제한 100배로 승인하면 실패한다."""
    got=ee.verify_competitor(proposal(model=model,value=value),TARGET,lambda *a:GAUSS,5)
    assert got['status']=='not_found' and 'value' not in got and 'text' not in got


def test_same_task_schema_prompt_and_false_or_legacy_display_gate():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 같은 과제 판단을 안 요청하거나 false·판독 없는 캐시를 메일에 싣거나 40자 제한을 잃으면 실패한다."""
    prompt=ee._prompt([TARGET])
    assert 'same_task' in prompt and 'SAME subtask and SAME metric' in prompt and 'task_note' in prompt
    comp=proposal()
    data={'papers':[{'paper_id':TARGET['paper_id'],'competitors':[comp]}]}
    assert ee.parse_proposal(json.dumps(data))['papers'][0]['competitors'][0]['same_task'] is True
    from jsonschema import ValidationError
    for change in ({'task_note':'가'*41}, {'same_task':'true'}):
        with pytest.raises(ValidationError):
            ee.parse_proposal(json.dumps({'papers':[{'paper_id':TARGET['paper_id'],'competitors':[{**comp,**change}]}]}))
    got=ee.verify_competitor({**comp,'same_task':False},TARGET,lambda *a:GAUSS,5)
    assert got['status']=='verified' and got['same_task'] is False
    for candidate in (got,{k:v for k,v in got.items() if k!='same_task'}, {**got,'same_task':True,'task_not_checked':True}):
        assert not po.external_visible(candidate)
        sections=po.sections({'_frontier':{'main':[_r(99.4,own=True)],'external':{'competitors':[candidate]}}})
        assert '외부 논문 표:' not in str(sections)


def test_external_provenance_line_plain_html_and_saved_revision_match():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 실제 헤더·표 번호·에이전트 판독·조건 미확인·링크가 세 메일 형식 중 하나에서 사라지면 실패한다."""
    # 출처 논문 제목에 모델 이름이 있으면 원저자 표, 없으면 다른 논문 표의 재인용 표시(2026-10-07 Codex 검토 P2-3).
    titled = '<title>GaussVLA: Geometry-Aware Spatial Reasoning</title>' + GAUSS
    assert ee.verify_competitor(proposal(),TARGET,lambda *a:GAUSS,5)['third_party'] is True
    c=ee.verify_competitor(proposal(),TARGET,lambda *a:titled,5)
    assert c['third_party'] is False
    main=_r(99.4,model='IG-VLA',own=True,bench='LIBERO',metric='Spatial / SR')
    main['compare']={'status':'first','best':None,'n':0}
    p={'arxiv_id':'2610.02626','title':'Study','link':'https://arxiv.org/abs/2610.02626',
       'deep_status':'abstract_only','abstract_brief':'요지','_frontier':{'main':[main],'external':{'competitors':[c]}},'_score':{}}
    scan={'papers':[p],'total_found':1}
    plain=digest.generate_digest(scan,'팀')
    rendered=digest.generate_digest_html(scan,'팀')
    restored=saved_digest.render_html(plain,'팀',[{'title':'Study','link':p['link']}])
    line='외부 논문 표: GaussVLA 100 — 출처 표 열 "Spatial" (Table 1) · 같은 과제 판단: 에이전트 판독 (같은 하위 과제 성공률) · 조건 대조 미확인'
    for content in (plain,rendered,restored):
        assert line in html.unescape(content) and c['source_url'] in content
        assert '표 값 사용' not in content and '최고 성능 후보' not in content


def _page(*tables: str) -> str:
    return '<title>Paper</title>' + ''.join(tables)


def _fig(rows: str, caption: str = 'Table 1: LIBERO results', fid: str = 'S4.T1', extra: str = '') -> str:
    return f'<figure class="ltx_table" id="{fid}"><figcaption>{caption}</figcaption><table>{rows}</table>{extra}</figure>'


def test_review_p1_broken_cell_boundary_never_synthesises_a_number():
    """2026-10-07 Codex 독립 검토 P1: `<td>9<td>1.5</td>` 처럼 닫힘이 빠진 칸이 글자를 이어 붙여 없는 수치 91.5 를 만들고 verified 됐다.
    실패시키는 것: 칸 안의 칸을 허용해 이어 붙인 수치를 승인하는 것. 그 표만 건너뛰므로 다른 정상 표는 계속 읽힌다."""
    broken = _fig('<tr><th>Method</th><th>SR</th></tr><tr><td>Ours</td><td>9<td>1.5</td></tr>')
    got = ee.verify_competitor(proposal('Ours', value='91.5', column='SR'), TARGET, lambda *a: _page(broken), 5)
    assert got['status'] != 'verified'
    good = _fig('<tr><th>Method</th><th>SR</th></tr><tr><td>Ours</td><td>88.0</td></tr>', 'Table 2: LIBERO', 'S4.T2')
    import arxiv_tables
    assert [t['id'] for t in arxiv_tables.parse_tables(_page(broken, good))] == ['S4.T2']


def test_review_p2_tables_in_one_figure_are_distinct_identities():
    """P2-1: 한 figure 의 두 표가 같은 id 라 서로 다른 Ours 행 둘을 한 행으로 보고 90 을 승인했다. 실패시키는 것: 표 순번 없이 행 신원을 정하는 것."""
    two = ('<figure class="ltx_table" id="S4.T1"><figcaption>Table 1: LIBERO</figcaption>'
           '<table><tr><th>Method</th><th>SR</th></tr><tr><td>Ours</td><td>90</td></tr></table>'
           '<table><tr><th>Method</th><th>SR</th></tr><tr><td>Ours</td><td>80</td></tr></table></figure>')
    got = ee.verify_competitor(proposal('Ours', value='90', column='SR'), TARGET, lambda *a: _page(two), 5)
    assert got['status'] == 'not_found' and got['reason'] == '행이 하나로 정해지지 않음'


def test_review_p2_ablation_rows_and_non_ratio_scale_are_refused():
    """P2-3·P2-4: 캡션에 ablation 이 없어도 `w/o`·`without` 행은 경쟁 결과가 아니다. 지연(초) 0.8 을 제시값 80 으로 승인하면 안 된다.
    실패시키는 것: 변형 행 검사 제거, 비율 지표가 아닌데 ×100 척도 허용."""
    abl = _fig('<tr><th>Method</th><th>SR</th></tr><tr><td>Ours w/o memory</td><td>90</td></tr>')
    assert ee.verify_competitor(proposal('Ours w/o memory', value='90', column='SR'), TARGET, lambda *a: _page(abl), 5)['status'] == 'not_found'
    lat = _fig('<tr><th>Method</th><th>Latency (s)</th></tr><tr><td>Fast</td><td>0.8</td></tr>')
    target = {**TARGET, 'metric': 'Latency (s)', 'value': '0.5'}
    assert ee.verify_competitor(proposal('Fast', value='80', column='Latency (s)'), target, lambda *a: _page(lat), 5)['status'] == 'not_found'
    sr = _fig('<tr><th>Method</th><th>Success rate</th></tr><tr><td>Fast</td><td>0.8</td></tr>')
    assert ee.verify_competitor(proposal('Fast', value='80', column='Success rate'), TARGET, lambda *a: _page(sr), 5)['status'] == 'verified'


def test_ours_row_is_shown_by_the_source_method_name():
    """2026-10-07 실측: 출처 표의 "Ours" 행이 메일에 "외부 논문 표: Ours 100.0" 으로 실려 누구인지 알 수 없었다.
    실패시키는 것: 출처 제목의 방법 이름 대신 "Ours" 를 그대로 싣는 것, 이름 있는 행까지 바꾸는 것."""
    titled = '<title>3DThinkVLA: Endowing VLA Models with 3D Priors</title>' + THINK
    got = ee.verify_competitor(proposal('Ours', column=' libero - object '), TARGET, lambda *a: titled, 5)
    if got['status'] == 'verified':
        assert got['display_model'] == '3DThinkVLA' and po.external_line(got)[0].startswith('외부 논문 표: 3DThinkVLA ')
    sai = '<title>SaiVLA-0: Tripartite Architecture</title>' + SAI
    got = ee.verify_competitor(proposal('SaiVLA0 (ours)', table='Table 7', value='99.8', aid='2603.08124'), TARGET, lambda *a: sai, 5)
    assert got['status'] == 'verified' and got['display_model'] == 'SaiVLA-0'
    titled_gauss = '<title>GaussVLA: Geometry-Aware</title>' + GAUSS
    assert ee.verify_competitor(proposal(), TARGET, lambda *a: titled_gauss, 5)['display_model'] == 'GaussVLA'
