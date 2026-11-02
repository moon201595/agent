"""⑨ 참고 자료 — 파일 검증과 수신 범위를 분리해 자료 실패가 아침 메일을 막지 않게 한다."""
from __future__ import annotations

from datetime import date, datetime, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
import tempfile
import unicodedata

import link_policy
import storage

_LOG = logging.getLogger(__name__)
_TEXT_FIELDS = ('item_id', 'profile_id', 'task_id', 'topic', 'name', 'what', 'helps',
                'status', 'added_at', 'checked_at', 'source')
_LIMITS = {'name': 120, 'topic': 40, 'what': 700, 'helps': 450}


def default_base() -> Path:
    """테스트의 데이터 격리를 그대로 따라 운영 자료를 건드리지 않게 한다."""
    return storage.DATA_DIR / 'research_assets'


def _clean_text(value: object, *, empty: bool = False) -> bool:
    """줄 경계와 숨은 제어문자가 저장 평문의 구조를 바꾸지 못하게 한다."""
    return (isinstance(value, str) and (empty or bool(value.strip()))
            and not any(unicodedata.category(c) in {'Cc', 'Cf', 'Zl', 'Zp'} for c in value))


def validate_item(item: dict) -> list[str]:
    """메일에 나갈 필드만 검사해 검토 원문의 길이가 자료 수집을 막지 않게 한다."""
    if not isinstance(item, dict):
        return ['item: 객체가 아님']
    errors = []
    for field in _TEXT_FIELDS:
        value = item.get(field)
        if not _clean_text(value):
            errors.append(f'{field}: 필수 문자열 또는 제어문자 오류')
        elif field in _LIMITS and len(value) > _LIMITS[field]:
            errors.append(f'{field}: 길이 초과')
    if item.get('status') not in ('ready', 'pending_review', 'rejected'):
        errors.append('status: 허용값 아님')
    if item.get('source') not in ('manual', 'collector'):
        errors.append('source: 허용값 아님')
    for field in ('added_at', 'checked_at'):
        try:
            value = item.get(field)
            if field == 'added_at':
                datetime.fromisoformat(value)
            elif date.fromisoformat(value).isoformat() != value:
                raise ValueError
        except (ValueError, TypeError):
            errors.append(f'{field}: 날짜 형식 오류')
    facts = item.get('facts')
    if (not isinstance(facts, list) or len(facts) > 6
            or any(not _clean_text(f) or len(f) > 220 for f in facts)):
        errors.append('facts: 목록 또는 길이 오류')
    refs = item.get('refs')
    if not isinstance(refs, list) or not 1 <= len(refs) <= 4:
        errors.append('refs: 1~4개 필요')
    else:
        for ref in refs:
            if (not isinstance(ref, dict) or not _clean_text(ref.get('label'))
                    or not _clean_text(ref.get('note'), empty=True)
                    or not _clean_text(ref.get('url')) or not link_policy.safe_link(ref['url'])):
                errors.append('refs: 문자열 또는 허용 HTTPS 링크 오류')
    if 'review' in item and not isinstance(item['review'], dict):
        errors.append('review: 객체가 아님')
    return errors


def load_config(base: Path) -> dict | None:
    """설정이 불완전하면 절을 생략해 실수로 팀 전체에 자료가 나가지 않게 한다."""
    try:
        cfg = json.loads((base / 'config.json').read_text(encoding='utf-8'))
        if not isinstance(cfg, dict) or cfg.get('enabled') is not True:
            return None
        if cfg.get('audience') not in ('operator', 'all'):
            return None
        days, profiles, limit = cfg.get('weekdays'), cfg.get('profiles'), cfg.get('max_items')
        if (not isinstance(days, list) or any(type(d) is not int or not 0 <= d <= 6 for d in days)
                or not isinstance(profiles, list) or any(not _clean_text(p) for p in profiles)
                or type(limit) is not int or not 1 <= limit <= 2):
            return None
        return cfg
    except Exception:
        return None


def load_items(base: Path) -> list[dict]:
    """항목 내용과 주소를 로그에 쓰지 않아 잘못된 자료도 비밀을 노출하지 않게 한다."""
    try:
        rows = json.loads((base / 'items.json').read_text(encoding='utf-8'))
        if not isinstance(rows, list):
            raise ValueError
    except FileNotFoundError:
        return []
    except Exception:
        _LOG.warning('참고 자료 목록 읽기 실패')
        return []
    kept, seen = [], set()
    for row in rows:
        if validate_item(row) or row['item_id'] in seen:
            _LOG.warning('참고 자료 항목 건너뜀: 검증 실패 또는 중복 키')
            continue
        kept.append(row)
        seen.add(row['item_id'])
    return kept


