"""피드백 반영 다이어그램 4장 생성 — diagram-design 규약(slide-16x9, presentation ramp, paper-harness 프로필)."""
from __future__ import annotations

import math
from pathlib import Path

OUT = Path(__file__).resolve().parent
OUT.mkdir(parents=True, exist_ok=True)

# ── 토큰 — 2026-09-18 사용자 지정: 사내 보고서(머신비전_조명환경검증_실험보고서)와 같은 네이비 계열로 맞춘다.
# 슬라이드와 다이어그램이 한 문서로 읽혀야 해서 팔레트를 그쪽에 종속시켰다(그전엔 운영 화면 #3B5BDB).
PAPER, PAPER2, INK, MUTED, SOFT = "#FFFFFF", "#F7F9FC", "#1F3864", "#595959", "#8C99AD"
RULE, RULE_SOLID = "rgba(31,56,100,0.14)", "#D9E2F3"
ACCENT, ACCENT_TINT = "#2E75B6", "#E9F0F8"
DONE, PARTIAL = "#1E9E5A", "#C55A11"
SANS = "'Malgun Gothic', 'Noto Sans KR', 'Apple SD Gothic Neo', sans-serif"
MONO = "'Geist Mono', ui-monospace, monospace"
SERIF = "'Malgun Gothic', 'Noto Sans KR', sans-serif"        # 보고서 본문과 같은 얼굴 — 주석도 같은 글꼴로
W, H = 1280, 720

FONT_LINK = ("https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Geist:wght@400;500;600"
             "&family=Geist+Mono:wght@400;500;600&family=Noto+Serif:ital@0;1&family=Noto+Sans+KR:wght@400;500;600"
             "&family=Noto+Serif+KR:wght@400&display=swap")


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text(x, y, s, *, size=16, weight=600, fill=INK, anchor="start", font=SANS, spacing=None, opacity=None):
    extra = f' letter-spacing="{spacing}"' if spacing else ""
    if opacity is not None:
        extra += f' opacity="{opacity}"'
    return (f'<text x="{x}" y="{y}" fill="{fill}" font-size="{size}" font-weight="{weight}" '
            f'font-family="{font}" text-anchor="{anchor}"{extra}>{esc(s)}</text>')


def mono(x, y, s, *, size=12, fill=MUTED, anchor="start", weight=500, spacing="0.04em"):
    return text(x, y, s, size=size, weight=weight, fill=fill, anchor=anchor, font=MONO, spacing=spacing)


def box(x, y, w, h, *, fill=PAPER, stroke=INK, rx=6, dash=None, sw=1):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{PAPER}"/>'
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>')


DEFS = f'''<defs>
  <marker id="arrow" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0, 8 3, 0 6" fill="{MUTED}"/></marker>
  <marker id="arrow-accent" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0, 8 3, 0 6" fill="{ACCENT}"/></marker>
  <marker id="arrow-soft" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0, 8 3, 0 6" fill="{SOFT}"/></marker>
</defs>'''


def svg(slug, title, desc, body):
    return (f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="{slug}-title {slug}-desc">\n'
            f'  <title id="{slug}-title">{esc(title)}</title>\n  <desc id="{slug}-desc">{esc(desc)}</desc>\n'
            f'  {DEFS}\n  <rect width="100%" height="100%" fill="{PAPER}"/>\n{body}\n</svg>')


def page(slug, eyebrow, title, svg_markup, subtitle=None):
    sub = f'<p class="sub">{esc(subtitle)}</p>' if subtitle else ""
    return f'''<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{esc(title)}</title>
  <link href="{FONT_LINK}" rel="stylesheet">
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    :root {{ --color-paper: {PAPER}; --color-ink: {INK}; --color-muted: {MUTED}; --color-accent: {ACCENT};
            --font-sans: {SANS}; --font-serif: {SERIF}; --font-mono: {MONO}; }}
    body {{ font-family: var(--font-sans); background: var(--color-paper); color: var(--color-ink); min-height: 100vh;
           display: flex; align-items: center; justify-content: center; padding: 3rem 2rem; }}
    .frame {{ max-width: 1280px; width: 100%; }}
    .eyebrow {{ font-family: var(--font-mono); font-size: 0.66rem; font-weight: 500; letter-spacing: 0.18em; text-transform: uppercase;
               color: var(--color-muted); margin-bottom: 0.5rem; }}
    h1 {{ font-family: var(--font-serif); font-size: clamp(1.5rem, 2.4vw + 0.75rem, 2rem); font-weight: 400; letter-spacing: -0.01em;
         line-height: 1.2; color: var(--color-ink); margin-bottom: 0.5rem; }}
    .sub {{ color: var(--color-muted); font-size: 0.95rem; margin-bottom: 1.25rem; }}
    svg {{ width: 100%; min-width: 900px; display: block; }}
  </style>
</head>
<body>
  <div class="frame">
    <p class="eyebrow">{esc(eyebrow)}</p>
    <h1>{esc(title)}</h1>
    {sub}
    {svg_markup}
  </div>
</body>
</html>
'''


