"""⑤ 표 주소·LaTeXML 역할·셀 존재는 최소 로컬 발췌와 가짜 호출로 검증한다."""
import json
from pathlib import Path
import pytest
import arxiv_tables as at
import external_evidence as ee
import performance_results as pr
import research_frontier as rf
import frontier_store as fs

TARGET = {"paper_id":"2610.02626", "title":"IG-VLA", "method":"IG-VLA", "benchmark":"LIBERO",
          "metric":"Spatial / SR", "metric_key":"spatialsr", "value":"99.4", "direction":"higher", "source_text":""}

# https://arxiv.org/html/2608.24959, CC BY 4.0; Claude 로컬 HTML 2026-10-07.
GAUSS = '<figure class="ltx_table" id="S4.T1"><figcaption>Table 1: Performance comparison on the LIBERO [16] benchmark. GaussVLA achieves the highest overall average success rate while using substantially fewer parameters than large-scale VLA baselines.</figcaption><table class="ltx_tabular"><tr class="ltx_tr">\n<th class="ltx_td ltx_align_left ltx_th ltx_th_column ltx_th_row ltx_border_t"><span class="ltx_text">Method</span></th>\n<th class="ltx_td ltx_align_left ltx_th ltx_th_column ltx_th_row ltx_border_t"><span class="ltx_text">Venue</span></th>\n<th class="ltx_td ltx_align_center ltx_th ltx_th_column ltx_border_t"><span class="ltx_text">Spatial</span></th>\n<th class="ltx_td ltx_align_center ltx_th ltx_th_column ltx_border_t"><span class="ltx_text">Object</span></th>\n<th class="ltx_td ltx_align_center ltx_th ltx_th_column ltx_border_t"><span class="ltx_text">Goal</span></th>\n<th class="ltx_td ltx_align_center ltx_th ltx_th_column ltx_border_t"><span class="ltx_text">Long</span></th>\n<th class="ltx_td ltx_align_center ltx_th ltx_th_column ltx_border_t"><span class="ltx_text">Average</span></th>\n<th class="ltx_td ltx_align_center ltx_th ltx_th_column ltx_border_t"><span class="ltx_text">Parameters</span></th></tr><tr class="ltx_tr">\n<th class="ltx_td ltx_align_left ltx_th ltx_th_row ltx_border_b ltx_border_t"><span class="ltx_text ltx_font_bold">GaussVLA</span></th>\n<th class="ltx_td ltx_align_left ltx_th ltx_th_row ltx_border_b ltx_border_t"><span class="ltx_text ltx_font_bold">BMVC’26</span></th>\n<td class="ltx_td ltx_align_center ltx_border_b ltx_border_t"><span class="ltx_text ltx_font_bold">100</span></td>\n<td class="ltx_td ltx_align_center ltx_border_b ltx_border_t"><span class="ltx_text ltx_font_bold">95.8</span></td>\n<td class="ltx_td ltx_align_center ltx_border_b ltx_border_t"><span class="ltx_text ltx_font_bold">95.3</span></td>\n<td class="ltx_td ltx_align_center ltx_border_b ltx_border_t"><span class="ltx_text ltx_underline">83.0</span></td>\n<td class="ltx_td ltx_align_center ltx_border_b ltx_border_t"><span class="ltx_text ltx_font_bold">93.5</span></td>\n<td class="ltx_td ltx_align_center ltx_border_b ltx_border_t"><span class="ltx_text ltx_font_bold">1B</span></td></tr></table></figure>'

