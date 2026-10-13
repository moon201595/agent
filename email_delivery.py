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

import re
import smtplib
import ssl
from email.message import EmailMessage

import summarize_engine as engine

# 개인 Google(Gmail) 계정 기준 — 다른 제공자를 쓰게 되면 이 두 값만
# 바꾸면 된다(계정·비밀번호는 이미 .env에서 읽으므로 코드 안 건드림).
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
# 연결·응답 한 번에 기다리는 상한(초, 2026-10-12). 없으면 네트워크가 멈췄을 때 send 가 끝나지 않아 아침 체인 뒤의 프로필 메일까지 묶인다(규칙 6).
# 소켓 연산마다 걸리는 상한이라 큰 HTML 본문을 보내는 동안 진행 중이면 끊기지 않는다.
SMTP_TIMEOUT_S = 60


# SMTP 오류 문구에는 메일 주소가 그대로 들어온다 — SMTPRecipientsRefused 의 str 은 {주소: (코드, 응답)}, SMTPSenderRefused 는 발신 주소를 싣는다.
# 그 문자열이 발송 상태·로그·운영 화면에 남으므로 발송 경로는 오류를 이 함수로만 적는다(2026-10-12). 응답 코드와 예외 종류는 남겨 원인은 보인다.
# 주소 꼴: 따옴표 local-part("a b"@x) · 일반 local-part(RFC 5322 atext — o'connor 의 ' 포함) @ 도메인 또는 주소 리터럴([192.0.2.1]).
# 2026-10-12 Codex 검토: 처음 정규식은 따옴표형·리터럴을 그대로 두고 o'connor 를 o'<주소> 로 앞부분을 남겼다.
_ADDRESS_RE = re.compile(r"""(?:"[^"\r\n]*"|[\w.!#$%&'*+/=?^`{|}~-]+)@(?:\[[^\]\s]*\]|[\w-]+(?:\.[\w-]+)+)""")


def describe_error(error: BaseException, limit: int = 200) -> str:
    """오류 첫 줄을 메일 주소를 가린 채 `종류: 내용` 으로 돌려준다.
    수신·발신 거절은 예외가 들고 있는 **구조화된 주소**를 먼저 지운다 — 정규식이 모르는 꼴의 주소도 그 자리에서는 빠진다."""
    known: list[str] = []
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        known = [str(a) for a in (error.recipients or {})]
        first = " · ".join(f"<주소> {code} {resp.decode('utf-8', 'replace') if isinstance(resp, bytes) else resp}"
                           for code, resp in (error.recipients or {}).values())
    else:
        if isinstance(error, smtplib.SMTPSenderRefused):
            known = [str(error.sender)]
        first = (str(error).splitlines() or [""])[0]
    first = (first.splitlines() or [""])[0]
    for address in sorted(filter(None, known), key=len, reverse=True):
        first = first.replace(address, "<주소>")
    return f"{type(error).__name__}: {_ADDRESS_RE.sub('<주소>', first)}"[:limit]



def build_message(
    digest_text: str, subject: str, sender: str, recipients: list[str],
    digest_html: str | None = None,
) -> EmailMessage:
    """실제 발송과 메시지 조립을 분리 — 이 함수는 네트워크가 전혀 없어서
    SMTP 정보 없이도 지금 바로 테스트할 수 있다.

    digest_html 을 주면 multipart/alternative 로 만든다(M3, 2026-08-28).
    set_content 로 plain 을 먼저 넣고 add_alternative 로 html 을 붙이는
    순서가 중요하다 — MIME 규약상 **뒤에 온 파트가 우선**이므로 이 순서라야
    HTML 을 읽는 클라이언트는 HTML 을, 못 읽거나 차단하는 환경은 텍스트를
    본다. 안 주면 기존과 완전히 동일한 단일 파트 메일이라 하위 호환이
    깨지지 않는다.
    """
    if not recipients:
        raise ValueError("수신자가 없음 — research_profile.add_recipient로 최소 1명 등록 필요")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(digest_text)
    if digest_html:
        msg.add_alternative(digest_html, subtype="html")
    return msg


def send_digest_email(
    digest_text: str, subject: str, recipients: list[str],
    digest_html: str | None = None,
) -> None:
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
    # 앱 비밀번호 공백 제거 — Google이 "abcd efgh ijkl mnop" 형태로 보여줘서
    # 그대로 복사하면 공백이 섞여 들어온다(실측: .env 값이 19자·공백 포함이었다).
    # 공백은 앱 비밀번호에 유효한 문자가 아니라 표시용 구분자일 뿐이라 지워도
    # 안전하다.
    password = password.replace(" ", "")
    # SMTP 직전 최종 링크 감사(2026-09-30) — 메일에 실린 모든 링크를 한 번 더 본다. 막힌 링크만 글자로 바꾸고 메일은 보낸다(규칙 6).
    # 로그에는 호스트와 이유만 남긴다 — 반응 버튼 URL 에는 수신자별 서명 토큰이 들어 있다.
    import link_policy
    digest_text, digest_html, blocked = link_policy.audit_mail(digest_text, digest_html)
    if blocked:
        try:                                            # 로그가 발송을 막으면 안 된다(Codex 검토 2026-09-30: `https://[oops` 가 여기서 터졌다)
            from urllib.parse import urlparse

            def host(u: str) -> str:
                try:
                    return urlparse(u).hostname or u[:20]
                except ValueError:
                    return u[:20]
            shown = sorted({f"{host(u)}: {why}" for u, why in blocked})
            print(f"[링크 감사] {len(blocked)}건을 링크 없이 보냄 — " + " · ".join(shown[:8]))
        except Exception:  # noqa: BLE001
            print(f"[링크 감사] {len(blocked)}건을 링크 없이 보냄")
    msg = build_message(digest_text, subject, sender, recipients, digest_html)
    # STARTTLS 는 인증서·호스트 이름을 검증하는 기본 문맥으로 연다(2026-10-12). context 를 안 주면 smtplib 이
    # ssl._create_stdlib_context() — 검증 없는 문맥 — 를 써서, 같은 망의 중간자가 앱 비밀번호를 그대로 받을 수 있었다.
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_S) as server:
        server.starttls(context=ssl.create_default_context())
        server.login(sender, password)
        server.send_message(msg)
