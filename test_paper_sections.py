"""⑥⑨ 상세 표시 — 명칭·조건부 비교·작은 근거만 바꾸고 원 자료를 보존한다."""
import copy
import sqlite3

import pytest

import digest
import mail_document
import paper_observations
import saved_digest
from test_mail_detail import DETAIL_LINES, Regions
from test_mail_ui import Elements


NAMES = ['연구 개요', '방법론', '실험 구성', '주요 결과', '연구 한계', '분석 메모']
MATCHED = ['dataset v1', 'test split', 'success protocol', 'frozen training']
FRONTIER = {'main': [{'benchmark': 'Bench', 'metric': 'Success', 'value': 85.4, 'text': '85.4%',
                     'model': 'Ours', 'locator': 'T1:r2c2'}],
            'external': {'status': 'done', 'competitors': [
                {'status': 'verified', 'same_conditions': True, 'differences': [], 'unverified_differences': 0,
                 'matched_conditions': MATCHED, 'value': 82.0, 'text': '82.0%', 'model': 'Other',
                 'locator': 'Other × Success', 'source_url': 'https://arxiv.org/abs/2402.00001'}]}}


def source_sections():
    return {'overview': ['目的 <unsafe> [S0011]'], 'method': ['모델 : Qwen 2B [S0020]'],
            'setup': ['split : test'], 'results': ['76.3% / 기준 3.7% [S0018]'],
            'author_limits': '단일 시행 [S0020]', 'limits': '일반화 미확인'}


def test_saved_and_new_mail_share_professional_names_and_keep_existing_tables(monkeypatch):
    """한 경로에 옛 제목이 남거나 이름 변경으로 여섯 행/근거/원 수치/본문 escape를 깨면 실패한다."""
    monkeypatch.setattr(digest, 'summary_sections', lambda aid: source_sections())
    old = mail_document.from_lines(DETAIL_LINES, saved_digest._line)
    new = digest._summary_block_html('2609.00001', {'title': 'Paper'}, 'ok')
    for html in [old, new]:
        r = Regions(html)
        assert r.section_titles == NAMES
        assert len(r.sections) == 6
        assert len(Elements(html).by_class('ph-document')) == 1
        footer = Elements(html).by_class('ph-evidence')
        assert len(footer) == 1 and footer[0]['tag'] == 'div'
        assert 'font-size:11.5px' in footer[0]['attrs']['style']
        assert '76.3%' in html and '3.7%' in html and '[S0018]' in html
        assert '<unsafe>' not in html and '&lt;unsafe&gt;' in html
        assert '단일 시행 [S0020]' in ''.join(r.inside + r.outside)
        assert '일반화 미확인' in ''.join(r.inside + r.outside)


def test_professional_names_in_plain_mail_can_be_saved_and_read_back(monkeypatch):
    """새 이름의 평문을 저장 후 다시 읽을 때 절을 합치거나 토글/내용을 잃으면 실패한다."""
    monkeypatch.setattr(digest, 'summary_sections', lambda aid: source_sections())
    monkeypatch.setattr(digest, 'injection_label', lambda aid: '')
    paper = {'arxiv_id': '2609.00001', 'title': 'Paper', 'deep_status': 'ok'}
    entry = digest._paper_entry(1, paper)
    for label in NAMES:
        assert label + ' :' in entry
    text = '연구 동향 브리핑 · 2026-10-02\n■ 오늘의 핵심 논문 1편 (전체 후보 9건 중)\n' + entry
    html = saved_digest.render_html(text, '팀', [paper])
    r = Regions(html)
    assert r.section_titles == NAMES and r.toggles == 1 and r.open_toggles == 0
    assert '76.3% / 기준 3.7% [S0018]' in ''.join(r.cards[0]['body'])


def test_reference_ids_are_small_footer_metadata_not_a_table_row():
    """S번호를 잃거나 다른 대괄호의 값을 근거로 만들거나 큰 표 행/카드로 추가하면 실패한다."""
    html = mail_document.from_lines(['연구 개요 :', '원문 [S0003, S0058] [S0003]',
                                     '방법 상세 :', '모델 [S0108] [P1:R] [fake]'], saved_digest._line)
    r = Regions(html)
    assert r.section_titles == ['연구 개요', '방법론']
    parsed = Elements(html)
    foot = parsed.by_class('ph-evidence')
    assert len(foot) == 1 and foot[0]['tag'] == 'div'
    assert 'font-size:11.5px' in foot[0]['attrs']['style']
    assert html.index('class="ph-evidence"') > html.index('</table>')
    assert '근거 · S0003 · S0058 · S0108' in ''.join(r.outside)
    assert '[S0003, S0058]' in ''.join(r.outside)
    assert '근거 · P1' not in html
    assert 'ph-evidence' not in mail_document.from_lines(['연구 개요 :', '근거 없는 원래 내용'], saved_digest._line)


