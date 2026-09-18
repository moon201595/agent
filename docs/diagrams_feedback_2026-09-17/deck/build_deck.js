// 피드백 반영 보고 — pptxgenjs.
// 디자인은 사내 보고서 `머신비전_조명환경검증_실험보고서_최종.pptx` 를 그대로 따른다(2026-09-18 사용자 지정):
// 네이비 1F3864 · 블루 4472C4 · 패널 E9F0F8 · 맑은 고딕, 머리(로마숫자+제목) → 배너 한 줄 → 내용 → 결론 바.
// 슬라이드마다 라벨·헤드·서브·태그를 겹겹이 쌓지 않는다 — 배너 한 줄이 그 슬라이드의 주장이다.
const pptxgen = require("pptxgenjs");
const path = require("path");

const DIAG = path.join(__dirname, "crops");           // crops/ 는 make_crops.py 가 만든다
const SIZES = require(path.join(DIAG, "sizes.json"));
const OUT = process.argv[2] ||
  path.join(__dirname, "..", "피드백_반영_보고_2026-09-18.pptx");

// ── 레퍼런스 보고서에서 뽑은 토큰
const NAVY = "1F3864", BLUE = "4472C4", BLUE2 = "2E75B6";
const PANEL = "E9F0F8", PANEL2 = "F7F9FC", ROW = "D9E2F3";
const GRAY = "595959", LIGHT = "BFBFBF", WHITE = "FFFFFF";
const GREEN = "1E7A46", ORANGE = "C55A11";
const KO = "Malgun Gothic";
const W = 13.333, H = 7.5;
const META1 = "paper-harness · 최신 연구 동향 모니터링 에이전트";
const META2 = "9/14 피드백 반영 보고 (2026-09-18)";

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";
pres.author = "paper-harness";
pres.title = "피드백 반영 보고 — 최신 연구 동향 모니터링 에이전트";

let pageNo = 1;      // 표지가 1쪽 — 머리를 그릴 때마다 다음 쪽

/** 머리: 로마숫자 상자 · 작은 구역명 · 제목 · 우상단 메타 · 배너 한 줄 */
function head(slide, roman, eyebrow, title, banner) {
  slide.addShape(pres.ShapeType.rect, { x: 0.45, y: 0.26, w: 0.62, h: 0.62,
    fill: { color: WHITE }, line: { color: NAVY, width: 1.25 } });
  slide.addText(roman, { x: 0.45, y: 0.26, w: 0.62, h: 0.62, fontFace: "Cambria", fontSize: 20,
    bold: true, color: NAVY, align: "center", valign: "middle", isTextBox: true, margin: 0 });
  slide.addText(eyebrow, { x: 1.22, y: 0.24, w: 6.5, h: 0.26, fontFace: KO, fontSize: 11,
    color: GRAY, isTextBox: true, margin: 0 });
  slide.addText(title, { x: 1.2, y: 0.46, w: 8.6, h: 0.5, fontFace: KO, fontSize: 23,
    bold: true, color: NAVY, isTextBox: true, margin: 0 });
  slide.addText(META1, { x: 8.0, y: 0.3, w: 4.9, h: 0.24, fontFace: KO, fontSize: 10,
    bold: true, color: BLUE2, align: "right", isTextBox: true, margin: 0 });
  slide.addText(META2, { x: 8.0, y: 0.54, w: 4.9, h: 0.24, fontFace: KO, fontSize: 9.5,
    color: GRAY, align: "right", isTextBox: true, margin: 0 });
  slide.addShape(pres.ShapeType.rect, { x: 0, y: 1.18, w: W, h: 0.52, fill: { color: BLUE }, line: { color: BLUE, width: 0 } });
  slide.addText(banner, { x: 0.5, y: 1.18, w: W - 1.0, h: 0.52, fontFace: KO, fontSize: 15,
    bold: true, color: WHITE, align: "center", valign: "middle", isTextBox: true, margin: 0 });
  pageNo += 1;
  slide.addText(String(pageNo), { x: W - 0.75, y: H - 0.42, w: 0.4, h: 0.24, fontFace: KO,
    fontSize: 9, color: LIGHT, align: "right", isTextBox: true, margin: 0 });
}