# https://arxiv.org/html/2606.04436, CC BY 4.0; Claude 로컬 HTML 2026-10-07.
THINK = '<figure class="ltx_table" id="S3.T1"><figcaption>Table 1: LIBERO Benchmark Results. Success rates (%) across 4 evaluation suites are presented.</figcaption><table class="ltx_tabular"><tr class="ltx_tr">\n<th class="ltx_td ltx_align_left ltx_th ltx_th_column ltx_th_row ltx_border_r ltx_border_tt"><span class="ltx_text ltx_font_bold">Method</span></th>\n<th class="ltx_td ltx_align_center ltx_th ltx_th_column ltx_border_tt"><span class="ltx_text ltx_font_bold">LIBERO-Spatial</span></th>\n<th class="ltx_td ltx_align_center ltx_th ltx_th_column ltx_border_tt"><span class="ltx_text ltx_font_bold">LIBERO-Object</span></th>\n<th class="ltx_td ltx_align_center ltx_th ltx_th_column ltx_border_tt"><span class="ltx_text ltx_font_bold">LIBERO-Goal</span></th>\n<th class="ltx_td ltx_align_center ltx_th ltx_th_column ltx_border_r ltx_border_tt"><span class="ltx_text ltx_font_bold">LIBERO-Long</span></th>\n<th class="ltx_td ltx_align_center ltx_th ltx_th_column ltx_border_tt"><span class="ltx_text ltx_font_bold">Average</span></th></tr><tr class="ltx_tr">\n<th class="ltx_td ltx_align_left ltx_th ltx_th_row ltx_border_bb ltx_border_r ltx_border_t"><span class="ltx_text ltx_font_bold">Ours</span></th>\n<td class="ltx_td ltx_align_center ltx_border_bb ltx_border_t"><span class="ltx_text ltx_font_bold">100.0</span></td>\n<td class="ltx_td ltx_align_center ltx_border_bb ltx_border_t"><span class="ltx_text ltx_font_bold">100.0</span></td>\n<td class="ltx_td ltx_align_center ltx_border_bb ltx_border_t"><span class="ltx_text ltx_font_bold">98.8</span></td>\n<td class="ltx_td ltx_align_center ltx_border_bb ltx_border_r ltx_border_t"><span class="ltx_text">95.8</span></td>\n<td class="ltx_td ltx_align_center ltx_border_bb ltx_border_t"><span class="ltx_text ltx_font_bold">98.7</span></td></tr></table></figure>'

# https://arxiv.org/html/2603.08124, arXiv perpetual non-exclusive license; 최소 표 머리와 1행만 발췌; Claude 로컬 HTML 2026-10-07.
SAI = '<figure class="ltx_table" id="S5.T7"><figcaption>Table 7: LIBERO success rates (%) for SaiVLA0 and several VLA models.</figcaption><span class="ltx_tabular"><span class="ltx_tr">\n<span class="ltx_td ltx_align_left ltx_th ltx_th_column ltx_th_row ltx_border_tt">Method</span>\n<span class="ltx_td ltx_align_center ltx_th ltx_th_column ltx_border_tt">Spatial</span>\n<span class="ltx_td ltx_align_center ltx_th ltx_th_column ltx_border_tt">Object</span>\n<span class="ltx_td ltx_align_center ltx_th ltx_th_column ltx_border_tt">Goal</span>\n<span class="ltx_td ltx_align_center ltx_th ltx_th_column ltx_border_tt">Long</span>\n<span class="ltx_td ltx_align_center ltx_th ltx_th_column ltx_border_tt">Mean</span></span><span class="ltx_tr">\n<span class="ltx_td ltx_align_left ltx_th ltx_th_row ltx_border_t">SaiVLA0 (ours)</span>\n<span class="ltx_td ltx_align_center ltx_border_t">99.8</span>\n<span class="ltx_td ltx_align_center ltx_border_t">100.0</span>\n<span class="ltx_td ltx_align_center ltx_border_t">98.2</span>\n<span class="ltx_td ltx_align_center ltx_border_t">97.8</span>\n<span class="ltx_td ltx_align_center ltx_border_t">99.0</span></span></span></figure>'


def proposal(model='GaussVLA', table='Table 1', column='Spatial', value='100', aid='2608.24959') -> dict:
    return {'source_url':f'https://arxiv.org/html/{aid}','model':model,'table':table,
            'column':column,'value':value,'same_conditions':False,'differences':[], 'same_task':True, 'task_note':'같은 하위 과제 성공률'}


