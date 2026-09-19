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

## 슬라이드 (`피드백_반영_보고_2026-09-19.pptx`, 11장, 16:9)

디자인은 사내 보고서 `머신비전_조명환경검증_실험보고서_최종.pptx` 를 따랐다(2026-09-18 사용자 지정) — 네이비 `1F3864` · 블루 `4472C4` ·
패널 `E9F0F8` · 맑은 고딕, 머리(로마숫자+제목) → 파란 배너 한 줄 → 내용 → 네이비 결론 바. 다이어그램 팔레트도 여기에 맞췄다.
슬라이드마다 라벨·헤드카피·서브카피·태그를 겹쳐 쌓지 않는다 — 배너 한 줄이 그 장의 주장이고, 결론 바는 배너와 다른 말을 한다.

표지 → I 피드백·반영 표(네이티브 표) → II 루프 → III 가중치 → **IV 동향 서술(9/19 추가)** → V 주간 관리(월요일로 이동) →
VI 재현 찾기 → VII 재현 실행·정리 → VIII 디스크 → IX 보안 → X 정리.

다시 만들기: `python3 deck/make_crops.py` → `cd deck && npm i pptxgenjs && node build_deck.js`.
시각 확인은 Windows PowerPoint COM 으로 PNG 내보내기(WSL 에 LibreOffice 가 없다):
`powershell.exe -NoProfile -Command "$app = New-Object -ComObject PowerPoint.Application; $p = $app.Presentations.Open('<win경로>',1,0,0); $p.Export('<win폴더>\\out','PNG',1600,900); $p.Close(); $app.Quit()"`
