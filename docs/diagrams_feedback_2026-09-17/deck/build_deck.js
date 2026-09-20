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

/** 머리: 번호 상자 · 제목 · 배너 한 줄. 쪽 번호와 상단 머리말은 두지 않는다. */
function head(slide, title, banner) {
  const no = String(++sectionNo);
  slide.addShape(pres.ShapeType.rect, { x: 0.5, y: 0.34, w: 0.6, h: 0.6,
    fill: { color: NAVY }, line: { width: 0 } });
  slide.addText(no, { x: 0.5, y: 0.34, w: 0.6, h: 0.6, fontFace: "Cambria", fontSize: 22,
    bold: true, color: WHITE, align: "center", valign: "middle", isTextBox: true, margin: 0 });
  slide.addText(title, { x: 1.28, y: 0.34, w: 11.5, h: 0.6, fontFace: KO, fontSize: 26,
    bold: true, color: NAVY, valign: "middle", isTextBox: true, margin: 0 });
  slide.addShape(pres.ShapeType.rect, { x: 0, y: 1.16, w: W, h: 0.52,
    fill: { color: BLUE }, line: { width: 0 } });
  slide.addText(banner, { x: 0.5, y: 1.16, w: W - 1.0, h: 0.52, fontFace: KO, fontSize: 15,
    bold: true, color: WHITE, align: "center", valign: "middle", isTextBox: true, margin: 0 });
}

/** 꼬리 한 줄 — 배너가 이미 그 장의 결론이라 여기는 보조다. 없으면 안 쓴다.
 *  2026-09-20: 네이비 결론 바를 없앴다. 배너와 결론 바가 한 장에서 두 문장을 말해
 *  읽을 것이 늘었고, 그 문장들은 슬라이드보다 발표자가 말하기 좋은 것이었다(대본으로 옮겼다). */