# ════════════════════════════════════════════════════════════════════
# ① 피드백 → 반영 매핑 (행 장부)
# ════════════════════════════════════════════════════════════════════
ROWS = [
    ("사용자 경험 · 사용자 피드백으로 업데이트, UI",
     "메일 1클릭 반응 → 매일 가중치 자동 조정 · 운영 화면 개편",
     "feedback_links · feedback_weights · review_app", "9/15–16", DONE, True),
    ("메일 기준 ID 로 가중치 업데이트",
     "발송 회차 id + 논문 P번호 토큰(서명) → 논문별 반응 → 키워드 가중치",
     "feedback_tokens · mail_ledger · target ±0.1/day · revision", "9/15–16", DONE, False),
    ("규칙만 설정, 하네스",
     "규칙 15개 → 최소 규칙 7개, 나머지는 코드가 강제",
     "CLAUDE.md · agent_maintenance.validate", "9/14", DONE, False),
    ("메일 바이러스·보안, 한 달 운영 가능하게 바로",
     "링크 허용 목록 · PDF/저장소 상한 · 격리 실행 · 06:30 메일 부재 경보",
     "link_policy · server · docker_runner · check_daily_mail", "9/16", DONE, False),
    ("저장소 못 찾아도 비슷한 것이라도 반드시",
     "코드 사다리: 공식 → 저자 연관 → 제3자 → 유사 구현 → 없음",
     "code_ladder", "9/16", DONE, False),
    ("과제 시 SOTA 제안, Claude·GPT, 에이전트처럼",
     "논문 자체 SOTA 주장만 추출(미검증 표시) · 주간 에이전트 Claude→Codex",
     "sota_claims · agent_maintenance (Fri 17:00)", "9/15–16", PARTIAL, False),
]


def diagram_mapping():
    b = []
    x0, x_div, x1 = 40, 496, 1240
    row_h, gap, y0 = 88, 12, 56
    b.append(text(x0, 36, "9/14 (월) 피드백", size=12, weight=600, fill=MUTED))
    b.append(text(x_div + 16, 36, "9/17 (목) 지금", size=12, weight=600, fill=MUTED))
    for i, (fb, done, mods, date, status, focal) in enumerate(ROWS):
        y = y0 + i * (row_h + gap)
        stroke = ACCENT if focal else INK
        b.append(box(x0, y, x1 - x0, row_h, stroke=stroke, sw=1.2 if focal else 1))
        # 왼쪽 패널(입력) 옅은 채움 — 둥근 왼쪽 모서리를 지키려고 clip 대신 두 조각
        b.append(f'<path d="M{x0+6} {y+0.5} H{x_div} V{y+row_h-0.5} H{x0+6} A6 6 0 0 1 {x0} {y+row_h-6.5} V{y+6.5} A6 6 0 0 1 {x0+6} {y+0.5} Z" fill="{PAPER2}"/>')
        b.append(f'<line x1="{x_div}" y1="{y}" x2="{x_div}" y2="{y+row_h}" stroke="{RULE_SOLID}" stroke-width="1"/>')
        if focal:
            b.append(f'<rect x="{x_div+1}" y="{y+1}" width="{x1-x_div-2}" height="{row_h-2}" rx="5" fill="{ACCENT_TINT}"/>')
        b.append(mono(x0 + 16, y + 24, f"FEEDBACK 0{i+1}", size=8, fill=SOFT, spacing="0.14em"))
        b.append(text(x0 + 16, y + 54, fb, size=16, weight=600))
        b.append(mono(x_div + 16, y + 24, "CHANGE", size=8, fill=SOFT, spacing="0.14em"))
        b.append(text(x_div + 16, y + 52, done, size=16, weight=600, fill=ACCENT if focal else INK))
        b.append(mono(x_div + 16, y + 72, mods, size=12, fill=MUTED, weight=400, spacing="0"))
        b.append(mono(x1 - 40, y + 72, date, size=12, fill=MUTED, anchor="end", spacing="0"))
        b.append(f'<circle cx="{x1-20}" cy="{y+row_h/2}" r="5" fill="{status}"/>')
    # 범례
    ly = y0 + 6 * (row_h + gap) + 20
    b.append(f'<line x1="{x0}" y1="{ly-8}" x2="{x1}" y2="{ly-8}" stroke="{RULE}" stroke-width="0.8"/>')
    b.append(mono(x0, ly + 10, "LEGEND", size=8, fill=MUTED, spacing="0.14em"))
    b.append(f'<circle cx="{x0+92}" cy="{ly+6}" r="5" fill="{DONE}"/>')
    b.append(text(x0 + 104, ly + 10, "운영 중", size=12, weight=500, fill=MUTED))
    b.append(f'<circle cx="{x0+188}" cy="{ly+6}" r="5" fill="{PARTIAL}"/>')
    b.append(text(x0 + 200, ly + 10, "일부 — 분야별 SOTA 추적은 자료원이 없어 보류", size=12, weight=500, fill=MUTED))
    b.append(text(x1, ly + 10, "파란 테두리 = 이번 주의 중심 변경", size=12, weight=500, fill=ACCENT, anchor="end"))
    return svg("feedback-map", "9/14 피드백 6개 → 9/17 반영",
               "월요일에 받은 피드백 여섯 항목 각각이 이번 주에 어떤 기능·모듈로 구현됐는지 한 줄씩 짝지어 보여 준다.",
               "\n".join(b))


# ════════════════════════════════════════════════════════════════════
# ② 피드백 루프 (Loop)
# ════════════════════════════════════════════════════════════════════
STATIONS = [
    ("아침 메일", "05:00 · 논문 5편 카드", False, None),
    ("1클릭 반응", "더 보고 싶음 · 유용함 · 관심 밖", True, None),
    ("수집·격리", "서명 검증 · 선열람·연타 격리 → 반응 기록", False, "기록"),
    ("가중치 갱신", "하루 ±0.1 · 목표값 추종 → revision", False, "기록"),
    ("주간 에이전트", "금 17:00 · Claude 제안 → Codex 판정", False, "기록"),
    ("검색·순위", "arXiv · S2 · 가중치 띠 → 5편", False, None),
]


