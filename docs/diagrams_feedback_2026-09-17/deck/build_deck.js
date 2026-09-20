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

/** 결론 바 — 이 장에서 가져갈 한 문장. */
function conclusion(slide, text, y = 6.62) {
  slide.addShape(pres.ShapeType.roundRect, { x: 0.5, y, w: W - 1.0, h: 0.56,
    fill: { color: NAVY }, line: { width: 0 }, rectRadius: 0.06 });
  slide.addText(text, { x: 0.7, y, w: W - 1.4, h: 0.56, fontFace: KO, fontSize: 12.5,
    bold: true, color: WHITE, align: "center", valign: "middle", isTextBox: true, margin: 0 });
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
  head(s, "지난 피드백과 이번 변경", "여섯 항목을 코드에 반영했다 — SOTA 순위표만 공개 자료원이 닫혀 일부");

  const hdr = (t) => ({ text: t, options: { fill: NAVY, color: WHITE, bold: true, fontSize: 12.5,
    fontFace: KO, align: "center", valign: "middle" } });
  const left = (t) => ({ text: t, options: { fill: PANEL2, color: NAVY, bold: true, fontSize: 12.5,
    fontFace: KO, valign: "middle", margin: [4, 12, 4, 12] } });
  const mid = (t) => ({ text: t, options: { fill: WHITE, color: "333333", fontSize: 12.5,
    fontFace: KO, valign: "middle", margin: [4, 12, 4, 12] } });
  const st = (t, c) => ({ text: t, options: { fill: WHITE, color: c, bold: true, fontSize: 12,
    fontFace: KO, align: "center", valign: "middle" } });

  const rows = [
    [hdr("받은 피드백"), hdr("이번 변경"), hdr("상태")],
    [left("사용자 반응을 학습하게"), mid("메일 논문마다 1클릭 반응 → 매일 가중치 자동 조정"), st("운영", GREEN)],
    [left("메일이 단순 논문 목록"), mid("오늘 동향 + 최근 7일 흐름 + 검색 기준 변화까지 한 통에"), st("운영", GREEN)],
    [left("키워드 관리를 에이전트처럼"), mid("월요일 새벽 주간 관리 → 바꾼 기준이 그날 아침 검색에 바로"), st("운영", GREEN)],
    [left("코드를 못 찾으면 끝"), mid("공식 → 저자 연관 → 제3자 → 유사 구현 5단계, 어느 칸인지 표시"), st("운영", GREEN)],
    [left("장기 운영이 되는가"), mid("격리 실행 · 판정 뒤 정리 · 링크와 크기 제한 · 메일 부재 경보"), st("운영", GREEN)],
    [left("과제 SOTA 제안"), mid("논문이 스스로 주장한 SOTA 문장만 추출하고 미검증으로 표시"), st("일부", ORANGE)],
  ];
  s.addTable(rows, {
    x: 0.5, y: 2.0, w: W - 1.0, colW: [3.5, 7.0, 1.833],
    rowH: [0.44, 0.55, 0.55, 0.55, 0.55, 0.55, 0.55],
    border: { type: "solid", color: ROW, pt: 0.75 }, fontFace: KO, autoPage: false,
  });

  const stats = [["프로필 4개", "무인 운영"], ["매일 05:00", "실제 발송"], ["1,171", "테스트 통과"]];
  let sx = 0.5;
  for (const [big, small] of stats) {
    s.addShape(pres.ShapeType.rect, { x: sx, y: 5.92, w: 4.11, h: 0.5, fill: { color: PANEL }, line: { width: 0 } });
    s.addText([{ text: big + "   ", options: { fontSize: 15, bold: true, color: NAVY } },
               { text: small, options: { fontSize: 11, color: GRAY } }],
      { x: sx + 0.3, y: 5.92, w: 3.6, h: 0.5, fontFace: KO, valign: "middle", isTextBox: true, margin: 0 });
    sx += 4.31;
  }
  conclusion(s, "\"일부\" 는 감추지 않는다 — 공개 리더보드 자료원이 닫혀 논문 자체 주장만 싣는다");
  s.addNotes("근거 §8-122·123·128·129·132·133·134. Papers with Code 는 2026-09-16 확인 시 huggingface.co/papers 로 리다이렉트되어 리더보드 API 가 없다.");
}