def upsert_items(items: list[dict], base: Path) -> list[str]:
    """유효 항목만 원자적으로 교체해 중간에 끊긴 JSON 이 메일로 읽히지 않게 한다."""
    errors, valid = [], []
    for index, item in enumerate(items):
        failures = validate_item(item)
        if failures:
            errors.extend(f'항목 {index + 1}: {failure}' for failure in failures)
        else:
            valid.append(item)
    if not valid:
        return errors
    temp = None
    try:
        base.mkdir(parents=True, exist_ok=True)
        # 수집기와 수동 갱신이 겹쳐도 서로의 항목을 잃지 않도록 읽기부터 교체까지 잠근다.
        import fcntl
        with (base / '.items.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = base / 'items.json'
            if path.exists():
                rows = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(rows, list):
                    raise ValueError
            merged = {row['item_id']: row for row in load_items(base)}
            merged.update({row['item_id']: row for row in valid})
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=base, delete=False) as out:
                temp = Path(out.name)
                json.dump(list(merged.values()), out, ensure_ascii=False, indent=2)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp, path)
    except Exception:
        errors.append('참고 자료 저장 실패')
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
    return errors


def pick_for_mail(profile_id: str, day: date, base: Path) -> list[dict]:
    """대상별 소개 이력을 나눠 운영자 시범이 팀의 첫 소개를 소진하지 않게 한다."""
    try:
        cfg = load_config(base)
        if not cfg or day.weekday() not in cfg['weekdays'] or profile_id not in cfg['profiles']:
            return []
        sent = set()
        path = base / 'sent.jsonl'
        if path.exists():
            for line in path.read_text(encoding='utf-8').splitlines():
                # 장부가 깨지면 절을 닫는다 — 이미 소개한 자료를 다시 싣는 것보다 하루 비는 쪽이 낫다(사용자 원칙: 반복하지 않는다).
                row = json.loads(line)
                if row['profile_id'] == profile_id and row['audience'] == cfg['audience']:
                    sent.add(row['item_id'])
        rows = [row for row in load_items(base) if row['profile_id'] == profile_id
                and row['status'] == 'ready' and row['item_id'] not in sent]
        rows.sort(key=lambda row: (row['added_at'], row['item_id']))
        return [{k: v for k, v in row.items() if k != 'review'} for row in rows[:cfg['max_items']]]
    except Exception as error:
        # logging 설정이 없는 아침 실행에서도 보이게 표준출력에 남긴다(로그 파일이 stdout 을 받는다). 내용·주소는 싣지 않는다.
        print(f"  [참고 자료] 선정 실패({type(error).__name__}) — 절 생략")
        _LOG.warning('참고 자료 선정 실패: 절 생략')
        return []


def record_sent(profile_id: str, items: list[dict], audience: str, day: date, base: Path) -> None:
    """SMTP 가 수락한 절만 기록해 실패한 날의 자료가 다음 소개 기회를 갖게 한다."""
    if audience not in ('operator', 'all'):
        raise ValueError('참고 자료 대상 오류')
    base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat()
    import fcntl
    with (base / 'sent.jsonl').open('a', encoding='utf-8') as out:
        fcntl.flock(out, fcntl.LOCK_EX)
        for item in items:
            out.write(json.dumps(dict(profile_id=profile_id, item_id=item['item_id'], audience=audience,
                                      day=day.isoformat(), sent_at=stamp), ensure_ascii=False) + '\n')
        out.flush()
        os.fsync(out.fileno())


def common_recipients(db_path: Path) -> set[str]:
    """알림과 같은 daily 전체의 교집합만 허용해 팀원을 운영자로 오인하지 않게 한다."""
    # `with sqlite3.connect()` 는 연결을 닫지 않는다 — 수신자 루프 근처라 닫기까지 한다(2026-11-02 Errno 24 조사).
    from contextlib import closing
    with closing(sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True)) as con:
        con.execute('PRAGMA query_only=ON')
        rows = con.execute("SELECT profile_id FROM profiles WHERE COALESCE(schedule_frequency, 'daily')='daily'")
        per_profile = [{r[0] for r in con.execute(
            'SELECT email FROM profile_recipients WHERE profile_id=? AND active=1', (pid,))}
            for (pid,) in rows.fetchall()]
    return set.intersection(*per_profile) if per_profile else set()
