"""⑤⑨ 외부 검색과 저자 보고 비교값 계약은 가짜 표·에이전트로 확인한다."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
import digest
import external_evidence as ee
import paper_observations as po
import research_frontier as rf
import frontier_store as fs
import saved_digest
from test_frontier import _r, TARGET, _comp


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 날짜·프로필 메모리와 상태 로그가 다른 테스트를 오염시키면 실패한다."""
    monkeypatch.setattr(rf, '_CALLED_ON', set())
    monkeypatch.setattr(rf, '_RESULTS', {})
    monkeypatch.setattr(rf, '_SIGNALS', {})


def test_agent_batches_five_targets_once_and_excludes_existing_methods():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 대상마다 에이전트를 부르거나 기존 비교 목록·SOTA 탐색 요청을 빼면 실패한다."""
    calls = []
    targets = [{**TARGET, 'paper_id': f'2610.0000{i}', 'existing_methods': ['OpenVLA', 'GE-Act']} for i in range(5)]
    def agent(prompt, timeout):
        calls.append((prompt, timeout))
        return json.dumps({'papers': [{'paper_id': t['paper_id'], 'competitors': []} for t in targets]})
    out = ee.check(targets, run_agent=agent, fetch=lambda *a: pytest.fail('후보 없이 fetch'), clock=lambda:0.0)
    assert len(calls) == 1
    assert 'OpenVLA' in calls[0][0] and 'GE-Act' in calls[0][0]
    assert 'NOT in the already compared' in calls[0][0] and 'SOTA' in calls[0][0]
    assert calls[0][1] == ee.BUDGET_S - ee.VERIFY_RESERVE_S and len(out) == 5
    assert all(r['status'] == 'done' for r in out.values())


def test_second_profile_has_its_own_five_slots_and_repeat_is_blocked(tmp_path, monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 첫 프로필 호출/DB 기록이 둘째 프로필을 막거나 5편 상한·같은 프로필 재호출을 풀면 실패한다."""
    monkeypatch.setattr(fs, 'external_today', lambda *a: [{'paper_id': 'unrelated', 'status': 'done'}])
    monkeypatch.setattr(fs, 'external_recent', lambda *a: None)
    monkeypatch.setattr(fs, 'save_external', lambda *a: None)
    calls = []
    def candidates(prefix):
        out = []
        for i in range(6):
            r = _r(90, model='Ours', own=True)
            p = {'arxiv_id': f'{prefix}{i}', '_frontier': {'main': [r]}}
            out.append(((False, i), p, r))
        return out
    def run(targets):
        calls.append([t['paper_id'] for t in targets])
        return {t['paper_id']: {'status': 'done', 'competitors': []} for t in targets}
    first, second = candidates('A'), candidates('B')
    for profile, cs in [('p1', first), ('p2', second), ('p2', candidates('C'))]:
        rf._external(tmp_path/'db', cs, '2026-10-07', run, profile_id=profile, status_path=tmp_path/'status')
    assert calls == [['A0','A1','A2','A3','A4'], ['B0','B1','B2','B3','B4']]
    assert second[5][1]['_frontier']['external']['status'] == 'not_selected'
    log = [json.loads(line) for line in (tmp_path/'status').read_text().splitlines()]
    assert {'date':'2026-10-07','profile':'p2','paper_id':'B5','stage':'external','reason':'외부 미선정·예산 소진'} in log
    assert {'date':'2026-10-07','profile':'p1','paper_id':'A0','stage':'external','reason':'외부 셀 확인 0: 비교 후보 없음'} in log


def test_baselines_are_same_table_column_sorted_and_never_own_variants():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 자기 변형·다른 표/열을 섞거나 방향별 3개 제한과 모르는 방향의 표 순서를 깨면 실패한다."""
    main = _r(95, model='Ours', own=True, locator='S4.T1#0:r1c1')
    rows = [main, _r(84.7,model='OpenVLA',locator='S4.T1#0:r2c1'),
            _r(96.8,model='π0',locator='S4.T1#0:r3c1'), _r(98.2,model='GE-Act',locator='S4.T1#0:r4c1'),
            _r(80,model='Octo',locator='S4.T1#0:r5c1'), _r(100,model='w/ ours',locator='S4.T1#0:r6c1'),
            _r(200,model='Other',locator='S4.T2#1:r1c1'), _r(300,model='Other',locator='S4.T1#0:r1c2')]
    assert [r['model'] for r in rf._baselines(rows, main)] == ['GE-Act','π0','OpenVLA']
    assert [r['model'] for r in rf._baselines(rows, {**main,'direction':'lower'})] == ['Octo','OpenVLA','π0']
    assert [r['model'] for r in rf._baselines(rows, {**main,'direction':None})] == ['OpenVLA','π0','GE-Act']


def test_zero_only_signals_hide_box_and_positive_or_official_signals_show():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 0/실패만 있는 신호 상자를 띄우거나 공식 저장소·양수 관측을 숨기면 실패한다."""
    zero = {'scholarly':{'citations':0},'hub':{'page':True,'n_models':0},'github':{'error':'Timeout'}}
    assert po.sections({'_signals':zero}) == [] and digest.signals_text({'_signals':zero}) == ''
    assert '외부 관측 신호' not in digest._observation_blocks_html({'_signals':zero})
    for sig in [{'scholarly':{'citations':1}}, {'hub':{'page':True,'n_models':1}},
                {'github_tier':'official','github':{'repo':'o/r','error':'Timeout'}},
                {'github_tier':'author','github':{'repo':'o/r','stars':0}}]:
        assert po.sections({'_signals':sig})[-1][0] == '외부 관측 신호'
    assert zero['github'] == {'error':'Timeout'}