def _rect_circle_intersections(cx, cy, R, x, y, w, h):
    pts = []
    for xe in (x, x + w):
        d = R * R - (xe - cx) ** 2
        if d >= 0:
            for yy in (cy + math.sqrt(d), cy - math.sqrt(d)):
                if y <= yy <= y + h:
                    pts.append((xe, yy))
    for ye in (y, y + h):
        d = R * R - (ye - cy) ** 2
        if d >= 0:
            for xx in (cx + math.sqrt(d), cx - math.sqrt(d)):
                if x <= xx <= x + w:
                    pts.append((xx, ye))
    return pts


def diagram_loop():
    cx, cy, R = 640, 352, 256
    sw_, sh_ = 232, 64
    hw, hh = 240, 104
    N = len(STATIONS)
    rects = []
    for k in range(N):
        th = math.radians(-90 + k * 360 / N)
        px, py = cx + R * math.cos(th), cy + R * math.sin(th)
        x = round((px - sw_ / 2) / 4) * 4
        y = round((py - sh_ / 2) / 4) * 4
        rects.append((x, y, th))
    b = []
    # 링 화살표
    def ang(p):
        return math.atan2(p[1] - cy, p[0] - cx)
    for k in range(N):
        j = (k + 1) % N
        xk, yk, thk = rects[k]
        xj, yj, thj = rects[j]
        pk = _rect_circle_intersections(cx, cy, R, xk, yk, sw_, sh_)
        pj = _rect_circle_intersections(cx, cy, R, xj, yj, sw_, sh_)
        # exit(k): 시계방향으로 theta_k 바로 뒤 · entry(j): theta_j 바로 앞
        def cw_after(pts, th):
            return min(pts, key=lambda p: (ang(p) - th) % (2 * math.pi))
        def cw_before(pts, th):
            return min(pts, key=lambda p: (th - ang(p)) % (2 * math.pi))
        q_exit = cw_after(pk, thk)
        q_entry = cw_before(pj, thj)
        phi_end = ang(q_entry) - 1.2 / R
        q_end = (cx + R * math.cos(phi_end), cy + R * math.sin(phi_end))
        b.append(f'<path d="M{q_exit[0]:.3f} {q_exit[1]:.3f} A{R} {R} 0 0 1 {q_end[0]:.3f} {q_end[1]:.3f}" '
                 f'fill="none" stroke="{MUTED}" stroke-width="1.2" marker-end="url(#arrow)"/>')
    # 기록 스포크(점선, 안쪽으로)
    for k, (name, sub, focal, spoke) in enumerate(STATIONS):
        if not spoke:
            continue
        x, y, th = rects[k]
        ux, uy = math.cos(th), math.sin(th)
        def box_dist(hw2, hh2):
            cands = []
            if abs(ux) > 1e-9:
                cands.append(hw2 / abs(ux))
            if abs(uy) > 1e-9:
                cands.append(hh2 / abs(uy))
            return min(cands)
        pxc, pyc = x + sw_ / 2, y + sh_ / 2
        d_st = box_dist(sw_ / 2, sh_ / 2)
        d_hub = box_dist(hw / 2, hh / 2)
        s = (pxc - d_st * ux, pyc - d_st * uy)
        e = (cx + (d_hub + 6) * ux, cy + (d_hub + 6) * uy)
        b.append(f'<line x1="{s[0]:.3f}" y1="{s[1]:.3f}" x2="{e[0]:.3f}" y2="{e[1]:.3f}" stroke="{SOFT}" stroke-width="1" '
                 f'stroke-dasharray="5,4" marker-end="url(#arrow-soft)"/>')
        # 라벨 없음 — 스테이션 부제가 기록 내용을 이미 말한다(type-loop §2.3)
    # 스테이션
    for k, (name, sub, focal, spoke) in enumerate(STATIONS):
        x, y, th = rects[k]
        b.append(box(x, y, sw_, sh_, fill=ACCENT_TINT if focal else PAPER, stroke=ACCENT if focal else INK, sw=1.2 if focal else 1))
        b.append(text(x + sw_ / 2, y + 28, name, size=16, weight=600, fill=ACCENT if focal else INK, anchor="middle"))
        b.append(text(x + sw_ / 2, y + 48, sub, size=12, weight=400, fill=MUTED, anchor="middle"))
    # 허브
    hx, hy = cx - hw / 2, cy - hh / 2
    b.append(f'<rect x="{hx}" y="{hy}" width="{hw}" height="{hh}" rx="8" fill="{INK}"/>')
    b.append(text(cx, cy - 8, "연구 프로필", size=16, weight=600, fill=PAPER, anchor="middle"))
    b.append(text(cx, cy + 14, "키워드 · 가중치 · 제외어", size=12, weight=400, fill="#D1D5DB", anchor="middle"))
    b.append(text(cx, cy + 34, "모든 변경은 revision — 되돌리기 가능", size=12, weight=400, fill="#D1D5DB", anchor="middle"))
    # 범례
    ly = 684
    b.append(f'<line x1="40" y1="{ly-8}" x2="1240" y2="{ly-8}" stroke="{RULE}" stroke-width="0.8"/>')
    b.append(mono(40, ly + 10, "LEGEND", size=8, fill=MUTED, spacing="0.14em"))
    b.append(f'<line x1="120" y1="{ly+5}" x2="160" y2="{ly+5}" stroke="{MUTED}" stroke-width="1.2" marker-end="url(#arrow)"/>')
    b.append(text(172, ly + 10, "매일 흐름", size=12, weight=500, fill=MUTED))
    b.append(f'<line x1="260" y1="{ly+5}" x2="300" y2="{ly+5}" stroke="{SOFT}" stroke-width="1" stroke-dasharray="5,4" marker-end="url(#arrow-soft)"/>')
    b.append(text(312, ly + 10, "프로필에 기록 (revision)", size=12, weight=500, fill=MUTED))
    b.append(text(1240, ly + 10, "최초 키워드는 사용자가 정하고 자동으로 지우지 않는다 (규칙 1)", size=12, weight=500, fill=MUTED, anchor="end"))
    return svg("feedback-loop", "메일 반응이 다음 메일을 바꾸는 루프",
               "아침 메일의 1클릭 반응이 수집·격리를 거쳐 매일 가중치를 바꾸고, 주 1회 에이전트가 키워드를 손질하며, 모든 변경이 연구 프로필에 revision 으로 남는 순환을 보여 준다.",
               "\n".join(b))


