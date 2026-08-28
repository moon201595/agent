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