/** 결론 바 — 네이비, 흰 글씨. 이 슬라이드에서 가져갈 한 문장. */
function conclusion(slide, text, y = 6.62) {
  slide.addShape(pres.ShapeType.roundRect, { x: 0.5, y, w: W - 1.0, h: 0.56,
    fill: { color: NAVY }, line: { width: 0 }, rectRadius: 0.06 });
  slide.addText(text, { x: 0.7, y, w: W - 1.4, h: 0.56, fontFace: KO, fontSize: 12.5,
    bold: true, color: WHITE, align: "center", valign: "middle", isTextBox: true, margin: 0 });
}

/** 구역 소제목 — 배너 아래 왼쪽 */
function label(slide, text, x, y, w = 5.4) {
  slide.addText(text, { x, y, w, h: 0.3, fontFace: KO, fontSize: 13, bold: true,
    color: BLUE2, isTextBox: true, margin: 0 });
}

/** 다이어그램 — 잘린 PNG 를 상자 안에 비율 유지로 맞춘다 */
function diagram(slide, file, { x, y, maxW = 12.2, maxH = 4.6 } = {}) {
  const [pw, ph] = SIZES[file];
  const ratio = ph / pw;
  let w = Math.min(maxW, maxH / ratio);
  let h = w * ratio;
  if (h > maxH) { h = maxH; w = h / ratio; }
  const X = x ?? (W - w) / 2;
  const Y = y ?? 1.95 + (maxH - h) / 2;
  slide.addImage({ path: path.join(DIAG, file), x: X, y: Y, w, h });
}

// ══════════════════════════════════════════════════ 1. 표지
{
  const s = pres.addSlide();
  s.addShape(pres.ShapeType.rect, { x: 0, y: 0, w: W, h: 0.22, fill: { color: NAVY }, line: { width: 0 } });
  s.addShape(pres.ShapeType.rect, { x: 0, y: H - 0.22, w: W, h: 0.22, fill: { color: NAVY }, line: { width: 0 } });
  s.addText("연구 지원 에이전트  |  9/14 (월) 피드백 반영 보고", { x: 0.9, y: 1.15, w: 10, h: 0.3,
    fontFace: KO, fontSize: 13, bold: true, color: BLUE2, isTextBox: true, margin: 0 });
  s.addText("피드백, 이렇게 반영했습니다", { x: 0.9, y: 1.55, w: 11.5, h: 0.85,
    fontFace: KO, fontSize: 36, bold: true, color: NAVY, isTextBox: true, margin: 0 });
  s.addText("최신 연구 동향 모니터링 에이전트 — 관심 분야를 반응으로 스스로 조정하는 논문 수집·요약·재현 시스템",
    { x: 0.9, y: 2.45, w: 11.5, h: 0.4, fontFace: KO, fontSize: 15, color: GRAY, isTextBox: true, margin: 0 });

  const rows = [
    ["대 상", "관심 분야 프로필 4개 — 우리팀 · 로봇·피지컬 AI · AI 에이전트 · 비전 검사·VLM"],
    ["운 영", "매일 05:00 논문 5편 메일 · 금 17:00 주간 관리 — 9/16 부터 무인 운영"],
    ["보고 범위", "9/14 피드백 6개의 반영 결과 · 9/18 재현 정리(디스크) 변경"],
    ["작 성", "2026. 09. 18"],
  ];
  let y = 3.2;
  for (const [k, v] of rows) {
    s.addShape(pres.ShapeType.rect, { x: 0.9, y, w: 1.55, h: 0.44, fill: { color: BLUE }, line: { width: 0 } });
    s.addText(k, { x: 0.9, y, w: 1.55, h: 0.44, fontFace: KO, fontSize: 11, bold: true,
      color: WHITE, align: "center", valign: "middle", isTextBox: true, margin: 0 });
    s.addText(v, { x: 2.65, y, w: 9.8, h: 0.44, fontFace: KO, fontSize: 11.5, color: "333333",
      valign: "middle", isTextBox: true, margin: 0 });
    y += 0.56;
  }
  s.addShape(pres.ShapeType.line, { x: 0.9, y: 5.75, w: 11.55, h: 0, line: { color: LIGHT, width: 0.75 } });
  s.addText("피드백 기반 가중치  ·  키워드 자동 조정  ·  코드 재현과 정리  ·  보안 상한",
    { x: 0.9, y: 5.95, w: 11.5, h: 0.32, fontFace: KO, fontSize: 12.5, bold: true, color: NAVY, isTextBox: true, margin: 0 });
  s.addNotes("근거: docs/PROGRESS.md §8-121~160, docs/AGENT_PLAN_2026-09-14.md. 다이어그램 원본과 재생성 스크립트: docs/diagrams_feedback_2026-09-17/");
}