# ════════════════════════════════════════════════════════════════════
# ③ 코드 탐색 사다리 (Flowchart, 가로 사슬)
# ════════════════════════════════════════════════════════════════════
LADDER = [
    ("1", "공식 저장소", "논문이 적은 링크", ["격리 컨테이너에서 실행", "성공·실패 그대로 기록"], False),
    ("2", "저자 연관", "저자·소속 저장소", ["같은 실행 경로", "'저자 연관' 라벨"], False),
    ("3", "제3자 구현", "다른 사람의 재현", ["이름만 같은 저장소는 제외", "'확인되지 않음' 표시"], False),
    ("4", "유사 구현", "같은 과제 최다 ★ 저장소", ["보여 주기만, 실행 안 함", "'이 논문 재현 아님' 명시"], True),
    ("5", "없음", "찾지 못함", ["'없음' 그대로", "만들어 넣지 않는다 (규칙 7)"], False),
]


def diagram_ladder():
    b = []
    bw, bh, gap = 192, 96, 48
    x_start = 64
    y = 272
    xs = [x_start + i * (bw + gap) for i in range(len(LADDER))]
    # 화살표 먼저 (같은 y → 직선 허용)
    for i in range(len(LADDER) - 1):
        x1 = xs[i] + bw
        x2 = xs[i + 1]
        yy = y + bh / 2
        b.append(f'<line x1="{x1}" y1="{yy}" x2="{x2 - 1}" y2="{yy}" stroke="{MUTED}" stroke-width="1.2" marker-end="url(#arrow)"/>')
        # 라벨 "없으면" — 한글 12px sans, 선 위 6px 이상 띄움
        wlab = 44
        lx = (x1 + x2) / 2 - wlab / 2
        b.append(f'<rect x="{lx}" y="{yy-26}" width="{wlab}" height="16" rx="2" fill="{PAPER}"/>')
        b.append(text((x1 + x2) / 2, yy - 14, "없으면", size=12, weight=500, fill=MUTED, anchor="middle"))
    # 상자
    for i, (num, name, sub, notes, focal) in enumerate(LADDER):
        x = xs[i]
        b.append(box(x, y, bw, bh, fill=ACCENT_TINT if focal else PAPER, stroke=ACCENT if focal else INK, sw=1.2 if focal else 1))
        b.append(f'<rect x="{x+12}" y="{y+10}" width="20" height="14" rx="2" fill="transparent" stroke="{ACCENT if focal else INK}" stroke-opacity="0.4" stroke-width="0.8"/>')
        b.append(mono(x + 22, y + 21, num, size=9, fill=ACCENT if focal else INK, anchor="middle", spacing="0"))
        b.append(text(x + bw / 2, y + 54, name, size=16, weight=600, fill=ACCENT if focal else INK, anchor="middle"))
        b.append(text(x + bw / 2, y + 76, sub, size=12, weight=400, fill=MUTED, anchor="middle"))
        # 아래 결과 메모 (연결선 없음 — 배치가 관계를 말한다)
        for j, n in enumerate(notes):
            b.append(text(x + bw / 2, y + bh + 40 + j * 20, n, size=12, weight=500 if j == 0 else 400,
                          fill=ACCENT if (focal and j == 0) else MUTED, anchor="middle"))
    # 찾은 결과는 아래로 — 짧은 안내 화살표 하나(첫 상자 아래), 나머지는 배치로 읽힌다
    for i in range(len(LADDER)):
        x = xs[i] + bw / 2
        b.append(f'<line x1="{x}" y1="{y+bh}" x2="{x}" y2="{y+bh+20}" stroke="{SOFT}" stroke-width="1" marker-end="url(#arrow-soft)"/>')
    # 편집자 주석
    b.append(text(640, 528, "메일에는 단계 이름 그대로 적는다 — 공식이 아닌 코드를 공식이라고 쓰지 않는다.", size=14, weight=400, fill=MUTED, anchor="middle", font=SERIF))
    b.append(text(640, 554, "그전엔 못 찾으면 그냥 비어 있었다 (9/14 피드백: \"비슷한 거라도 반드시\").", size=14, weight=400, fill=MUTED, anchor="middle", font=SERIF))
    # 상단 입력 표시
    b.append(mono(64, 236, "INPUT", size=8, fill=SOFT, spacing="0.14em"))
    b.append(text(64 + 52, 236, "요약이 끝난 논문 한 편마다 · 결과는 한 달 캐시", size=12, weight=500, fill=MUTED))
    # 범례
    ly = 616
    b.append(f'<line x1="40" y1="{ly-8}" x2="1240" y2="{ly-8}" stroke="{RULE}" stroke-width="0.8"/>')
    b.append(mono(40, ly + 10, "LEGEND", size=8, fill=MUTED, spacing="0.14em"))
    b.append(f'<rect x="120" y="{ly-3}" width="24" height="14" rx="3" fill="{ACCENT_TINT}" stroke="{ACCENT}" stroke-width="1"/>')
    b.append(text(154, ly + 10, "9/16 새로 붙인 단계", size=12, weight=500, fill=MUTED))
    b.append(text(1240, ly + 10, "code_ladder.py · 단계는 이미 있는 사실(repro_results·저자 이름)로만 정한다", size=12, weight=500, fill=MUTED, anchor="end"))
    return svg("code-ladder", "코드 탐색 사다리 — 없으면 한 칸 아래로",
               "논문의 구현 코드를 공식 저장소부터 저자 연관, 제3자, 유사 구현 순으로 찾고, 어느 단계에서 찾았는지를 메일에 그대로 표시하는 다섯 단계를 보여 준다.",
               "\n".join(b))


