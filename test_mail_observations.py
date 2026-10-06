"""⑨ 메일 관측 구역은 실제 근거·누락·조회 범위를 서로 바꾸면 안 된다."""
import sqlite3
from datetime import date

import pytest

import adoption_signals as adoption
import digest
import mail_layout
import paper_observations
import performance_results as performance
import research_frontier
import saved_digest
from test_mail_layout import SAVED, VisibleText


REPORT = ("Setup precedes. Our Router reaches 76.3% success on L4, compared with 3.7% for the baseline. "
          "It also improves search: keeping the agent fixed raises success from 32.7% to 85.4% at L4 and from 28.0% to 84.0% on L5.")


def test_mail_uses_wider_contrasting_canvas_and_compact_padding():
    """UI 시안의1040px·제한된 회색 바탕·작은 여백을 되돌리거나 모바일 칸을 고정하면 실패한다."""
    html = mail_layout.frame(mail_layout.columns(["첫째", "둘째"]))
    # 2026-10-02 UI 시안으로 내부 회색/흰 카드로 바꿨다. 본문 폭·작은 여백 계약은 유지한다.
    assert 'max-width:1040px;background-color:#EEF2F5' in html and 'padding:16px;' in html and 'style="padding:8px;"' in html
    assert '#EEF2F5' in html and 'min-width:760px' in html and 'padding:12px 10px!important' in html
    assert 'width:100%;border-collapse:collapse;' in html and 'max-width:340px' not in html


def test_own_results_without_sota_or_known_benchmark_keep_both_source_sentences():
    """SOTA/벤치마크 문지기·bare success 누락·It 연결/문장번호 오류로 VHop 유형의 원문 결과를 잃으면 실패한다."""
    rows = performance.performance_evidence(REPORT, [], "text")
    assert [(r['benchmark'], r['metric'], r['index']) for r in rows] == [('', 'success', 2), ('', 'success', 3)]
    assert rows[0]['sentence'] == 'Our Router reaches 76.3% success on L4, compared with 3.7% for the baseline.'
    assert rows[1]['sentence'] == 'It also improves search: keeping the agent fixed raises success from 32.7% to 85.4% at L4 and from 28.0% to 84.0% on L5.'
    assert all(r['source'] == 'text' for r in rows)


@pytest.mark.parametrize('text', [
    'Previous methods report 99.1% accuracy; Router addresses their limitations.',
    'Our model uses 10.5% of the training samples and reports accuracy later.',
    'Table 2. Our accuracy is 99.1%.',
    'Our accuracy is 99.1% \\uparrow.',
    'Our method builds an index. It also improves accuracy to 99.1%.',
    'Our accuracy is evaluated next. Existing models report 98.0% accuracy.',
    'Our recall-ceiling analysis (§ 4.4) compares retrievers by R@5.',
])
def test_unstructured_fallback_rejects_other_work_and_non_scores(text):
    """다른 연구·데이터 비율·캡션·깨진 표·근거 없는 대명사를 자신의 성능 결과로 받으면 실패한다."""
    assert performance.performance_evidence(text, [], 'text', 'Router') == []


@pytest.mark.parametrize('fulltext', [True, False])
def test_scan_preserves_actual_source_and_surfaces_non_sota_results(tmp_path, fulltext):
    """실제 analyze에서 SOTA 없는 문장을 버리거나 길이로 원문/초록 출처와 S번호를 바꾸면 실패한다."""
    db = tmp_path / 'papers.db'
    path = tmp_path / 'short.txt'
    path.write_text(REPORT)
    abstract = REPORT + ' Background discussion.' * 220
    with sqlite3.connect(db) as con:
        con.execute('CREATE TABLE papers(arxiv_id TEXT, text_path TEXT, abstract TEXT, title TEXT, published TEXT)')
        con.execute('INSERT INTO papers VALUES(?,?,?,?,?)', ('2609.00001', str(path) if fulltext else '', abstract, 'Routing study', '2026-10-01'))
    papers = [{'arxiv_id': '2609.00001', 'title': 'Routing study'}]
    research_frontier.analyze(db, papers, tables_of=lambda aid: [], run_external=lambda targets: {}, signals_of=lambda aid: {})
    fr = papers[0]['_frontier']
    assert fr['main'] == [] and fr['claims'] == [] and len(fr['sentences']) == 2
    assert [(s['source'], s['index']) for s in fr['sentences']] == ([('text', 2), ('text', 3)] if fulltext else [('abstract', None), ('abstract', None)])
    with sqlite3.connect(db) as con:
        assert con.execute('SELECT COUNT(*) FROM observed_results').fetchone()[0] == 0