def test_plain_html_saved_revision_have_identical_author_reported_line():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 새 소제목/수치/출처가 평문·HTML·저장 평문 복원 중 하나에서 사라지면 실패한다."""
    main = _r(95,model='Ours',own=True,locator='S4.T1#0:r1c1',bench='LIBERO',metric='Spatial SR')
    main['baselines'] = [_r(98.2,model='GE-Act'), _r(96.8,model='π0'), _r(84.7,model='OpenVLA')]
    p = {'arxiv_id':'2610.00001','title':'Study','link':'https://arxiv.org/abs/2610.00001',
         'deep_status':'abstract_only','abstract_brief':'요지', '_frontier':{'main':[main]}, '_score':{}}
    scan = {'papers':[p], 'total_found':1}
    plain = digest.generate_digest(scan,'팀')
    html = digest.generate_digest_html(scan,'팀')
    restored = saved_digest.render_html(plain,'팀',[{'title':'Study','link':p['link']}])
    line = 'LIBERO · Spatial SR — GE-Act 98.2 · π0 96.8 · OpenVLA 84.7 (S4.T1#0)'
    heading = '비교 대상 · 이 논문 표(저자 보고값)'
    for content in [plain, html, restored]:
        assert line in content and heading in content
    box_heading = '<div style="font-size:13.5px;font-weight:700;color:#0B5F68;">비교 대상 · 이 논문 표(저자 보고값)</div>'
    assert box_heading in html and box_heading in restored


@pytest.mark.parametrize('label', ['DreamVLA [12]†', ' dreamvla (Ours) ', 'DreamVLA⋄'])
def test_row_footnotes_spaces_case_normalize_without_variants(label):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 각주·공백·대소문자로 같은 행을 놓치거나 다른 변형을 숫자로 승인하면 실패한다."""
    md = f'# Bench2Drive\n\n| Method | DS |\n|---|---|\n| {label} | 97.5 |\n| DreamVLA-base | 99 |\n'
    got = ee.verify_competitor(_comp(source_url='https://github.com/o/Bench2Drive',model='DreamVLA',value=97.5,differences=[]),TARGET,lambda *a:md,5)
    assert got['status'] == 'verified' and got['value'] == 97.5


def test_ours_multiple_tables_need_address_and_ambiguous_cells_still_reject():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: Ours 다중 표를 숫자로 고르거나 주소가 있는데 표를 못 좁히거나 중복 셀을 승인하면 실패한다."""
    def table(id, value):
        return f'<figure class="ltx_table" id="{id}"><figcaption>Table {id}. Bench2Drive</figcaption><table><tr><th>Method</th><th>DS</th></tr><tr><td>Ours⋄</td><td>{value}</td></tr></table></figure>'
    page = table('T1',90) + table('T2',95)
    comp = _comp(model='Ours⋄',value=95,differences=[])
    assert ee.verify_competitor(comp,TARGET,lambda *a:page,5)['status'] == 'not_found'
    got = ee.verify_competitor({**comp,'table':'T2'},TARGET,lambda *a:page,5)
    assert got['status'] == 'verified' and got['value'] == 95
    duplicate = page.replace('<th>DS</th>','<th>DS</th><th>DS</th>').replace('<td>95</td>','<td>95</td><td>95</td>')
    assert ee.verify_competitor({**comp,'table':'T2'},TARGET,lambda *a:duplicate,5)['status'] == 'not_found'


def test_row_candidates_and_w_ours_preserve_identity():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 저자 표의 Ours 대체 라벨을 못 받거나 w/ ours와 w/o ours를 합치면 실패한다."""
    md = '# Bench2Drive\n\n| Method | DS |\n|---|---|\n| w/ ours | 95 |\n| w/o ours | 90 |\n'
    comp = _comp(source_url='https://github.com/o/Bench2Drive',model='w/ ours',value=95,differences=[])
    got = ee.verify_competitor(comp,TARGET,lambda *a:md,5)
    assert got['status'] == 'verified' and got['value'] == 95
    md = md.replace('w/ ours', 'Ours')
    got = ee.verify_competitor({**comp,'model':'DeltaWorld','row_candidates':['Ours']},TARGET,lambda *a:md,5)
    assert got['status'] == 'verified' and got['value'] == 95


