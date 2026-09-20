/**
 * 피드백 반영 보고 슬라이드 — 2026-09-20 재구성.
 *
 * 이 발표의 목적은 "우리가 이런 시스템을 만들었다"가 아니라 **"지난 피드백을 이렇게 반영했고
 * 실제로 이렇게 달라졌다"** 다. 그래서 순서를 뒤집었다 — 피드백·변경 한 장, **결과 화면 먼저**,
 * 그다음에 왜 그렇게 바뀌었는지. 14장에서 11장으로 줄였다(2026-09-20 지적).
 *
 * 장식은 뺐다: 쪽 번호·상단 머리말(제품명/부제)이 매 장 반복되며 내용을 밀어내고 있었다.
 * 절 번호는 로마숫자 대신 1, 2, 3 이다.
 *
 * 만들기: python3 ../make_crops.py → npm i pptxgenjs → node build_deck.js [출력경로]
 */
const pptxgen = require("pptxgenjs");
const path = require("path");

const DIAG = path.join(__dirname, "crops");
const SIZES = require(path.join(DIAG, "sizes.json"));
const OUT = process.argv[2] ||
  path.join(__dirname, "..", "피드백_반영_보고_2026-09-20.pptx");

// ── 레퍼런스 보고서에서 뽑은 토큰
const NAVY = "1F3864", BLUE = "4472C4", BLUE2 = "2E75B6";
const PANEL = "E9F0F8", PANEL2 = "F7F9FC", ROW = "D9E2F3";
const GRAY = "595959", LIGHT = "BFBFBF", WHITE = "FFFFFF";
const GREEN = "1E7A46", ORANGE = "C55A11";
const KO = "Malgun Gothic";
const W = 13.333, H = 7.5;

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";
pres.author = "paper-harness";
pres.title = "피드백 반영 보고 — 최신 연구 동향 모니터링 에이전트";

let sectionNo = 0;

/** 머리 — 번호 · 제목 · 한 줄 결론 · 얇은 선.
 *
 *  2026-09-20 2차: **파란 가로띠를 없앴다.** 매 장 같은 자리에 같은 색 띠가 있으니 교육자료처럼 보였고,
 *  그 띠 때문에 파란색이 강조가 아니라 배경색이 됐다. 지금 파랑은 한 장에 **수치 하나**에만 쓴다.
 */
function head(slide, title, lead) {
  const no = String(++sectionNo);
  slide.addShape(pres.ShapeType.rect, { x: 0.5, y: 0.42, w: 0.06, h: 0.52,
    fill: { color: NAVY }, line: { width: 0 } });
  slide.addText(no, { x: 0.68, y: 0.42, w: 0.4, h: 0.52, fontFace: "Cambria", fontSize: 17,
    bold: true, color: NAVY, valign: "middle", isTextBox: true, margin: 0 });
  slide.addText(title, { x: 1.15, y: 0.4, w: 11.7, h: 0.56, fontFace: KO, fontSize: 25,
    bold: true, color: NAVY, valign: "middle", isTextBox: true, margin: 0 });
  slide.addText(lead, { x: 1.15, y: 1.02, w: 11.7, h: 0.34, fontFace: KO, fontSize: 13.5,
    color: GRAY, valign: "middle", isTextBox: true, margin: 0 });
  slide.addShape(pres.ShapeType.line, { x: 0.5, y: 1.52, w: W - 1.0, h: 0,
    line: { color: LIGHT, width: 0.75 } });
}

/** 부록 머리 — 번호를 매기지 않는다. */
function appendix(slide, title, lead) {
  slide.addText("부록", { x: 0.5, y: 0.42, w: 1.0, h: 0.52, fontFace: KO, fontSize: 13,
    bold: true, color: LIGHT, valign: "middle", isTextBox: true, margin: 0 });
  slide.addText(title, { x: 1.45, y: 0.4, w: 11.4, h: 0.56, fontFace: KO, fontSize: 22,
    bold: true, color: NAVY, valign: "middle", isTextBox: true, margin: 0 });
  slide.addText(lead, { x: 1.45, y: 1.02, w: 11.4, h: 0.34, fontFace: KO, fontSize: 12.5,
    color: GRAY, valign: "middle", isTextBox: true, margin: 0 });
  slide.addShape(pres.ShapeType.line, { x: 0.5, y: 1.52, w: W - 1.0, h: 0,
    line: { color: LIGHT, width: 0.75 } });
}

/** 기술 근거 묶음 — 작은 글씨로 아래에 둔다. **내용을 줄이는 게 아니라 무게를 줄인다.**
 *  이 항목들이 "왜 함부로 흔들리지 않는가"를 말한다 — 빼면 발표가 얕아진다(2026-09-20 지적). */
function grounds(slide, label, items, x, y, w) {
  slide.addShape(pres.ShapeType.line, { x, y, w, h: 0, line: { color: LIGHT, width: 0.75 } });
  slide.addText(label, { x, y: y + 0.1, w, h: 0.28, fontFace: KO, fontSize: 11, bold: true,
    color: NAVY, isTextBox: true, margin: 0 });
  let iy = y + 0.44;
  for (const it of items) {
    slide.addText("· " + it, { x, y: iy, w, h: 0.28, fontFace: KO, fontSize: 10.5,
      color: GRAY, isTextBox: true, margin: 0 });
    iy += 0.28;
  }
  return iy;
}