def test_frontier_source_reads_use_read_only_uri_and_never_create_database(tmp_path, monkeypatch):
    """원문 읽기가 없는 DB를 만들거나 mode=ro/query_only 없이 연결하면 실패한다."""
    missing = tmp_path / 'missing.db'
    assert research_frontier._paper_row(missing, 'x') == ('', '', '', 'abstract')
    assert not missing.exists()
    db = tmp_path / 'source.db'
    with sqlite3.connect(db) as con:
        con.execute('CREATE TABLE papers(arxiv_id TEXT, text_path TEXT, abstract TEXT, title TEXT, published TEXT)')
        con.execute('INSERT INTO papers VALUES(?,?,?,?,?)', ('x', '', 'Abstract', 'Title', '2026-10-01'))
    before = db.read_bytes()
    opened = []
    real_connect = sqlite3.connect
    def connect(path, **kwargs):
        connection = real_connect(path, **kwargs)
        opened.append((path, kwargs, connection))
        return connection
    monkeypatch.setattr(research_frontier.sqlite3, 'connect', connect)
    assert research_frontier._paper_row(db, 'x') == ('Abstract', 'Title', '2026-10-01', 'abstract')
    assert opened[0][0].endswith('?mode=ro') and opened[0][1] == {'uri': True}
    assert opened[0][2].execute('PRAGMA query_only').fetchone()[0] == 1 and db.read_bytes() == before


SIGNALS = {'scholarly': {'citations': 21, 'influential': 3, 'publication_date': '2026-09-30'},
           'github': {'repo': 'owner/project', 'stars': 1284, 'forks': 73, 'pushed_at': '2026-09-30T20:55:55Z'},
           'github_tier': 'official', 'collected_on': '2026-10-01',
           'hub': {'page': True, 'n_models': 4, 'upvotes': 13, 'models': [
               {'downloads_30d': 27000, 'likes': 180}, {'downloads_30d': 400, 'likes': 6}, {'downloads_30d': None, 'likes': None}]}}


def test_signal_rows_show_stored_fields_and_limit_aggregation_to_queried_models():
    """repo/Fork/push/HF 모델/likes/영향력 인용을 숨기거나 일부 조회합을 전체 모델의 합으로 부르면 실패한다."""
    rows = adoption.signal_rows(SIGNALS, date(2026, 10, 2))
    assert rows == [('학술', '인용 21회 · 영향력 인용 3회 · 공개 2일'),
                    ('GitHub', '공식 · owner/project · ★1,284 · Fork 73 · 최근 push 2026-10-01 KST'),
                    ('Hugging Face', '연결 모델 4개 · 조회 모델 3개 · 30일 다운로드 27,400 (값 확인 2개 합) · Likes 186 (값 확인 2개 합) · Paper 추천 13'),
                    ('관측일', '2026-10-01 UTC')]
    assert not any(word in str(rows) for word in ['우수', '인기 높', 'SOTA'])


def test_observed_zero_is_kept_but_missing_hf_fields_are_not_zero():
    """HF 응답의 빠진 필드를0으로 채우거나 실제0인 인용/포크/모델/추천을 미관측으로 덮으면 실패한다."""
    h = adoption.hub('2609.1', get=lambda *args: {'upvotes': 0})
    assert h['n_models'] is None and h['n_datasets'] is None and h['n_spaces'] is None
    assert adoption.signal_rows({'hub': h}, date(2026, 10, 2)) == [('Hugging Face', 'Paper 추천 0')]
    sig = {'scholarly': {'citations': 0, 'influential': 0}, 'github': {'repo': 'o/r', 'stars': 0, 'forks': 0},
           'github_tier': 'author', 'hub': {'page': True, 'n_models': 0, 'upvotes': 0}}
    assert adoption.signal_rows(sig) == [('학술', '인용 0회 · 영향력 인용 0회'),
                                        ('GitHub', '저자 연관 · o/r · ★0 · Fork 0'), ('Hugging Face', '연결 모델 0개 · Paper 추천 0')]


def test_no_hf_page_and_third_party_repo_stay_absent_and_errors_stay_errors():
    """없는 HF 페이지/제3자 별을 관측으로 넣거나 실패를0으로 덮거나 미래 공개일을 음수로 표시하면 실패한다."""
    sig = {**SIGNALS, 'hub': {'page': False}, 'github_tier': 'third_party', 'scholarly': {'publication_date': '2026-11-01'}}
    assert adoption.signal_rows(sig, date(2026, 10, 2)) == [('관측', '관측값 없음'), ('관측일', '2026-10-01 UTC')]
    assert adoption.signal_rows({'hub': {'error': 'Timeout'}, 'scholarly': {'error': 'Timeout'}}) == [('학술', '조회 실패'), ('Hugging Face', '조회 실패')]
    assert adoption.signal_rows({'hub': {'page': True, 'n_models': 4, 'models_error': 'Timeout'}}) == [('Hugging Face', '연결 모델 4개 · 모델 다운로드·likes 조회 실패')]


