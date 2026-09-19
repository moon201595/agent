"""⑨ 배달 — 다이제스트 HTML 을 수신자에게 보내고 발송 대장에 남긴다(`_deliver`). 2026-09-17 에 `run_profile_scan.py` 에서 갈라냈다(§8-155).

발송 결과 문자열(`DELIVERY_*` 접두사)은 이 모듈이 소유한다 — 종료코드(`run_profile_scan._exit_code`)와 주간 후속은 `delivery_failed`·
`delivery_reached_someone` 으로만 판별한다. 함수 이름·시그니처는 옮기기 전과 같다.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import digest
import research_profile
from time_policy import KST as READER_TZ


# 발송 결과 문자열은 _deliver 가 소유한다. 바깥에서 실패를 판별할 때
# 이 접두사로만 본다 — 문자열이 흩어지면 "실패했는데 성공으로 읽는" 사고가
# 난다(2026-09-07, ⑨ 종료코드 전파).
DELIVERY_FAILED_PREFIX = "발송 실패"
DELIVERY_NO_RECIPIENT = "수신자 없음 — 발송 안 함"
DELIVERY_SENT_PREFIX = "발송 완료"


def delivery_failed(message: str | None) -> bool:
    """발송이 **실패**했는가. 수신자가 없는 것은 설정 상태지 실패가 아니다.
    종료코드는 이 판정을 쓴다 — 매일 울리는 경보는 무시되기 때문이다."""
    return bool(message) and str(message).startswith(DELIVERY_FAILED_PREFIX)


def delivery_reached_someone(message: str | None) -> bool:
    """메일이 **실제로 사람에게 갔는가**. 소비 처리는 이 판정을 쓴다.

    `delivery_failed` 와 나누는 이유(2026-09-08, 외부 검토가 잡았다):
    "수신자 없음"은 고장이 아니라 설정 상태라 **경보를 울리면 안 되지만**,
    메일이 안 간 것은 사실이므로 **소비 처리도 하면 안 된다.** 하나로 묶었더니
    수신자를 안 넣은 프로필에서 논문이 조용히 소비됐다 — 나중에 수신자를
    등록해도 그 논문은 후보에서 이미 빠져 있다.
    """
    return bool(message) and str(message).startswith(DELIVERY_SENT_PREFIX)


def field_label(profile_name: str) -> str:
    """메일 제목 괄호에 넣을 분야 이름 — 프로필 이름에서 ' — ' 앞부분. 설명을 길게 붙인 이름("우리팀 — 자율제조·…")은
    제목이 휴대폰에서 잘리므로 앞 이름만 쓴다."""
    return (profile_name or "").split(" — ")[0].strip() or (profile_name or "")


def mail_subject(profile_name: str, when: datetime | None = None) -> str:
    """제목은 읽는 사람 기준이다(2026-09-11). 2026-09-16 사용자 요청으로 분야를 **항상** 붙인다 — 분야별 프로필(로봇·에이전트·비전)을
    한 사람이 함께 받게 되면서 제목만 보고 어느 분야 메일인지 알아야 한다. 날짜는 받는 사람(KST) 기준(2026-09-14)."""
    day = (when or datetime.now(READER_TZ)).astimezone(READER_TZ).strftime("%Y-%m-%d")
    return f"[연구 동향 브리핑({field_label(profile_name)})] {day}"


def _deliver(db_path: Path, profile_id: str, result: dict, digest_text: str) -> str:
    """다이제스트를 그 프로필의 수신자에게 보낸다. returns 사람이 읽을 상태 한 줄.

    **논문이 0편이어도 보낸다**(M8, 2026-08-28). 매일 오는 메일 자체가
    "파이프라인이 살아 있다"는 증거라서다 — healthchecks.io 같은 외부
    dead-man's switch 를 안 붙인 지금, 이게 그 역할을 대신한다. 메일이 안 온
    날은 "새 논문이 없었다"가 아니라 "무언가 고장났다"로 읽어야 한다
    (docs/TRIAL_CHECKLIST.md 의 (a) 항목이 이 전제 위에 서 있다).

    발송 실패를 예외로 올리지 않는다 — 한 프로필의 SMTP 실패가 나머지
    프로필의 스캔·발송을 막으면 안 된다.
    """
    import email_delivery

    recipients = research_profile.get_recipients(db_path, profile_id)
    if not recipients:
        return DELIVERY_NO_RECIPIENT
    profile = research_profile.get_profile(db_path, profile_id)
    name = profile["name"] if profile else profile_id
    import evidence_state
    failures = []
    sent = 0
    content_keys = {research_profile.paper_key(p) for p in result.get("papers") or []}
    # 반응 버튼(2026-09-15): 이번 메일 회차 하나에 수신자마다 다른 서명 링크를 만든다. 설정(.env 의
    # FEEDBACK_WEBAPP_URL·FEEDBACK_HMAC_SECRET)이 없으면 빈 dict 라 메일은 예전 그대로다.
    import feedback_links
    issue_id = feedback_links.new_issue_id(profile_id)
    # 주간 관리 에이전트가 바꾼 것은 **`weekly_profile_changes` 한 곳**에서만 메일에 실린다(2026-09-20).
    # 그전에는 여기서 `agent_maintenance.pending_report` 를 따로 읽어 붙였는데, 주간 관리가 월요일 체인으로
    # 옮겨 오면서(§8-163) 같은 변경이 `지난 7일 검색 기준 변화` 절과 **한 메일에 두 번** 실리게 됐다.
    # 게다가 이 경로는 `reported_at` 기준이라 월요일 발송이 한 명에게라도 실패하면 **화요일 메일에 다시** 붙었다.
    # 수신자 하나가 거절돼도 다른 수신자의 기준 상태를 함께 전진시키면 안 된다.
    # 기존 SMTP 함수에 한 명씩 넘기므로 부분 거절도 그 수신자의 실패로 드러난다.
    subject = mail_subject(name)
    for recipient in recipients:
        try:
            view = dict(result)
            states = result.get("_evidence_states")
            # 이전 수신 이력을 다음 메일 내용으로 다시 조립하지 않는다.
            view.pop("state_updates", None)
            try:
                links = feedback_links.issue_links(db_path, profile_id, issue_id, recipient, result.get("papers") or [])
            except Exception as error:  # noqa: BLE001 — 버튼을 못 만들어도 메일은 나가야 한다(CLAUDE.md 규칙 6)
                print(f"  [반응] 링크 생성 실패(버튼 없이 발송): {type(error).__name__}")
                links = {}
            if links:
                view["papers"] = [dict(p, _feedback_links=links.get(research_profile.paper_key(p)))
                                  for p in result.get("papers") or []]
            text = digest.generate_digest(view, name)
            digest_html = digest.generate_digest_html(view, name)
            email_delivery.send_digest_email(text, subject, [recipient], digest_html)
            sent += 1
            if links:
                feedback_links.mark_delivered(db_path, issue_id, recipient)
            if states is not None:
                visible = content_keys
                evidence_state.acknowledge(db_path, profile_id, recipient,
                    [i for i in states if i["paper_key"] in visible])
        except Exception as error:
            failures.append(str(error).splitlines()[0][:200])
    # 회차 기록(2026-09-16, 운영 화면용) — 실패한 회차도 남긴다. 기록 실패는 발송 결과를 바꾸지 않는다.
    try:
        import mail_ledger
        mail_ledger.record_issue(db_path, issue_id, profile_id, subject, result.get("papers") or [],
                                 len(recipients), sent)
    except Exception as error:  # noqa: BLE001
        print(f"  [발송 기록] 실패(무시): {type(error).__name__}")
    if failures:
        return f"{DELIVERY_FAILED_PREFIX}: {sent}/{len(recipients)}명 전송 수락 · " + " / ".join(failures)
    return f"{DELIVERY_SENT_PREFIX} → {sent}명"