/** before → after 를 선 하나와 점 둘로. 박스를 또 그리지 않는다. */
function dumbbell(slide, { x, y, w, caption, before, after, note }) {
  slide.addText(caption, { x, y, w, h: 0.34, fontFace: KO, fontSize: 15, color: GRAY,
    isTextBox: true, margin: 0 });
  slide.addText(before, { x, y: y + 0.42, w: 1.6, h: 0.6, fontFace: "Cambria", fontSize: 26,
    color: GRAY, valign: "middle", isTextBox: true, margin: 0 });
  slide.addText(after, { x: x + w - 2.0, y: y + 0.42, w: 2.0, h: 0.6, fontFace: "Cambria",
    fontSize: 34, bold: true, color: BLUE, align: "right", valign: "middle", isTextBox: true, margin: 0 });
  slide.addShape(pres.ShapeType.line, { x: x + 1.5, y: y + 0.72, w: w - 3.6, h: 0,
    line: { color: BLUE, width: 1.5 } });
  slide.addShape(pres.ShapeType.ellipse, { x: x + 1.42, y: y + 0.64, w: 0.16, h: 0.16,
    fill: { color: GRAY }, line: { width: 0 } });
  slide.addShape(pres.ShapeType.ellipse, { x: x + w - 2.18, y: y + 0.64, w: 0.16, h: 0.16,
    fill: { color: BLUE }, line: { width: 0 } });
  if (note) {
    slide.addText(note, { x, y: y + 1.12, w, h: 0.3, fontFace: KO, fontSize: 11, color: GRAY,
      isTextBox: true, margin: 0 });
  }
}

/** 구역 소제목 */
function label(slide, text, x, y, w = 5.4) {
  slide.addText(text, { x, y, w, h: 0.3, fontFace: KO, fontSize: 13, bold: true,
    color: BLUE2, isTextBox: true, margin: 0 });
}

/** 다이어그램 — 잘린 PNG 를 상자 안에 비율 유지로 */
function diagram(slide, file, { x, y, maxW = 12.2, maxH = 4.6 } = {}) {
  const [pw, ph] = SIZES[file];
  const ratio = ph / pw;
  let w = Math.min(maxW, maxH / ratio);
  let h = w * ratio;
  if (h > maxH) { h = maxH; w = h / ratio; }
  slide.addImage({ path: path.join(DIAG, file), x: x ?? (W - w) / 2, y: y ?? 1.95 + (maxH - h) / 2, w, h });
}

// ══════════════════════════════════════════════════ 표지
{
  const s = pres.addSlide();
  s.addShape(pres.ShapeType.rect, { x: 0, y: 0, w: W, h: 0.22, fill: { color: NAVY }, line: { width: 0 } });
  s.addShape(pres.ShapeType.rect, { x: 0, y: H - 0.22, w: W, h: 0.22, fill: { color: NAVY }, line: { width: 0 } });
  s.addText("최신 연구 동향 모니터링 에이전트", { x: 0.9, y: 1.5, w: 10, h: 0.34,
    fontFace: KO, fontSize: 14, bold: true, color: BLUE2, isTextBox: true, margin: 0 });
  s.addText("피드백 반영 보고", { x: 0.9, y: 1.95, w: 11.5, h: 0.95,
    fontFace: KO, fontSize: 40, bold: true, color: NAVY, isTextBox: true, margin: 0 });
  s.addText("논문을 보내는 시스템에서, 반응을 받아 검색 기준을 바꾸고 그 변화를 설명하는 시스템으로",
    { x: 0.9, y: 2.95, w: 11.5, h: 0.4, fontFace: KO, fontSize: 16, color: GRAY, isTextBox: true, margin: 0 });
  const rows = [
    ["대 상", "관심 분야 프로필 4개 — 우리팀 · 로봇·피지컬 AI · AI 에이전트 · 비전 검사·VLM"],
    ["운 영", "매일 05:00 논문 5편 메일 · 월요일 새벽 주간 관리 — 9/16 부터 무인 운영"],
    ["보고 범위", "9/14 받은 피드백 6개의 반영 결과와 실측"],
    ["작 성", "2026. 09. 20"],
  ];
  let y = 3.85;
  for (const [k, v] of rows) {
    s.addShape(pres.ShapeType.rect, { x: 0.9, y, w: 1.55, h: 0.44, fill: { color: NAVY }, line: { width: 0 } });
    s.addText(k, { x: 0.9, y, w: 1.55, h: 0.44, fontFace: KO, fontSize: 11, bold: true,
      color: WHITE, align: "center", valign: "middle", isTextBox: true, margin: 0 });
    s.addText(v, { x: 2.65, y, w: 9.8, h: 0.44, fontFace: KO, fontSize: 11.5, color: "333333",
      valign: "middle", isTextBox: true, margin: 0 });
    y += 0.56;
  }
  s.addNotes("근거: docs/PROGRESS.md §8-121~172, docs/AGENT_PLAN_2026-09-14.md.");
}

