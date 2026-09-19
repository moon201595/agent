"""② 선별 — 주간 모델에 줄 동향 자료를 기존 집계에서 읽고 근거별로 묶는다."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import narrative_store
import observation_signals
import trend_report

# 입력 크기만 제한한다. 이 수는 채택 조건이나 주당 변경 상한이 아니다.
MAX_PAPERS = 40
MAX_TEXT = 1200


def collect(db: Path, profile: dict, now: datetime) -> dict:
    """관측 없는 자료는 미측정으로 남기고, 서술의 숫자를 집계로 승격하지 않는다."""
    pid = profile['profile_id']
    start = now - timedelta(days=7)
    records, errors = [], {}

    def add(kind: str, payload: object, text: str = '') -> None:
        records.append({'id': f'T{len(records) + 1}', 'kind': kind,
                        'text': text, 'data': payload})

    # 오래된 설치에서 한 자료원이 없더라도 다른 자료와 피드백은 사용할 수 있다.
    try:
        movement = trend_report.window_movement(db, profile, days=7, end=now)
        if movement:
            add('window_movement', movement, '\n'.join(t[0] for t in movement['keywords'] + movement['terms']))
        current = trend_report.observed_rows(db, profile, start, now)
        previous = trend_report.observed_rows(db, profile, start - timedelta(days=7), start)
        terms = trend_report.emerging_terms(current, profile, previous)
        if terms:
            add('emerging_terms', terms, '\n'.join(t[0] for t in terms))
        if current or previous:
            add('weekly_review', {'report': trend_report.format_report(current, previous, profile),
                                 'comparable': bool(previous),
                                 'window': [start.isoformat(), now.isoformat()]})
        for row in current[-MAX_PAPERS:]:
            text = f"{row['title'] or ''}. {(row['abstract'] or '')[:MAX_TEXT]}"
            add('recent_paper', {'published': row.get('published')}, text)
    except sqlite3.OperationalError as exc:
        errors['collection'] = type(exc).__name__
    try:
        with sqlite3.connect(db) as con:
            row = con.execute('SELECT scan_id FROM scan_runs WHERE profile_id=? AND started_at>=? '
                              'AND started_at<? ORDER BY started_at DESC, rowid DESC LIMIT 1',
                              (pid, start.isoformat(), now.isoformat())).fetchone()
        if row:
            reserve = observation_signals.reserve_terms(db, pid, scan_id=row[0])
            if reserve:
                add('reserve_terms', reserve, '\n'.join(t[0] for t in reserve['terms']))
    except sqlite3.OperationalError as exc:
        errors['reserve'] = type(exc).__name__
    before = (now.astimezone(narrative_store.KST) + timedelta(days=1)).strftime('%Y-%m-%d')
    for kind in narrative_store.KINDS:
        for row in narrative_store.recent(db, pid, kind=kind, days=7, before=before):
            if row['created_at'] > now.isoformat():
                continue
            add('narrative', {'kind': kind, 'date': row['reader_date'],
                              'unverified_interpretation': True}, row['body'][:MAX_TEXT])
    return {'records': records, 'unavailable': errors,
            'scope': '수집된 표본이며 분야 전체 출판량이 아니다. 서술은 해석이며 수치 근거가 아니다. '
                     'comparable=false이면 직전 편수 0을 증가로 해석하지 않는다.'}