// ══════════════════════════════════════════════════ 2. 피드백 → 반영 (표)
{
  const s = pres.addSlide();
  head(s, "I", "개요", "받은 피드백과 반영 결과",
       "여섯 항목을 모두 코드에 반영했다 — SOTA 만 자료원이 없어 일부");

  const hdr = (t, w) => ({ text: t, options: { fill: NAVY, color: WHITE, bold: true, fontSize: 12,
    fontFace: KO, align: "center", valign: "middle" } });
  const left = (t) => ({ text: t, options: { fill: PANEL2, color: NAVY, bold: true, fontSize: 12,
    fontFace: KO, valign: "middle", margin: [4, 10, 4, 10] } });
  const mid = (t) => ({ text: t, options: { fill: WHITE, color: "333333", fontSize: 12,
    fontFace: KO, valign: "middle", margin: [4, 10, 4, 10] } });
  const st = (t, c) => ({ text: t, options: { fill: WHITE, color: c, bold: true, fontSize: 11.5,
    fontFace: KO, align: "center", valign: "middle" } });

  const rows = [
    [hdr("9/14 (월) 받은 피드백"), hdr("9/17~18 반영"), hdr("상태")],
    [left("사용자 반응으로 업데이트 · UI"), mid("메일 논문마다 1클릭 반응 → 매일 가중치 자동 조정"), st("운영", GREEN)],
    [left("메일 기준 ID 로 가중치 갱신"), mid("발송 회차 + 논문 번호를 서명한 토큰 → 논문 단위로 반응 수집"), st("운영", GREEN)],
    [left("규칙만 설정, 하네스로"), mid("규칙 15개 → 7개. 나머지는 문서가 아니라 코드가 강제"), st("운영", GREEN)],
    [left("메일 보안, 한 달 운영 가능하게"), mid("링크 허용 목록 · 내려받기 크기 상한 · 격리 실행 · 메일 부재 경보"), st("운영", GREEN)],
    [left("저장소 못 찾아도 비슷한 것이라도"), mid("코드 사다리 5단계 — 공식부터 유사 구현까지, 어느 칸인지 메일에 표시"), st("운영", GREEN)],
    [left("과제 SOTA 제안 · 에이전트처럼"), mid("논문이 스스로 주장한 SOTA 문장만 추출(미검증 표시) · 주간 에이전트"), st("일부", ORANGE)],
  ];
  s.addTable(rows, {
    x: 0.5, y: 2.0, w: W - 1.0, colW: [3.5, 7.0, 1.833],
    rowH: [0.44, 0.64, 0.64, 0.64, 0.64, 0.64, 0.64],
    border: { type: "solid", color: ROW, pt: 0.75 },
    fontFace: KO, autoPage: false,
  });
  conclusion(s, "\"일부\" 는 감추지 않는다 — 분야별 SOTA 순위표는 공개 자료원이 닫혀 대신 논문 자체 주장만 싣는다", 6.5);
  s.addNotes("1)2) §8-123·129  3) §8-122  4) §8-134  5) §8-132  6) §8-133·128. 6번: Papers with Code 가 2026-09-16 확인 시 huggingface.co/papers 로 리다이렉트되어 리더보드 API 가 없다.");
}