// ══════════════════════════════════════════════════ 1. 피드백 → 변경
{
  const s = pres.addSlide();
  head(s, "지난 피드백과 이번 변경", "여섯 항목을 코드에 반영했다. 오늘은 그중 앞의 세 가지를 중심으로 말씀드린다.");

  const main = [
    ["반응을 학습하게", "매일 가중치 자동 조정", "메일 논문마다 1클릭 · 스캔 직전 반영"],
    ["단순 논문 목록", "동향 + 검색 기준 변화", "오늘 · 최근 7일 · 무엇이 왜 바뀌었나"],
    ["키워드 관리를 에이전트처럼", "월요일 자동 점검 · 적용", "바꾼 기준이 그날 아침 검색에"],
  ];
  let y = 1.95;
  for (const [before, after, detail] of main) {
    s.addText(before, { x: 0.5, y, w: 3.5, h: 0.5, fontFace: KO, fontSize: 13.5, color: GRAY,
      valign: "middle", isTextBox: true, margin: 0 });
    s.addText("→", { x: 4.05, y, w: 0.5, h: 0.5, fontFace: KO, fontSize: 14, color: LIGHT,
      align: "center", valign: "middle", isTextBox: true, margin: 0 });
    s.addText(after, { x: 4.6, y, w: 4.3, h: 0.5, fontFace: KO, fontSize: 17, bold: true,
      color: NAVY, valign: "middle", isTextBox: true, margin: 0 });
    s.addText(detail, { x: 9.0, y, w: 3.83, h: 0.5, fontFace: KO, fontSize: 11, color: GRAY,
      valign: "middle", isTextBox: true, margin: 0 });
    s.addShape(pres.ShapeType.line, { x: 0.5, y: y + 0.58, w: W - 1.0, h: 0, line: { color: LIGHT, width: 0.75 } });
    y += 0.78;
  }

  const rest = [
    ["코드를 못 찾으면 끝", "공식 → 저자 연관 → 제3자 → 유사 구현 5단계, 어느 칸인지 메일에 표시", "운영"],
    ["장기 운영이 되는가", "격리 실행 · 판정 뒤 정리 · 링크와 크기 제한 · 메일 부재 경보", "운영"],
    ["과제 SOTA 제안", "논문이 스스로 주장한 문장만 추출하고 미검증으로 표시", "일부"],
  ];
  s.addText("나머지 세 항목", { x: 0.5, y: 4.5, w: 6, h: 0.3, fontFace: KO, fontSize: 11,
    bold: true, color: NAVY, isTextBox: true, margin: 0 });
  let ry = 4.85;
  for (const [k, v, st] of rest) {
    s.addText(k, { x: 0.5, y: ry, w: 3.3, h: 0.32, fontFace: KO, fontSize: 11.5, color: "333333",
      isTextBox: true, margin: 0 });
    s.addText(v, { x: 3.9, y: ry, w: 8.0, h: 0.32, fontFace: KO, fontSize: 11, color: GRAY,
      isTextBox: true, margin: 0 });
    s.addText(st, { x: 11.9, y: ry, w: 0.93, h: 0.32, fontFace: KO, fontSize: 11, bold: true,
      color: st === "일부" ? ORANGE : GRAY, align: "right", isTextBox: true, margin: 0 });
    ry += 0.38;
  }
  s.addText("\"일부\" 는 감추지 않는다 — 공개 리더보드 자료원이 닫혀 논문 자체 주장만 싣는다.",
    { x: 0.5, y: 6.15, w: 8.5, h: 0.3, fontFace: KO, fontSize: 10.5, color: GRAY, isTextBox: true, margin: 0 });
  s.addText([{ text: "프로필 4개", options: { bold: true, color: NAVY, fontSize: 14 } },
             { text: " 무인 운영      ", options: { color: GRAY, fontSize: 11 } },
             { text: "매일 05:00", options: { bold: true, color: NAVY, fontSize: 14 } },
             { text: " 실제 발송      ", options: { color: GRAY, fontSize: 11 } },
             { text: "1,171", options: { bold: true, color: NAVY, fontSize: 14 } },
             { text: " 테스트 통과", options: { color: GRAY, fontSize: 11 } }],
    { x: 0.5, y: 6.65, w: 12.33, h: 0.4, fontFace: KO, valign: "middle", isTextBox: true, margin: 0 });
  s.addNotes("근거 §8-122·123·128·129·132·133·134. Papers with Code 는 2026-09-16 확인 시 huggingface.co/papers 로 리다이렉트되어 리더보드 API 가 없다.");
}