def test_main_prefers_full_method_over_w_ours_variant():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 먼저 나온 자기 변형이 완전한 자기 방법 행을 밀어내면 실패한다."""
    rows = [_r(90,model='w/ ours',own=True,locator='T#0:r1c1'),_r(95,model='Ours',own=True,locator='T#0:r2c1')]
    assert rf._main_results(rows,[])[0]['model'] == 'Ours'


def test_analyze_failure_reasons_are_json_lines_and_fail_open(tmp_path, monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: HTML 실패·표0·추출0·자기행0·방향미상 사유를 잃거나 로그 실패가 배달을 막으면 실패한다."""
    monkeypatch.setattr(rf,'_paper_row',lambda *a:('We evaluate on Real3D-AD.','Study','','text'))
    monkeypatch.setattr(fs,'record',lambda *a:0)
    monkeypatch.setattr(fs,'compare',lambda *a:{'status':'first','best':None,'n':0})
    cases = [(None,'HTML 확보 실패'),([], '표 0'),([{'id':'T','caption':'none','grid':[['x']],'header_rows':1}], '추출 0'),
             ([{'id':'T','caption':'Real3D-AD','grid':[['Method','AUROC'],['Old','90']],'header_rows':1}], '자기 행 인식 0')]
    path = tmp_path/'status'
    for i,(tables,reason) in enumerate(cases):
        ps = [{'arxiv_id':f'2610.0000{i}','title':'Study'}]
        rf.analyze(tmp_path/'db',ps,tables_of=lambda a:tables,signals_of=lambda a:{},run_external=lambda t:{},profile_id='p',status_path=path)
        row = json.loads(path.read_text().splitlines()[-1])
        assert row['reason'] == reason and row['profile'] == 'p' and row['stage'] == 'extract'
    rf.analyze(tmp_path/'db',[{'arxiv_id':'pdf-test'}],signals_of=lambda a:{},status_path=tmp_path,profile_id='p')


def test_agent_timeout_returns_incomplete_without_waiting_for_worker():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 가짜 도구가 timeout을 무시할 때 호출자가 작업 종료까지 기다리면 실패한다."""
    import threading
    release = threading.Event()
    try:
        out = ee.check([TARGET],budget_s=0.02,run_agent=lambda *a:release.wait(1))
        assert out[TARGET['paper_id']]['status'] == 'incomplete'
        assert out[TARGET['paper_id']]['reason'] == 'TimeoutError'
    finally:
        release.set()


def test_analyze_passes_all_baselines_to_external_prompt_and_never_promotes_unknown_direction(tmp_path, monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 대표 결과 밖 비교 방법을 프롬프트에서 빼거나 방향 미상 자체 결과를 대표 결과로 올리면 실패한다(2026-10-07 Claude 검토: 방향 모르는 열 — 파라미터 수·시간 — 이 카드 대표 결과가 되면 안 된다)."""
    monkeypatch.setattr(rf,'_paper_row',lambda *a:('We evaluate on Real3D-AD.','Study','','text'))
    monkeypatch.setattr(fs,'record',lambda *a:0)
    monkeypatch.setattr(fs,'compare',lambda *a:{'status':'no_direction','best':None,'n':0})
    monkeypatch.setattr(fs,'external_today',lambda *a:[])
    monkeypatch.setattr(fs,'external_recent',lambda *a:None)
    monkeypatch.setattr(fs,'save_external',lambda *a:None)
    import performance_results as pr
    rows = [_r(90,model='Ours',own=True,locator='T#0:r1c1',direction='higher'),
            _r(80,model='Old',locator='T#0:r2c1',direction='higher'),
            _r(70,model='Elsewhere',locator='T#1:r2c1',direction='higher')]
    monkeypatch.setattr(pr,'table_results',lambda *a,**k:rows)
    targets = []
    p = {'arxiv_id':'2610.00001','title':'Study'}
    rf.analyze(tmp_path/'db',[p],tables_of=lambda *a:[{'grid':[['x']]}], signals_of=lambda a:{},
               run_external=lambda ts:targets.extend(ts) or {}, profile_id='p',status_path=tmp_path/'status')
    assert targets[0]['existing_methods'] == ['Old','Elsewhere']
    assert p['_frontier']['main'][0]['baselines'][0]['model'] == 'Old'
    log = [json.loads(line) for line in (tmp_path/'status').read_text().splitlines()]
    assert [r['reason'] for r in log] == ['외부 비교 미완료: 응답 없음']
    for r in rows:
        r['direction'] = None
    q = {'arxiv_id':'2610.00002','title':'Study'}
    rf.analyze(tmp_path/'db',[q],tables_of=lambda *a:[{'grid':[['x']]}], signals_of=lambda a:{},
               run_external=lambda ts:targets.extend(ts) or {}, profile_id='p2',status_path=tmp_path/'status2')
    assert q['_frontier']['main'] == [] and len(targets) == 1
    assert [json.loads(line)['reason'] for line in (tmp_path/'status2').read_text().splitlines()] == ['방향 미상']
    assert rf._better(95,90,None,'AUROC') is None