// ══════════════════════════════════════════════════ 3. 피드백 루프
{
  const s = pres.addSlide();
  head(s, "II", "구조", "반응이 다음 메일을 바꾸는 순환",
       "사용자가 누른 버튼 하나가 가중치 → 키워드 → 다음 날 논문 선정까지 이어진다");
  diagram(s, "02-feedback-loop.png", { x: 0.55, y: 1.95, maxW: 7.9, maxH: 4.5 });

  const stats = [
    ["+1.0 · +0.7 · −1.0", "더 보고 싶음 · 유용함 · 관심 밖"],
    ["하루 ±0.1", "가중치 이동 상한 — 클릭 한 번에 흔들리지 않는다"],
    ["90일", "반응 반감기 — 오래된 취향은 서서히 잊는다"],
  ];
  let y = 2.15;
  for (const [big, small] of stats) {
    s.addShape(pres.ShapeType.rect, { x: 8.75, y, w: 4.05, h: 1.28, fill: { color: PANEL }, line: { width: 0 } });
    s.addText(big, { x: 9.0, y: y + 0.14, w: 3.6, h: 0.42, fontFace: KO, fontSize: 19, bold: true,
      color: NAVY, isTextBox: true, margin: 0 });
    s.addText(small, { x: 9.0, y: y + 0.6, w: 3.6, h: 0.56, fontFace: KO, fontSize: 11,
      color: GRAY, isTextBox: true, margin: 0, valign: "top" });
    y += 1.45;
  }
  conclusion(s, "모든 자동 변경은 revision 으로 남는다 — 운영 화면에서 언제든 되돌릴 수 있다");
  s.addNotes("반응 수집: Apps Script 시트, HMAC 서명·만료 14일·선열람(발송 120초 안)·연타 격리. 가중치 갱신은 매일 05:00 스캔 직전.");
}

// ══════════════════════════════════════════════════ 4. 가중치 관리
{
  const s = pres.addSlide();
  head(s, "III", "피드백 기반 가중치", "매일 05:00, 모델 없이 코드가 계산한다",
       "반응이 목표값을 정하고, 가중치는 하루에 0.1 씩만 그쪽으로 간다");

  const cards = [
    ["논문 3편에 \"더 보고 싶음\"", "목표 +0.34 → 3~4일에 걸쳐 도달"],
    ["서로 다른 논문 2편 이상", "한 편은 그 논문이 좋았다는 뜻이지 그 키워드가 좋다는 뜻이 아니다"],
    ["되돌리기는 한 번에", "매일·주간 변경 전부 revision — 운영 화면에서 그 시점으로 복원"],
  ];
  let x = 0.5;
  for (const [h1, b] of cards) {
    s.addShape(pres.ShapeType.rect, { x, y: 2.0, w: 4.05, h: 1.55, fill: { color: PANEL }, line: { width: 0 } });
    s.addText(h1, { x: x + 0.28, y: 2.18, w: 3.5, h: 0.4, fontFace: KO, fontSize: 13.5, bold: true,
      color: NAVY, isTextBox: true, margin: 0 });
    s.addText(b, { x: x + 0.28, y: 2.62, w: 3.5, h: 0.8, fontFace: KO, fontSize: 11,
      color: GRAY, isTextBox: true, margin: 0, valign: "top" });
    x += 4.26;
  }

  label(s, "코드가 지키는 것", 0.5, 3.85, 6);
  const rules = [
    ["최초 키워드는 사용자 몫", "자동으로 지우지 않는다 — 내려가기만 한다"],
    ["같은 반응을 두 번 더하지 않는다", "기준선이 밀려 같은 클릭이 반복 적용되던 결함을 실측으로 잡았다 (1.337 → 1.657)"],
    ["순위는 점수 합이 아니라 순서", "관련성 계층 → 3일 날짜 띠 → 개념 폭. 계약 테스트가 감시한다"],
    ["에이전트 제안도 근거가 없으면 버린다", "좋다고 반응한 논문에 그 용어가 있어야 키워드가 올라간다"],
  ];
  let ry = 4.25;
  for (const [h1, b] of rules) {
    s.addShape(pres.ShapeType.rect, { x: 0.55, y: ry + 0.09, w: 0.1, h: 0.1, fill: { color: BLUE }, line: { width: 0 } });
    s.addText([{ text: h1 + "   ", options: { bold: true, color: NAVY } },
               { text: b, options: { color: GRAY } }],
      { x: 0.85, y: ry, w: 11.9, h: 0.36, fontFace: KO, fontSize: 11.5, isTextBox: true, margin: 0, valign: "middle" });
    ry += 0.52;
  }
  conclusion(s, "판단은 모델이 하더라도, 값을 바꾸는 것은 언제나 코드다");
  s.addNotes("feedback_weights.py — signal=(긍정−부정)/(긍정+부정+4), target=base+0.8·signal(0.35~2.0), 하루 |Δ|≤0.1, 반감기 90일, 최소 2편.");
}