// ══════════════════════════════════════════════════ 2. 실제 메일
{
  const s = pres.addSlide();
  head(s, "실제 메일은 이렇게 바뀌었습니다", "논문 5편만 오던 메일이 동향 · 흐름 · 기준 변화까지 한 통에 담는다.");
  const idx = [["오늘의 동향", "오늘 논문 + 지난 6일 서술을 읽고 쓴 글"],
               ["최근 7일 흐름", "무엇이 늘고 줄었나 — 직전 7일과 비교"],
               ["지난 7일 검색 기준 변화", "그래서 기준을 이렇게 바꿨다 (월요일만)"],
               ["오늘의 신규 논문 5편", "그 기준으로 고른 것"]];
  let y = 2.05;
  let n = 1;
  for (const [t, sub] of idx) {
    s.addShape(pres.ShapeType.ellipse, { x: 0.5, y: y + 0.04, w: 0.34, h: 0.34,
      fill: { color: NAVY }, line: { width: 0 } });
    s.addText(String(n++), { x: 0.5, y: y + 0.04, w: 0.34, h: 0.34, fontFace: "Cambria", fontSize: 11,
      bold: true, color: WHITE, align: "center", valign: "middle", isTextBox: true, margin: 0 });
    s.addText(t, { x: 1.0, y, w: 4.0, h: 0.4, fontFace: KO, fontSize: 15, bold: true,
      color: NAVY, isTextBox: true, margin: 0 });
    s.addText(sub, { x: 1.0, y: y + 0.42, w: 4.2, h: 0.34, fontFace: KO, fontSize: 10.5,
      color: GRAY, isTextBox: true, margin: 0 });
    y += 0.92;
  }
  grounds(s, "매일 나간다", [
    "논문이 0편인 날도 보낸다 — 메일이 안 온 날은 고장 신호다",
    "평문과 HTML 두 판이 같은 값을 읽는다",
    "제목은 원문 옆에 한국어를 병기한다 (한 통에 호출 한 번)",
  ], 0.5, 5.85, 5.4);
  // 실제 화면은 발표 때 직접 보여 준다 — 그림으로 흉내 내지 않는다(2026-09-20 사용자 결정).
  s.addShape(pres.ShapeType.rect, { x: 6.2, y: 1.95, w: 6.63, h: 5.05,
    fill: { color: WHITE }, line: { color: LIGHT, width: 0.75 } });
  s.addText("실제 메일 화면", { x: 6.2, y: 4.3, w: 6.63, h: 0.4, fontFace: KO, fontSize: 12,
    color: LIGHT, align: "center", isTextBox: true, margin: 0 });
  s.addNotes("이 자리에서 실제 받은 메일을 그대로 보여 준다.");
}

// ══════════════════════════════════════════════════ 3. 반응 → 검색
{
  const s = pres.addSlide();
  head(s, "반응이 다음 검색에 반영됩니다", "서로 다른 논문의 반응이 쌓인 경우에만 가중치가 움직인다.");
  s.addText("더 보고 싶음  ──  반응 누적  ──  가중치 이동  ──  다음 날 검색",
    { x: 0.5, y: 1.95, w: 7.6, h: 0.44, fontFace: KO, fontSize: 13, color: GRAY,
      valign: "middle", isTextBox: true, margin: 0 });
  dumbbell(s, { x: 0.5, y: 2.75, w: 7.6, caption: "defect detection",
    before: "1.00", after: "1.80", note: "서로 다른 논문 2편에 \"더 보고 싶음\" — 9/14~21 실측" });
  grounds(s, "변화 제어", [
    "하루 최대 ±0.1 — 클릭 한 번에 검색이 뒤집히지 않는다",
    "서로 다른 논문 2편 이상 — 한 편은 그 논문이 좋았다는 뜻이지 키워드가 좋다는 뜻이 아니다",
    "반감기 90일 — 오래된 취향은 서서히 잊는다",
    "사용자가 정한 초기 키워드는 삭제하지 않는다 (내려가기만 한다)",
    "모든 변경은 revision 으로 기록 — 운영 화면에서 그 시점으로 되돌린다",
  ], 0.5, 4.5, 7.6);
  s.addShape(pres.ShapeType.line, { x: 8.9, y: 1.95, w: 0, h: 4.9, line: { color: LIGHT, width: 0.75 } });
  s.addText("목표값을 정하고 그쪽으로 조금씩", { x: 9.3, y: 2.0, w: 3.5, h: 0.34, fontFace: KO,
    fontSize: 12, bold: true, color: NAVY, isTextBox: true, margin: 0 });
  const calc = [["신호", "(긍정 − 부정) / (긍정 + 부정 + 4)"],
                ["목표", "기준 + 0.8 × 신호   (0.35 ~ 2.0)"],
                ["이동", "하루 |Δ| ≤ 0.1 만큼 목표 쪽으로"],
                ["적용", "매일 05:00, 검색 시작 전"]];
  let cy = 2.5;
  for (const [k, v] of calc) {
    s.addText(k, { x: 9.3, y: cy, w: 0.8, h: 0.3, fontFace: KO, fontSize: 10.5, color: GRAY,
      isTextBox: true, margin: 0 });
    s.addText(v, { x: 10.1, y: cy, w: 2.73, h: 0.3, fontFace: KO, fontSize: 10.5, color: "333333",
      isTextBox: true, margin: 0 });
    cy += 0.36;
  }
  s.addText("모델을 쓰지 않는다.\n값을 바꾸는 것은 언제나 코드다.",
    { x: 9.3, y: 4.2, w: 3.53, h: 0.7, fontFace: KO, fontSize: 11.5, color: NAVY,
      lineSpacing: 18, isTextBox: true, margin: 0, valign: "top" });
  s.addNotes("feedback_weights.py. 같은 반응을 두 번 더하지 않는다 — 기준선이 밀려 같은 클릭이 반복 적용되던 결함을 실측으로 잡았다(1.337 → 1.657).");
}