def test_no_benchmark_and_non_arxiv_get_distinct_failure_reasons(tmp_path, monkeypatch):
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 벤치마크 후보 없음과 비arXiv를 같은 표 실패로 기록하면 실패한다."""
    monkeypatch.setattr(rf,'_paper_row',lambda *a:('No numeric evidence here.','Study','','abstract'))
    monkeypatch.setattr(fs,'record',lambda *a:0)
    rf.analyze(tmp_path/'db',[{'arxiv_id':'2610.00002'}, {'arxiv_id':'pdf-abc'}],
               tables_of=lambda *a:pytest.fail('벤치마크 없이 HTML 조회'),signals_of=lambda a:{},
               run_external=lambda ts:pytest.fail('main 없이 외부 조사'),profile_id='p',status_path=tmp_path/'status')
    rows = [json.loads(line) for line in (tmp_path/'status').read_text().splitlines()]
    assert [r['reason'] for r in rows] == ['벤치마크 후보 없음','비arXiv']


def test_row_normalization_keeps_greek_method_identity():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 각주를 정규화하면서 π0와 μ0를 같은 모델로 합쳐 다른 셀을 승인하면 실패한다."""
    md = '# Bench2Drive\n\n| Method | DS |\n|---|---|\n| μ0 | 95 |\n'
    got = ee.verify_competitor(_comp(source_url='https://github.com/o/Bench2Drive',model='π0',value=95,differences=[]),TARGET,lambda *a:md,5)
    assert got['status'] == 'not_found'
    md += '| π0† | 90 |\n'
    got = ee.verify_competitor(_comp(source_url='https://github.com/o/Bench2Drive',model='π0',value=95,differences=[]),TARGET,lambda *a:md,5)
    assert got['status'] == 'not_found'
    got = ee.verify_competitor(_comp(source_url='https://github.com/o/Bench2Drive',model='π0',value=90,differences=[]),TARGET,lambda *a:md,5)
    assert got['status'] == 'verified' and got['value'] == 90


def test_ours_does_not_select_only_ablation_variant():
    """이 테스트가 무엇을 망가뜨리면 실패하는가: 완전 Ours 행을 묻는데 하나뿐인 w/ ours 변형을 승인하면 실패한다."""
    md = '# Bench2Drive\n\n| Method | DS |\n|---|---|\n| w/ ours | 95 |\n'
    got = ee.verify_competitor(_comp(source_url='https://github.com/o/Bench2Drive',model='Ours',value=95,differences=[]),TARGET,lambda *a:md,5)
    assert got['status'] == 'not_found'


def test_own_size_variants_are_not_shown_as_comparison_targets():
    """2026-10-07 Codex 독립 검토 P2-2: 방법 이름이 Foo 일 때 Foo-S·Foo-B 는 추출에서 own=False 가 될 수 있고, 카드에
    "비교 대상: Foo-B 93 · Foo-S 90 · Other 80" 으로 실렸다. 실패시키는 것: 방법 이름 접두 변형을 비교 대상으로 남기는 것."""
    main = _r(95, own=True, model='Foo-L', locator='T#0:r1c1', direction='higher')
    rows = [main, _r(93, model='Foo-B', locator='T#0:r2c1', direction='higher'),
            _r(90, model='Foo-S', locator='T#0:r3c1', direction='higher'),
            _r(80, model='Other', locator='T#0:r4c1', direction='higher')]
    assert [b['model'] for b in rf._baselines(rows, main, 'Foo')] == ['Other']
    assert [b['model'] for b in rf._baselines(rows, main, '')] == ['Foo-B', 'Foo-S', 'Other']   # 방법 이름을 모르면 예전 그대로