// ══════════════════════════════════════════════════ 5. 주간 관리
{
  const s = pres.addSlide();
  head(s, "IV", "키워드 자동 조정 · DB 관리", "금요일 17:00 — 제안은 모델, 적용은 코드",
       "Claude 가 제안하고 Codex 가 깎아낸 뒤, 코드가 근거를 확인해야 반영된다");
  diagram(s, "05-weekly-agent.png", { y: 1.9, maxW: 12.2, maxH: 4.55 });
  conclusion(s, "DB 정리는 백업 뒤에만 · 모델이 답을 못 주면 그 주는 건너뛰고 가중치는 반응대로 계속 움직인다");
  s.addNotes("두 CLI 모두 도구를 끈 채 돌린다(저장소 파일을 못 읽는 것을 실측). 한 번 600초 상한. DB 보존: 후보 90일·관측/실행/논문 180일.");
}

// ══════════════════════════════════════════════════ 6. 코드 재현 ① 찾기
{
  const s = pres.addSlide();
  head(s, "V", "코드 재현 (1) 찾기", "없으면 한 칸 아래로 — 대신 어느 칸인지 밝힌다",
       "공식 저장소가 없어도 비슷한 구현까지 찾아 보여 주되, 공식이라고 쓰지 않는다");
  diagram(s, "03-code-ladder.png", { y: 2.05, maxW: 12.2, maxH: 4.2 });
  conclusion(s, "유사 구현은 보여 주기만 한다 — 격리 실행에 태우지 않고, 한 달마다 다시 찾는다");
  s.addNotes("유사 구현은 그 논문이 걸린 핵심 키워드로 GitHub 검색, 링크 모음·소개 페이지는 제외, 한 달 캐시. 격리 실행에는 태우지 않는다.");
}

// ══════════════════════════════════════════════════ 7. 코드 재현 ② 실행·정리
{
  const s = pres.addSlide();
  head(s, "VI", "코드 재현 (2) 실행과 정리", "격리해서 돌리고, 판정 뒤에는 흔적을 남기지 않는다",
       "9/18 변경 — 판정이 끝나면 이미지 · 빌더 · 컨테이너 · 복제본을 모두 지운다");
  diagram(s, "06-repro-isolation.png", { y: 2.05, maxW: 12.2, maxH: 4.2 });
  conclusion(s, "지운 뒤에도 판정과 실행 로그는 DB 에 남는다 — 사라지는 것은 다시 만들 수 있는 것뿐이다");
  s.addNotes("docker_runner.py — 시도마다 UUID 전용 빌더 → buildx build --load → 격리 실행 → 이미지·빌더 제거. 실패·타임아웃·예외도 정리하고 전역 prune 이나 볼륨 삭제는 하지 않는다. clone 은 로그·DB 저장 뒤에 지우고, 저장이 실패하면 조사용으로 남긴다.");
}

