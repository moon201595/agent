"""email_delivery.py — Digest를 이메일로 보낸다 (설계 문서 §6-2, §9).

2026-08-24 확인: `.env`에 SMTP 관련 키가 전혀 없고, 회사 내부 SMTP 서버가
있는지조차 아직 확인 안 됐다. 그래서 실제 발송 자격증명은 전부 주석으로
남겨두고 함수 흐름만 먼저 만든다("메일 정보는 일단 주석처리 해놓고" 요청
그대로). 이 파일은 지금 아무 메일도 보내지 않는다 — send_digest_email()을
실제로 호출하는 코드가 없고(run_profile_scan.py는 --send를 줘도 이 함수가
NotImplementedError를 내는 것까지만 확인됨), SMTP 정보가 확정되면 아래
설정 3줄만 채우고 주석 처리된 발송 로직을 활성화하면 된다.

메시지 조립(build_message)과 실제 발송(send_digest_email)을 분리했다 —
조립 쪽은 네트워크가 전혀 없어서 SMTP 정보 없이도 지금 바로 테스트할 수
있다(이 프로젝트 전반의 "결정적 계산과 I/O를 분리한다" 원칙과 같다).

외부 유료 메일 서비스는 쓰지 않는다(설계 문서 §6-2) — 내부 SMTP가
확인되면 표준 라이브러리 smtplib로 충분하다.
"""

from __future__ import annotations

import smtplib  # noqa: F401 — 지금은 안 쓰지만(아래 주석 블록 활성화 시 씀) 미리 남겨둔다
from email.message import EmailMessage

# --- 아직 미확정 — 회사 내부 SMTP 서버 유무부터 확인 필요 (2026-08-24) ---
# SMTP_HOST = "mail.example-company.internal"
# SMTP_PORT = 587
# SMTP_USER = "harness-noreply@example-company.co.kr"
# SMTP_PASSWORD = engine.ENV.get("SMTP_PASSWORD")  # .env에 아직 없음 — 추가 필요
# ---------------------------------------------------------------------


def build_message(digest_text: str, subject: str, sender: str, recipients: list[str]) -> EmailMessage:
    """실제 발송과 메시지 조립을 분리 — 이 함수는 네트워크가 전혀 없어서
    SMTP 정보 없이도 지금 바로 테스트할 수 있다."""
    if not recipients:
        raise ValueError("수신자가 없음 — research_profile.add_recipient로 최소 1명 등록 필요")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(digest_text)
    return msg


def send_digest_email(digest_text: str, subject: str, recipients: list[str]) -> None:
    """SMTP 정보가 확정되기 전까지는 호출하면 바로, 명확하게 실패한다 —
    조용히 아무 일도 안 하고 넘어가면 "왜 메일이 안 왔지"를 아무도 알아챌
    방법이 없다(이 프로젝트 전체의 "조용히 넘어가지 않는다" 원칙과 같은
    이유 — verify.py가 애매하면 flag만 하고 넘기지 자동 통과시키지 않는
    것과 같은 결)."""
    raise NotImplementedError(
        "SMTP 설정이 아직 없음 — email_delivery.py 상단의 SMTP_HOST/PORT/USER/"
        "PASSWORD를 채우고 아래 주석 처리된 발송 로직을 활성화할 것. "
        "내부 SMTP 서버 유무 확인이 먼저 필요(설계 문서 §9)."
    )
    # SMTP 정보가 채워지면 위 raise를 지우고 아래를 활성화:
    #
    # msg = build_message(digest_text, subject, SMTP_USER, recipients)
    # with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
    #     server.starttls()
    #     server.login(SMTP_USER, SMTP_PASSWORD)
    #     server.send_message(msg)