// ══════════════════════════════════════════════════ 2. 결과 — 오늘 아침 메일
{
  const s = pres.addSlide();
  head(s, "결과 — 오늘 아침에 받은 메일", "논문 5편만 오던 메일이 동향 · 흐름 · 기준 변화까지 한 통에 담는다");

  const steps = [
    ["오늘의 동향 정리", "오늘 논문 + 지난 6일 서술"],
    ["최근 7일 흐름", "무엇이 늘고 줄었나"],
    ["지난 7일 검색 기준 변화", "그래서 기준을 이렇게 바꿨다"],
    ["오늘의 신규 논문 5편", "그 기준으로 고른 것"],
  ];
  let y = 2.05;
  let n = 1;
  for (const [t, sub] of steps) {
    s.addShape(pres.ShapeType.rect, { x: 0.5, y, w: 4.5, h: 0.92, fill: { color: PANEL }, line: { width: 0 } });
    s.addShape(pres.ShapeType.ellipse, { x: 0.74, y: y + 0.26, w: 0.4, h: 0.4, fill: { color: BLUE }, line: { width: 0 } });
    s.addText(String(n++), { x: 0.74, y: y + 0.26, w: 0.4, h: 0.4, fontFace: "Cambria", fontSize: 12,
      bold: true, color: WHITE, align: "center", valign: "middle", isTextBox: true, margin: 0 });
    s.addText(t, { x: 1.3, y: y + 0.14, w: 3.5, h: 0.32, fontFace: KO, fontSize: 13, bold: true,
      color: NAVY, isTextBox: true, margin: 0 });
    s.addText(sub, { x: 1.3, y: y + 0.48, w: 3.5, h: 0.3, fontFace: KO, fontSize: 10.5, color: GRAY,
      isTextBox: true, margin: 0 });
    if (n <= steps.length) {
      s.addText("▼", { x: 2.55, y: y + 0.92, w: 0.4, h: 0.22, fontFace: KO, fontSize: 10,
        color: BLUE, align: "center", isTextBox: true, margin: 0 });
    }
    y += 1.14;
  }

  mailPanel(s, { x: 5.35, y: 1.95, w: 7.48, h: 4.5, title: "지난 7일 검색 기준 변화", draw: (px, py) => {
    s.addText("가중치 변화", { x: px + 0.3, y: py + 0.62, w: 6.9, h: 0.26, fontFace: KO, fontSize: 11,
      bold: true, color: "333333", isTextBox: true, margin: 0 });
    const moves = [["defect detection", "[에이전트] [반응]", 1.00, 1.80, 1.0],
                   ["LLM agent", "[에이전트]", 1.00, 1.15, 0.19]];
    let my = py + 0.95;
    for (const [kw, tags, before, after, frac] of moves) {
      s.addText("▲", { x: px + 0.33, y: my, w: 0.26, h: 0.24, fontFace: KO, fontSize: 10,
        color: BLUE, isTextBox: true, margin: 0 });
      s.addText(kw, { x: px + 0.6, y: my, w: 3.2, h: 0.24, fontFace: KO, fontSize: 11.5, bold: true,
        color: NAVY, isTextBox: true, margin: 0 });
      s.addText(tags, { x: px + 3.85, y: my + 0.02, w: 3.3, h: 0.22, fontFace: KO, fontSize: 9,
        color: GRAY, isTextBox: true, margin: 0 });
      s.addShape(pres.ShapeType.rect, { x: px + 0.6, y: my + 0.29, w: 4.3, h: 0.09, fill: { color: ROW }, line: { width: 0 } });
      s.addShape(pres.ShapeType.rect, { x: px + 0.6, y: my + 0.29, w: 4.3 * frac, h: 0.09, fill: { color: BLUE }, line: { width: 0 } });
      s.addText(before.toFixed(2) + " → " + after.toFixed(2) + "   +" + (after - before).toFixed(2),
        { x: px + 0.6, y: my + 0.44, w: 4.3, h: 0.24, fontFace: KO, fontSize: 9.5, color: GRAY, isTextBox: true, margin: 0 });
      my += 0.86;
    }
    s.addText("신규", { x: px + 0.3, y: my - 0.1, w: 6.9, h: 0.26, fontFace: KO, fontSize: 11,
      bold: true, color: "333333", isTextBox: true, margin: 0 });
    s.addShape(pres.ShapeType.roundRect, { x: px + 0.33, y: my + 0.22, w: 0.62, h: 0.26,
      fill: { color: "E7F4EC" }, line: { width: 0 }, rectRadius: 0.04 });
    s.addText("NEW", { x: px + 0.33, y: my + 0.22, w: 0.62, h: 0.26, fontFace: KO, fontSize: 8.5,
      bold: true, color: GREEN, align: "center", valign: "middle", isTextBox: true, margin: 0 });
    s.addText([{ text: "defect detection", options: { bold: true, color: NAVY, fontSize: 11.5 } },
               { text: "   검색어 · 에이전트", options: { color: GRAY, fontSize: 9.5 } }],
      { x: px + 1.07, y: my + 0.22, w: 6.1, h: 0.26, fontFace: KO, isTextBox: true, margin: 0, valign: "middle" });
    s.addText("검색 영향", { x: px + 0.3, y: my + 0.62, w: 6.9, h: 0.26, fontFace: KO, fontSize: 11,
      bold: true, color: "333333", isTextBox: true, margin: 0 });
    const impact = [["적격 논문", "145 → 157"], ["잃은 논문", "0"], ["상위 겹침", "0.20"]];
    let iy = my + 0.94;
    for (const [k, v] of impact) {
      s.addText(k, { x: px + 0.6, y: iy, w: 1.6, h: 0.24, fontFace: KO, fontSize: 10, color: GRAY, isTextBox: true, margin: 0 });
      s.addText(v, { x: px + 2.25, y: iy, w: 2.0, h: 0.24, fontFace: KO, fontSize: 10.5, color: NAVY, isTextBox: true, margin: 0 });
      iy += 0.28;
    }
  }});
  s.addText("실제 발송 메일에서 그대로 옮긴 지면", { x: 5.35, y: 6.5, w: 7.48, h: 0.24,
    fontFace: KO, fontSize: 9.5, color: LIGHT, align: "right", isTextBox: true, margin: 0 });
  s.addNotes("막대는 그 주 가장 큰 변화를 기준으로 한 상대 길이다. |Δ|<0.1 은 감추되 건수는 남긴다. 신규·삭제는 전부 싣는다.");
}