function footnote(slide, text, y = 6.45) {
  slide.addText(text, { x: 0.5, y, w: W - 1.0, h: 0.34, fontFace: KO, fontSize: 11.5,
    color: GRAY, isTextBox: true, margin: 0 });
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

/** 메일 지면 — 실제 자료로 그린 축소판. 여러 장에서 쓴다. */
function mailPanel(slide, { x, y, w, h, title, draw }) {
  slide.addShape(pres.ShapeType.rect, { x, y, w, h, fill: { color: WHITE }, line: { color: ROW, width: 1 } });
  slide.addText(title, { x: x + 0.3, y: y + 0.16, w: w - 0.6, h: 0.32, fontFace: KO,
    fontSize: 14, bold: true, color: NAVY, isTextBox: true, margin: 0 });
  draw(x, y);
}

/** 부록 머리 — 번호를 매기지 않는다. 본 발표에서 뺀 상세가 여기 있다. */
function appendix(slide, title, banner) {
  slide.addText(title, { x: 0.5, y: 0.34, w: 12.3, h: 0.6, fontFace: KO, fontSize: 22,
    bold: true, color: NAVY, valign: "middle", isTextBox: true, margin: 0 });
  slide.addShape(pres.ShapeType.rect, { x: 0, y: 1.16, w: W, h: 0.52, fill: { color: BLUE }, line: { width: 0 } });
  slide.addText(banner, { x: 0.5, y: 1.16, w: W - 1.0, h: 0.52, fontFace: KO, fontSize: 14,
    bold: true, color: WHITE, align: "center", valign: "middle", isTextBox: true, margin: 0 });
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
    s.addShape(pres.ShapeType.rect, { x: 0.9, y, w: 1.55, h: 0.44, fill: { color: BLUE }, line: { width: 0 } });
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
  head(s, "지난 피드백과 이번 변경", "오늘 말씀드릴 것은 이 세 가지입니다");
  const cards = [
    ["반응을 학습하게", "매일 가중치 자동 조정"],
    ["단순 논문 목록", "동향 + 검색 기준 변화"],
    ["키워드 관리를 에이전트처럼", "월요일 자동 점검 · 적용"],
  ];
  let cx = 0.5;
  for (const [before, after] of cards) {
    s.addShape(pres.ShapeType.rect, { x: cx, y: 2.35, w: 4.11, h: 2.95, fill: { color: PANEL }, line: { width: 0 } });
    s.addText(before, { x: cx + 0.35, y: 2.75, w: 3.4, h: 0.5, fontFace: KO, fontSize: 14,
      color: GRAY, isTextBox: true, margin: 0, valign: "top" });
    s.addText("↓", { x: cx + 0.35, y: 3.4, w: 3.4, h: 0.4, fontFace: KO, fontSize: 18,
      color: BLUE, isTextBox: true, margin: 0 });
    s.addText(after, { x: cx + 0.35, y: 4.0, w: 3.4, h: 0.95, fontFace: KO, fontSize: 19, bold: true,
      color: NAVY, isTextBox: true, margin: 0, valign: "top" });
    cx += 4.31;
  }
  footnote(s, "코드 탐색 · 장기 운영 · SOTA 대응도 함께 반영했다", 5.75);
  footnote(s, "프로필 4개 무인 운영     매일 05:00 실제 발송     테스트 1,171 통과", 6.3);
  s.addNotes("받은 피드백 6개 중 5개는 운영 중이고 SOTA 순위표만 일부다 — 공개 리더보드 자료원(Papers with Code)이 2026-09-16 확인 시 닫혀 있어 논문이 스스로 주장한 문장만 미검증 표시로 싣는다. 근거 §8-122·123·128·129·132·133·134.");
}

// ══════════════════════════════════════════════════ 2. 결과 — 메일
{
  const s = pres.addSlide();
  head(s, "실제 메일은 이렇게 바뀌었습니다", "논문 5편만 오던 메일이 동향 · 흐름 · 기준 변화까지 한 통에 담는다");
  const steps = ["오늘의 동향", "최근 7일 흐름", "지난 7일 검색 기준 변화", "오늘의 신규 논문 5편"];
  let y = 2.15;
  for (let i = 0; i < steps.length; i++) {
    s.addShape(pres.ShapeType.rect, { x: 1.1, y, w: 5.2, h: 0.78,
      fill: { color: i === steps.length - 1 ? NAVY : PANEL }, line: { width: 0 } });
    s.addText(steps[i], { x: 1.1, y, w: 5.2, h: 0.78, fontFace: KO, fontSize: 17, bold: true,
      color: i === steps.length - 1 ? WHITE : NAVY, align: "center", valign: "middle", isTextBox: true, margin: 0 });
    if (i < steps.length - 1) {
      s.addText("↓", { x: 3.35, y: y + 0.78, w: 0.7, h: 0.26, fontFace: KO, fontSize: 14,
        color: BLUE, align: "center", isTextBox: true, margin: 0 });
    }
    y += 1.05;
  }
  // 오른쪽은 지면 모양만 — 값은 뒤 슬라이드에서 다시 나오므로 여기서 숫자를 보여 주지 않는다.
  s.addShape(pres.ShapeType.rect, { x: 7.4, y: 2.15, w: 5.43, h: 3.93,
    fill: { color: WHITE }, line: { color: ROW, width: 1 } });
  s.addText("연구 동향 브리핑", { x: 7.7, y: 2.35, w: 4.8, h: 0.34, fontFace: KO, fontSize: 15,
    bold: true, color: NAVY, isTextBox: true, margin: 0 });
  let my = 2.85;
  for (const t of steps) {
    s.addText(t, { x: 7.7, y: my, w: 4.8, h: 0.3, fontFace: KO, fontSize: 12.5, bold: true,
      color: "333333", isTextBox: true, margin: 0 });
    for (let k = 0; k < 3; k++) {
      s.addShape(pres.ShapeType.rect, { x: 7.7, y: my + 0.38 + k * 0.17, w: (k === 2 ? 2.6 : 4.5), h: 0.08,
        fill: { color: ROW }, line: { color: ROW, width: 0 } });
    }
    my += 0.76;
  }
  footnote(s, "받는 사람은 논문 목록이 아니라 \"무엇이 뜨는가 → 그래서 기준을 어떻게 바꿨나 → 그 기준으로 무엇을 골랐나\"를 받는다", 6.4);
  s.addNotes("실제 발송 메일의 절 구성이다. 값은 뒤 슬라이드에서 보여 준다.");
}

// ══════════════════════════════════════════════════ 3. 반응 → 검색
{
  const s = pres.addSlide();
  head(s, "반응이 다음 검색을 바꾼다", "클릭 한 번에 흔들리지 않는다 — 서로 다른 논문의 반응이 쌓여야 움직인다");
  const chain = ["더 보고 싶음", "반응 누적", "가중치 변화", "다음 검색"];
  let y = 2.2;
  for (let i = 0; i < chain.length; i++) {
    s.addShape(pres.ShapeType.rect, { x: 0.9, y, w: 5.0, h: 0.74, fill: { color: PANEL }, line: { width: 0 } });
    s.addText(chain[i], { x: 0.9, y, w: 5.0, h: 0.74, fontFace: KO, fontSize: 17, bold: true,
      color: NAVY, align: "center", valign: "middle", isTextBox: true, margin: 0 });
    if (i < chain.length - 1) {
      s.addText("↓", { x: 3.05, y: y + 0.74, w: 0.7, h: 0.26, fontFace: KO, fontSize: 14,
        color: BLUE, align: "center", isTextBox: true, margin: 0 });
    }
    y += 1.0;
  }
  s.addShape(pres.ShapeType.rect, { x: 7.2, y: 2.55, w: 5.63, h: 2.75, fill: { color: PANEL2 }, line: { color: ROW, width: 1 } });
  s.addText("defect detection", { x: 7.6, y: 3.0, w: 4.9, h: 0.4, fontFace: KO, fontSize: 17,
    color: GRAY, isTextBox: true, margin: 0 });
  s.addText("1.00  →  1.80", { x: 7.6, y: 3.55, w: 4.9, h: 1.0, fontFace: "Cambria", fontSize: 44,
    bold: true, color: BLUE, isTextBox: true, margin: 0 });
  footnote(s, "급격한 변화를 막는다 — 서로 다른 논문의 반응이 쌓여야 하고, 하루 변화량에 상한이 있다", 6.4);
  s.addNotes("하루 |Δ|≤0.1, 서로 다른 논문 2편 이상, 반감기 90일, 사용자가 정한 최초 키워드는 삭제 금지. 모든 자동 변경은 revision 으로 남아 운영 화면에서 되돌릴 수 있다. feedback_weights.py — signal=(긍정−부정)/(긍정+부정+4), target=base+0.8·signal(0.35~2.0).");
}

// ══════════════════════════════════════════════════ 4. 하루에서 흐름으로
{
  const s = pres.addSlide();
  head(s, "하루치 스냅숏에서 흐름으로", "그날 논문만 보고 쓰던 동향이 지난 일주일을 함께 본다");
  const cols = [["이전", "오늘 논문만 본다", ["그날 13편의 제목과 초록", "무엇이 늘었는지 말할 수 없다"], LIGHT],
                ["현재", "오늘 + 지난 일주일", ["오늘 논문 + 지난 6일 서술", "최근 7일 편수를 직전 7일과 비교"], BLUE]];
  let y = 2.35;
  for (const [tag, title, items, tone] of cols) {
    s.addShape(pres.ShapeType.rect, { x: 0.5, y, w: 6.1, h: 1.7,
      fill: { color: tone === BLUE ? PANEL : PANEL2 }, line: { color: tone === BLUE ? BLUE : ROW, width: tone === BLUE ? 1.25 : 0.75 } });
    s.addShape(pres.ShapeType.rect, { x: 0.5, y, w: 0.86, h: 0.42, fill: { color: tone }, line: { width: 0 } });
    s.addText(tag, { x: 0.5, y, w: 0.86, h: 0.42, fontFace: KO, fontSize: 11, bold: true,
      color: WHITE, align: "center", valign: "middle", isTextBox: true, margin: 0 });
    s.addText(title, { x: 1.55, y: y + 0.04, w: 4.8, h: 0.36, fontFace: KO, fontSize: 15, bold: true,
      color: tone === BLUE ? NAVY : GRAY, valign: "middle", isTextBox: true, margin: 0 });
    let iy = y + 0.62;
    for (const it of items) {
      s.addText("· " + it, { x: 0.8, y: iy, w: 5.6, h: 0.4, fontFace: KO, fontSize: 12,
        color: "333333", isTextBox: true, margin: 0, valign: "top" });
      iy += 0.46;
    }
    y += 2.0;
  }
  s.addShape(pres.ShapeType.rect, { x: 6.95, y: 2.35, w: 5.88, h: 3.35, fill: { color: WHITE }, line: { color: ROW, width: 1 } });
  s.addText("최근 7일 흐름", { x: 7.3, y: 2.55, w: 5.2, h: 0.34, fontFace: KO, fontSize: 15,
    bold: true, color: NAVY, isTextBox: true, margin: 0 });
  const rows = [["▲", "defect detection", "+23", BLUE], ["▲", "digital twin", "+9", BLUE],
                ["▼", "world model", "−15", ORANGE], ["▼", "vision-language model", "−12", ORANGE]];
  let ry = 3.15;
  for (const [arrow, kw, d, tone] of rows) {
    s.addText(arrow, { x: 7.35, y: ry, w: 0.3, h: 0.34, fontFace: KO, fontSize: 12,
      color: tone, isTextBox: true, margin: 0 });
    s.addText(kw, { x: 7.7, y: ry, w: 3.4, h: 0.34, fontFace: KO, fontSize: 14,
      color: NAVY, valign: "middle", isTextBox: true, margin: 0 });
    s.addText(d, { x: 11.1, y: ry, w: 1.4, h: 0.34, fontFace: KO, fontSize: 16, bold: true,
      color: tone, align: "right", valign: "middle", isTextBox: true, margin: 0 });
    ry += 0.6;
  }
  footnote(s, "직전 구간에 관측이 없으면 증감을 만들지 않는다 — 0 → N 은 급증이 아니다", 6.1);
  s.addNotes("오늘 논문 + 지난 6일 서술 = 7일. 옆의 수치 창도 7일이라 산문과 숫자가 같은 기간을 본다. 편수는 이 프로필이 발견한 수이지 분야 전체 발표량이 아니다.");
}

// ══════════════════════════════════════════════════ 5. 월요일 주간 관리
{
  const s = pres.addSlide();
  head(s, "월요일마다 검색 기준을 점검한다", "금요일에 바꾸면 주말을 헛돌았다 — 월요일 새벽으로 옮겨 그날 검색에 바로 쓰인다");
  const flow = ["지난 일주일 관측", "에이전트 제안", "코드 검증", "검색 기준 수정", "같은 날 검색"];
  let y = 2.3;
  for (let i = 0; i < flow.length; i++) {
    const last = i === flow.length - 1;
    s.addShape(pres.ShapeType.rect, { x: 0.9, y, w: 5.0, h: 0.66,
      fill: { color: last ? NAVY : PANEL }, line: { width: 0 } });
    s.addText(flow[i], { x: 0.9, y, w: 5.0, h: 0.66, fontFace: KO, fontSize: 15, bold: true,
      color: last ? WHITE : NAVY, align: "center", valign: "middle", isTextBox: true, margin: 0 });
    if (!last) {
      s.addText("↓", { x: 3.05, y: y + 0.66, w: 0.7, h: 0.26, fontFace: KO, fontSize: 13,
        color: BLUE, align: "center", isTextBox: true, margin: 0 });
    }
    y += 0.92;
  }
  const facts = [["가중치", "defect detection  1.00 → 1.80"], ["검색어 추가", "defect detection"],
                 ["적격 논문", "145 → 157편"]];
  let fy = 2.62;
  for (const [k, v] of facts) {
    s.addShape(pres.ShapeType.rect, { x: 7.2, y: fy, w: 5.63, h: 1.0, fill: { color: PANEL2 }, line: { color: ROW, width: 0.75 } });
    s.addText(k, { x: 7.55, y: fy + 0.14, w: 4.9, h: 0.28, fontFace: KO, fontSize: 11, color: GRAY,
      isTextBox: true, margin: 0 });
    s.addText(v, { x: 7.55, y: fy + 0.44, w: 4.9, h: 0.4, fontFace: KO, fontSize: 16, bold: true,
      color: NAVY, isTextBox: true, margin: 0 });
    fy += 1.14;
  }
  s.addNotes("주간 관리와 일일 스캔은 같은 실행 안에서 순서대로 돈다. 주간 관리가 실패해도 아침 메일은 나간다. 회고가 아니라 오늘 논문을 고른 근거다 — 바꾼 기준이 몇 분 뒤 검색에 쓰인다.");
}

// ══════════════════════════════════════════════════ 6. 제안은 모델, 적용은 코드
{
  const s = pres.addSlide();
  head(s, "제안은 모델이, 적용은 코드가 한다", "두 모델이 동의해도 근거가 없으면 통과하지 못한다");
  const lanes = [["Claude", "제안", BLUE2], ["Codex", "검토", BLUE], ["Python", "검증 · 적용", NAVY]];
  let x = 1.15;
  for (let i = 0; i < lanes.length; i++) {
    const [who, what, tone] = lanes[i];
    s.addShape(pres.ShapeType.rect, { x, y: 2.75, w: 3.15, h: 2.2, fill: { color: tone }, line: { width: 0 } });
    s.addText(who, { x, y: 3.15, w: 3.15, h: 0.6, fontFace: "Cambria", fontSize: 26, bold: true,
      color: WHITE, align: "center", isTextBox: true, margin: 0 });
    s.addText(what, { x, y: 3.9, w: 3.15, h: 0.5, fontFace: KO, fontSize: 16,
      color: WHITE, align: "center", isTextBox: true, margin: 0 });
    if (i < lanes.length - 1) {
      s.addText("→", { x: x + 3.2, y: 3.6, w: 0.6, h: 0.5, fontFace: KO, fontSize: 22,
        color: BLUE, align: "center", isTextBox: true, margin: 0 });
    }
    x += 3.95;
  }
  s.addText("모델이 DB 를 직접 바꾸지 않는다", { x: 0.5, y: 5.6, w: 12.33, h: 0.6, fontFace: KO,
    fontSize: 22, bold: true, color: NAVY, align: "center", isTextBox: true, margin: 0 });
  s.addNotes("검증 규칙은 프롬프트가 아니라 agent_maintenance.validate 가 강제한다: 좋다고 반응한 논문에 그 용어가 있어야 키워드가 올라가고, 제외어는 관심 밖 논문 2편 이상, 사용자가 정한 키워드는 삭제 금지. 두 CLI 모두 도구를 끈 채 600초 상한으로 돈다. 상세 흐름은 부록.");
}

// ══════════════════════════════════════════════════ 7. 코드 재현
{
  const s = pres.addSlide();
  head(s, "코드를 못 찾아도 한 칸 아래로", "공식이 없으면 비슷한 구현까지 찾되, 공식이라고 쓰지 않는다");
  diagram(s, "03-code-ladder-steps.png", { y: 2.5, maxW: 12.2, maxH: 3.4 });
  footnote(s, "찾은 칸을 메일에 그대로 적는다 — 유사 구현은 보여 주기만 하고 격리 실행에 태우지 않는다", 6.2);
  s.addNotes("요약이 끝난 논문 한 편마다 돌고 결과는 한 달 캐시. 유사 구현은 그 논문이 걸린 핵심 키워드로 GitHub 검색, 링크 모음·소개 페이지는 제외. 찾은 코드는 네트워크 차단·권한 제거·읽기 전용 컨테이너에서 실행하고 판정 뒤 정리한다.");
}

// ══════════════════════════════════════════════════ 8. 디스크
{
  const s = pres.addSlide();
  head(s, "자동 재현을 돌리니 C 드라이브가 찼다", "원인은 지우지 않는 설계였다 — 코드를 고치고, 이미 쌓인 것은 확인하며 지웠다");
  s.addText("빌드 캐시", { x: 0.9, y: 2.5, w: 6.0, h: 0.4, fontFace: KO, fontSize: 15,
    color: GRAY, align: "center", isTextBox: true, margin: 0 });
  s.addText("143.03 GB", { x: 0.9, y: 2.95, w: 6.0, h: 1.0, fontFace: "Cambria", fontSize: 54,
    bold: true, color: GRAY, align: "center", isTextBox: true, margin: 0 });
  s.addText("↓", { x: 0.9, y: 4.05, w: 6.0, h: 0.5, fontFace: KO, fontSize: 24,
    color: BLUE, align: "center", isTextBox: true, margin: 0 });
  s.addText("186.8 MB", { x: 0.9, y: 4.6, w: 6.0, h: 1.0, fontFace: "Cambria", fontSize: 54,
    bold: true, color: BLUE, align: "center", isTextBox: true, margin: 0 });
  const acts = [["시도마다 전용 빌더", "끝나면 그 빌더째 제거"],
                ["판정 뒤 전부 삭제", "실패 · 타임아웃 · 예외에서도"],
                ["지우기 전에 기록", "실행 로그와 DB 결과를 먼저 저장"]];
  let ay = 2.85;
  for (const [k, v] of acts) {
    s.addShape(pres.ShapeType.rect, { x: 7.4, y: ay, w: 5.43, h: 0.86, fill: { color: PANEL }, line: { width: 0 } });
    s.addText(k, { x: 7.75, y: ay + 0.1, w: 4.8, h: 0.34, fontFace: KO, fontSize: 14, bold: true,
      color: NAVY, isTextBox: true, margin: 0 });
    s.addText(v, { x: 7.75, y: ay + 0.46, w: 4.8, h: 0.3, fontFace: KO, fontSize: 11, color: GRAY,
      isTextBox: true, margin: 0 });
    ay += 1.0;
  }
  footnote(s, "재현 기록 132건은 그대로 남았다 — 사라지는 것은 다시 만들 수 있는 것뿐이다", 6.1);
  s.addNotes("캐시 143.03 GB 는 두 차례(43.47 + 99.56) 합계다. 운영 DB 는 백업 후 integrity_check=ok, 삭제 목록은 repro-cleanup-20260918.json 에 남겼다. prune·볼륨 삭제는 하지 않는다 — 이번 시도가 만든 것만 지운다.");
}

// ══════════════════════════════════════════════════ 9. 정리
{
  const s = pres.addSlide();
  head(s, "정리와 남은 것", "네 프로필이 무인으로 돌고 있고, 못 한 것은 못 했다고 적어 두었다");
  label(s, "돌아가고 있는 것", 0.5, 2.3, 6);
  const done = [["프로필 4개 무인 운영", "매일 05:00 · 월요일 새벽 주간 관리"],
                ["동향이 쌓이고 이어진다", "오늘 논문 + 최근 7일 + 지난 6일 서술"],
                ["검색 기준 변화를 보고한다", "무엇이 · 얼마나 · 누가 바꿨는지"]];
  let y = 2.75;
  for (const [k, v] of done) {
    s.addShape(pres.ShapeType.rect, { x: 0.5, y, w: 6.1, h: 1.0, fill: { color: PANEL }, line: { width: 0 } });
    s.addText(k, { x: 0.85, y: y + 0.16, w: 5.5, h: 0.34, fontFace: KO, fontSize: 14, bold: true,
      color: NAVY, isTextBox: true, margin: 0 });
    s.addText(v, { x: 0.85, y: y + 0.54, w: 5.5, h: 0.32, fontFace: KO, fontSize: 11.5, color: GRAY,
      isTextBox: true, margin: 0 });
    y += 1.14;
  }
  label(s, "남은 것 · 하지 않은 것", 6.95, 2.3, 6);
  const todo = [["분야별 SOTA 순위 추적", "공개 자료원이 닫혔다 — 논문 자체 주장만"],
                ["사설 IP 판정 · 인젝션 차단", "미구현으로 보안 점검표에 적었다"],
                ["운영 화면 시각화", "메일은 훑기용, 화면은 탐색용"]];
  let ty = 2.75;
  for (const [k, v] of todo) {
    s.addShape(pres.ShapeType.rect, { x: 6.95, y: ty, w: 5.88, h: 1.0, fill: { color: PANEL2 }, line: { color: ROW, width: 0.75 } });
    s.addText(k, { x: 7.3, y: ty + 0.16, w: 5.3, h: 0.34, fontFace: KO, fontSize: 14, bold: true,
      color: ORANGE, isTextBox: true, margin: 0 });
    s.addText(v, { x: 7.3, y: ty + 0.54, w: 5.3, h: 0.32, fontFace: KO, fontSize: 11.5, color: GRAY,
      isTextBox: true, margin: 0 });
    ty += 1.14;
  }
  s.addNotes("실측하지 않은 값은 '미실측', 실패는 실패로 적는다. 다음 확인: 반응이 쌓이면 가중치가 실제로 분야를 따라가는지 수치로 본다.");
}

// ══════════════════════════════════════════════════ 부록 A — 주간 관리 상세
{
  const s = pres.addSlide();
  appendix(s, "부록 — 월요일 주간 관리 상세", "Claude 제안 → Codex 판정 → Python 검증·적용, 그리고 DB 정리");
  diagram(s, "05-weekly-agent.png", { y: 2.0, maxW: 12.2, maxH: 4.7 });
  s.addNotes("두 CLI 모두 도구를 끈 채 600초 상한으로 돈다. DB 보존: 후보 90일 · 관측/실행/논문 180일.");
}

// ══════════════════════════════════════════════════ 부록 B — 보안
{
  const s = pres.addSlide();
  appendix(s, "부록 — 외부 입력이 지나는 다섯 관문", "논문 · 링크 · 저장소는 신뢰하지 않는 입력으로 다룬다");
  diagram(s, "04-security-layers.png", { y: 2.0, maxW: 12.2, maxH: 4.7 });
  s.addNotes("미구현으로 기록한 것 — DNS 해석 뒤 사설 IP 판정, 논문 본문 인젝션은 표시만 하고 차단하지 않는다. 시크릿(.env)은 어느 모델에도 넣지 않는다.");
}

pres.writeFile({ fileName: OUT }).then(() => console.log("wrote " + OUT));