// ══════════════════════════════════════════════════ 8. 디스크 정리 실측
{
  const s = pres.addSlide();
  head(s, "VII", "디스크 문제와 처리", "재현이 쌓아 둔 것이 C 드라이브를 채웠다",
       "원인은 지우지 않는 설계였다 — 코드를 고치고, 이미 쌓인 것은 확인하며 지웠다");

  label(s, "무엇이 쌓였나", 0.5, 2.0, 6);
  const before = [
    ["빌드 캐시", "시도마다 공용 빌더에 남았다", "143.03 GB"],
    ["성공한 복제본", "\"성공하면 보관\" 이 기본이었다", "3.17 GB"],
    ["오래된 복제본", "지난 실행이 남긴 것", "1.04 GB"],
  ];
  let y = 2.38;
  for (const [k, why, size] of before) {
    s.addShape(pres.ShapeType.rect, { x: 0.5, y, w: 6.1, h: 0.66, fill: { color: PANEL }, line: { width: 0 } });
    s.addText(k, { x: 0.75, y, w: 1.7, h: 0.66, fontFace: KO, fontSize: 12, bold: true, color: NAVY,
      valign: "middle", isTextBox: true, margin: 0 });
    s.addText(why, { x: 2.45, y, w: 2.6, h: 0.66, fontFace: KO, fontSize: 10.5, color: GRAY,
      valign: "middle", isTextBox: true, margin: 0 });
    s.addText(size, { x: 5.0, y, w: 1.4, h: 0.66, fontFace: KO, fontSize: 13, bold: true, color: BLUE2,
      align: "right", valign: "middle", isTextBox: true, margin: 0 });
    y += 0.78;
  }
  s.addText("* 크기는 지우기 전 실측값이다. 캐시 143.03 GB 는 두 차례(43.47 + 99.56) 합계.",
    { x: 0.5, y: 4.78, w: 6.2, h: 0.3, fontFace: KO, fontSize: 10, color: GRAY, isTextBox: true, margin: 0 });

  label(s, "어떻게 고쳤나", 6.95, 2.0, 6);
  const after = [
    ["시도마다 전용 빌더", "다른 작업의 캐시를 건드리지 않고, 끝나면 그 빌더째 제거"],
    ["판정 뒤 전부 삭제", "이미지 · 컨테이너 · 복제본 — 실패 · 타임아웃 · 예외에서도"],
    ["지우기 전에 기록", "실행 로그와 DB 결과를 먼저 저장. 저장이 실패하면 복제본을 남긴다"],
    ["전역 삭제는 안 한다", "prune · 볼륨 삭제 금지 — 이번 시도가 만든 것만 지운다"],
  ];
  let ay = 2.38;
  for (const [k, v] of after) {
    s.addShape(pres.ShapeType.rect, { x: 6.95, y: ay + 0.1, w: 0.1, h: 0.1, fill: { color: BLUE }, line: { width: 0 } });
    s.addText(k, { x: 7.25, y: ay, w: 5.6, h: 0.28, fontFace: KO, fontSize: 12, bold: true, color: NAVY,
      isTextBox: true, margin: 0 });
    s.addText(v, { x: 7.25, y: ay + 0.28, w: 5.6, h: 0.42, fontFace: KO, fontSize: 10.5, color: GRAY,
      isTextBox: true, margin: 0, valign: "top" });
    ay += 0.78;
  }
  s.addText([{ text: "정리 뒤 실측", options: { bold: true, color: NAVY } },
             { text: "  빌드 캐시 186.8 MB  ·  재현 기록 132건 그대로  ·  테스트 1,086 통과", options: { color: GRAY } }],
    { x: 6.95, y: 5.62, w: 5.9, h: 0.34, fontFace: KO, fontSize: 10.5, isTextBox: true, margin: 0, valign: "top" });

  conclusion(s, "호스트 디스크 회수(VHDX 압축)는 아직 남았다 — WSL 안에서 지운 양과 C 여유는 다른 값이다");
  s.addNotes("운영 DB 는 data/backups/papers-pre-repro-cleanup-20260918.db 로 온라인 백업 후 integrity_check=ok. 삭제 목록은 repro-cleanup-20260918.json 에 보관. WSL 사용량 약 29GiB, C 여유 약 21GiB — 호스트 디스크 회수(VHDX 압축)는 아직 남아 있고 별도 작업이다.");
}