// ══════════════════════════════════════════════════ 3. 반응 → 검색
{
  const s = pres.addSlide();
  head(s, "반응이 다음 검색을 바꾼다", "클릭 한 번에 흔들리지 않는다 — 서로 다른 논문의 반응이 쌓여야 움직인다");

  const chain = [["메일", "논문마다 버튼 3개"], ["반응", "더 보고 싶음 · 유용함 · 관심 밖"],
                 ["가중치 갱신", "매일 05:00, 코드가 계산"], ["다음 검색", "그날 순위가 바뀐다"]];
  let x = 0.5;
  for (let i = 0; i < chain.length; i++) {
    const [t, sub] = chain[i];
    s.addShape(pres.ShapeType.rect, { x, y: 2.05, w: 2.72, h: 1.15, fill: { color: PANEL }, line: { width: 0 } });
    s.addText(t, { x: x + 0.22, y: 2.22, w: 2.3, h: 0.34, fontFace: KO, fontSize: 14, bold: true,
      color: NAVY, isTextBox: true, margin: 0 });
    s.addText(sub, { x: x + 0.22, y: 2.6, w: 2.3, h: 0.5, fontFace: KO, fontSize: 10.5, color: GRAY,
      isTextBox: true, margin: 0, valign: "top" });
    if (i < chain.length - 1) {
      s.addText("▶", { x: x + 2.76, y: 2.5, w: 0.34, h: 0.28, fontFace: KO, fontSize: 11,
        color: BLUE, align: "center", isTextBox: true, margin: 0 });
    }
    x += 3.1;
  }

  label(s, "실제로 움직인 값", 0.5, 3.5, 6);
  const moved = [["defect detection", "1.00 → 1.80", "긍정 반응 2건 · 서로 다른 논문"],
                 ["LLM agent", "1.00 → 1.15", "긍정 반응 1건 — 소폭만"]];
  let my = 3.88;
  for (const [kw, val, why] of moved) {
    s.addShape(pres.ShapeType.rect, { x: 0.5, y: my, w: 6.1, h: 0.86, fill: { color: PANEL2 }, line: { color: ROW, width: 0.75 } });
    s.addText(kw, { x: 0.8, y: my + 0.1, w: 3.4, h: 0.3, fontFace: KO, fontSize: 12.5, bold: true,
      color: NAVY, isTextBox: true, margin: 0 });
    s.addText(why, { x: 0.8, y: my + 0.44, w: 3.4, h: 0.3, fontFace: KO, fontSize: 10, color: GRAY,
      isTextBox: true, margin: 0 });
    s.addText(val, { x: 4.2, y: my, w: 1.9, h: 0.86, fontFace: KO, fontSize: 15, bold: true,
      color: BLUE, align: "right", valign: "middle", isTextBox: true, margin: 0 });
    my += 0.98;
  }

  label(s, "코드가 지키는 것", 6.95, 3.5, 6);
  const rules = [
    ["하루 ±0.1 까지", "클릭 한 번에 검색이 뒤집히지 않는다"],
    ["서로 다른 논문 2편 이상", "한 편은 그 논문이 좋았다는 뜻이지 키워드가 좋다는 뜻이 아니다"],
    ["반감기 90일", "오래된 취향은 서서히 잊는다"],
    ["최초 키워드는 지우지 않는다", "사용자가 정한 것은 내려가기만 한다"],
  ];
  let ry = 3.88;
  for (const [k, v] of rules) {
    s.addShape(pres.ShapeType.rect, { x: 6.95, y: ry + 0.09, w: 0.1, h: 0.1, fill: { color: BLUE }, line: { width: 0 } });
    s.addText([{ text: k + "   ", options: { bold: true, color: NAVY } }, { text: v, options: { color: GRAY } }],
      { x: 7.25, y: ry, w: 5.6, h: 0.42, fontFace: KO, fontSize: 11, isTextBox: true, margin: 0, valign: "top" });
    ry += 0.63;
  }
  conclusion(s, "모든 자동 변경은 revision 으로 남는다 — 운영 화면에서 언제든 되돌릴 수 있다");
  s.addNotes("feedback_weights.py — signal=(긍정−부정)/(긍정+부정+4), target=base+0.8·signal(0.35~2.0), 하루 |Δ|≤0.1, 반감기 90일, 최소 2편. 반응 수집은 HMAC 서명 토큰, 만료 14일.");
}

