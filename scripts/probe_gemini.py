"""Gemini 가 지금 응답하는지 한 번만 확인한다. 종료코드 0 = 정상.

`.venv/bin/python scripts/probe_gemini.py`

**왜 레포 안에 두나**: 2026-09-02·09-03 에 같은 실수를 두 번 했다. 프로브를
임시 디렉터리에 두었더니 (1) sys.path 누락으로 ModuleNotFoundError 가 나고
(2) 나중엔 파일 자체가 정리돼 사라졌는데, 두 번 다 그 실패를 **"Gemini 가
막혔다"로 잘못 읽었다.**

프로브가 죽은 것과 대상이 막힌 것을 구분하지 못하면 확인 자체가 무의미하다.
그래서 실패 원인을 항상 찍고, 파일은 레포에 둬서 사라지지 않게 한다.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

import summarize_engine as se  # noqa: E402


async def main() -> int:
    names = se.gemini_key_names()
    if not names:
        print("키가 설정돼 있지 않다(GOOGLE_API_KEY…)", file=sys.stderr)
        return 2
    async with httpx.AsyncClient() as client:
        try:
            await se._post_gemini(client, "Reply with OK.")
            print(f"정상 — 키 {len(names)}개 설정됨")
            return 0
        except httpx.HTTPStatusError as e:
            print(f"막힘 — HTTP {e.response.status_code} "
                  f"({'한도 소진' if e.response.status_code == 429 else '서버 혼잡/오류'})",
                  file=sys.stderr)
            return 1
        except Exception as e:  # noqa: BLE001
            print(f"프로브 자체 실패 — {type(e).__name__}: {str(e)[:100]}", file=sys.stderr)
            return 3


sys.exit(asyncio.run(main()))
