"""평가 스크립트 — 저장된 모든 요약의 수치 검증 통과율을 일괄 측정한다.

용도: 하네스 회귀 테스트의 기준선. 프롬프트 템플릿을 바꾸거나(Phase 0),
LLM 백엔드를 로컬 모델로 교체할 때(Phase 1) 이 스크립트의 통과율로
품질 변화를 숫자로 비교한다.

사용:
    python eval.py                 # 전체 요약 검증 보고
    python eval.py --min-ratio 0.9 # 통과율이 기준 미만이면 종료코드 1 (CI 게이트용)

주의: 이 지표는 '숫자의 원문 존재 여부'만 본다. 요약의 논리적 정확성은
사람이 표본 검토로 보완해야 한다 — 지표 하나로 품질을 단정하지 말 것.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from verify import verify_numbers

BASE_DIR = Path(__file__).resolve().parent
SUMMARY_DIR = BASE_DIR / "data" / "summaries"
TEXT_DIR = BASE_DIR / "data" / "text"


def main() -> int:
    parser = argparse.ArgumentParser(description="요약 수치 검증 일괄 실행")
    parser.add_argument("--min-ratio", type=float, default=None,
                        help="전체 통과율이 이 값 미만이면 종료코드 1")
    args = parser.parse_args()

    summaries = sorted(SUMMARY_DIR.glob("*.md")) if SUMMARY_DIR.exists() else []
    if not summaries:
        print("검증할 요약이 없다. save_summary로 요약을 먼저 저장할 것.")
        return 0

    grand_total = 0
    grand_matched = 0
    print(f"{'arxiv_id':<18} {'numbers':>7} {'matched':>7} {'ratio':>6}  unmatched")
    print("-" * 78)
    for s_path in summaries:
        arxiv_id = s_path.stem
        t_path = TEXT_DIR / f"{arxiv_id}.txt"
        if not t_path.exists():
            print(f"{arxiv_id:<18} {'—':>7} {'—':>7} {'—':>6}  원문 텍스트 없음 (fetch_paper 필요)")
            continue
        report = verify_numbers(
            s_path.read_text(encoding="utf-8"), t_path.read_text(encoding="utf-8")
        )
        grand_total += report.total
        grand_matched += report.matched
        unmatched = ", ".join(c.token for c in report.unmatched) or "-"
        print(f"{arxiv_id:<18} {report.total:>7} {report.matched:>7} "
              f"{report.pass_ratio:>6.2f}  {unmatched}")

    overall = 1.0 if grand_total == 0 else grand_matched / grand_total
    print("-" * 78)
    print(f"전체: 숫자 {grand_total}개 중 {grand_matched}개 일치 — 통과율 {overall:.3f}")

    if args.min_ratio is not None and overall < args.min_ratio:
        print(f"FAIL: 통과율 {overall:.3f} < 기준 {args.min_ratio}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
