"""⑨ 화면 검증에서 시크릿 로딩·재서명·SMTP 경로를 막는다."""
import sys
from pathlib import Path

import pytest

import feedback_links
import mail_reformat
from test_mail_reformat import original


def test_offline_preview_reuses_original_urls_without_secret_or_writes(original, tmp_path, monkeypatch):
    """시크릿 설정을 읽거나 새 토큰을 만들거나 원 회차와 다른 링크를 넣거나 DB를 쓰면 실패한다."""
    db, links, cfg = original
    urls = next(iter(links.values()))
    source = tmp_path / 'original.html'
    source.write_text(''.join(f'<a href="{url}">반응</a>' for url in urls.values()))
    def forbidden(*args, **kwargs):
        raise AssertionError('시크릿·재서명 경로를 호출했다')
    monkeypatch.setattr(feedback_links, 'config', forbidden)
    monkeypatch.setattr(feedback_links, 'make_token', forbidden)
    before = db.read_bytes()
    result = mail_reformat.load_preview(db, 'p', '2026-10-02', source)
    assert result['preview_only'] and result['feedback_restored']
    assert db.read_bytes() == before
    for url in urls.values():
        assert url in result['html']
    source.write_text(source.read_text().replace('more', 'invalid-action'))
    with pytest.raises(ValueError, match='토큰'):
        mail_reformat.load_preview(db, 'p', '2026-10-02', source)


def test_offline_preview_without_original_does_not_invent_buttons(original):
    """원래 링크를 찾을 수 없는데 새 반응 버튼을 만들어 복원 완료로 보고하면 실패한다."""
    result = mail_reformat.load_preview(original[0], 'p', '2026-10-02')
    assert result['feedback_restored'] is False and result['preview_only'] is True
    assert '_feedback_links' not in result['items'][0]


def test_offline_preview_cannot_enter_send_path(monkeypatch):
    """오프라인 미리보기에 --send를 붙여 발송용 인증을 우회하면 실패한다."""
    monkeypatch.setattr(sys, 'argv', ['mail_reformat.py', '--profile', 'p', '--date', '2026-10-02', '--offline-preview', '--send'])
    with pytest.raises(SystemExit) as error:
        mail_reformat.main()
    assert error.value.code == 2


def test_offline_preview_rejects_half_restored_axis_buttons(original, tmp_path):
    """2026-10-12 Codex 검토. 이 테스트가 무엇을 망가뜨리면 실패하는가: 원 HTML 에 연구축 버튼이 하나만 남았는데(예: "더 보고 싶음" 만) 복원 완료로
    보고해 반쪽 버튼을 그리면 실패한다. 연구축 토큰이 논문 토큰 대조를 깨도 실패한다."""
    from datetime import datetime, timezone
    db, links, cfg = original
    when = datetime(2026, 10, 1, 20, tzinfo=timezone.utc)
    axis = feedback_links.axis_links(db, "p", "original-issue", "user@example.com", ["visual anomaly reasoning"], now=when)
    feedback_links.mark_delivered(db, "original-issue", "user@example.com", when=when)
    paper_urls = list(next(iter(links.values())).values())
    axis_urls = axis["visual anomaly reasoning"]
    source = tmp_path / "original.html"
    source.write_text("".join(f'<a href="{u}">x</a>' for u in paper_urls + list(axis_urls.values())))
    assert mail_reformat.load_preview(db, "p", "2026-10-02", source)["feedback_restored"]
    source.write_text("".join(f'<a href="{u}">x</a>' for u in paper_urls + [axis_urls["more"]]))
    with pytest.raises(ValueError, match="연구축"):
        mail_reformat.load_preview(db, "p", "2026-10-02", source)
