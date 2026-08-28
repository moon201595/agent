"""email_delivery.py 단위 테스트 — 네트워크 없이 돈다.

send_digest_email()은 engine.ENV(=.env)에서 계정을 읽으므로, 테스트는 그
딕셔너리 자체를 monkeypatch해서 실제 .env 상태(있든 없든)와 무관하게
"설정 없음"/"설정 있음" 두 경로를 각각 확정적으로 재현한다. smtplib.SMTP도
가짜로 바꿔서 실제 네트워크 연결은 절대 안 나간다."""

import smtplib

import pytest

import email_delivery
import summarize_engine as engine
from email_delivery import build_message, send_digest_email


def test_build_message_sets_headers_and_body():
    msg = build_message("본문 내용", "제목", "from@example.com", ["a@x.com", "b@x.com"])
    assert msg["Subject"] == "제목"
    assert msg["From"] == "from@example.com"
    assert msg["To"] == "a@x.com, b@x.com"
    assert msg.get_content().strip() == "본문 내용"


def test_build_message_rejects_empty_recipients():
    with pytest.raises(ValueError, match="수신자"):
        build_message("본문", "제목", "from@example.com", [])


def test_send_digest_email_fails_loudly_without_smtp_config(monkeypatch):
    """SMTP 계정 정보가 .env에 없으면 조용히 성공한 척하지 않고 명확한
    이유와 함께 실패해야 한다(이 프로젝트의 "조용히 넘어가지 않는다" 원칙)."""
    monkeypatch.setattr(engine, "ENV", {})
    with pytest.raises(RuntimeError, match="SMTP_USER"):
        send_digest_email("본문", "제목", ["a@x.com"])


def test_send_digest_email_sends_via_smtp_when_configured(monkeypatch):
    """설정이 있으면 실제로 smtplib.SMTP를 올바른 인자로 부른다 — 진짜
    네트워크는 절대 안 나가게 SMTP 클래스 자체를 가짜로 바꾼다."""
    monkeypatch.setattr(engine, "ENV", {"SMTP_USER": "me@gmail.com", "SMTP_PASSWORD": "app-password"})

    calls = {}

    class FakeSMTP:
        def __init__(self, host, port):
            calls["host"] = host
            calls["port"] = port

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            calls["starttls"] = True

        def login(self, user, password):
            calls["login"] = (user, password)

        def send_message(self, msg):
            calls["sent_to"] = msg["To"]
            calls["sent_from"] = msg["From"]

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)

    send_digest_email("본문", "제목", ["a@x.com", "b@x.com"])

    assert calls["host"] == email_delivery.SMTP_HOST
    assert calls["port"] == email_delivery.SMTP_PORT
    assert calls["starttls"] is True
    assert calls["login"] == ("me@gmail.com", "app-password")
    assert calls["sent_from"] == "me@gmail.com"
    assert calls["sent_to"] == "a@x.com, b@x.com"


# ---------------------------------------------------------------- M3: multipart/alternative


def test_build_message_stays_single_part_without_html():
    """하위 호환 — digest_html을 안 주면 기존과 똑같은 단일 파트 메일이다."""
    msg = build_message("본문 내용", "제목", "from@example.com", ["a@x.com"])
    assert not msg.is_multipart()
    assert msg.get_content_type() == "text/plain"
    assert msg.get_content().strip() == "본문 내용"


def test_build_message_creates_multipart_alternative_with_html():
    """(d) plain·html 두 파트가 multipart/alternative로 묶인다."""
    msg = build_message("텍스트 본문", "제목", "from@example.com", ["a@x.com"],
                         digest_html="<div>HTML 본문</div>")

    assert msg.is_multipart()
    assert msg.get_content_type() == "multipart/alternative"
    subtypes = [p.get_content_type() for p in msg.iter_parts()]
    assert subtypes == ["text/plain", "text/html"]


def test_multipart_plain_part_keeps_original_text():
    """(d-2) plain 파트에 기존 텍스트 다이제스트가 그대로 들어간다 — HTML을
    못 읽거나 차단하는 환경에서 이게 유일한 내용이 된다."""
    text = "[HARNESS Daily] 2026-08-28\n\n1. [★★★] 어떤 논문\n   [검증 43/43 통과]"
    msg = build_message(text, "제목", "from@example.com", ["a@x.com"],
                         digest_html="<div>무관한 HTML</div>")

    plain = [p for p in msg.iter_parts() if p.get_content_type() == "text/plain"][0]
    assert "[검증 43/43 통과]" in plain.get_content()
    assert "<div>" not in plain.get_content()


def test_multipart_html_part_carries_html():
    msg = build_message("텍스트", "제목", "from@example.com", ["a@x.com"],
                         digest_html="<details open><summary>제목</summary></details>")
    html = [p for p in msg.iter_parts() if p.get_content_type() == "text/html"][0]
    assert "<details open>" in html.get_content()


def test_html_part_comes_last_so_clients_prefer_it():
    """MIME 규약상 multipart/alternative는 뒤에 온 파트가 우선이다 — HTML이
    마지막이어야 HTML 읽는 클라이언트가 HTML을 고른다."""
    msg = build_message("텍스트", "제목", "from@example.com", ["a@x.com"],
                         digest_html="<div>h</div>")
    assert list(msg.iter_parts())[-1].get_content_type() == "text/html"


def test_send_strips_spaces_from_app_password(monkeypatch):
    """Google 앱 비밀번호는 "abcd efgh ijkl mnop"으로 표시돼 그대로 복사하면
    공백이 섞인다(실측: .env 값이 19자·공백 포함) — 로그인 전에 지운다."""
    monkeypatch.setattr(engine, "ENV", {
        "SMTP_USER": "me@gmail.com", "SMTP_PASSWORD": "abcd efgh ijkl mnop",
    })
    seen = {}

    class FakeSMTP:
        def __init__(self, host, port): pass
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def starttls(self): pass
        def login(self, user, password): seen["password"] = password
        def send_message(self, msg): seen["multipart"] = msg.is_multipart()

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    send_digest_email("본문", "제목", ["a@x.com"], digest_html="<div>h</div>")

    assert seen["password"] == "abcdefghijklmnop"  # 공백 제거됨
    assert seen["multipart"] is True               # HTML을 넘기면 multipart로 나간다