// ══════════════════════════════════════════════════ 4. 하루에서 흐름으로
{
  const s = pres.addSlide();
  head(s, "하루치 스냅숏에서 흐름으로", "그날 논문만 보고 쓰던 동향이 지난 일주일을 함께 본다");

  const cols = [["이전", "오늘 논문만 본다", ["그날 13편의 제목과 초록", "어제 쓴 글은 다시 못 읽는다", "무엇이 늘었는지 말할 수 없다"], LIGHT],
                ["현재", "오늘 + 지난 일주일", ["오늘 논문 + 지난 6일 서술", "최근 7일 편수를 직전 7일과 비교", "오늘 쓴 글이 내일의 입력이 된다"], BLUE]];
  let y = 2.05;
  for (const [tag, title, items, tone] of cols) {
    s.addShape(pres.ShapeType.rect, { x: 0.5, y, w: 6.1, h: 1.95,
      fill: { color: tone === BLUE ? PANEL : PANEL2 }, line: { color: tone === BLUE ? BLUE : ROW, width: tone === BLUE ? 1.25 : 0.75 } });
    s.addShape(pres.ShapeType.rect, { x: 0.5, y, w: 0.86, h: 0.42, fill: { color: tone }, line: { width: 0 } });
    s.addText(tag, { x: 0.5, y, w: 0.86, h: 0.42, fontFace: KO, fontSize: 11, bold: true,
      color: WHITE, align: "center", valign: "middle", isTextBox: true, margin: 0 });
    s.addText(title, { x: 1.55, y: y + 0.04, w: 4.8, h: 0.36, fontFace: KO, fontSize: 14, bold: true,
      color: tone === BLUE ? NAVY : GRAY, valign: "middle", isTextBox: true, margin: 0 });
    let iy = y + 0.58;
    for (const it of items) {
      s.addText("· " + it, { x: 0.8, y: iy, w: 5.6, h: 0.4, fontFace: KO, fontSize: 11,
        color: "333333", isTextBox: true, margin: 0, valign: "top" });
      iy += 0.42;
    }
    y += 2.2;
  }

  mailPanel(s, { x: 6.95, y: 2.05, w: 5.88, h: 4.1, title: "최근 7일 흐름", draw: (px, py) => {
    s.addText("관련 논문 611편  (직전 579편)", { x: px + 0.3, y: py + 0.58, w: 5.2, h: 0.26,
      fontFace: KO, fontSize: 11.5, color: "333333", isTextBox: true, margin: 0 });
    const groups = [["상승", BLUE, "▲", [["defect detection", "27", "+23"], ["digital twin", "23", "+9"], ["surface defect detection", "8", "+8"]]],
                    ["하락", ORANGE, "▼", [["world model", "12", "−15"], ["vision-language model", "37", "−12"], ["LLM agent", "41", "−10"]]]];
    let gy = py + 0.95;
    for (const [name, tone, arrow, rows] of groups) {
      s.addText(name, { x: px + 0.3, y: gy, w: 5.2, h: 0.26, fontFace: KO, fontSize: 11, bold: true,
        color: "333333", isTextBox: true, margin: 0 });
      gy += 0.3;
      for (const [kw, n, d] of rows) {
        s.addText(arrow, { x: px + 0.33, y: gy, w: 0.26, h: 0.24, fontFace: KO, fontSize: 9,
          color: tone, isTextBox: true, margin: 0 });
        s.addText(kw, { x: px + 0.62, y: gy, w: 3.1, h: 0.24, fontFace: KO, fontSize: 11,
          color: NAVY, isTextBox: true, margin: 0 });
        s.addText(n, { x: px + 3.75, y: gy, w: 0.7, h: 0.24, fontFace: KO, fontSize: 10.5,
          color: GRAY, align: "right", isTextBox: true, margin: 0 });
        s.addText(d, { x: px + 4.55, y: gy, w: 0.8, h: 0.24, fontFace: KO, fontSize: 10.5,
          bold: true, color: tone, align: "right", isTextBox: true, margin: 0 });
        gy += 0.29;
      }
      gy += 0.16;
    }
  }});
  conclusion(s, "직전 구간에 관측이 없으면 증감을 만들지 않는다 — 수집을 막 시작한 프로필에서 0→N 은 급증이 아니다");
  s.addNotes("오늘 논문 + 지난 6일 서술 = 7일. 옆의 수치 창도 7일이라 산문과 숫자가 같은 기간을 본다.");
}