// ══════════════════════════════════════════════════ 4. 하루 → 흐름
{
  const s = pres.addSlide();
  head(s, "하루치 스냅숏에서 흐름으로", "그날 논문만 보고 쓰던 동향이 지난 일주일을 함께 본다.");
  const cmp = [["이전", "그날 논문 13편의 제목과 초록만", GRAY],
               ["현재", "오늘 논문 + 지난 6일 서술 + 최근 7일 편수 비교", NAVY]];
  let y = 1.95;
  for (const [tag, text, tone] of cmp) {
    s.addText(tag, { x: 0.5, y, w: 0.9, h: 0.42, fontFace: KO, fontSize: 11, bold: true,
      color: tone, valign: "middle", isTextBox: true, margin: 0 });
    s.addText(text, { x: 1.45, y, w: 6.6, h: 0.42, fontFace: KO, fontSize: tone === NAVY ? 15 : 13,
      bold: tone === NAVY, color: tone === NAVY ? NAVY : GRAY, valign: "middle", isTextBox: true, margin: 0 });
    s.addShape(pres.ShapeType.line, { x: 0.5, y: y + 0.5, w: 7.57, h: 0, line: { color: LIGHT, width: 0.75 } });
    y += 0.68;
  }
  grounds(s, "무엇을 보고 쓰는가", [
    "오늘 논문 + 지난 6일 서술 = 7일 — 옆의 수치 창과 같은 기간을 본다",
    "서술은 프로필·날짜별로 쌓인다 — 오늘 쓴 글이 내일의 입력이 된다",
    "자리에 못 든 후보 수백 편의 용어도 집계로 남긴다 (9/18 실측 431편)",
    "직전 구간에 관측이 없으면 증감을 만들지 않는다 — 0 → N 은 급증이 아니다",
    "수치는 Python 이 DB 에서 세고, 모델은 늘어난 \"말\"만 맥락으로 받는다",
  ], 0.5, 3.5, 7.57);
  s.addText("최근 7일 흐름", { x: 8.6, y: 1.95, w: 4.2, h: 0.34, fontFace: KO, fontSize: 13,
    bold: true, color: NAVY, isTextBox: true, margin: 0 });
  s.addText("관련 논문 611편  ·  직전 579편", { x: 8.6, y: 2.32, w: 4.2, h: 0.3, fontFace: KO,
    fontSize: 11, color: GRAY, isTextBox: true, margin: 0 });
  const rows = [["▲", "defect detection", "+23", BLUE], ["▲", "digital twin", "+9", BLUE],
                ["▼", "world model", "−15", ORANGE], ["▼", "vision-language model", "−12", ORANGE]];
  let ry = 2.85;
  for (const [arrow, kw, d, tone] of rows) {
    s.addText(arrow, { x: 8.6, y: ry, w: 0.28, h: 0.32, fontFace: KO, fontSize: 10,
      color: tone, isTextBox: true, margin: 0 });
    s.addText(kw, { x: 8.92, y: ry, w: 2.8, h: 0.32, fontFace: KO, fontSize: 12.5,
      color: "333333", valign: "middle", isTextBox: true, margin: 0 });
    s.addText(d, { x: 11.7, y: ry, w: 1.13, h: 0.32, fontFace: KO, fontSize: 14, bold: true,
      color: tone, align: "right", valign: "middle", isTextBox: true, margin: 0 });
    ry += 0.44;
  }
  s.addText("이 프로필이 발견한 편수이지 분야 전체 발표량이 아니다.",
    { x: 8.6, y: 4.75, w: 4.2, h: 0.5, fontFace: KO, fontSize: 10.5, color: GRAY,
      isTextBox: true, margin: 0, valign: "top" });
  s.addNotes("trend_report.window_movement · narrative_store · observation_signals.reserve_terms. 근거 §8-161·162.");
}

