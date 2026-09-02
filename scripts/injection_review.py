"""인젝션 flag 를 사람이 판단할 수 있게 문맥과 함께 꺼내 준다 (§8-17).

`.venv/bin/python scripts/injection_review.py [arxiv_id]`

왜 필요한가: `injection_scan` 이 flag 를 달면 다이제스트에 "⚠ 본문에 모델
대상 지시로 보이는 패턴"이 뜬다. 그런데 그게 **진짜 프롬프트 주입**인지
**에이전트 논문이 실험용 시스템 프롬프트를 인용한 것**인지는 본문을 봐야
안다. §8-17 이 한 달 넘게 열려 있던 이유는 "그러려면 논문을 열어야 한다"는
마찰 때문이었다 — 그 마찰을 없앤다.

아무것도 판정하지 않는다. 걸린 자리 앞뒤를 그대로 보여줄 뿐이고, 판단은
사람이 한다(CLAUDE.md 7: 기계가 위조 불가능하게 판정할 수 있는 것만
자동화한다 — "이게 공격인가"는 거기 해당하지 않는다).
"""

from __future__ import annotations

import sqlite3
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import injection_scan  # noqa: E402

DB = ROOT / "data" / "papers.db"
CONTEXT = 700   # 걸린 자리 앞뒤로 보여줄 글자 수


def _context_at(text: str, index: int) -> str:
    """걸린 자리 앞뒤를 읽을 수 있게 다듬어 돌려준다.

    추출된 본문은 토큰마다 줄바꿈이 들어간 경우가 흔해서(실측: PDF 경로)
    그대로 찍으면 한 줄에 한 단어씩 나와 사람이 못 읽는다. 공백만 정리하고
    내용은 안 건드린다 — 판단 근거를 바꾸면 안 된다.
    """
    lo, hi = max(0, index - CONTEXT), min(len(text), index + CONTEXT)
    body = " ".join(text[lo:hi].split())
    body = ("…" if lo else "") + body + ("…" if hi < len(text) else "")
    return textwrap.fill(body, width=74, initial_indent="  ", subsequent_indent="  ")


def main(argv: list[str]) -> int:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    if len(argv) > 1:
        rows = con.execute("SELECT arxiv_id, title, text_path, injection_suspect "
                           "FROM papers WHERE arxiv_id=?", (argv[1],)).fetchall()
    else:
        rows = con.execute("SELECT arxiv_id, title, text_path, injection_suspect "
                           "FROM papers WHERE injection_suspect IS NOT NULL "
                           "AND injection_suspect != ''").fetchall()
    con.close()

    if not rows:
        print("flag 된 논문이 없다.")
        return 0

    for r in rows:
        print("=" * 74)
        print(f"{r['arxiv_id']}  {r['title']}")
        print(f"https://arxiv.org/abs/{r['arxiv_id']}")
        print(f"\nflag 사유: {r['injection_suspect']}")
        try:
            text = Path(r["text_path"]).read_text(encoding="utf-8")
        except (OSError, TypeError):
            print("  (본문 파일 없음)")
            continue

        # 저장된 사유 문자열로 원문을 되찾으면 안 된다 — 사유에 담긴 조각은
        # 공백이 정규화돼 있어서 원문과 안 맞는다(첫 시도가 이걸로 실패했다).
        # 패턴을 직접 다시 돌려 걸린 **위치**를 얻는다.
        hits = 0
        for pattern, label in injection_scan._COMPILED:
            for m in pattern.finditer(text):
                hits += 1
                print("-" * 74)
                print(f"▶ {label}  (원문 {m.start():,}자 지점)")
                print(f"\n[본문 문맥]\n{_context_at(text, m.start())}\n")
                break   # 같은 패턴은 첫 건만 — 같은 문구가 반복되는 경우가 많다
        invisible = injection_scan._INVISIBLE_RE.findall(text)
        if invisible:
            hits += 1
            codes = sorted({f"U+{ord(c):04X}" for c in invisible})
            print("-" * 74)
            print(f"▶ 비정상 유니코드 {len(invisible)}개 — {', '.join(codes[:8])}")
        if not hits:
            print("  (지금 다시 스캔하니 걸리는 게 없다 — 본문이 갱신됐거나 "
                  "스캐너가 바뀐 것이다)")

        print("판단 기준:")
        print("  · 이 지시가 **논문이 인용한 실험 재료**인가(에이전트 논문의 시스템")
        print("    프롬프트, 벤치마크 예시 등) → 오탐. flag 를 지워도 된다.")
        print("  · 이 지시가 **읽는 모델을 향해** 쓰였나(요약기에게 지시, 평가를")
        print("    조작하려는 문구) → 진짜. 그 논문은 ④ 에 넣지 말 것.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