@pytest.mark.parametrize('page,comp,value,table_id,column', [
    (GAUSS,proposal(),100.0,'S4.T1',2),
    (THINK,proposal('Ours',column='LIBERO-Spatial',value='100.0',aid='2606.04436'),100.0,'S3.T1',1),
    (SAI,proposal('SaiVLA0 (ours)',table='Table 7',value='99.8',aid='2603.08124'),99.8,'S5.T7',1),
], ids=['GaussVLA','3DThinkVLA','SaiVLA0'])
def test_three_source_excerpts_resolve_exact_spatial_cell(page, comp, value, table_id, column):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: Table 번호·span 표 역할·SR 생략을 못 읽거나 Object/Mean 셀을 집으면 실패한다."""
    got = ee.verify_competitor(comp,TARGET,lambda *a:page,5)
    assert got['status'] == 'verified' and got['value'] == value
    assert got['table_id'] == table_id and got['column'] == column and got['row'] == 1
    assert 'Spatial' in got['locator']


@pytest.mark.parametrize('selector', ['Table 1','Tab. 1','S4.T1'])
def test_table_number_selector_and_wrong_number_never_fall_back(selector):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 표 번호를 전체 캡션과만 비교하거나 잘못된 번호의 다른 표를 대신 집으면 실패한다."""
    assert ee.verify_competitor(proposal(table=selector),TARGET,lambda *a:GAUSS,5)['status'] == 'verified'
    assert ee.verify_competitor(proposal(table='Table 9'),TARGET,lambda *a:GAUSS,5)['status'] == 'not_found'
    assert ee.verify_competitor(proposal(table='S4.T9'),TARGET,lambda *a:GAUSS,5)['status'] == 'not_found'


def test_uninterpretable_selector_needs_unique_cell_and_caption_number_wins():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 알 수 없는 선택자가 표를 모두 버리거나 중복 표를 승인하거나 캡션 번호를 무시하면 실패한다."""
    assert ee.verify_competitor(proposal(table='main results'),TARGET,lambda *a:GAUSS,5)['status'] == 'verified'
    other = GAUSS.replace('S4.T1','S4.T2').replace('Table 1:','Table 2:')
    assert ee.verify_competitor(proposal(table='main results'),TARGET,lambda *a:GAUSS+other,5)['status'] == 'not_found'
    disagreement = GAUSS.replace('Table 1:','Table 8:')
    assert ee.verify_competitor(proposal(table='Table 1'),TARGET,lambda *a:disagreement,5)['status'] == 'not_found'


@pytest.mark.parametrize('target_metric,column', [('Object / SR','Spatial'),('Spatial / SR','Object'),('Mean / SR','Spatial')])
def test_unique_value_records_actual_header_instead_of_agent_semantics(target_metric,column):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 의미를 Python이 판정하거나 에이전트 열 이름을 실제 출처 헤더로 둔갑시키면 실패한다."""
    target={**TARGET,'metric':target_metric,'metric_key':pr.metric_key(target_metric)}
    got=ee.verify_competitor(proposal(column=column),target,lambda *a:GAUSS,5)
    assert got['status'] == 'verified' and got['column_path'] == ['Spatial']


def test_metric_words_do_not_gate_source_cell_existence():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 캡션 지표·변형 이름으로 실제 셀 존재 확인을 거부하면 실패한다."""
    for page in (GAUSS.replace('success rate','score'), GAUSS.replace('>Object<','>Object / AUROC<'),
                 GAUSS.replace('LIBERO [16]', 'LIBERO-Plus [16]')):
        got=ee.verify_competitor(proposal(),TARGET,lambda *a:page,5)
        assert got['status'] == 'verified' and got['column_path'] == ['Spatial']


def test_one_explicit_metric_in_other_header_can_support_omission():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 다른 열의 유일한 SR 근거를 못 쓰거나 Object 하위 과제를 지우면 실패한다."""
    page = GAUSS.replace('success rate','score').replace('>Object<','>Object / SR<')
    got=ee.verify_competitor(proposal(),TARGET,lambda *a:page,5)
    assert got['status'] == 'verified' and got['value'] == 100