// ══════════════════════════════════════════════════ 5. 월요일 주간 관리
{
  const s = pres.addSlide();
  head(s, "월요일마다 검색 기준을 점검합니다", "금요일에 바꾸면 주말을 헛돌았다 — 월요일 새벽으로 옮겨 그날 검색에 바로 쓰인다.");
  s.addText("지난 7일 관측  ──  제안  ──  검증  ──  적용  ──  같은 날 검색",
    { x: 0.5, y: 1.95, w: 7.6, h: 0.44, fontFace: KO, fontSize: 13.5, bold: true, color: NAVY,
      valign: "middle", isTextBox: true, margin: 0 });
  s.addText("                             Claude      Codex · Python",
    { x: 0.5, y: 2.34, w: 7.6, h: 0.3, fontFace: KO, fontSize: 10.5, color: GRAY, isTextBox: true, margin: 0 });
  grounds(s, "무엇을 보고 제안하는가", [
    "28일 관측 · 반응 집계 · 최근 7일 상승어 · 자리 밖 후보 용어",
    "적용 전 재채점 — 지난 4주 관측에 이 변경을 대 보면 몇 편이 들고 나는지 먼저 잰다",
    "검색어를 바꾸면 격리 검색으로 실제로 다시 찾아본다 (적격 145 → 157편, 잃은 논문 0)",
    "주간 관리가 실패해도 아침 메일은 나간다 — 같은 실행 안에서 순서대로 돈다",
  ], 0.5, 3.0, 7.57);
  s.addText("9/21 월요일에 실제로 적용된 것", { x: 8.6, y: 1.95, w: 4.2, h: 0.34, fontFace: KO,
    fontSize: 13, bold: true, color: NAVY, isTextBox: true, margin: 0 });
  const facts = [["가중치", "defect detection", "1.00 → 1.80"],
                 ["가중치", "LLM agent", "1.00 → 1.15"],
                 ["검색어 추가", "defect detection", "신규"],
                 ["적격 논문", "격리 검색 재현", "145 → 157"]];
  let fy = 2.45;
  for (const [k, kw, v] of facts) {
    s.addText(k, { x: 8.6, y: fy, w: 4.2, h: 0.26, fontFace: KO, fontSize: 10, color: GRAY,
      isTextBox: true, margin: 0 });
    s.addText(kw, { x: 8.6, y: fy + 0.26, w: 2.6, h: 0.3, fontFace: KO, fontSize: 12,
      color: "333333", isTextBox: true, margin: 0 });
    s.addText(v, { x: 11.2, y: fy + 0.22, w: 1.63, h: 0.34, fontFace: KO, fontSize: 13, bold: true,
      color: BLUE, align: "right", isTextBox: true, margin: 0 });
    s.addShape(pres.ShapeType.line, { x: 8.6, y: fy + 0.64, w: 4.23, h: 0, line: { color: LIGHT, width: 0.75 } });
    fy += 0.78;
  }
  s.addText("이 변경의 사유는 모델이 쓴 문장 그대로 메일에 실린다.",
    { x: 8.6, y: 5.75, w: 4.2, h: 0.5, fontFace: KO, fontSize: 10.5, color: GRAY,
      isTextBox: true, margin: 0, valign: "top" });
  s.addNotes("run_daily_scan.sh 가 월요일(KST)이면 db_retention → agent_maintenance 를 먼저 돌리고 바로 일일 스캔으로 잇는다. 같은 flock 안이라 겹치지 않는다. 회고가 아니라 오늘 논문을 고른 근거다.");
}

// ══════════════════════════════════════════════════ 6. 제안은 모델, 적용은 코드
{
  const s = pres.addSlide();
  head(s, "제안은 모델이, 적용은 코드가", "두 모델이 동의해도 근거가 없으면 통과하지 못한다.");
  s.addText("LLM 은 제안만 하고, 실제 변경은 코드가 검증한 뒤에 적용한다",
    { x: 0.5, y: 2.1, w: 12.33, h: 0.6, fontFace: KO, fontSize: 21, bold: true, color: NAVY,
      isTextBox: true, margin: 0 });
  s.addText("Claude 제안   →   Codex 검토   →   Python 검증 · 적용",
    { x: 0.5, y: 2.85, w: 7.6, h: 0.36, fontFace: KO, fontSize: 13, color: GRAY,
      isTextBox: true, margin: 0 });
  grounds(s, "코드가 강제하는 것 — 프롬프트가 아니라 검증기가 막는다", [
    "좋다고 반응한 논문에 그 용어가 실제로 있어야 키워드를 올린다",
    "제외어는 관심 밖으로 표시된 논문 2편 이상일 때만 추가한다",
    "사용자가 정한 키워드는 어느 경우에도 삭제하지 않는다",
    "증거 id 가 실재하는지, 표기가 변형되지 않았는지, 우산어가 아닌지 검사한다",
    "출력에 시크릿 의심 문자열이 있으면 그 제안을 통째로 버린다",
  ], 0.5, 3.6, 7.57);
  const checks = ["근거 없는 키워드 추가 차단", "사용자 초기 키워드 삭제 금지",
                  "모든 변경 revision 기록", "두 CLI 모두 도구를 끈 채 실행", "한 번 600초 상한"];
  s.addText("적용 전에 통과해야 하는 것", { x: 8.6, y: 3.6, w: 4.2, h: 0.3, fontFace: KO,
    fontSize: 11, bold: true, color: NAVY, isTextBox: true, margin: 0 });
  let cy = 4.04;
  for (const c of checks) {
    s.addText("✓", { x: 8.6, y: cy, w: 0.3, h: 0.3, fontFace: KO, fontSize: 11, bold: true,
      color: GREEN, isTextBox: true, margin: 0 });
    s.addText(c, { x: 8.95, y: cy, w: 3.88, h: 0.3, fontFace: KO, fontSize: 11, color: "333333",
      isTextBox: true, margin: 0 });
    cy += 0.36;
  }
  s.addNotes("agent_maintenance.validate 가 강제한다 — 두 모델이 동의해도 통과 못 한다. 상세 흐름은 부록 A.");
}

