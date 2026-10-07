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
        # 평문 쪽도 HTML 과 같은 표시 규칙으로 보낸다(P3-1). 원 기록과 같은 글인지는 `source_text` 의 해시로 남긴다.
        return {"text": saved_digest.plain_text(row["last_digest"]), "source_text": row["last_digest"], "html": html, "issue_id": issue["issue_id"],
                "subject": issue["subject"] + " [형식 수정본]", "papers": len(papers),
                "items": papers, "profile_name": row["name"]}



def load_preview(db: Path, profile_id: str, date: str, original_html: Path | None = None) -> dict:
    """서명 설정 없이 화면만 확인한다. 기존 HTML의 토큰은 DB 명세와 대조하고 재서명하지 않는다.

    서명·수신자 인증은 하지 못하므로 이 결과는 발송용 snapshot으로 쓰지 않는다.
    원 HTML이 없으면 버튼을 복원하지 않는다. 현재 관측값으로 옛 글을 보충하지 않는다.
    """
    from html.parser import HTMLParser
    from urllib.parse import parse_qs, urlsplit
    import feedback_links

    class Links(HTMLParser):
        def __init__(self, html: str) -> None:
            super().__init__()
            self.urls: list[str] = []
            self.feed(html)

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            url = dict(attrs).get("href")
            if tag == "a" and url:
                self.urls.append(url)

    with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as con:
        con.execute("PRAGMA query_only=ON")
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT name,last_digest FROM profiles WHERE profile_id=?", (profile_id,)).fetchone()
        if row is None or saved_digest.parse(row["last_digest"] or "")["date"] != date:
            raise ValueError("요청 날짜의 저장 메일이 없음")
        issue = next((dict(r) for r in con.execute(
            "SELECT * FROM mail_issues WHERE profile_id=? AND status='sent' ORDER BY sent_at DESC", (profile_id,))
            if kst_day(r["sent_at"]) == date), None)
        if issue is None:
            raise ValueError("요청 날짜의 성공한 발송 회차가 없음")
        papers = [dict(r) for r in con.execute(
            "SELECT * FROM mail_issue_items WHERE issue_id=? ORDER BY position", (issue["issue_id"],))]
        tokens = {r["tid"]: dict(r) for r in con.execute(
            "SELECT * FROM feedback_tokens WHERE issue_id=? AND delivered_at IS NOT NULL", (issue["issue_id"],))}
        recovered: dict[str, dict[str, str]] = {}
        recipients = set()
        if original_html:
            for url in Links(original_html.read_text(encoding="utf-8")).urls:
                token = parse_qs(urlsplit(url).query).get("t", [""])[0].split(".")
                if len(token) != 5:
                    continue
                stored = tokens.get(token[1])
                if (stored is None or token[0] != feedback_links.TOKEN_VERSION
                        or token[2] not in {a for a, _ in feedback_links.ACTIONS}
                        or token[3] != str(stored["expires_at"])):
                    raise ValueError("기존 HTML의 토큰과 원 회차가 다름")
                recipients.add(stored["recipient_hash"])
                recovered.setdefault(stored["paper_key"], {})[token[2]] = url
            if len(recipients) != 1 or set(recovered) != {p["paper_key"] for p in papers}:
                raise ValueError("기존 HTML의 수신자·논문 토큰 명세가 다름")
            for paper in papers:
                if set(recovered[paper["paper_key"]]) != {a for a, _ in feedback_links.ACTIONS}:
                    raise ValueError("기존 HTML의 반응 버튼이 누락됨")
                paper["_feedback_links"] = recovered[paper["paper_key"]]
        return {"text": saved_digest.plain_text(row["last_digest"]), "source_text": row["last_digest"],
                "html": saved_digest.render_html(row["last_digest"], row["name"], papers),
                "issue_id": issue["issue_id"], "papers": len(papers), "items": papers,
                "subject": issue["subject"], "profile_name": row["name"],
                "feedback_restored": bool(original_html), "preview_only": True}


def main() -> None:
    """기본은 미리보기다. --send인 경우에만 백업 후 기존 SMTP 감사를 거쳐 한 통 보낸다."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--recipient")
    parser.add_argument("--offline-preview", action="store_true", help="서명 설정 없이 화면만 확인하며 발송을 금지한다")
    parser.add_argument("--original-html", type=Path, help="오프라인 미리보기에서 기존 반응 링크를 그대로 복원할 HTML")
    parser.add_argument("--db", type=Path, default=Path("data/papers.db"))
    parser.add_argument("--output", type=Path, default=Path("data/mail_previews"))
    parser.add_argument("--title-only-count", type=int, help="저장 평문에 없는 값은 원래 메일에서 확인한 경우에만 지정한다")
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()
    if args.offline_preview:
        if args.send or args.recipient or args.title_only_count is not None:
            parser.error("오프라인 미리보기는 발송·수신자·미저장 숫자 지정과 함께 쓰지 않는다")
        snapshot = load_preview(args.db, args.profile, args.date, args.original_html)
    else:
        if not args.recipient or args.original_html:
            parser.error("발송용 snapshot은 수신자와 기존 서명 설정을 요구한다")
        snapshot = load_snapshot(args.db, args.profile, args.date, args.recipient, title_only_count=args.title_only_count)
    args.output.mkdir(parents=True, exist_ok=True)
    stem = args.output / f"{args.profile}_{args.date}_reformatted"
    outcome = stem.with_suffix(".sent.json")
    if args.send and outcome.exists():
        raise ValueError("이 형식 수정본의 SMTP 성공 기록이 이미 있음 — 중복 발송하지 않음")
    stem.with_suffix(".html").write_text(snapshot["html"], encoding="utf-8")
    stem.with_suffix(".txt").write_text(snapshot["text"], encoding="utf-8")
    info = {"issue_id": snapshot["issue_id"], "papers": snapshot["papers"],
            "text_sha256": hashlib.sha256(snapshot["source_text"].encode()).hexdigest(),
            "html_bytes": len(snapshot["html"].encode()), "preview": str(stem.with_suffix(".html")), "sent": False}
    if args.offline_preview:
        info.update(preview_only=True, feedback_restored=snapshot["feedback_restored"])
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