NESTED_HEADER = ('<figure class="ltx_table" id="S4.T4"><figcaption>Table 4: Ablation</figcaption>'
 '<table class="ltx_tabular"><thead><tr><th>Method</th><th class="ltx_th_column">'
 '<table class="ltx_tabular"><tr><td>Spatial</td></tr><tr><td>SR(%)</td></tr></table>'
 '</th></tr></thead><tr><td>Ours</td><td>95</td></tr></table></figure>')


def test_text_only_nested_header_does_not_poison_other_tables():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: LaTeXML 문자 머리글을 이유로 정상 주 결과표 전체를 버리거나 두 머리 낱말을 붙이면 실패한다."""
    tables = at.parse_tables(THINK+NESTED_HEADER)
    assert [t['id'] for t in tables] == ['S3.T1','S4.T4']
    assert at.column_path(tables[1],1) == ['Spatial SR(%)']
    assert ee.verify_competitor(proposal('Ours',column='LIBERO-Spatial'),TARGET,lambda *a:THINK+NESTED_HEADER,5)['status'] == 'verified'


@pytest.mark.parametrize('page', [
    NESTED_HEADER.replace('>Spatial<','>95<'),
    NESTED_HEADER.replace('class="ltx_th_column"','class="body"').replace('<thead>','<tbody>').replace('</thead>','</tbody>').replace('<th','<td').replace('</th>','</td>'),
    NESTED_HEADER.replace('<td>Spatial</td>','<td>Spatial</td><td>Object</td>'),
])
def test_nested_numeric_or_multi_column_tables_remain_rejected(page):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 수치 중첩이나 여러 열의 머리표를 느슨하게 펼쳐 다른 셀을 집으면 실패한다."""
    assert at.parse_tables(page) == []