// ══════════════════════════════════════════════════ 5. 월요일 주간 관리
{
  const s = pres.addSlide();
  head(s, "월요일마다 검색 기준을 점검한다", "금요일에 바꾸면 주말을 헛돌았다 — 월요일 새벽으로 옮겨 그날 검색에 바로 쓰인다");

  const flow = [["지난 일주일 관측", "반응 · 동향 · 자리 밖 후보"], ["에이전트 제안", "키워드 · 검색어 · 제외어"],
                ["검증", "근거 없는 제안은 버린다"], ["검색 기준 수정", "revision 으로 기록"],
                ["그날 아침 검색", "바뀐 기준으로 오늘 논문을 고른다"]];
  let y = 2.05;
  for (let i = 0; i < flow.length; i++) {
    const [t, sub] = flow[i];
    const last = i === flow.length - 1;
    s.addShape(pres.ShapeType.rect, { x: 0.5, y, w: 6.1, h: 0.68,
      fill: { color: last ? NAVY : PANEL }, line: { width: 0 } });
    s.addText(t, { x: 0.78, y, w: 3.0, h: 0.68, fontFace: KO, fontSize: 13, bold: true,
      color: last ? WHITE : NAVY, valign: "middle", isTextBox: true, margin: 0 });
    s.addText(sub, { x: 3.7, y, w: 2.7, h: 0.68, fontFace: KO, fontSize: 10.5,
      color: last ? PANEL : GRAY, valign: "middle", isTextBox: true, margin: 0 });
    if (!last) {
      s.addText("▼", { x: 3.35, y: y + 0.68, w: 0.4, h: 0.18, fontFace: KO, fontSize: 9,
        color: BLUE, align: "center", isTextBox: true, margin: 0 });
    }
    y += 0.86;
  }

  label(s, "9/21 월요일에 실제로 일어난 일", 6.95, 2.05, 6);
  const facts = [["가중치", "defect detection  1.00 → 1.80"], ["가중치", "LLM agent  1.00 → 1.15"],
                 ["검색어 추가", "defect detection"], ["적격 논문", "145 → 157편  (잃은 논문 0)"]];
  let fy = 2.5;
  for (const [k, v] of facts) {
    s.addShape(pres.ShapeType.rect, { x: 6.95, y: fy, w: 5.88, h: 0.66, fill: { color: PANEL2 }, line: { color: ROW, width: 0.75 } });
    s.addText(k, { x: 7.22, y: fy, w: 1.8, h: 0.66, fontFace: KO, fontSize: 11, color: GRAY,
      valign: "middle", isTextBox: true, margin: 0 });
    s.addText(v, { x: 9.0, y: fy, w: 3.7, h: 0.66, fontFace: KO, fontSize: 12, bold: true,
      color: NAVY, valign: "middle", isTextBox: true, margin: 0 });
    fy += 0.78;
  }
  s.addText("주간 관리와 일일 스캔은 같은 실행 안에서 순서대로 돈다. 주간 관리가 실패해도 아침 메일은 나간다.",
    { x: 6.95, y: 5.72, w: 5.88, h: 0.5, fontFace: KO, fontSize: 10.5, color: GRAY, isTextBox: true, margin: 0, valign: "top" });
  conclusion(s, "회고가 아니라 오늘 논문을 고른 근거다 — 바꾼 기준이 몇 분 뒤 검색에 쓰인다");
  s.addNotes("run_daily_scan.sh 가 월요일(KST)이면 db_retention → agent_maintenance 를 먼저 돌리고 바로 일일 스캔으로 잇는다. 같은 flock 안이라 겹치지 않는다.");
}