# ════════════════════════════════════════════════════════════════════
# ④ 보안 층 (Layer stack — 보완 계층)
# ════════════════════════════════════════════════════════════════════
LAYERS = [
    ("L1", "메일 링크", "허용 호스트 25개 · https 만 · 못 통과한 링크는 빼고 발송", "—", True),
    ("L2", "논문 PDF", "30MB · 120초 · 리다이렉트마다 IP·localhost·userinfo 거부", "DNS 뒤 사설 IP 판정은 미구현", False),
    ("L3", "코드 저장소", "clone 전 500MB 확인 · blob 20MB · LFS 제외 · clone 뒤 재확인", "—", False),
    ("L4", "격리 실행", "네트워크 없음 · 권한 전부 제거 · 읽기 전용 · nobody · pids/메모리/CPU 상한", "—", False),
    ("L5", "감시", "06:30 메일 부재 경보(운영자 1인) · 본문 인젝션 의심 표시", "인젝션은 표시만, 차단 안 함", False),
]


def diagram_layers():
    b = []
    x, w = 160, 960
    lh, y0 = 88, 88
    b.append(text(x, 64, "외부 입력 — 논문·링크·저장소는 신뢰하지 않는다 (규칙 4)", size=12, weight=600, fill=MUTED))
    b.append(text(x + w, 64, "이 층이 못 막는 것", size=12, weight=600, fill=MUTED, anchor="end"))
    for i, (tag, name, mit, residual, focal) in enumerate(LAYERS):
        y = y0 + i * lh
        fill = PAPER if i % 2 == 0 else PAPER2
        b.append(f'<rect x="{x}" y="{y}" width="{w}" height="{lh}" fill="{fill}"/>')
        if focal:
            b.append(f'<rect x="{x}" y="{y}" width="{w}" height="{lh}" fill="{ACCENT_TINT}"/>')
        if i > 0:
            b.append(f'<line x1="{x}" y1="{y}" x2="{x+w}" y2="{y}" stroke="{RULE}" stroke-width="1"/>')
        b.append(mono(x + 16, y + 24, tag, size=9, fill=ACCENT if focal else SOFT, spacing="0.14em"))
        b.append(text(x + 56, y + 40, name, size=16, weight=600, fill=ACCENT if focal else INK))
        b.append(text(x + 56, y + 64, mit, size=12, weight=400, fill=MUTED))
        if residual != "—":
            b.append(f'<circle cx="{x+w-16}" cy="{y+lh/2}" r="4" fill="none" stroke="{PARTIAL}" stroke-width="1.5"/>')
            b.append(text(x + w - 32, y + lh / 2 + 4, residual, size=12, weight=500, fill=MUTED, anchor="end"))
        else:
            b.append(text(x + w - 32, y + lh / 2 + 4, "—", size=12, weight=500, fill=SOFT, anchor="end"))
    total_h = lh * len(LAYERS)
    b.append(f'<rect x="{x}" y="{y0}" width="{w}" height="{total_h}" fill="none" stroke="{INK}" stroke-width="1"/>')
    if True:  # 초점 층 테두리
        b.append(f'<rect x="{x}" y="{y0}" width="{w}" height="{lh}" fill="none" stroke="{ACCENT}" stroke-width="1.2"/>')
    # 왼쪽 방향 표시
    ax = 112
    b.append(f'<line x1="{ax}" y1="{y0+8}" x2="{ax}" y2="{y0+total_h-8}" stroke="{MUTED}" stroke-width="1" marker-end="url(#arrow)"/>')
    b.append(f'<rect x="{ax-40}" y="{y0+total_h/2-10}" width="80" height="20" rx="2" fill="{PAPER}"/>')
    b.append(text(ax, y0 + total_h / 2 + 4, "처리 순서", size=12, weight=500, fill=MUTED, anchor="middle"))
    # 아래: 최종 남는 위험
    fy = y0 + total_h + 40
    b.append(text(x, fy, "끝까지 남는 것 — 사설 IP 판정과 인젝션 차단은 미구현이라고 적어 둔다 (docs/SECURITY_CHECKLIST.md 10항목).", size=14, weight=400, fill=MUTED, font=SERIF))
    b.append(text(x, fy + 26, "격리 실행 한 층만 그전부터 있었고, 나머지 네 층은 9/16 에 붙였다 (§8-134).", size=14, weight=400, fill=MUTED, font=SERIF))
    # 범례
    ly = 616
    b.append(f'<line x1="40" y1="{ly-8}" x2="1240" y2="{ly-8}" stroke="{RULE}" stroke-width="0.8"/>')
    b.append(mono(40, ly + 10, "LEGEND", size=8, fill=MUTED, spacing="0.14em"))
    b.append(f'<rect x="120" y="{ly-3}" width="24" height="14" rx="2" fill="{ACCENT_TINT}" stroke="{ACCENT}" stroke-width="1"/>')
    b.append(text(154, ly + 10, "피드백 \"메일 바이러스\" 의 직접 답", size=12, weight=500, fill=MUTED))
    b.append(f'<circle cx="{420}" cy="{ly+5}" r="4" fill="none" stroke="{PARTIAL}" stroke-width="1.5"/>')
    b.append(text(434, ly + 10, "이 층이 못 막는 것", size=12, weight=500, fill=MUTED))
    return svg("security-layers", "보안 층 — 외부 입력이 지나는 다섯 관문",
               "메일 링크, 논문 PDF, 코드 저장소, 격리 실행, 감시의 다섯 층이 각각 무엇을 막고 무엇을 못 막는지 보여 준다.",
               "\n".join(b))



