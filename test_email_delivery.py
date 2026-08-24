"""email_delivery.py 단위 테스트 — 네트워크 없이 돈다.

send_digest_email()이 실제로 SMTP를 타지 않는다는 것 자체가 이 테스트의
핵심이다 — SMTP 정보가 없는 지금 상태에서 이 함수가 조용히 성공한 척하면
"메일이 왜 안 왔지"를 아무도 알아챌 수 없다."""

import pytest

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


def test_send_digest_email_fails_loudly_without_smtp_config():
    """SMTP 정보가 아직 없다 — 조용히 성공한 척하지 않고 명확한 이유와
    함께 실패해야 한다(이 프로젝트의 "조용히 넘어가지 않는다" 원칙)."""
    with pytest.raises(NotImplementedError, match="SMTP"):
        send_digest_email("본문", "제목", ["a@x.com"])