// ══════════════════════════════════════════════════ 6. 제안은 모델, 적용은 코드
{
  const s = pres.addSlide();
  head(s, "제안은 모델이, 적용은 코드가 한다", "두 모델이 동의해도 근거가 없으면 통과하지 못한다");
  diagram(s, "05-weekly-agent.png", { y: 1.9, maxW: 12.2, maxH: 4.55 });
  conclusion(s, "모델이 DB 를 직접 바꾸지 않는다 — 검증을 통과한 제안만 revision 으로 적용된다");
  s.addNotes("검증 규칙은 프롬프트가 아니라 agent_maintenance.validate 가 강제한다: 좋다고 반응한 논문에 그 용어가 있어야 키워드가 올라가고, 제외어는 관심 밖 논문 2편 이상, 사용자가 정한 키워드는 삭제 금지. 두 CLI 모두 도구를 끈 채 600초 상한으로 돈다.");
}

// ══════════════════════════════════════════════════ 7. 코드 재현
{
  const s = pres.addSlide();
  head(s, "코드를 못 찾아도 한 칸 아래로", "공식이 없으면 비슷한 구현까지 찾되, 공식이라고 쓰지 않는다");
  diagram(s, "03-code-ladder.png", { y: 1.95, maxW: 12.2, maxH: 3.75 });

  const steps = [["격리 실행", "네트워크 차단 · 권한 제거 · 읽기 전용"],
                 ["성공 · 실패 기록", "판정과 실행 로그를 DB 에 먼저 저장"],
                 ["정리", "이미지 · 빌더 · 복제본을 판정 뒤 전부 삭제"]];
  let x = 0.5;
  for (const [k, v] of steps) {
    s.addShape(pres.ShapeType.rect, { x, y: 5.78, w: 4.11, h: 0.66, fill: { color: PANEL }, line: { width: 0 } });
    s.addText([{ text: k + "   ", options: { bold: true, color: NAVY, fontSize: 12 } },
               { text: v, options: { color: GRAY, fontSize: 9.5 } }],
      { x: x + 0.25, y: 5.78, w: 3.7, h: 0.66, fontFace: KO, valign: "middle", isTextBox: true, margin: 0 });
    x += 4.31;
  }
  conclusion(s, "유사 구현은 보여 주기만 한다 — 격리 실행에 태우지 않고, 한 달마다 다시 찾는다");
  s.addNotes("찾은 칸은 메일에 그대로 표시한다. 유사 구현은 그 논문이 걸린 핵심 키워드로 GitHub 검색, 링크 모음·소개 페이지는 제외, 한 달 캐시.");
}