// ══════════════════════════════════════════════════ 7. 코드 재현
{
  const s = pres.addSlide();
  head(s, "코드를 못 찾아도 한 칸 아래로", "공식이 없으면 비슷한 구현까지 찾되, 공식이라고 쓰지 않는다.");
  diagram(s, "03-code-ladder-steps.png", { y: 2.0, maxW: 12.2, maxH: 2.9 });
  grounds(s, "찾은 뒤", [
    "찾은 칸을 메일에 그대로 적는다 — 공식이 아닌 코드를 공식이라고 쓰지 않는다",
    "유사 구현은 보여 주기만 하고 격리 실행에 태우지 않는다 (\"이 논문 재현 아님\" 명시)",
    "실행은 네트워크 차단 · 권한 제거 · 읽기 전용 · 시간과 메모리 상한 아래에서",
    "판정과 실행 로그를 DB 에 먼저 저장하고, 그 뒤에 이미지 · 빌더 · 복제본을 지운다",
  ], 0.5, 5.15, 7.57);
  s.addText("못 찾으면 만들어 넣지 않는다", { x: 8.6, y: 5.3, w: 4.2, h: 0.4, fontFace: KO,
    fontSize: 14, bold: true, color: NAVY, isTextBox: true, margin: 0 });
  s.addText("\"없음\" 을 그대로 적는다. 그전엔 못 찾으면 그냥 비어 있었다 — 9/14 피드백이 \"비슷한 거라도 반드시\" 였다.",
    { x: 8.6, y: 5.75, w: 4.23, h: 0.8, fontFace: KO, fontSize: 10.5, color: GRAY,
      isTextBox: true, margin: 0, valign: "top" });
  s.addNotes("요약이 끝난 논문 한 편마다 돌고 결과는 한 달 캐시. 유사 구현은 그 논문이 걸린 핵심 키워드로 GitHub 검색, 링크 모음·소개 페이지는 제외.");
}

// ══════════════════════════════════════════════════ 8. 디스크
{
  const s = pres.addSlide();
  head(s, "자동 재현을 돌리니 C 드라이브가 찼다", "원인은 지우지 않는 설계였다 — 코드를 고치고, 이미 쌓인 것은 확인하며 지웠다.");
  s.addText("빌드 캐시", { x: 0.5, y: 2.05, w: 5.3, h: 0.34, fontFace: KO, fontSize: 13,
    color: GRAY, isTextBox: true, margin: 0 });
  s.addText("143.03 GB", { x: 0.5, y: 2.45, w: 5.3, h: 0.9, fontFace: "Cambria", fontSize: 46,
    bold: true, color: GRAY, isTextBox: true, margin: 0 });
  s.addText("↓", { x: 0.5, y: 3.4, w: 5.3, h: 0.45, fontFace: KO, fontSize: 20,
    color: LIGHT, isTextBox: true, margin: 0 });
  s.addText("186.8 MB", { x: 0.5, y: 3.9, w: 5.3, h: 0.9, fontFace: "Cambria", fontSize: 46,
    bold: true, color: BLUE, isTextBox: true, margin: 0 });
  s.addText("재현 기록 132건은 그대로 남았다.", { x: 0.5, y: 4.85, w: 5.3, h: 0.34, fontFace: KO,
    fontSize: 11, color: GRAY, isTextBox: true, margin: 0 });
  s.addText("무엇이 쌓였나", { x: 6.4, y: 2.05, w: 6.43, h: 0.3, fontFace: KO, fontSize: 11,
    bold: true, color: NAVY, isTextBox: true, margin: 0 });
  const before = [["빌드 캐시", "시도마다 공용 빌더에 남았다", "143.03 GB"],
                  ["성공한 복제본", "\"성공하면 보관\" 이 기본이었다", "3.17 GB"],
                  ["오래된 복제본", "지난 실행이 남긴 것", "1.04 GB"]];
  let by = 2.45;
  for (const [k, why, size] of before) {
    s.addText(k, { x: 6.4, y: by, w: 1.9, h: 0.3, fontFace: KO, fontSize: 11.5, color: "333333",
      isTextBox: true, margin: 0 });
    s.addText(why, { x: 8.3, y: by, w: 3.0, h: 0.3, fontFace: KO, fontSize: 10.5, color: GRAY,
      isTextBox: true, margin: 0 });
    s.addText(size, { x: 11.3, y: by, w: 1.53, h: 0.3, fontFace: KO, fontSize: 11.5, bold: true,
      color: GRAY, align: "right", isTextBox: true, margin: 0 });
    s.addShape(pres.ShapeType.line, { x: 6.4, y: by + 0.34, w: 6.43, h: 0, line: { color: LIGHT, width: 0.75 } });
    by += 0.44;
  }
  grounds(s, "조치", [
    "시도마다 전용 빌더 — 다른 작업의 캐시를 건드리지 않고, 끝나면 그 빌더째 제거",
    "판정 뒤 이미지 · 컨테이너 · 복제본 전부 삭제 — 실패 · 타임아웃 · 예외에서도",
    "지우기 전에 실행 로그와 DB 결과를 먼저 저장. 저장이 실패하면 복제본을 남긴다",
    "전역 삭제는 하지 않는다 — prune · 볼륨 삭제 금지, 이번 시도가 만든 것만 지운다",
  ], 6.4, 4.0, 6.43);
  s.addText("크기는 지우기 전 실측값이다. 캐시 143.03 GB 는 두 차례(43.47 + 99.56) 합계.",
    { x: 6.4, y: 5.6, w: 6.43, h: 0.3, fontFace: KO, fontSize: 10, color: LIGHT, isTextBox: true, margin: 0 });
  s.addNotes("운영 DB 는 백업 후 integrity_check=ok, 삭제 목록은 repro-cleanup-20260918.json 에 남겼다.");
}