# ════════════════════════════════════════════════════════════════════
# ⑤ 주간 관리 — Claude 제안 · Codex 판정 · Python 저장·검증·적용 (Swimlane)
# ════════════════════════════════════════════════════════════════════
def _elbow_v(x1, y1, x2, y2, r=8):
    """세로로 내려가서 가로로 꺾는 둥근 직각 연결(§6 규칙 1). y1<y2 가정."""
    sx = 1 if x2 > x1 else -1
    return (f"M{x1} {y1} V{y2 - r} A{r} {r} 0 0 {0 if sx>0 else 1} {x1 + sx*r} {y2} H{x2}")


def diagram_weekly():
    b = []
    lanes = [("CLAUDE CODE", "해석·제안"), ("CODEX", "비판·판정"), ("PYTHON", "저장·검증·적용·되돌리기")]
    lx, lw = 176, 1064
    ly0, lh = 72, 136
    # 레인
    for i, (name, role) in enumerate(lanes):
        y = ly0 + i * lh
        b.append(f'<rect x="{lx}" y="{y}" width="{lw}" height="{lh}" fill="{PAPER2 if i % 2 else PAPER}"/>')
        b.append(f'<line x1="{lx}" y1="{y}" x2="{lx+lw}" y2="{y}" stroke="{RULE}" stroke-width="1"/>')
        b.append(mono(lx - 16, y + 28, name, size=9, fill=INK, anchor="end", spacing="0.14em"))
        b.append(text(lx - 16, y + 48, role, size=12, weight=500, fill=MUTED, anchor="end"))
    b.append(f'<line x1="{lx}" y1="{ly0+3*lh}" x2="{lx+lw}" y2="{ly0+3*lh}" stroke="{RULE}" stroke-width="1"/>')
    b.append(text(lx, 52, "금요일 17:00 · cron", size=12, weight=600, fill=MUTED))
    b.append(text(lx + lw, 52, "세션을 안 열어도 돈다 · 모델이 답을 못 주면 그 주는 건너뛴다", size=12, weight=500, fill=MUTED, anchor="end"))
    # 단계 (x, lane, name, sub)  — 폭 176, 높이 64
    bw, bh = 176, 64
    def cy_of(lane):
        return ly0 + lane * lh + lh / 2
    steps = [
        (200, 2, "DB 정리", "백업 → 보존표대로 삭제", False),
        (408, 2, "브리프", "28일 반응·적중·반복어", False),
        (616, 0, "키워드 제안", "추가·가중치·제외어 JSON", False),
        (824, 1, "판정", "근거 없는 제안은 뺀다", False),
        (1032, 2, "검증·적용", "revision · 다음 메일에 표시", True),
    ]
    rects = [(x, cy_of(l) - bh / 2) for x, l, *_ in steps]
    # 화살표 먼저
    for i in range(len(steps) - 1):
        (x1, l1, *_), (x2, l2, *_) = steps[i], steps[i + 1]
        r1, r2 = rects[i], rects[i + 1]
        if l1 == l2:
            yy = r1[1] + bh / 2
            b.append(f'<line x1="{x1+bw}" y1="{yy}" x2="{x2-1}" y2="{yy}" stroke="{MUTED}" stroke-width="1.2" marker-end="url(#arrow)"/>')
        else:
            # 출발 상자 위/아래 가장자리에서 세로로 나가 목적 상자 왼쪽 가장자리로 꺾어 들어간다
            sx_ = x1 + bw / 2
            if l2 < l1:   # 위로
                y_from = r1[1]
                y_to = r2[1] + bh / 2
                path = f"M{sx_} {y_from} V{y_to + 8} A8 8 0 0 1 {sx_ + 8} {y_to} H{x2 - 1}"
            else:         # 아래로
                y_from = r1[1] + bh
                y_to = r2[1] + bh / 2
                path = f"M{sx_} {y_from} V{y_to - 8} A8 8 0 0 0 {sx_ + 8} {y_to} H{x2 - 1}"
            focal = steps[i + 1][4]
            b.append(f'<path d="{path}" fill="none" stroke="{ACCENT if focal else MUTED}" stroke-width="1.2" '
                     f'marker-end="url(#{"arrow-accent" if focal else "arrow"})"/>')
    # 상자
    for (x, l, name, sub, focal), (rx_, ry_) in zip(steps, rects):
        b.append(box(rx_, ry_, bw, bh, fill=ACCENT_TINT if focal else PAPER, stroke=ACCENT if focal else INK, sw=1.2 if focal else 1))
        b.append(text(rx_ + bw / 2, ry_ + 28, name, size=16, weight=600, fill=ACCENT if focal else INK, anchor="middle"))
        b.append(text(rx_ + bw / 2, ry_ + 48, sub, size=12, weight=400, fill=MUTED, anchor="middle"))
    # 검증 규칙 — 두 모델이 동의해도 코드가 막는다
    gy = ly0 + 3 * lh + 36
    b.append(text(lx, gy, "적용 전 Python 이 강제하는 것 — 두 모델이 동의해도 통과 못 한다 (agent_maintenance.validate)", size=12, weight=600, fill=INK))
    rules = ["새 키워드·상향은 좋다고 반응한 논문에 그 용어가 있을 때만", "제외어는 서로 다른 관심 밖 논문 2편 이상", "사용자가 정한 키워드는 지우지 않는다 — 하향만 (규칙 1)",
             "증거 id 실재 · 표기 변형·우산어 거르기 · 출력에 시크릿 모양이 있으면 그 주는 버린다"]
    for i, r in enumerate(rules):
        b.append(f'<circle cx="{lx+6}" cy="{gy+22+i*22-4}" r="2.5" fill="{MUTED}"/>')
        b.append(text(lx + 18, gy + 22 + i * 22, r, size=12, weight=400, fill=MUTED))
    # 범례
    lyy = 684
    b.append(f'<line x1="40" y1="{lyy-8}" x2="1240" y2="{lyy-8}" stroke="{RULE}" stroke-width="0.8"/>')
    b.append(mono(40, lyy + 10, "LEGEND", size=8, fill=MUTED, spacing="0.14em"))
    b.append(f'<rect x="120" y="{lyy-3}" width="24" height="14" rx="3" fill="{ACCENT_TINT}" stroke="{ACCENT}" stroke-width="1"/>')
    b.append(text(154, lyy + 10, "실제로 DB 를 바꾸는 유일한 단계", size=12, weight=500, fill=MUTED))
    b.append(text(1240, lyy + 10, "매일 05:00 가중치 조정은 Python 만 — 모델 없이 반응대로 (feedback_weights)", size=12, weight=500, fill=MUTED, anchor="end"))
    return svg("weekly-agent", "주간 관리 — 제안은 모델, 적용은 코드",
               "금요일 17:00 에 Python 이 DB 를 정리하고 브리프를 만들면 Claude 가 키워드 변경을 제안하고 Codex 가 판정하며 Python 이 규칙으로 검증해 revision 으로 적용하는 흐름을 보여 준다.",
               "\n".join(b))


