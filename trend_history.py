"""⑨ 연구 흐름의 시간 연결 — 저장된 관측만 후보로 주고 연결은 선택적으로 표시한다."""
from __future__ import annotations

import json
import re
from datetime import date

RELATIONS = {"continuing": "이어짐", "expanding": "확장", "branching": "갈라짐"}
HISTORY_PREFIX = "↳ 지난 관측 "


def catalog(past: list[dict] | None, max_items: int = 12) -> dict[str, dict]:
    """구조가 없는 옛 기록은 되읽는다. ID는 이번 입력의 주소이며 원본 날짜·기록 ID도 함께 남긴다."""
    import trend_report
    out: dict[str, dict] = {}
    for row in sorted(past or [], key=lambda r: str(r.get("reader_date") or ""), reverse=True):
        try:
            day = date.fromisoformat(str(row.get("reader_date") or ""))
        except ValueError:
            continue
        try:
            audit = json.loads(row.get("audit_json") or "{}")
        except (ValueError, TypeError):
            audit = {}
        stored = audit.get("story") if isinstance(audit, dict) else None
        story = stored if isinstance(stored, dict) and stored.get("version") == "story-v3" else None
        if story is None:
            story = trend_report.parse_rendered_story(row.get("body") or "")
        for index, thread in enumerate(story.get("threads") or [], start=1):
            if not isinstance(thread, dict):
                continue
            body = re.sub(r"\[P\d+:[^]]+\]", "", str(thread.get("body") or ""))
            body = " ".join(body.split())
            if not body:
                continue
            key = f"H{len(out) + 1}"
            titles = thread.get("titles") or [i.get("title", "") for i in thread.get("items") or [] if isinstance(i, dict)]
            out[key] = {"ref": key, "reader_date": day.isoformat(), "name": str(thread.get("name") or ""),
                        "body": body[:500], "titles": [t for t in titles if isinstance(t, str)][:5],
                        "narrative_id": row.get("narrative_id") or "", "thread_index": index}
            if len(out) >= max_items:
                return out
    return out


def context(records: dict[str, dict]) -> str:
    """과거의 P번호는 오늘과 충돌하므로 후보의 H번호만 준다."""
    if not records:
        return ""
    blocks = [f"[{key}] {r['reader_date']}\n연구 문제: {r['name'] or '(이름 없음)'}\n관측 요지: {r['body']}"
              for key, r in records.items()]
    return ("\n\n--- 저장된 지난 연구 흐름(H번호만 참조) ---\n" + "\n\n".join(blocks)
            + "\n--- 여기까지가 지난 관측이다 ---\n")


def display(history: dict) -> str:
    """날짜·흐름 이름은 원본 관측에서 붙인다. 의미 관계는 모델의 해석이다."""
    source = history["source"]
    name = f" · {source['name']}" if source.get("name") else ""
    return (f"{HISTORY_PREFIX}{source['reader_date']}{name} · {RELATIONS[history['relation']]} — "
            f"{history['note']} (해석)")
