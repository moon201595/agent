"""email_delivery.py — Digest를 이메일로 보낸다 (설계 문서 §6-2, §9).

2026-08-24: 회사(keti.re.kr) 내부 SMTP 서버는 필요 없다는 게 확인됐다 —
수신자만 회사 메일이면 되고, 발신자는 개인 계정 아무거나 상관없다는 결정.
발신자로 개인 Google 계정을 쓰기로 확정 — Gmail의 표준 SMTP 서버는
smtp.gmail.com:587, STARTTLS(구글 공식 문서에 고정으로 명시된 값이라
KETI 쪽 문서 확인이 아예 필요 없었다).

인증 정보(계정·비밀번호)는 `.env`에서 읽는다(summarize_engine.ENV 재사용 —
GOOGLE_API_KEY/GROQ_API_KEY와 같은 자리) — 코드에 직접 안 적는다. 아직
`.env`에 SMTP_USER/SMTP_PASSWORD가 없으면 send_digest_email() 호출 시
바로, 명확하게 실패한다(조용히 넘어가지 않는다는 원칙).

계정 비밀번호를 그대로 쓰면 안 된다 — Google이 스크립트 로그인(smtplib
같은 "보안 수준이 낮은 앱")을 기본 차단하므로, 계정 보안 설정(2단계 인증
켜져 있어야 나타남)에서 별도로 발급하는 "앱 비밀번호"를 SMTP_PASSWORD에
넣어야 한다.

메시지 조립(build_message)과 실제 발송(send_digest_email)을 분리했다 —
조립 쪽은 네트워크가 전혀 없어서 SMTP 정보 없이도 지금 바로 테스트할 수
있다(이 프로젝트 전반의 "결정적 계산과 I/O를 분리한다" 원칙과 같다).

외부 유료 메일 서비스는 쓰지 않는다(설계 문서 §6-2) — 개인 계정 SMTP는
무료라 이 원칙과 안 부딪힌다.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

import summarize_engine as engine

# 개인 Google(Gmail) 계정 기준 — 다른 제공자를 쓰게 되면 이 두 값만
# 바꾸면 된다(계정·비밀번호는 이미 .env에서 읽으므로 코드 안 건드림).
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


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
    """.env에 SMTP_USER/SMTP_PASSWORD가 아직 없으면 바로, 명확하게 실패한다
    — 조용히 아무 일도 안 하고 넘어가면 "왜 메일이 안 왔지"를 아무도
    알아챌 방법이 없다(이 프로젝트 전체의 "조용히 넘어가지 않는다" 원칙과
    같은 이유 — verify.py가 애매하면 flag만 하고 넘기지 자동 통과시키지
    않는 것과 같은 결)."""
    sender = engine.ENV.get("SMTP_USER")
    password = engine.ENV.get("SMTP_PASSWORD")
    if not sender or not password:
        raise RuntimeError(
            ".env에 SMTP_USER/SMTP_PASSWORD가 없음 — Google 계정과 "
            "앱 비밀번호(일반 로그인 비밀번호 아님, 계정 보안 설정에서 별도 발급)를 "
            ".env에 추가할 것(email_delivery.py 모듈 docstring 참고)."
        )
    msg = build_message(digest_text, subject, sender, recipients)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(sender, password)
        server.send_message(msg)