# ════════════════════════════════════════════════════════════════════
# ⑥ 코드 재현 — 격리 실행 (Process, 가로)
# ════════════════════════════════════════════════════════════════════
def diagram_repro():
    b = []
    bw, bh, gap = 168, 96, 28
    xs = [66 + i * (bw + gap) for i in range(6)]      # 6칸이라 오른쪽 여백을 남기려면 폭·간격을 줄여야 한다
    y = 272
    nodes = [
        ("논문 → 저장소", "사다리 1~3단계에서 찾은 것", "clone 전 500MB 확인", False),
        ("설치", "시도마다 전용 빌더", "빌드 단계만 네트워크", False),
        ("격리 실행", "--network none", "cap-drop ALL · read-only", False),
        ("판정", "exit code 만 본다", "논문 수치 재현이 아니다", False),
        ("기록", "실행 로그 JSON · DB 결과", "지우기 전에 먼저 남긴다", False),
        ("정리", "이미지·빌더·컨테이너", "clone 까지 — 실패해도 (9/18)", True),
    ]
    for i in range(5):
        yy = y + bh / 2
        b.append(f'<line x1="{xs[i]+bw}" y1="{yy}" x2="{xs[i+1]-1}" y2="{yy}" stroke="{MUTED}" stroke-width="1.2" marker-end="url(#arrow)"/>')
    # 재시도: 판정 → 설치 (아래로 돌아가는 둥근 직각 경로)
    px1, px2 = xs[3] + bw / 2, xs[1] + bw / 2      # 판정 → 설치(다음 후보)
    yb = y + bh + 56
    b.append(f'<path d="M{px1} {y+bh} V{yb-8} A8 8 0 0 1 {px1-8} {yb} H{px2+8} A8 8 0 0 1 {px2} {yb-8} V{y+bh+1}" fill="none" stroke="{SOFT}" stroke-width="1" stroke-dasharray="5,4" marker-end="url(#arrow-soft)"/>')
    lab = "실패하면 다음 후보로 — 최대 3회"
    wl = 4 * math.ceil((len(lab) * 12 * 0.8 + 8) / 4)
    mx = (px1 + px2) / 2
    b.append(f'<rect x="{mx-wl/2}" y="{yb+8}" width="{wl}" height="16" rx="2" fill="{PAPER}"/>')
    b.append(text(mx, yb + 20, lab, size=12, weight=500, fill=SOFT, anchor="middle"))
    for (name, s1, s2, focal), x in zip(nodes, xs):
        b.append(box(x, y, bw, bh, fill=ACCENT_TINT if focal else PAPER, stroke=ACCENT if focal else INK, sw=1.2 if focal else 1))
        b.append(text(x + bw / 2, y + 36, name, size=16, weight=600, fill=ACCENT if focal else INK, anchor="middle"))
        b.append(text(x + bw / 2, y + 58, s1, size=12, weight=400, fill=MUTED, anchor="middle"))
        b.append(text(x + bw / 2, y + 78, s2, size=12, weight=400, fill=MUTED, anchor="middle"))
    b.append(text(64, 228, "논문·저장소는 신뢰하지 않는 입력 — 논문 속 명령문은 데이터로만 (규칙 4)", size=12, weight=600, fill=MUTED))
    b.append(text(640, 508, "재현 성공 = \"설치되고 실행이 exit 0 으로 끝났다\" 까지다. 논문의 숫자를 다시 얻었다는 뜻이 아니다.", size=14, weight=400, fill=MUTED, anchor="middle", font=SERIF))
    b.append(text(640, 534, "그전엔 빌드 캐시와 성공 clone 이 계속 쌓여 C 드라이브를 채웠다 — 이제 판정 뒤 전부 지운다.", size=14, weight=400, fill=MUTED, anchor="middle", font=SERIF))
    lyy = 600
    b.append(f'<line x1="40" y1="{lyy-8}" x2="1240" y2="{lyy-8}" stroke="{RULE}" stroke-width="0.8"/>')
    b.append(mono(40, lyy + 10, "LEGEND", size=8, fill=MUTED, spacing="0.14em"))
    b.append(f'<rect x="120" y="{lyy-3}" width="24" height="14" rx="3" fill="{ACCENT_TINT}" stroke="{ACCENT}" stroke-width="1"/>')
    b.append(text(154, lyy + 10, "2026-09-18 새로 붙인 단계", size=12, weight=500, fill=MUTED))
    b.append(text(1240, lyy + 10, "전역 prune·볼륨 삭제는 하지 않는다 — 이번 시도가 만든 것만 지운다", size=12, weight=500, fill=MUTED, anchor="end"))
    return svg("repro-isolation", "코드 재현 — 격리 실행 후 흔적을 남기지 않는다",
               "찾은 저장소를 네트워크 없는 격리 컨테이너에서 설치·실행하고 exit code 로 판정한 뒤, 근거를 기록하고 이미지·빌더·clone 을 모두 지우는 여섯 단계를 보여 준다.",
               "\n".join(b))