// ══════════════════════════════════════════════════ 9. 보안
{
  const s = pres.addSlide();
  head(s, "VIII", "보안", "외부 입력이 지나는 다섯 관문",
       "논문 · 링크 · 저장소는 신뢰하지 않는 입력으로 다룬다 — 못 막는 것도 적어 두었다");
  diagram(s, "04-security-layers.png", { y: 1.95, maxW: 12.2, maxH: 4.5 });
  conclusion(s, "\"한 달 돌리기 전에 보안부터\" — 네 층을 9/16 에 붙였고, 격리 실행은 그전부터 있었다");
  s.addNotes("미구현으로 기록한 것: DNS 해석 뒤 사설 IP 판정, 논문 본문 인젝션은 표시만(차단 안 함). 시크릿(.env)은 어느 모델에도 넣지 않는다.");
}

// ══════════════════════════════════════════════════ 10. 남은 것
{
  const s = pres.addSlide();
  head(s, "IX", "정리", "지금 상태와 남은 것",
       "네 프로필이 무인으로 돌고 있고, 못 한 것은 못 했다고 적어 두었다");

  label(s, "돌아가고 있는 것", 0.5, 2.0, 6);
  const done = [
    ["프로필 4개 무인 운영", "9/16~ 매일 05:00 메일 5편 · 금 17:00 주간 관리"],
    ["구조 정리와 외부 검토", "죽은 코드 삭제 · 모듈 분할 · Codex 검토가 잡은 회귀 1건 수정"],
    ["전체 테스트 1,086 통과", "완료 조건은 초록불이다 — 테스트를 고쳐서 맞추지 않는다"],
  ];
  let y = 2.4;
  for (const [k, v] of done) {
    s.addShape(pres.ShapeType.rect, { x: 0.5, y, w: 6.1, h: 0.8, fill: { color: PANEL }, line: { width: 0 } });
    s.addText(k, { x: 0.78, y: y + 0.1, w: 5.6, h: 0.3, fontFace: KO, fontSize: 12.5, bold: true,
      color: NAVY, isTextBox: true, margin: 0 });
    s.addText(v, { x: 0.78, y: y + 0.4, w: 5.6, h: 0.32, fontFace: KO, fontSize: 10.5, color: GRAY,
      isTextBox: true, margin: 0 });
    y += 0.92;
  }

  label(s, "남은 것 · 하지 않은 것", 6.95, 2.0, 6);
  const todo = [
    ["분야별 SOTA 순위 추적", "공개 리더보드 자료원이 닫혔다 — 논문 자체 주장만 싣는 중"],
    ["사설 IP 판정 · 인젝션 차단", "미구현으로 보안 점검표에 적었다. 만든 척하지 않는다"],
    ["호스트 디스크 회수", "WSL 안은 정리했고 VHDX 압축은 아직 — C 여유 약 21 GB"],
  ];
  let ty = 2.4;
  for (const [k, v] of todo) {
    s.addShape(pres.ShapeType.rect, { x: 6.95, y: ty, w: 5.88, h: 0.8, fill: { color: PANEL2 },
      line: { color: ROW, width: 0.75 } });
    s.addText(k, { x: 7.23, y: ty + 0.1, w: 5.4, h: 0.3, fontFace: KO, fontSize: 12.5, bold: true,
      color: ORANGE, isTextBox: true, margin: 0 });
    s.addText(v, { x: 7.23, y: ty + 0.4, w: 5.4, h: 0.32, fontFace: KO, fontSize: 10.5, color: GRAY,
      isTextBox: true, margin: 0 });
    ty += 0.92;
  }

  s.addText("근거 기록 : docs/PROGRESS.md §8-121~160  ·  다이어그램과 재생성 스크립트 : docs/diagrams_feedback_2026-09-17/",
    { x: 0.5, y: 5.6, w: 12.3, h: 0.3, fontFace: KO, fontSize: 10, color: GRAY, isTextBox: true, margin: 0 });
  conclusion(s, "실측하지 않은 값은 \"미실측\", 실패는 실패로 적는다 — 그래야 다음 판단을 이 기록 위에서 할 수 있다");
  s.addNotes("다음 주 확인 예정: 반응이 쌓이면 가중치가 실제로 분야를 따라가는지 수치로 본다.");
}

pres.writeFile({ fileName: OUT }).then(f => console.log("wrote", f));