// ══════════════════════════════════════════════════ 9. 정리
{
  const s = pres.addSlide();
  head(s, "정리와 남은 것", "네 프로필이 무인으로 돌고 있고, 못 한 것은 못 했다고 적어 두었다.");
  s.addText("돌아가고 있는 것", { x: 0.5, y: 1.95, w: 6, h: 0.3, fontFace: KO, fontSize: 12,
    bold: true, color: NAVY, isTextBox: true, margin: 0 });
  const done = [["프로필 4개 무인 운영", "매일 05:00 메일 5편 · 월요일 새벽 주간 관리가 스캔 앞에"],
                ["동향이 쌓이고 이어진다", "오늘 논문 + 최근 7일 창 + 지난 6일 서술"],
                ["검색 기준 변화를 보고한다", "무엇이 · 얼마나 · 누가 바꿨는지 월요일 메일에"],
                ["모든 자동 변경을 되돌릴 수 있다", "revision 기록 — 운영 화면에서 그 시점으로"],
                ["외부 입력은 신뢰하지 않는다", "격리해서 처리하고, 못 막는 것은 적어 둔다"]];
  let y = 2.35;
  for (const [k, v] of done) {
    s.addText(k, { x: 0.5, y, w: 3.4, h: 0.32, fontFace: KO, fontSize: 12, bold: true,
      color: "333333", isTextBox: true, margin: 0 });
    s.addText(v, { x: 3.95, y, w: 4.12, h: 0.32, fontFace: KO, fontSize: 10.5, color: GRAY,
      isTextBox: true, margin: 0 });
    s.addShape(pres.ShapeType.line, { x: 0.5, y: y + 0.36, w: 7.57, h: 0, line: { color: LIGHT, width: 0.75 } });
    y += 0.5;
  }
  s.addText("남은 것 · 하지 않은 것", { x: 8.6, y: 1.95, w: 4.23, h: 0.3, fontFace: KO, fontSize: 12,
    bold: true, color: NAVY, isTextBox: true, margin: 0 });
  const todo = [["분야별 SOTA 순위 추적", "공개 자료원이 닫혔다 — 논문 자체 주장만"],
                ["사설 IP 판정 · 인젝션 차단", "미구현으로 보안 점검표에 적었다"],
                ["운영 화면 시각화", "메일은 훑기용, 화면은 탐색용"],
                ["반응이 쌓인 뒤의 검증", "가중치가 실제로 분야를 따라가는지"]];
  let ty = 2.35;
  for (const [k, v] of todo) {
    s.addText(k, { x: 8.6, y: ty, w: 4.23, h: 0.3, fontFace: KO, fontSize: 12, bold: true,
      color: ORANGE, isTextBox: true, margin: 0 });
    s.addText(v, { x: 8.6, y: ty + 0.3, w: 4.23, h: 0.3, fontFace: KO, fontSize: 10.5, color: GRAY,
      isTextBox: true, margin: 0 });
    ty += 0.78;
  }
  s.addText("실측하지 않은 값은 \"미실측\", 실패는 실패로 적는다 — 그래야 다음 판단을 이 기록 위에서 할 수 있다.",
    { x: 0.5, y: 5.6, w: 12.33, h: 0.34, fontFace: KO, fontSize: 11.5, color: NAVY, isTextBox: true, margin: 0 });
  s.addText("근거 기록 : docs/PROGRESS.md §8-121~172", { x: 0.5, y: 6.1, w: 12.33, h: 0.3,
    fontFace: KO, fontSize: 10, color: LIGHT, isTextBox: true, margin: 0 });
  s.addNotes("다음 확인: 반응이 쌓이면 가중치가 실제로 분야를 따라가는지 수치로 본다.");
}

// ══════════════════════════════════════════════════ 부록 A
{
  const s = pres.addSlide();
  appendix(s, "월요일 주간 관리 상세", "Claude 제안 → Codex 판정 → Python 검증·적용, 그리고 DB 정리");
  diagram(s, "05-weekly-agent.png", { y: 1.85, maxW: 12.2, maxH: 4.9 });
  s.addNotes("DB 보존: 후보 90일 · 관측/실행/논문 180일.");
}

// ══════════════════════════════════════════════════ 부록 B
{
  const s = pres.addSlide();
  appendix(s, "외부 입력이 지나는 다섯 관문", "논문 · 링크 · 저장소는 신뢰하지 않는 입력으로 다룬다 — 못 막는 것도 적어 두었다");
  diagram(s, "04-security-layers.png", { y: 1.85, maxW: 12.2, maxH: 4.9 });
  s.addNotes("미구현으로 기록한 것 — DNS 해석 뒤 사설 IP 판정, 논문 본문 인젝션은 표시만 하고 차단하지 않는다. 시크릿(.env)은 어느 모델에도 넣지 않는다.");
}

pres.writeFile({ fileName: OUT }).then(() => console.log("wrote " + OUT));