def test_comparison_is_separate_only_with_confirmed_conditions(monkeypatch):
    """실제 같은 조건 자료를 누락하거나 전체 조건·원 수치/출처를 잃거나 승자를 만들면 실패한다."""
    monkeypatch.setattr(digest, 'summary_sections', lambda aid: source_sections())
    paper = {'arxiv_id': '2609.00001', 'title': 'Paper', '_frontier': copy.deepcopy(FRONTIER)}
    html = digest._summary_block_html(paper['arxiv_id'], paper, 'ok')
    r = Regions(html)
    assert r.section_titles == ['연구 개요', '방법론', '실험 구성', '주요 결과', '성능 비교', '연구 한계', '분석 메모']
    for phrase in ['85.4%', '82.0%', 'Ours', 'Other', *MATCHED]:assert phrase in html
    assert 'https://arxiv.org/abs/2402.00001' in html
    assert '최고' not in html and '우수' not in html and '향상 확인' not in html
    plain = digest._paper_entry(1, paper)
    assert plain.index('주요 결과 :') < plain.index('성능 비교 :') < plain.index('연구 한계 :')
    # 저장 메일에 이미 있는 비교 절도 새 표의 정확한 위치에 그대로 남는다.
    text = '연구 동향 브리핑 · 2026-10-02\n■ 오늘의 핵심 논문 1편 (전체 후보 9건 중)\n' + plain
    old = saved_digest.render_html(text, '팀', [paper])
    assert Regions(old).section_titles == r.section_titles


@pytest.mark.parametrize('change', [
    {'same_conditions': False}, {'differences': ['split 다름']}, {'unverified_differences': 1},
    {'conditions_not_checked': True}, {'matched_conditions': MATCHED[:2]},
    {'matched_conditions': ['같은 문장'] * 4}, {'status': 'not_found'}, {'value': None},
    {'matched_conditions': '조건이 확인되지 않은 문자열'}, {'value': float('nan')}])
def test_incomparable_observations_never_create_a_comparison_section(change, monkeypatch):
    """부분 조건·조건 차이·캐시 미대조·미검증·없는 수치로 별도 비교 행을 만들면 실패한다."""
    monkeypatch.setattr(digest, 'summary_sections', lambda aid: source_sections())
    fr = copy.deepcopy(FRONTIER);fr['external']['competitors'][0].update(change)
    html = digest._summary_block_html('2609.00001', {'_frontier': fr}, 'ok')
    assert Regions(html).section_titles == NAMES
    assert '성능 비교' not in html


def test_ui_keeps_cards_and_uses_same_professional_names_and_small_evidence(monkeypatch):
    """UI만 옛 명칭/질문형으로 남거나 요약 카드·안전한 수식/S번호·작은 근거를 깨면 실패한다."""
    import review_app
    captured=[]
    d = {'title': 'Paper <T>', 'abstract': 'abs', 'authors': '', 'categories': '', 'source': 'arXiv',
         'published': '2026-10-01', 'link': '', 'coverage': None, 'repro': [], 'code_line': '', 'sota_line': '',
         'summary_engine': 'engine', 'summary_md': '### 연구 개요\n- 무엇을 하려 했는가 : 목표 <b> [S0003]\n'
         '### 방법 상세\n- 학습 : $L$ [S0058]\n### 실험 설정\n- split : test\n'
         '### 결과\n- 76.3% [S0108]\n### 논문의 한계점\n- 저자가 밝힌 한계 : 단일 시행\n'
         '- 요약자가 판단한 한계 : 일반화 미확인\n', 'comparisons': []}
    monkeypatch.setattr(review_app.ops_dashboard, 'paper_detail', lambda db,aid: d)
    monkeypatch.setattr(review_app.ops_dashboard, 'paper_catalog', lambda *a,**kw: [])
    monkeypatch.setattr(review_app.ops_dashboard, 'paper_core_hits', lambda *a: [])
    monkeypatch.setattr(review_app.st, 'markdown', lambda value,**kwargs: captured.append(value))
    review_app.render_paper_detail('2609.00001', None)
    html = ''.join(captured)
    for label in NAMES:assert 'rm-ct\'>' + label + '<' in html
    assert html.count("class='rm-card'") == 8  # 요약 6개·기본 정보·원 초록이며 큰 근거 카드를 추가하지 않는다.
    assert '<b>연구 목적</b>' in html and '목표 &lt;b&gt;' in html
    assert 'class=\'rm-math\'' in html and '[S0003]' in html
    assert 'ph-evidence' in html and 'font-size:11.5px' in html
    assert '성능 비교' not in html


