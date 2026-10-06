"""⑨ 형식 수정본 — 한 수신자의 저장 본문과 기존 반응 토큰을 읽기 전용으로 되쓴다."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import saved_digest

from time_policy import kst_day


def load_snapshot(db: Path, profile_id: str, date: str, recipient: str, *, title_only_count: int | None = None) -> dict:
    """새 토큰·회차를 만들지 않는다. 본문과 원 회차가 다르면 발송 전에 멈춘다."""
    import feedback_links
    with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as con:
        con.execute("PRAGMA query_only=ON")
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT name,last_digest,last_digest_at FROM profiles WHERE profile_id=?", (profile_id,)).fetchone()
        if row is None or saved_digest.parse(row["last_digest"] or "")["date"] != date:
            raise ValueError("요청 날짜의 저장 메일이 없음")
        issues = [dict(r) for r in con.execute("SELECT * FROM mail_issues WHERE profile_id=? AND status='sent' ORDER BY sent_at DESC", (profile_id,))]
        issue = next((r for r in issues if kst_day(r["sent_at"]) == date), None)
        if issue is None:
            raise ValueError("요청 날짜의 성공한 발송 회차가 없음")
        papers = [dict(r) for r in con.execute("SELECT * FROM mail_issue_items WHERE issue_id=? ORDER BY position", (issue["issue_id"],))]
        cfg = feedback_links.config()
        if not cfg:
            raise ValueError("기존 수신자를 대조할 반응 설정이 없음")
        url, secret = cfg
        rows = con.execute("SELECT * FROM feedback_tokens WHERE issue_id=? AND recipient_hash=? AND delivered_at IS NOT NULL",
                           (issue["issue_id"], feedback_links.recipient_hash(recipient, secret))).fetchall()
        tokens = {r["paper_key"]: dict(r) for r in rows}
        if len(tokens) != len(papers) or not papers:
            raise ValueError("이 수신자에게 원래 발송된 논문 토큰과 명세가 다름")
        page, did = feedback_links.page_url(), feedback_links.deploy_id(url)
        relay = f"{page}?{urlencode({'d': did})}&" if page and did else f"{url}?"
        for paper in papers:
            token = tokens.get(paper["paper_key"])
            if token is None or token["position"] != paper["position"]:
                raise ValueError("기존 반응 토큰의 논문 위치가 다름")
            if token["expires_at"] <= int(datetime.now(timezone.utc).timestamp()):
                raise ValueError("원래 반응 토큰이 만료되어 그대로 재발송할 수 없음")
            paper["_feedback_links"] = {action: relay + urlencode({"t": feedback_links.make_token(token["tid"], action, token["expires_at"], secret)})
                                        for action, _label in feedback_links.ACTIONS}
        html = saved_digest.render_html(row["last_digest"], row["name"], papers, title_only_count=title_only_count)
        return {"text": row["last_digest"], "html": html, "issue_id": issue["issue_id"],
                "subject": issue["subject"] + " [형식 수정본]", "papers": len(papers),
                "items": papers, "profile_name": row["name"]}


def main() -> None:
    """기본은 미리보기다. --send인 경우에만 백업 후 기존 SMTP 감사를 거쳐 한 통 보낸다."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--recipient", required=True)
    parser.add_argument("--db", type=Path, default=Path("data/papers.db"))
    parser.add_argument("--output", type=Path, default=Path("data/mail_previews"))
    parser.add_argument("--title-only-count", type=int, help="저장 평문에 없는 값은 원래 메일에서 확인한 경우에만 지정한다")
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()
    snapshot = load_snapshot(args.db, args.profile, args.date, args.recipient, title_only_count=args.title_only_count)
    args.output.mkdir(parents=True, exist_ok=True)
    stem = args.output / f"{args.profile}_{args.date}_reformatted"
    outcome = stem.with_suffix(".sent.json")
    if args.send and outcome.exists():
        raise ValueError("이 형식 수정본의 SMTP 성공 기록이 이미 있음 — 중복 발송하지 않음")
    stem.with_suffix(".html").write_text(snapshot["html"], encoding="utf-8")
    stem.with_suffix(".txt").write_text(snapshot["text"], encoding="utf-8")
    info = {"issue_id": snapshot["issue_id"], "papers": snapshot["papers"],
            "text_sha256": hashlib.sha256(snapshot["text"].encode()).hexdigest(),
            "html_bytes": len(snapshot["html"].encode()), "preview": str(stem.with_suffix(".html")), "sent": False}
    if args.send:
        backup = stem.with_suffix(".backup.db")
        with sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True) as source, sqlite3.connect(backup) as target:
            source.execute("PRAGMA query_only=ON")
            source.backup(target)
        import email_delivery
        email_delivery.send_digest_email(snapshot["text"], snapshot["subject"], [args.recipient], snapshot["html"])
        info.update(sent=True, sent_at=datetime.now(timezone.utc).isoformat(), backup=str(backup))
        outcome.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(info, ensure_ascii=False))


if __name__ == "__main__":
    main()
