# 9/14 피드백 → 9/17 반영 다이어그램 (2026-09-17)

월요일(9/14) 받은 피드백 6개(`docs/AGENT_PLAN_2026-09-14.md` 표)가 이번 주 무엇으로 구현됐는지 보여 주는 4장. 근거는 `docs/PROGRESS.md` §8-121~159.

| 파일 | 내용 | 유형 |
|---|---|---|
| `01-feedback-map` | 피드백 6개 ↔ 반영 (모듈·날짜·상태) | 매핑 |
| `02-feedback-loop` | 메일 반응 → 매일 가중치 → 주간 에이전트 → 다음 메일 | 루프 |
| `03-code-ladder` | 코드 탐색 사다리 5단계 | 흐름 |
| `04-security-layers` | 보안 층 5개와 못 막는 것 | 층 |
| `07-narrative-inputs` | 동향 서술의 입력 셋 → 서술 → 보관 → 내일의 입력 | 흐름 |

- `.html` 원본(발표용 1280×720) · `.png` 2배 해상도 · `all-four.html` 은 4장 한 페이지.
- 다시 만들기: `python3 gen_diagrams.py` → PNG 는 `.venv/bin/python export_png.py <html> <png> 2` (Playwright; WSL 에선 `LD_LIBRARY_PATH` 필요, `~/.claude/projects/.../memory/wsl_playwright_no_sudo.md`).
- 색은 `docs/DESIGN.md` 의 운영 화면 팔레트(`~/.diagram-design/profiles/paper-harness.md`). diagram-design 플러그인 규약(slide-16x9, 한글 12px 이상 sans).

## 슬라이드 (`피드백_반영_보고_2026-09-20.pptx`, 본문 10장 + 부록 2장, 16:9)

**발표 목적이 "이런 시스템을 만들었다"가 아니라 "지난 피드백을 이렇게 반영했고 실제로 이렇게 달라졌다"** 라서
9/20 에 순서를 뒤집었다. 피드백·변경 한 장 → **결과 화면 먼저** → 그다음에 왜 그렇게 바뀌었는지.

표지 → 1 지난 피드백과 이번 변경 → **2 실제 메일은 이렇게 바뀌었습니다** → 3 반응이 다음 검색을 바꾼다 →
4 하루치 스냅숏에서 흐름으로 → 5 월요일마다 검색 기준을 점검한다 → 6 제안은 모델이, 적용은 코드가 →
7 코드를 못 찾아도 한 칸 아래로 → 8 자동 재현을 돌리니 C 드라이브가 찼다 → 9 정리와 남은 것 →
부록 A(주간 관리 상세) · 부록 B(보안 5단계).

### 이 자료가 지키는 것 (2026-09-20 사용자 지적으로 정한 규칙)

- **한 장에 세 가지 넘게 설명하지 않는다.** 제목 · 배너 한 문장 · 그림이나 숫자 하나 · 보조 문장 최대 두 줄.
- **네이비 결론 바를 없앴다.** 배너와 결론 바가 한 장에서 두 문장을 말해 읽을 것이 늘었고, 그 문장들은
  슬라이드보다 발표자가 말하기 좋은 것이었다 — `addNotes` 로 옮겼다.
- **밀도에 리듬을 준다.** 2·6장은 아주 단순하게, 4·7장은 시각 중심, 8장은 숫자 중심으로.
- **쪽 번호 없음 · 매 장 반복되던 상단 머리말 없음 · 절 번호는 1, 2, 3**(로마숫자 아님).
- 제목은 말 걸기("어떻게 고쳤나") 대신 명사형("조치")으로 쓴다.
- **상세는 부록으로 내린다.** 본문에서 뺀 흐름도는 부록 A·B 에 있다.

디자인은 사내 보고서 `머신비전_조명환경검증_실험보고서_최종.pptx` 를 따랐다(2026-09-18 사용자 지정) —
네이비 `1F3864` · 블루 `4472C4` · 패널 `E9F0F8` · 맑은 고딕, 번호 상자 + 제목 → 파란 배너 한 줄 → 내용.

값(1.00→1.80 · 145→157 · 143.03 GB→186.8 MB)은 전부 실측이다. 같은 값을 두 장에서 되풀이하지 않는다.

다시 만들기: `python3 deck/make_crops.py` → `cd deck && npm i pptxgenjs && node build_deck.js [출력경로]`.
`03-code-ladder-steps.png` 는 사다리 그림에서 아래 설명·범례를 잘라낸 것이다(발표에서는 단계만 보이면 된다).
검증은 `pptx` 스킬의 `scripts/office/validate.py`(`defusedxml`·`lxml` 필요). 시각 확인은 Windows PowerPoint COM 으로
PNG 내보내기(WSL 에 LibreOffice 가 없다):
`powershell.exe -NoProfile -Command "$app = New-Object -ComObject PowerPoint.Application; $p = $app.Presentations.Open('<win경로>',1,0,0); $p.Export('<win폴더>\\out','PNG',1600,900); $p.Close(); $app.Quit()"`