// ══════════════════════════════════════════════════ 8. 디스크
{
  const s = pres.addSlide();
  head(s, "자동 재현을 돌리니 C 드라이브가 찼다", "원인은 지우지 않는 설계였다 — 코드를 고치고, 이미 쌓인 것은 확인하며 지웠다");

  label(s, "무엇이 쌓였나", 0.5, 2.0, 6);
  const before = [["빌드 캐시", "시도마다 공용 빌더에 남았다", "143.03 GB"],
                  ["성공한 복제본", "\"성공하면 보관\" 이 기본이었다", "3.17 GB"],
                  ["오래된 복제본", "지난 실행이 남긴 것", "1.04 GB"]];
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
  s.addText("크기는 지우기 전 실측값이다. 캐시 143.03 GB 는 두 차례(43.47 + 99.56) 합계.",
    { x: 0.5, y: 4.78, w: 6.2, h: 0.3, fontFace: KO, fontSize: 10, color: GRAY, isTextBox: true, margin: 0 });

  label(s, "조치", 6.95, 2.0, 6);
  const after = [["시도마다 전용 빌더", "다른 작업의 캐시를 건드리지 않고, 끝나면 그 빌더째 제거"],
                 ["판정 뒤 전부 삭제", "이미지 · 컨테이너 · 복제본 — 실패 · 타임아웃 · 예외에서도"],
                 ["지우기 전에 기록", "실행 로그와 DB 결과를 먼저 저장. 저장이 실패하면 복제본을 남긴다"],
                 ["전역 삭제는 안 한다", "prune · 볼륨 삭제 금지 — 이번 시도가 만든 것만 지운다"]];
  let ay = 2.38;
  for (const [k, v] of after) {
    s.addShape(pres.ShapeType.rect, { x: 6.95, y: ay + 0.1, w: 0.1, h: 0.1, fill: { color: BLUE }, line: { width: 0 } });
    s.addText(k, { x: 7.25, y: ay, w: 5.6, h: 0.28, fontFace: KO, fontSize: 12, bold: true, color: NAVY,
      isTextBox: true, margin: 0 });
    s.addText(v, { x: 7.25, y: ay + 0.28, w: 5.6, h: 0.42, fontFace: KO, fontSize: 10.5, color: GRAY,
      isTextBox: true, margin: 0, valign: "top" });
    ay += 0.78;
  }
  s.addShape(pres.ShapeType.rect, { x: 0.5, y: 5.4, w: 12.33, h: 0.72, fill: { color: PANEL2 }, line: { color: ROW, width: 0.75 } });
  s.addText([{ text: "결과   ", options: { bold: true, color: NAVY, fontSize: 13 } },
             { text: "빌드 캐시 143.03 GB → 186.8 MB", options: { bold: true, color: BLUE, fontSize: 13 } },
             { text: "     재현 기록 132건 그대로     테스트 통과", options: { color: GRAY, fontSize: 11 } }],
    { x: 0.8, y: 5.4, w: 11.8, h: 0.72, fontFace: KO, valign: "middle", isTextBox: true, margin: 0 });
  conclusion(s, "지운 뒤에도 판정과 실행 로그는 DB 에 남는다 — 사라지는 것은 다시 만들 수 있는 것뿐이다");
  s.addNotes("운영 DB 는 data/backups/papers-pre-repro-cleanup-20260918.db 로 온라인 백업 후 integrity_check=ok. 삭제 목록은 repro-cleanup-20260918.json 에 보관. 호스트 디스크 회수(VHDX 압축)는 별도 작업으로 9/18 에 따로 했다.");
}