def comparison_db(path):
    """운영 DB 없이 저장 관측 읽기/조건부 표시를 검사한다."""
    import json
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE observed_results(reported_in TEXT,own INT,bench_key TEXT,metric_key TEXT,benchmark TEXT,metric TEXT,value REAL,cell_text TEXT,model TEXT,locator TEXT,observed_at TEXT)')
        db.execute('CREATE TABLE frontier_external(paper_id TEXT,checked_on TEXT,bench_key TEXT,metric_key TEXT,result_json TEXT)')
        db.execute('INSERT INTO observed_results VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                   ('p',1,'bench','success','Bench','Success',85.4,'85.4%','Ours','T1:r2c2','2026-10-01T20:00:00+00:00'))
        db.execute('INSERT INTO frontier_external VALUES(?,?,?,?,?)',
                   ('p','2026-10-02','bench','success',json.dumps(FRONTIER['external'])))


def test_stored_comparison_reads_only_current_unambiguous_values(tmp_path, monkeypatch):
    """UI 조회가 DB를 쓰거나 같은 날·유일한 대상 셀/검증된 비교 값·출처를 잃으면 실패한다."""
    path=tmp_path/'comparison.db';comparison_db(path)
    before=path.read_bytes();opened=[];real=sqlite3.connect
    def connect(db,**kwargs):
        con=real(db,**kwargs);opened.append((db,kwargs,con));return con
    monkeypatch.setattr(paper_observations.sqlite3,'connect',connect)
    rows=paper_observations.stored_comparison_rows(path,'p')
    assert rows==[('Bench · Success — Ours 85.4% (T1:r2c2)',''),
                  ('Other 82.0% (Other × Success) — 대조 조건: dataset v1, test split, success protocol, frozen training','https://arxiv.org/abs/2402.00001')]
    assert opened[0][0].endswith('?mode=ro') and opened[0][1]=={'uri': True}
    assert opened[0][2].execute('PRAGMA query_only').fetchone()[0]==1
    assert path.read_bytes()==before


@pytest.mark.parametrize('ambiguous',[False,True])
def test_stored_comparison_omits_stale_or_ambiguous_targets(tmp_path,ambiguous):
    """옛 대상 조건을 현재 결과에 재사용하거나 같은 지표의 여러 모델 중 임의로 골라 비교하면 실패한다."""
    path=tmp_path/'comparison.db';comparison_db(path)
    with sqlite3.connect(path) as db:
        if ambiguous:db.execute('INSERT INTO observed_results SELECT * FROM observed_results')
        else:db.execute("UPDATE observed_results SET observed_at='2026-10-03T20:00:00+00:00'")
    assert paper_observations.stored_comparison_rows(path,'p')==[]
    missing=tmp_path/'missing.db'
    assert paper_observations.stored_comparison_rows(missing,'p')==[] and not missing.exists()


def test_ui_renders_stored_comparison_in_place_without_creating_a_new_layout(tmp_path,monkeypatch):
    """실제 DB→상세 자료→UI 호출에서 비교 값·위치·출처가 빠지거나 별도 페이지/표로 바뀌면 실패한다."""
    import review_app
    import server
    import storage
    path=tmp_path/'comparison.db';comparison_db(path);storage.init_storage(path)
    md=tmp_path/'summary.md'
    md.write_text('### 연구 개요\n- 무엇을 하려 했는가 : 목적 [S0003]\n### 방법 상세\n- 모델 : Qwen\n'
                  '### 실험 설정\n- split : test\n### 결과\n- 85.4% [S0058]\n### 논문의 한계점\n'
                  '- 저자가 밝힌 한계 : 단일 시행\n- 요약자가 판단한 한계 : 일반화 미확인\n')
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO papers(arxiv_id,title,abstract) VALUES(?,?,?)',('p','Paper','원래 초록'))
        db.execute('INSERT INTO summaries(arxiv_id,path) VALUES(?,?)',('p',str(md)))
    monkeypatch.setattr(server,'DB_PATH',path)
    monkeypatch.setattr(review_app.ops_dashboard,'paper_catalog',lambda *a,**kw: [])
    monkeypatch.setattr(review_app.ops_dashboard,'paper_core_hits',lambda *a: [])
    captured=[]
    monkeypatch.setattr(review_app.st,'markdown',lambda value,**kw:captured.append(value))
    review_app.render_paper_detail('p',None)
    html=''.join(captured)
    assert html.index('>주요 결과<') < html.index('>성능 비교<') < html.index('>연구 한계<') < html.index('>분석 메모<')
    for phrase in ['85.4%','82.0%',*MATCHED,'https://arxiv.org/abs/2402.00001']:assert phrase in html
    assert html.count("class='rm-card'")==9
    assert html.index('ph-evidence') > html.index('>분석 메모<')


def test_incompatible_metric_scales_do_not_create_a_comparison(monkeypatch):
    """RMSE처럼 비율 지표가 아닌 서로 다른 척도를 성능 비교 가능으로 표시하면 실패한다."""
    fr=copy.deepcopy(FRONTIER);fr['main'][0]['metric']='RMSE'
    fr['external']['competitors'][0].update(value=.82,text='0.82')
    monkeypatch.setattr(digest,'summary_sections',lambda aid:source_sections())
    assert '성능 비교' not in digest._summary_block_html('p',{'_frontier':fr},'ok')