def test_cross_paper_observations_preserve_conditions_and_never_name_a_winner():
    """벤치마크/지표만 같은 값으로 최고를 말하거나 미검증 외부 수치/조건/출처를 섞으면 실패한다."""
    main = {'benchmark': 'Bench', 'metric': 'Success', 'text': '85.4%', 'model': 'Ours', 'locator': 'T1:r2c2',
            'compare': {'status': 'above_observed', 'best': {'value': 84.0, 'model': 'Old', 'reported_in': '2401.00001'}}}
    fr = {'main': [main], 'external': {'status': 'done', 'competitors': [
        {'status': 'verified', 'model': 'Other', 'text': '82.0%', 'source_url': 'https://arxiv.org/abs/2402.00001', 'differences': ['split 다름']},
        {'status': 'not_found', 'model': 'Unverified', 'text': '99.9%'}]}}
    sections = paper_observations.sections({'_frontier': fr})
    text = str(sections)
    assert sections[0][0] == '성능 관측 · 논문 내 결과' and sections[1][0] == '논문 간 관측'
    assert '85.4%' in text and '84' in text and '82.0%' in text and '99.9%' not in text
    assert 'split 다름' in text and 'https://arxiv.org/abs/2402.00001' in text and '우열을 판정하지 않음' in text
    assert '최고' not in text and 'SOTA' not in text and 'backbone' in text


def test_reading_point_does_not_promote_partial_conditions_to_full_agreement():
    """외부 결과의 일부 조건 플래그만으로 모든 평가 조건이 같다고 독자에게 말하면 실패한다."""
    main = {'benchmark': 'Bench', 'metric': 'Success', 'text': '85.4%', 'compare': {'status': 'first'}}
    external = {'status': 'done', 'competitors': [{'status': 'verified', 'same_conditions': True, 'differences': [], 'unverified_differences': 0}]}
    point = research_frontier.reading_point({'_frontier': {'main': [main], 'external': external}})
    assert '일부 조건 대조 기록' in point and '전체 평가 조건의 일치는 별도 확인' in point
    assert '같은 조건으로 확인한' not in point


def test_digest_keeps_grounded_results_and_full_signals_above_detail_toggle(monkeypatch):
    """실제 카드에서 원문 문장/외부 신호를 누락·토글 안으로 숨기거나 원문 HTML을 실행하면 실패한다."""
    monkeypatch.setattr(digest, 'summary_sections', lambda aid: {'overview': ['분석 내용'], 'limits': ''})
    paper = {'arxiv_id': '2609.00001', 'title': 'Test', '_signals': SIGNALS,
             '_frontier': {'main': [], 'claims': [], 'sentences': performance.performance_evidence(REPORT.replace('Our Router', 'Our <script>Router</script>'), [], 'text')}}
    html = digest._paper_entry_html(1, paper)
    text = ''.join(VisibleText(html).data)
    for expected in ['성능 관측 · 논문 내 결과', '76.3%', '3.7%', '[S0002]', '32.7%', '85.4%', '[S0003]',
                     '외부 관측 신호', 'owner/project', 'Fork 73', 'Likes 186', '영향력 인용 3회']:
        assert expected in text and html.index(expected) < html.index('<details')
    assert '<script>' not in html and '&lt;script&gt;' in html and '분석 내용' in text
    assert '논문 간 수치 비교에는 쓰지 않음' in text
    plain = digest._paper_entry(1, paper)
    assert '76.3%' in plain and 'owner/project' in plain and '연구 개요' in plain


def test_sota_claims_remain_separate_unverified_author_claims():
    """SOTA 주장만으로 성능 관측이나 비교 결과를 만들어내면 실패한다."""
    paper = {'_sota_claims': [{'sentence': 'Our model is state-of-the-art.', 'benchmarks': []}]}
    rows = paper_observations.sections(paper)
    assert [heading for heading, _ in rows] == ['논문 자체 주장 · 미검증']
    assert '논문 자체 주장 · 미검증' in rows[0][1][0][0]


def test_saved_card_can_show_stored_observations_without_losing_original_body():
    """보관 메일 미리보기에서 추가 원문 근거·저장 신호를 숨기거나 원래 분석/버튼을 누락하면 실패한다."""
    paper = {'title': 'One & Paper', 'link': 'https://arxiv.org/abs/2609.00001', '_signals': SIGNALS,
             '_frontier': {'main': [], 'sentences': performance.performance_evidence(REPORT, [], 'text')},
             '_feedback_links': {'more': 'https://example.com/existing-token'}}
    old_text = SAVED.replace('   읽을 포인트 — 조건', '   읽을 포인트 — 조건\n   외부 신호 옛 축약 관측 없음')
    html = saved_digest.render_html(old_text, '팀', [paper])
    assert html.index('성능 관측') < html.index('<details') and html.index('owner/project') < html.index('<details')
    assert '저장 분석 &lt;unsafe&gt;' in html and 'existing-token' in html
    assert '옛 축약 관측 없음' not in html