// ══════════════════════════════════════════════════ 9. 정리
{
  const s = pres.addSlide();
  head(s, "정리와 남은 것", "네 프로필이 무인으로 돌고 있고, 못 한 것은 못 했다고 적어 두었다");

  label(s, "돌아가고 있는 것", 0.5, 2.05, 6);
  const done = [["프로필 4개 무인 운영", "매일 05:00 메일 5편 · 월요일 새벽 주간 관리가 스캔 앞에"],
                ["동향이 쌓이고 이어진다", "오늘 논문 + 최근 7일 창 + 지난 6일 서술"],
                ["검색 기준 변화를 보고한다", "무엇이 · 얼마나 · 누가 바꿨는지 월요일 메일에"],
                ["외부 입력은 신뢰하지 않는다", "논문 · 링크 · 저장소 — 격리해서 처리하고 못 막는 것은 적어 둔다"]];
  let y = 2.45;
  for (const [k, v] of done) {
    s.addShape(pres.ShapeType.rect, { x: 0.5, y, w: 6.1, h: 0.8, fill: { color: PANEL }, line: { width: 0 } });
    s.addText(k, { x: 0.78, y: y + 0.1, w: 5.6, h: 0.3, fontFace: KO, fontSize: 12.5, bold: true,
      color: NAVY, isTextBox: true, margin: 0 });
    s.addText(v, { x: 0.78, y: y + 0.4, w: 5.6, h: 0.32, fontFace: KO, fontSize: 10.5, color: GRAY,
      isTextBox: true, margin: 0 });
    y += 0.92;
  }

  label(s, "남은 것 · 하지 않은 것", 6.95, 2.05, 6);
  const todo = [["분야별 SOTA 순위 추적", "공개 리더보드 자료원이 닫혔다 — 논문 자체 주장만 싣는 중"],
                ["사설 IP 판정 · 인젝션 차단", "미구현으로 보안 점검표에 적었다"],
                ["운영 화면 시각화", "메일은 훑기용, 화면은 탐색용으로 역할을 나눈다"],
                ["반응이 쌓인 뒤의 검증", "가중치가 실제로 분야를 따라가는지 수치로 본다"]];
  let ty = 2.45;
  for (const [k, v] of todo) {
    s.addShape(pres.ShapeType.rect, { x: 6.95, y: ty, w: 5.88, h: 0.8, fill: { color: PANEL2 }, line: { color: ROW, width: 0.75 } });
    s.addText(k, { x: 7.23, y: ty + 0.1, w: 5.4, h: 0.3, fontFace: KO, fontSize: 12.5, bold: true,
      color: ORANGE, isTextBox: true, margin: 0 });
    s.addText(v, { x: 7.23, y: ty + 0.4, w: 5.4, h: 0.32, fontFace: KO, fontSize: 10.5, color: GRAY,
      isTextBox: true, margin: 0 });
    ty += 0.92;
  }
  conclusion(s, "실측하지 않은 값은 \"미실측\", 실패는 실패로 적는다 — 그래야 다음 판단을 이 기록 위에서 할 수 있다");
  s.addNotes("다음 확인: 반응이 쌓이면 가중치가 실제로 분야를 따라가는지 수치로 본다.");
}

// ══════════════════════════════════════════════════ 부록 — 보안
{
  const s = pres.addSlide();
  s.addText("부록", { x: 0.5, y: 0.34, w: 2.0, h: 0.6, fontFace: KO, fontSize: 14, bold: true,
    color: GRAY, valign: "middle", isTextBox: true, margin: 0 });
  s.addText("외부 입력이 지나는 다섯 관문", { x: 1.6, y: 0.34, w: 11.2, h: 0.6, fontFace: KO,
    fontSize: 24, bold: true, color: NAVY, valign: "middle", isTextBox: true, margin: 0 });
  s.addShape(pres.ShapeType.rect, { x: 0, y: 1.16, w: W, h: 0.52, fill: { color: BLUE }, line: { width: 0 } });
  s.addText("논문 · 링크 · 저장소는 신뢰하지 않는 입력으로 다룬다 — 못 막는 것도 적어 두었다",
    { x: 0.5, y: 1.16, w: W - 1.0, h: 0.52, fontFace: KO, fontSize: 15, bold: true, color: WHITE,
      align: "center", valign: "middle", isTextBox: true, margin: 0 });
  diagram(s, "04-security-layers.png", { y: 1.95, maxW: 12.2, maxH: 4.5 });
  conclusion(s, "미구현으로 기록한 것 — DNS 해석 뒤 사설 IP 판정 · 논문 본문 인젝션은 표시만 하고 차단하지 않는다");
  s.addNotes("시크릿(.env)은 어느 모델에도 넣지 않는다.");
}

pres.writeFile({ fileName: OUT }).then(() => console.log("wrote " + OUT));