PAGES = [
    ("01-feedback-map", "Feedback → Change · paper-harness", "9/14 피드백 6개 → 9/17 반영",
     "월요일 피드백 한 줄씩, 지금 어디에 어떻게 들어갔는지", diagram_mapping),
    ("02-feedback-loop", "Loop · paper-harness", "메일 반응이 다음 메일을 바꾸는 루프",
     "1클릭 반응 → 매일 가중치 → 주 1회 키워드, 전부 revision 으로", diagram_loop),
    ("03-code-ladder", "Flowchart · paper-harness", "코드 탐색 사다리 — 없으면 한 칸 아래로",
     "공식 → 저자 연관 → 제3자 → 유사 구현 → 없음, 단계 이름을 메일에 그대로", diagram_ladder),
    ("04-security-layers", "Layer stack · paper-harness", "보안 층 — 외부 입력이 지나는 다섯 관문",
     "한 달 무인 운영 전에 붙인 상한과 그래도 남는 것", diagram_layers),
    ("05-weekly-agent", "Swimlane · paper-harness", "주간 관리 — 제안은 모델, 적용은 코드",
     "DB 정리 → 브리프 → Claude 제안 → Codex 판정 → Python 검증·적용", diagram_weekly),
    ("06-repro-isolation", "Process · paper-harness", "코드 재현 — 격리 컨테이너에서 exit code 로만 판정",
     "찾은 저장소 → 설치 → 격리 실행 → 판정 → 메일 라벨, 최대 3회", diagram_repro),
]

if __name__ == "__main__":
    combined = []
    for slug, eyebrow, title, sub, fn in PAGES:
        markup = fn()
        (OUT / f"{slug}.html").write_text(page(slug, eyebrow, title, markup, sub), encoding="utf-8")
        combined.append((eyebrow, title, sub, markup))
        print("wrote", slug)
    # 한 파일에 4장
    sections = "\n".join(
        f'<section class="one"><p class="eyebrow">{esc(e)}</p><h1>{esc(t)}</h1><p class="sub">{esc(s)}</p>{m}</section>'
        for e, t, s, m in combined)
    html = page("all", "paper-harness · 2026-09-17", "피드백 → 반영 · 다이어그램 6장", "", None)
    html = html.replace('<p class="eyebrow">paper-harness · 2026-09-17</p>\n    <h1>피드백 → 반영 · 다이어그램 6장</h1>\n    \n    ',
                        sections).replace("svg { width: 100%; min-width: 900px; display: block; }",
                                          "svg { width: 100%; min-width: 900px; display: block; }\n    .one { margin-bottom: 4rem; }")
    (OUT / "all-four.html").write_text(html, encoding="utf-8")
    print("wrote all-four")