def test_span_tables_obey_original_resource_caps(monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: span으로 만든 표·셀을 자원 상한에서 빼면 실패한다."""
    monkeypatch.setattr(at,'MAX_TABLES',0)
    assert at.parse_tables(SAI) == []
    monkeypatch.setattr(at,'MAX_TABLES',128)
    monkeypatch.setattr(at,'MAX_TOTAL_CELLS',1)
    assert at.parse_tables(SAI) == []


def test_zero_tables_has_distinct_status_and_failure_log(tmp_path,monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 0표를 행 불일치로 뭉개거나 조사 완료로 캐시하거나 상태 로그에서 사유를 잃으면 실패한다."""
    raw=json.dumps({'papers':[{'paper_id':TARGET['paper_id'],'competitors':[proposal()]}]})
    res=ee.check([TARGET],run_agent=lambda *a:raw,fetch=lambda *a:'<p>no tables</p>')
    comp=res[TARGET['paper_id']]['competitors'][0]
    assert comp['status'] == 'table_failed' and comp['reason'] == '출처 표 확보 실패'
    assert res[TARGET['paper_id']]['status'] == 'incomplete'
    for name in ['external_today','external_recent','save_external']:
        monkeypatch.setattr(fs,name,lambda *a, name=name:[] if name=='external_today' else None)
    main={'benchmark':'LIBERO','metric':'Spatial / SR','metric_key':'spatialsr','bench_key':'libero','text':'99.4','direction':'higher'}
    paper={'arxiv_id':TARGET['paper_id'],'_frontier':{'main':[main]}}
    rf._external(tmp_path/'db',[((False,0),paper,main)],'2026-10-07',lambda *a:res,profile_id='p',status_path=tmp_path/'status')
    assert any(json.loads(line)['reason']=='출처 표 확보 실패' for line in (tmp_path/'status').read_text().splitlines())


def test_raw_decode_ignores_trailing_braces_but_keeps_schema():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 뒤쪽 잡글의 중괄호를 JSON에 붙이거나 스키마 밖 키를 승인하면 실패한다."""
    good='prefix ```json {"papers":[]} ``` trailing {not json}'
    assert ee.parse_proposal(good)=={'papers':[]}
    with pytest.raises(Exception):ee.parse_proposal('{"papers":[],"extra":1}')


def test_invalid_json_retries_once_same_prompt_with_remaining_budget():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: JSON 실패를 재시도하지 않거나 2번 넘게 재호출하거나 새 300초 예산을 주면 실패한다."""
    now=[0.0];calls=[]
    def agent(prompt,timeout):
        calls.append((prompt,timeout));now[0]+=70
        if len(calls)==1:return '{"papers":'
        return json.dumps({'papers':[{'paper_id':TARGET['paper_id'],'competitors':[]}]})
    got=ee.check([TARGET],run_agent=agent,clock=lambda:now[0])[TARGET['paper_id']]
    assert got['status']=='done' and got['retried'] is True and got['agent_calls']==2
    assert calls[0][0]==calls[1][0] and [c[1] for c in calls]==[ee.BUDGET_S-ee.VERIFY_RESERVE_S, ee.BUDGET_S-ee.VERIFY_RESERVE_S-70]
    calls.clear();now[0]=0
    def broken(prompt,timeout):calls.append(prompt);return '{'
    got=ee.check([TARGET],run_agent=broken,clock=lambda:now[0])[TARGET['paper_id']]
    assert len(calls)==2 and got['status']=='incomplete' and got['retried'] is True


def test_insufficient_budget_skips_retry_and_tool_errors_are_not_retried():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 검증 예산이 부족한데 재호출하거나 JSON이 아닌 도구 실패를 재시도하면 실패한다."""
    now=[0.0];calls=[]
    def agent(*args):calls.append(args);now[0]=270;return '{'
    got=ee.check([TARGET],run_agent=agent,clock=lambda:now[0])[TARGET['paper_id']]
    assert len(calls)==1 and got['status']=='incomplete' and got['retried'] is False
    assert got['retry_skipped']=='남은 예산 부족'
    calls.clear()
    def fail(*args):calls.append(args);raise ValueError('tool failed')
    got=ee.check([TARGET],run_agent=fail)[TARGET['paper_id']]
    assert len(calls)==1 and got['status']=='incomplete' and got['retried'] is False


def test_class_without_value_is_not_a_parser_exception():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 비신뢰 HTML의 빈 class 속성이 예외로 올라가 파서를 중단시키면 실패한다."""
    assert at.parse_tables('<span class></span>'+SAI)[0]['id']=='S5.T7'


def test_schema_failure_is_not_retried_and_budget_overrun_marks_every_target():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 스키마 위반을 재호출하거나 회차 예산을 넘겼는데 앞 대상을 done으로 남기면 실패한다."""
    calls=[]
    def invalid(*args):calls.append(args);return '{"papers":[],"extra":1}'
    out=ee.check([TARGET],run_agent=invalid)[TARGET['paper_id']]
    assert len(calls)==1 and out['retried'] is False and out['status']=='incomplete'
    now=[0.0]
    targets=[TARGET,{**TARGET,'paper_id':'other'}]
    def over(*args):
        now[0]=301
        return json.dumps({'papers':[{'paper_id':t['paper_id'],'competitors':[]} for t in targets]})
    out=ee.check(targets,run_agent=over,clock=lambda:now[0])
    assert [r['status'] for r in out.values()]==['incomplete','incomplete']


def test_duplicate_spatial_cells_do_not_get_resolved_by_agent_number():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 동일 값·동일 Spatial 헤더가 둘인데 하나를 골라 verified하면 실패한다."""
    page=GAUSS.replace('>Object<','>Spatial<').replace('>95.8<','>100<')
    assert ee.verify_competitor(proposal(),TARGET,lambda *a:page,5)['status']=='not_found'


def test_caption_variant_is_not_overridden_by_shortened_header():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 캡션 변형을 이유로 숫자 존재 확인을 거부하거나 실제 열 이름을 바꾸면 실패한다."""
    page=THINK.replace('LIBERO Benchmark Results','LIBERO-Plus Benchmark Results')
    comp=proposal('Ours',column='LIBERO-Spatial')
    got=ee.verify_competitor(comp,TARGET,lambda *a:page,5)
    assert got['status']=='verified' and got['column_path']==['LIBERO-Spatial']
