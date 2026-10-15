"""④⑤ 성능 결과 — 논문이 보고한 (벤치마크·지표·값)을 뽑는다. 결정적, LLM·네트워크 없음(표 HTML 은 호출부가 준다).

2026-09-30 방향 전환(사용자): "SOTA 라는 말이 있을 때만" 보면 최신 논문 대부분을 놓친다(최근 123편 중 주장 7편). 그래서 **모든 최종 후보**에서
결과를 뽑아 관측 DB(`frontier_store`)에 쌓고, 성능 경계를 민 후보를 찾는다. `sota_claims` 는 버리지 않고 우선순위 신호 하나로 쓴다.

두 갈래이고 **쓰임이 다르다**:
- **표 결과**(`table_results`) — arXiv HTML 표의 행(방법) × 열(벤치마크·지표) 교차 셀. 구조가 확인된 값이라 **수치 비교에 쓴다**.
- **문장 근거**(`performance_evidence`) — 이 논문 자신의 결과를 말한 원문 문장. 구조가 없어 **숫자 비교는 하지 않고** 원문 주장으로만 보인다
  (사용자 결정 2026-09-30: 표를 못 읽었다고 주장을 버리지는 않는다, 숫자 비교만 안 한다).
"""
from __future__ import annotations

import functools
import re

import sentence_grounding

MAX_BENCHMARKS = 2
MAX_QUOTE_CHARS = 200

# 지표 — 약어는 대소문자를 가린다("AP"·"SR" 은 소문자면 다른 낱말이다), 구절은 가리지 않는다.
_METRIC_ABBR = re.compile(
    r"(?<!\w)(AUROC|AUPRO|AUPR|mAP(?:@[\d.:]+)?|AP(?:50|75)?|mIoU|FB-IoU|IoU|F1|PSNR|SSIM|LPIPS|FID|BLEU|ROUGE(?:-[L12])?|EM|"
    r"SR|DS|RC|WER|CER|MRR|nDCG(?:@\d+)?|Dice|PCK|ADE|FDE|EPE|MAE|RMSE|NDS|HOTA|MOTA)(?![\w-])")
_METRIC_PHRASE = re.compile(
    r"\b(accuracy|solve rates?|success(?: rates?)?|driving score|route completion|error rates?|collision rates?|top-1|top-5|recall@\d+|pass@\d+|"
    r"precision|recall|f1[- ]score|exact match|win rate|(?:any[- ])?medal rates?)\b", re.IGNORECASE)
# 수치 — 소수 또는 백분율만. 정수는 "Table 1"·"[41]"·연도와 구별이 안 된다.
_VALUE_RE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?\s?%|(?<![\w.])\d+\.\d+(?![\w.%])")   # 백분율을 먼저 — "10.5" 만 잡고 "%" 를 흘리지 않게
_CAPTION_RE = re.compile(r"^\s*(?:table|fig(?:ure)?\.?)\s*[\dIVX]+", re.IGNORECASE)
_OWN_RE = re.compile(r"\b(?:we|our|ours|proposed|achiev\w*|reach\w*|improv\w*|outperform\w*|surpass\w*|gain\w*|attain\w*)\b", re.IGNORECASE)


def _metric(sentence: str) -> str:
    m = _METRIC_ABBR.search(sentence) or _METRIC_PHRASE.search(sentence)
    return m.group(1) if m else ""


def _bench_re(name: str) -> re.Pattern:
    return re.compile(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", re.IGNORECASE)


def _sentence_rank(sentence: str) -> tuple:
    """근거 문장 고르기. 표가 깨진 줄(수치가 7개 이상·LaTeX 잔재)과 캡션은 뒤로, 이 논문 성과를 말하는 문장을 앞으로."""
    values = len(_VALUE_RE.findall(sentence))
    junk = values >= 7 or "\\" in sentence
    return (junk, bool(_CAPTION_RE.match(sentence)), not _OWN_RE.search(sentence))


def _window(sentence: str, pat: re.Pattern) -> str:
    """긴 문장은 **벤치마크 언급 둘레**로 자른다 — 앞에서부터 자르면 정작 벤치마크·지표가 잘려 나간다(실측: Real3D-AD 가 잘렸다)."""
    if len(sentence) <= MAX_QUOTE_CHARS:
        return sentence
    m = pat.search(sentence)
    center = m.start() if m else 0
    start = max(0, min(center - MAX_QUOTE_CHARS // 2, len(sentence) - MAX_QUOTE_CHARS))
    body = sentence[start:start + MAX_QUOTE_CHARS].strip()
    return ("…" if start > 0 else "") + body + ("…" if start + MAX_QUOTE_CHARS < len(sentence) else "")


# 이 논문 자신의 결과라는 단서. 동사(achieve·improve…)만으로는 안 된다 — "Previous methods obtain 99.1% AUROC" 도 동사가 있다
# (Codex 검토 2026-09-30 재현). 1인칭·제안 기법·제목의 기법 이름만 받는다.
_SELF_RE = re.compile(r"\b(?:we|our|ours|the proposed|proposed (?:method|approach|model|framework)|this (?:paper|work))\b", re.IGNORECASE)
_OTHERS_RE = re.compile(r"\b(?:previous|prior|existing|baseline|competing|other)\s+(?:[\w-]+\s+){0,2}?(?:methods?|approaches|works?|models?)\b"
                        r"\s+(?:obtain|achiev|report|reach|get|score)\w*", re.IGNORECASE)
# 성능이 아닌 수치 — "10.5% of the training samples" 는 데이터 비율이다(Codex 검토 재현).
_NOT_SCORE_RE = re.compile(r"^\s*%?\s*(?:of|for)\s+(?:the\s+)?(?:training|train|labeled|labelled|annotated|data|dataset|samples?|images?|"
                           r"labels?|parameters?|budget|compute|time|runtime)\b", re.IGNORECASE)
_ADJACENT = 40                  # 지표 이름과 수치 사이 최대 글자 수


def _scored_value(sentence: str) -> bool:
    """지표 이름 **가까이에** 성능 수치가 있는가. 같은 문장 안에 있기만 한 숫자는 근거가 아니다."""
    metrics = [m.span() for m in _METRIC_ABBR.finditer(sentence)] + [m.span() for m in _METRIC_PHRASE.finditer(sentence)]
    for v in _VALUE_RE.finditer(sentence):
        if re.search(r"(?:§|\bsection|\bsec\.?)\s*$", sentence[:v.start()], re.I):
            continue                                        # §4.4는 소수처럼 생겼지만 성능 값이 아니다
        if _NOT_SCORE_RE.match(sentence[v.end():]):
            continue
        if any(v.start() - me <= _ADJACENT and v.start() >= me or ms - v.end() <= _ADJACENT and ms >= v.end()
               for ms, me in metrics):
            return True
    return False


def _about_this_paper(sentence: str, method: str) -> bool:
    if method and re.search(r"(?<![\w-])" + re.escape(method) + r"(?![\w-])", sentence, re.IGNORECASE):
        return True
    return bool(_SELF_RE.search(sentence)) and not _OTHERS_RE.search(sentence)


def method_name(title: str | None, opening: str = "") -> str:
    """제목의 이름을 우선하고 도입부의 직접 제안 토큰만 보충해 일반 제목을 이름으로 만들지 않는다."""
    head = (title or "").split(":", 1)[0].strip()
    # 제목 전체 이름과 저자의 직접 제안 약어가 연결될 때만 약어를 우선한다(2026-10-13).
    opening = " ".join(opening[:6000].split())
    if head:
        aliases = set(re.findall(r"\bwe\s+(?:propose|introduce|present)\s+" + re.escape(head)
                                 + r"\s*\(\s*([A-Z][A-Z0-9]{1,9})\s*\)", opening, re.I))
        initials = "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", head)).upper()
        if len(aliases) == 1:
            alias = next(iter(aliases))
            if alias.isupper() and alias == initials:
                return alias
    if ":" in (title or "") and 2 <= len(head) <= 30 and len(head.split()) <= 3:
        return head
    # 도입부에서 저자가 직접 제안한 단일 고유 토큰만 받는다. 일반 명사와 여러 제안은 추측하지 않는다.
    names = re.findall(r"\bwe\s+(?:propose|introduce|present)\s+([A-Za-z][A-Za-z0-9-]{1,29})(?=\s*[,(])",
                       " ".join(opening[:6000].split()), re.I)
    names = list(dict.fromkeys(n for n in names if sum(c.isupper() for c in n) >= 2))
    return names[0] if len(names) == 1 else ""


def performance_evidence(text: str, claims: list[dict], source: str, method: str = "") -> list[dict]:
    """주장의 벤치마크마다 (벤치마크·지표·수치가 한 문장에 있는) 근거 문장 하나. [{benchmark, metric, index, sentence, source}].

    source 가 'text' 면 index 는 원문 S번호, 'abstract' 면 None(초록 문장 번호는 원문 S번호가 아니다)."""
    benches = list(dict.fromkeys(b for c in claims for b in c.get("benchmarks") or []))
    if not text:
        return []
    sentences = sentence_grounding.segment_sentences(text)
    out: list[dict] = []
    for bench in benches:
        pat = _bench_re(bench)
        found = [(i, s) for i, s in enumerate(sentences, start=1)
                 if len(s) <= 600 and pat.search(s) and _metric(s) and _scored_value(s) and _about_this_paper(s, method)]
        if not found:
            continue
        i, s = min(found, key=lambda item: (_sentence_rank(item[1]), item[0]))
        if _sentence_rank(s)[0]:
            continue                                        # 깨진 표 줄밖에 없으면 근거로 싣지 않는다
        quote = _window(" ".join(s.split()), pat)
        out.append({"benchmark": bench, "metric": _metric(s), "index": i if source == "text" else None,
                    "sentence": quote, "source": source})
        if len(out) >= MAX_BENCHMARKS:
            break
    return out or reported_sentences(text, source)


def reported_sentences(text: str, source: str) -> list[dict]:
    """SOTA·벤치마크 이름이 없어도 저자 자신의 성능 보고는 남긴다. 비교 DB에 넣는 구조화 수치는 아니다.

    제목의 기법 이름만 언급한 관련 연구는 받지 않는다. 앞선 자기 결과를 잇는 It 문장만 제한적으로 허용한다.
    2026-10-02 VHop의 L4·L5 원문 수치가 SOTA 벤치마크 문지기에 가려진 결함을 막는다."""
    out: list[dict] = []
    previous_own = False
    previous_caption = False
    for index, sentence in enumerate(sentence_grounding.segment_sentences(text), 1):
        own = bool(_SELF_RE.search(sentence)) and not _OTHERS_RE.search(sentence)
        continuation = previous_own and bool(re.match(r"^It (?:also )?(?:improves|achieves|reaches|obtains)\b", sentence, re.I))
        good = (len(sentence) <= 320 and not previous_caption and not any(_sentence_rank(sentence)[:2])
                and _scored_value(sentence) and (own or continuation))
        previous_own = good and own
        previous_caption = bool(_CAPTION_RE.match(sentence)) and len(sentence) < 50
        if not good:
            continue
        out.append({"benchmark": "", "metric": _metric(sentence), "index": index if source == "text" else None,
                    "sentence": " ".join(sentence.split()), "source": source})
        if len(out) >= MAX_BENCHMARKS:
            break
    return out


# ---------------------------------------------------------------- 외부 조회(무료 API) — 테스트는 conftest 가 `_get_json` 을 막는다

S2_MIN_INTERVAL_S = 1.1        # S2 한도는 "초당 1회, 엔드포인트 합산"이다(http_client 와 같은 값)
_last_s2 = 0.0



# ---------------------------------------------------------------- 표 결과 — 수치 비교에 쓰는 유일한 경로

_LOWER_BETTER_RE = re.compile(r"(?i)(?<![\w-])(?:FID|LPIPS|WER|CER|ADE|FDE|EPE|MAE|RMSE|MSE|CD|error|err|collision|latency|miss rate|"
                              r"chamfer|infraction|violation)(?![\w-])|↓")
_HIGHER_HINT_RE = re.compile(r"↑")
_OURS_RE = re.compile(r"(?i)\bours\b|\(ours\)|\bproposed\b|\bour (?:method|model|approach)\b")
_MEAN_RE = re.compile(r"(?i)\b(?:mean|avg\.?|average|overall|total|all)\b")
_METHOD_HEADER_RE = re.compile(r"(?i)^(?:methods?|models?|approach(?:es)?|baselines?|settings?|backbone)$")
_ABLATION_RE = re.compile(r"(?i)\babla(?:tion|te|ting)\b|\bcontribution of\b|\beffect of\b")
MAX_COLUMNS_PER_KEY = 8          # 한 (벤치마크, 지표)에 열이 이보다 많으면 범주별 열이다 — 평균 열만 쓴다


_ARROW_RE = re.compile(r"\\(?:uparrow|downarrow|Uparrow|Downarrow)|[↑↓]|\(\s*\)")


def direction(metric_label: str) -> str | None:
    """지표 방향. 표의 ↑/↓(LaTeX `\\uparrow` 포함)가 있으면 그것을, 없으면 알려진 지표 이름으로. 모르면 None — 대소 비교를 하지 않는다."""
    if "↓" in metric_label or re.search(r"\\(?:down|Down)arrow", metric_label):
        return "lower"
    if _HIGHER_HINT_RE.search(metric_label) or re.search(r"\\(?:up|Up)arrow", metric_label):
        return "higher"
    if _LOWER_BETTER_RE.search(metric_label):
        return "lower"
    if _METRIC_ABBR.search(metric_label) or _METRIC_PHRASE.search(metric_label):
        return "higher"
    return None


def clean_label(text: str) -> str:
    """표시용 — 방향 표식·빈 괄호를 뗀다. `O-AUROC(\\uparrow)` → `O-AUROC`."""
    # 수식 alttext 의 `\\text{DS}`·`\\mathrm{AP}` 는 글자만 남긴다(합성 픽스처에서 발견 — LaTeXML 표 머리에 흔한 꼴).
    plain = re.sub(r"\\(?:text|mathrm|textbf|mathbf|textit)\{([^{}]*)\}", r"\1", text or "")
    no_arrow = re.sub(r"\\(?:uparrow|downarrow|Uparrow|Downarrow)|[↑↓]", "", plain)
    return re.sub(r"\s+", " ", re.sub(r"\(\s*\)", "", no_arrow)).strip(" /")


def norm_key(text: str) -> str:
    """비교 키 — `MVTec AD`/`MVTecAD`/`mvtec-ad` 를 하나로. 방향 표식·대소문자·공백·기호만 지운다(뜻을 바꾸는 정규화는 안 한다)."""
    return re.sub(r"[^0-9a-z]+", "", clean_label(text).lower())


# 같은 지표의 긴 이름 — 리더보드 README 는 "Driving Score", 논문 표는 "DS" 로 쓴다(2026-09-30 실측: Bench2Drive). 뜻이 같은 것만.
_METRIC_ALIASES = {"drivingscore": "ds", "successrate": "sr", "successrates": "sr", "routecompletion": "rc", "accuracy": "acc", "top1accuracy": "top1",
                   # 평균 열 이름 — 실측(2026-09-30): GRIM 표는 "Mean", Group3AD 표는 "Average" 로 같은 열을 부른다.
                   "average": "mean", "avg": "mean", "overall": "mean"}


def metric_key(label: str) -> str:
    """지표 키 — norm_key 에 긴 이름 동의어를 더한다. 경로(`1-shot / mIoU`)는 칸마다 바꾼다."""
    parts = [norm_key(p) for p in re.split(r"\s*/\s*", clean_label(label)) if p.strip()]
    return "".join(_METRIC_ALIASES.get(p, p) for p in parts)


def metric_token(text: str) -> str:
    """글에서 지표 이름 하나 — `O-AUROC score on Real3D-AD` → `O-AUROC`(앞 접두 `O-`·`P-` 를 살린다). 없으면 빈 문자열."""
    m = _METRIC_ABBR.search(text or "") or _METRIC_PHRASE.search(text or "")
    if not m:
        return ""
    start = m.start(1)
    pre = re.search(r"([A-Za-z]{1,2}-)$", (text or "")[:start])
    return (pre.group(1) if pre else "") + m.group(1)


def _is_metric(text: str) -> bool:
    return bool(_METRIC_ABBR.search(text) or _METRIC_PHRASE.search(text))


def _bench_in(texts: list[str], candidates: list[str]) -> str:
    for cand in candidates:
        pat = _bench_re(cand)
        if any(pat.search(t) for t in texts):
            return cand
    return ""


def benchmark_candidates(text: str, claims: list[dict] | None = None) -> list[str]:
    """원문에서 벤치마크 이름 후보 — 주장 문장의 것을 먼저, 그다음 원문 전체의 "on X"·"benchmarks: A, B" 꼴."""
    import sota_claims
    out = [b for c in claims or [] for b in c.get("benchmarks") or []]
    for sent in sentence_grounding.segment_sentences(text or "")[:4000]:
        if len(sent) <= 600:
            out.extend(sota_claims._benchmarks(sent))
    # 실제 2026-10-12 재생에서 나온 서지·구현 후보다. 임의의 새 금지어를 늘리지 않는다.
    out.extend(re.findall(r"(?<![\w-])([A-Z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+) benchmark\b", text or ""))
    # -bench 명칭도 고유 벤치마크 토큰이다. 대문자 접두를 요구해 일반 문구를 받지 않는다.
    out.extend(re.findall(r"(?<![\w-])([A-Z][A-Za-z0-9]*-bench)(?![\w-])", text or ""))
    unique: dict[str, str] = {}
    for bench in out:
        if bench and not _BENCH_NOISE.search(bench):
            unique.setdefault(bench.casefold(), bench)
    return list(unique.values())[:30]


# diag.json의 후보 및 그 원문 문맥을 확인한 잡음만 제외한다.
_BENCH_NOISE = re.compile(
    r"(?i)conference|learning representations|information processing systems|robotics and automation|"
    r"^(?:Task Group \d+|Tasks \d+|CPU|GPU|TRL|HuggingFace Transformers|GPT-5|Qwen2|FULL|Robot Learning|"
    r"Intelligent Robots|Robotics 39|Computer Vision|Pattern Recognition|Machine Learning|"
    r"Autonomous Agents|Multiagent Systems|Artificial Intelligence|World Wide Web|"
    r"Language Modeling|Knowledge Discovery|Data Mining|Empirical Methods|Intelligent Transportation Systems)$")


def _unique_metric(text: str) -> str:
    """서로 다른 지표가 함께 있으면 첫 지표를 표 전체에 씌우지 않는다."""
    names = [m.group(1) for pattern in (_METRIC_ABBR, _METRIC_PHRASE) for m in pattern.finditer(text)]
    keys = {metric_key(n) for n in names}
    return names[0] if len(keys) == 1 else ""


def _context_metric(context: str, bench: str) -> str:
    """벤치마크가 직접 나온 문장 또는 On X로 한정한 다음 문장까지만 읽는다.

    문서 전체의 가까운 숫자나 다른 벤치마크 문장을 임의로 연결하지 않는다.
    서로 다른 지표의 근거가 있으면 모두 버린다.
    """
    sentences = [clause.strip() for sentence in sentence_grounding.segment_sentences(context)
                 for clause in sentence.split(";") if clause.strip()]
    evidence = []
    for i, sentence in enumerate(sentences):
        if len(sentence) > 600 or not _bench_re(bench).search(sentence):
            continue
        evidence.append(sentence)
        if re.match(r"On\s+" + re.escape(bench) + r",", sentence, re.I) and i + 1 < len(sentences):
            following = sentences[i + 1]
            if len(following) < 600 and not re.match(r"On\b|Table\b", following, re.I):
                evidence.append(following)
    return _unique_metric(" ".join(evidence))


def _transposed_results(t: dict, ti: int, method: str, benchmarks: list[str]) -> list[dict] | None:
    """자기 열과 Task N 행이 명시된 표만 축을 교환하고 원래 셀 위치를 보존한다."""
    import arxiv_tables as at
    paths = [at.column_path(t, c) for c in range(len(t["grid"][0]))]
    if not any(_OURS_RE.search(" / ".join(p)) or (method and p and _bench_re(method).fullmatch(p[-1])) for p in paths):
        return None
    # 자기 열이 있는데 구조가 모호하면 일반 행 해석으로 되돌리지 않는다.
    labels = [at.row_label(t, r) for r in range(t["header_rows"], len(t["grid"]))]
    if not labels or not all(re.fullmatch(r"Task \d+|Average|Avg\.?|Mean", label, re.I) for label in labels):
        return []
    caption = t.get("caption", "")
    named = [b for b in benchmarks if re.search(r"(?<![\w-])" + re.escape(b) + r" benchmark\b", caption)]
    metric = _unique_metric(caption)
    if len(named) != 1 or not metric:
        return []
    bench = named[0]
    out = []
    for cell in at.find_cells(t, lambda _: True, lambda _: True):
        path = cell["column_path"]
        model = clean_label(" / ".join(path))
        if cell["col"] == 0 or not path:
            continue
        task = cell["row_label"]
        label = metric + " / " + task
        out.append({"benchmark": bench, "metric": label, "bench_key": norm_key(bench),
                    "metric_key": metric_key(label), "direction": direction(metric),
                    "value": cell["value"], "text": cell["text"], "model": model,
                    "own": bool(_OURS_RE.search(model) or (method and _bench_re(method).fullmatch(path[-1]))),
                    "orientation": "transposed",
                    "locator": f"{t.get('id')}#{ti}:r{cell['row']}c{cell['col']}", "caption": caption[:160]})
    return out


_LATEX_SPACING_RE = re.compile(r"^\\{0,2}\[\s*-?[\d.]+\s*(?:pt|em|ex|mm)(?:\s*/\s*-?[\d.]+\s*(?:pt|em|ex|mm))?\s*\]\s*")


def model_label(text: str) -> str:
    """행 이름의 LaTeX 조판 찌꺼기를 뗀다 — `\\[4pt/2pt] POrchestra` 의 `[4pt/2pt]`(2026-10-13 메일 실측: 카드에 그대로 나갔다)."""
    return _LATEX_SPACING_RE.sub("", (text or "").strip()).strip()


_MISSING_CELL_RE = re.compile(r"[-–—−]+|n/?a|N/?A")


def row_blocks(table: dict) -> dict[int, tuple[str, str]]:
    """표 안의 구획 {본문 행 번호: (구획 식별자, 표시 이름)}. **구획 머리 행 자체는 이 dict 에 없다** — 호출부가 그 행의 칸을 값으로 읽지 않게
    (2026-10-13 Codex 검토 P1: 병합된 "Gemini-3-Flash" 머리에서 3.0 을 SWE-Bench 값으로 저장했다).

    구획 머리: 첫 칸에 글자가 있고 나머지가 모두 비었거나 같은 글(LaTeXML colspan 을 파서가 모든 열에 채운 꼴), 또는 모두 결측 표시("-"·N/A).
    결측 표시뿐인 행은 결과 없는 방법일 수도 있다 — 그래도 **경계로** 본다(구획을 잘못 잇느니 비교 대상을 줄인다, Codex 검토 P2).
    식별자는 행 번호를 포함한다 — 떨어져 되풀이되는 같은 이름("Results")을 한 구획으로 합치지 않는다. 숫자 판정(cell_number)은 쓰지 않는다 —
    "Gemini-3-Flash" 를 3.0 으로 읽어 구획 머리를 놓쳤다(2026-10-13 재생). 글자가 있으면 이름이다."""
    grid = table.get("grid") or []
    block, out = ("", ""), {}
    for r in range(table.get("header_rows") or 0, len(grid)):
        row = grid[r]
        first = row[0].strip() if row else ""
        rest = [c.strip() for c in row[1:]]
        if first and re.search(r"[^\W\d_]{2,}", first) and rest and (
                all(not c or c == first for c in rest) or all(_MISSING_CELL_RE.fullmatch(c) for c in rest)):
            label = model_label(first)
            block = (f"{r}:{label}", label)
            continue
        out[r] = block
    return out


def _referenced_benchmark(context: str, caption: str, benchmarks: list[str]) -> str:
    """표 번호가 있는 평가 문장과 바로 앞의 명시적 평가 대상만 연결한다.

    문서 전체의 유일 후보를 모든 표에 배정하지 않고, 상충하는 표 참조는 버린다.
    """
    tag = re.match(r"Table\s+(\d+)\s*[:.]", caption, re.I)
    if not tag:
        return ""
    reference = re.compile(r"\bTable\s+" + tag[1] + r"(?!\d)(?!\.\d)", re.I)
    sentences = _sentences(context)
    found: set[str] = set()
    for i, sentence in enumerate(sentences):
        if len(sentence) > 600 or not reference.search(sentence):
            continue
        # 절 단위 — "We report accuracy on Toy-bench in Table 1; Table 8 lists the configuration." 에서 표 8 에 Toy-bench 를 잇지 않는다.
        # 그 절이 다른 표도 부르면 어느 표의 평가 대상인지 모른다(Codex 검토 P2).
        for clause in re.split(r";|, while\b|, whereas\b", sentence):
            if (not reference.search(clause) or not re.search(r"\b(?:report|evaluat\w*)\b", clause, re.I)
                    or any(n != tag[1] for n in re.findall(r"\bTables?\s+(\d+)", clause, re.I))):
                continue
            evidence = clause
            if i and len(sentences[i - 1]) <= 600 and re.search(
                    r"\b(?:evaluation|evaluate)\s+(?:uses|on)\b", sentences[i - 1], re.I):
                evidence = sentences[i - 1] + " " + evidence
            found.update(b for b in benchmarks if _bench_re(b).search(evidence))
    return next(iter(found)) if len(found) == 1 else ""


def _defined_pass_metric(label: str, context: str) -> str:
    """p@N을 무조건 precision/pass로 정하지 않고 본문의 직접 정의가 있을 때만 보충한다."""
    match = re.fullmatch(r"p@(\d+)", label)
    if not match:
        return ""
    n = match[1]
    definitions = re.findall(r"\bp@" + n + r"\s+(?:is|denotes)\s+([^.!?]{1,100})", " ".join(context.split()))
    meaning = r"(?:the\s+)?(?:mean\s+)?Pass@\s*" + n + r"(?!\d)"
    return "pass@" + n if definitions and all(re.match(meaning, d) for d in definitions) else ""


def _table_benchmarks(t: dict, caption: str, benchmarks: list[str], context: str) -> list[str]:
    """표 하나에 캡션·본문 표 참조로 이어지는 벤치마크 후보. 열마다 벤치마크가 있으면 빈 목록(그때는 열 머리가 정한다)."""
    import arxiv_tables
    in_caption = [b for b in benchmarks if _bench_re(b).search(caption)]
    if not in_caption:
        referenced = _referenced_benchmark(context, caption, benchmarks) if context else ""
        if referenced:
            in_caption = [referenced]
    # 벤치마크별 열이 있으면 독립 Avg를 캡션의 한 후보에 배정하지 않는다.
    if any(_bench_in(arxiv_tables.column_path(t, c), benchmarks) for c in range(len(t["grid"][0]))):
        in_caption = []
    return in_caption


def own_label(label: str, method: str) -> bool:
    """이 논문 행인가 — 방법 이름(낱말 경계)·"Ours"·"proposed"."""
    return bool((method and re.search(r"(?<![\w-])" + re.escape(method) + r"(?![\w-])", label, re.IGNORECASE)) or _OURS_RE.search(label))


# 저자 문장의 "지표 of 값%" · "값% 지표". 지표는 이름이 분명한 구절만(_METRIC_PHRASE) — "DS"·"RC" 같은 약어는 우연히 겹친다.
_AUTHOR_VALUE_RE = re.compile(
    r"(?P<m1>" + _METRIC_PHRASE.pattern + r")\s+(?:of|at|=|:)\s*(?P<v1>\d+(?:\.\d+)?)\s?%"
    r"|(?P<v2>\d+(?:\.\d+)?)\s?%\s+(?P<m2>" + _METRIC_PHRASE.pattern + r")", re.IGNORECASE)


def _lead_number(text: str | None) -> str:
    """칸 글의 첫 수치를 글자 그대로("78.2 +8.0" → "78.2"). 저자 문장의 값과 자릿수까지 같아야 같은 값이다.
    수치가 끝까지 끊겨야 한다 — "66.5e-1" 을 66.5 로 읽으면 실제 값(6.65)과 다른 칸을 잇는다(Codex 검토 P2)."""
    m = re.match(r"\s*(\d+(?:\.\d+)?)(?![\d.]|[eE][-+]?\d|[A-Za-z])", text or "")
    return m[1] if m else ""


@functools.lru_cache(maxsize=4)
def _sentences(context: str) -> tuple[str, ...]:
    """본문 문장 분리는 한 번만 — 표마다·주장마다 다시 나누면 주장 수 × 표 수로 커진다(Codex 검토 P2)."""
    return tuple(sentence_grounding.segment_sentences(context))


# 값의 주체가 이 방법이 아닐 수 있는 문형 — 값 앞에 비교·관계절이 오면 그 값은 다른 방법의 것일 수 있다(Codex 검토 P2:
# "NewMethod outperforms Rival, which achieves accuracy of 66.5%"). 값 뒤의 비교("…78.2%, exceeding the baseline")는 상관없다.
_SUBJECT_BREAK_RE = re.compile(r"(?i)\b(?:than|outperform\w*|surpass\w*|beat\w*|exceed\w*|compared|versus|vs\.?|whereas|while|which|who|whose|"
                               r"unlike|against|relative|baselines?)\b")
# 시간·크기·비용 열은 같은 숫자여도 지표 열이 아니다(Codex 검토 P2: "Time (s)" 열에 accuracy 를 붙였다).
_UNIT_COLUMN_RE = re.compile(r"(?i)\b(?:time|seconds?|hours?|minutes?|params?|parameters|flops|tokens?|cost|memory|latency|size)\b|"
                             r"\((?:s|h|ms|min|gb|mb|k|m|b|\$)\)")
MAX_AUTHOR_CLAIMS = 32           # 중복을 뺀 저자 수치 문장 상한 — 비신뢰 원문이 같은 문장을 수천 번 되풀이해도 일이 커지지 않게


_GAIN_CELL_RE = re.compile(
    r"\s*\d+(?:\.\d+)?%?(?:\s*(?P<paren>\()\s*|\s+)"
    r"([+−-])\s?(\d+(?:\.(\d+))?)%?\s*(?(paren)\))\s*")


def _table_metadata(caption: str, path: list[str], text: str, metric: str) -> dict:
    """표가 직접 밝힌 단위·반복 수만 붙여 다른 지표의 설명을 옮기지 않는다."""
    metadata: dict = {}
    percent_metrics = [m.group(1) for pattern in (_METRIC_ABBR, _METRIC_PHRASE)
                       for m in pattern.finditer(caption)
                       if re.match(r"\s*\(\s*%\s*\)", caption[m.end():])]
    row_metric = _unique_metric(metric)
    if ("%" in " / ".join(path) or "%" in text
            or (row_metric and any(metric_key(row_metric) == metric_key(m) for m in percent_metrics))):
        metadata["unit"] = "%"
    # 정상 구절 하나 옆에 숫자 아닌 구절이 있어도 표 전체의 반복 수로 단정하지 않는다.
    mentions = list(re.finditer(r"\baveraged\s+over\b", caption, re.I))
    if len(mentions) == 1:
        count = re.match(r"\s+([0-9]+)\s+(?:random\s+)?(?:seeds|runs|trials)\b",
                         caption[mentions[0].end():], re.I)
        if count and int(count[1]) > 0:
            metadata["averaged_over"] = int(count[1])
    return metadata


def _mark_gains(rows: list[dict]) -> None:
    """칸 글의 부호 붙은 뒤 수치(공백 또는 괄호 표기)가 **향상폭인지 산수로 확인**되면 `gain`·`gain_base_model`·`gain_base_text` 를 단다.

    2026-10-14 사용자가 "66.5 +25.2" 를 합으로 읽었다 — 뒤 수치는 기준 행 대비 증감이다(66.5 − 41.3 = 25.2). 그러나 부호 붙은 둘째 수치가 늘
    증감은 아니다(하한·둘째 지표 — Codex 검토 P2). 그래서 같은 열에서 **값 − 기준 값 = 뒤 수치**(뒤 수치의 자릿수 반올림 안)인 기준이 하나로
    정해질 때만 증감으로 본다: "+Ours" 행이면 바로 위 기준 행, 아니면 같은 구획의 비교 행 중 맞는 것이 하나뿐일 때. 안 맞으면 원 글 그대로 둔다."""
    by_cell = {(r["locator"].rsplit(":r", 1)[1].split("c")[0], r["_col"]): r for r in rows}
    for r in rows:
        base_row = r.pop("_base_row", None)
        m = _GAIN_CELL_RE.fullmatch(r.get("text") or "")
        if not m or not isinstance(r.get("value"), (int, float)):
            continue
        signed = float(m[3]) * (1 if m[2] == "+" else -1)
        tolerance = 0.5 * 10 ** -len(m[4] or "") + 1e-9
        fits = lambda c: isinstance(c.get("value"), (int, float)) and abs((r["value"] - c["value"]) - signed) <= tolerance
        if base_row is not None:
            found = [c for c in [by_cell.get((str(base_row), r["_col"]))] if c is not None and fits(c)]
        else:
            found = [c for c in rows if c is not r and not c["own"] and c["_col"] == r["_col"]
                     and c.get("block_id", "") == r.get("block_id", "") and fits(c)]
        if len({(c["model"], c["text"]) for c in found}) == 1:
            r["gain"] = ("+" if signed >= 0 else "-") + m[3]
            r["gain_base_model"], r["gain_base_text"] = found[0]["model"], found[0]["text"]


def _plus_base_row(table: dict, row: int, blocks: dict[int, tuple[str, str]]) -> int | None:
    """"+Ours"·"+ our method" 처럼 "+" 로 시작하는 행의 기준 행 — **바로 위** 본문 행이 같은 구획이고 "+" 로 시작하지 않을 때만.
    "+A", "+B" 가 이어지는 누적 표는 "+B" 가 무엇에 더한 것인지(기준만인지 기준+A 인지) 표만으로 모른다 — 이름을 잇지 않는다."""
    import arxiv_tables
    prev = row - 1
    if prev < (table.get("header_rows") or 0) or prev not in blocks or blocks.get(row) != blocks[prev]:
        return None
    label = arxiv_tables.row_label(table, prev).strip()
    # 평균·합계 행("Average")은 기준 모델이 아니다(Codex 검토 P2).
    if not label or label.startswith("+") or len(re.findall(r"[A-Za-z]", label)) < 2 or _MEAN_RE.match(label):
        return None
    return prev


def _author_metric_columns(tables: list[dict], *, method: str, benchmarks: list[str], context: str) -> dict[tuple[int, int], dict]:
    """머리에 지표 이름이 없는 열 중 **저자가 문장에서 지표 이름과 함께 말한 값**이 들어 있는 열 — {(표 순번, 열): {metric, sentence, value, row}}.

    2026-10-13 2610.13494(HMA): 핵심 열 "Overall Metrics (%) / All (75)" 에 지표 낱말이 없어 버려지고, 부분집합(Lite 22개) 열이 대표값이 됐다.
    초록은 "Across 75 Kaggle tasks in MLE-bench, HMA reaches ... any-medal rate of 78.2%" 라고 그 값을 직접 말한다. 받는 조건(하나라도 어긋나면 버린다):
    - 문장: 방법 이름이 값 **앞**에 있고 그 사이(와 문장 머리)에 비교·관계절이 없다, 벤치마크 후보는 **하나**, 값은 백분율.
    - 칸: 그 벤치마크 표의 **이 논문 행 전체**(지표가 이미 읽히는 열 포함)에서 같은 값(수치 끝까지 글자 그대로)이 **한 칸**뿐이고 그 칸의 열에
      지표 이름이 없다(있으면 이미 읽힌다 — 같은 값의 엉뚱한 열을 고르지 않는다). 그 열은 백분율 표시가 있고 시간·크기 열이 아니며, 캡션의 지표와
      충돌하지 않고, 머리의 표본 수 "(N)" 이 있으면 같은 N 이 문장에도 있다.
    지표 이름은 그 **열 전체**에 붙인다(같은 열 기준선과 지표 키가 같아야 비교된다). 대표값으로 앞세우는 것은 그 **한 칸**(`row`)뿐이다."""
    import arxiv_tables
    if not method or not context:
        return {}
    name_re = re.compile(r"(?<![\w-])" + re.escape(method) + r"(?![\w-])", re.IGNORECASE)
    claims: dict[tuple[str, str, str], tuple[str, str]] = {}
    for sentence in _sentences(context)[:4000]:
        if len(sentence) > 600 or not name_re.search(sentence):
            continue
        named = [b for b in benchmarks if _bench_re(b).search(sentence)]
        if len({norm_key(b) for b in named}) != 1:
            continue
        for m in _AUTHOR_VALUE_RE.finditer(sentence):
            before = sentence[:m.start()]
            names = list(name_re.finditer(before))
            gap = before[names[-1].end():] if names else ""
            if not names or len(gap) > 80 or "," in gap or re.search(r"(?i)\band\b", gap) or _SUBJECT_BREAK_RE.search(before):
                continue
            metric, value = (m["m1"], m["v1"]) if m["m1"] else (m["m2"], m["v2"])
            claims.setdefault((named[0], metric_key(metric), value), (metric, sentence))
        if len(claims) >= MAX_AUTHOR_CLAIMS:
            break
    if not claims:
        return {}
    # 표마다 한 번만 계산한다 — 벤치마크 배정·구획·이 논문 행의 칸.
    own_cells: list[tuple[int, dict, str, list[str], str]] = []
    for ti, t in enumerate(tables):
        caption = t.get("caption") or ""
        if not t.get("grid") or _ABLATION_RE.search(caption):
            continue
        in_caption = _table_benchmarks(t, caption, benchmarks, context)
        blocks = row_blocks(t)
        for cell in arxiv_tables.find_cells(t, lambda _lbl: True, lambda _path: True):
            path = [x for x in cell["column_path"] if x]
            if cell["row"] not in blocks or not path or not own_label(cell["row_label"], method):
                continue
            bench = _bench_in(path, benchmarks) or (in_caption[0] if len(in_caption) == 1 else "")
            own_cells.append((ti, cell, bench, path, caption))
    found: dict[tuple[int, int], dict] = {}
    rejected: set[tuple[int, int]] = set()
    for (bench, key, value), (metric, sentence) in claims.items():
        hits = [(ti, cell, path, caption) for ti, cell, b, path, caption in own_cells if b == bench and _lead_number(cell["text"]) == value]
        if len(hits) != 1:
            continue                                         # 0 이면 표에 없는 값, 2 이상이면 어느 칸인지 모른다(다른 설정의 자기 행일 수 있다)
        ti, cell, path, caption = hits[0]
        joined = " / ".join(path)
        cap_metric = _unique_metric(caption)
        if (_is_metric(joined) or _UNIT_COLUMN_RE.search(joined)
                or not ("%" in joined or "%" in (cell["text"] or "") or re.search(r"(?i)\bpercent", caption))
                or (cap_metric and metric_key(cap_metric) != key)
                or any(not re.search(r"(?<![\d.])" + n + r"(?![\d.])", sentence) for n in re.findall(r"\((\d+)\)", joined))):
            continue
        slot = (ti, cell["col"])
        if slot in found and metric_key(found[slot]["metric"]) != key:
            rejected.add(slot)                               # 한 열에 서로 다른 지표 — 버린다
        found.setdefault(slot, {"metric": metric, "sentence": sentence[:300], "value": value, "row": cell["row"]})
    return {k: v for k, v in found.items() if k not in rejected}


def table_results(tables: list[dict], *, method: str, benchmarks: list[str], context: str = "") -> list[dict]:
    """표의 교차 셀마다 (벤치마크, 지표, 값, 행). own=True 는 이 논문 행(제목의 기법 이름·"Ours"·"proposed").

    [{benchmark, metric, bench_key, metric_key, direction, value, text, model, own, locator, caption}]
    벤치마크는 **열 머리 → 행 이름 → (후보가 하나만 있을 때) 캡션** 순으로, 원문에서 뽑은 후보 이름과 일치할 때만 정한다 — 모르는 벤치마크를
    지어내지 않는다. 실측(2026-09-30): P³-SAM 표는 데이터셋 이름이 열이 아니라 **행** 앞 칸에 있다(`Surface Defects-4i Ours ResNet50`)."""
    import arxiv_tables
    out: list[dict] = []
    context_metrics: dict[str, str] = {}
    author = _author_metric_columns(tables, method=method, benchmarks=benchmarks, context=context)
    for ti, t in enumerate(tables):
        if not t.get("grid"):
            continue
        caption = t.get("caption") or ""
        cap_metric = _unique_metric(caption)
        if _ABLATION_RE.search(caption):
            continue                                        # 절제 실험 표 — 변형 행은 경쟁 결과가 아니다(행 이름도 "✓" 뿐이다)
        transposed = _transposed_results(t, ti, method, benchmarks)
        if transposed is not None:
            out.extend(transposed)
            continue
        in_caption = _table_benchmarks(t, caption, benchmarks, context)
        rows: list[dict] = []
        blocks = row_blocks(t)
        for cell in arxiv_tables.find_cells(t, lambda _lbl: True, lambda _path: True):
            if cell["row"] not in blocks:
                continue                                    # 구획 머리 행 — 이름 속 숫자("Gemini-3-Flash")를 값으로 읽지 않는다
            path = [p for p in cell["column_path"] if p]
            # 성공 횟수로 나눈 과제 수 열은 pass 점수가 아니다(2026-10-13 로컬 표 대조).
            if any(re.search(r"pass@\d+\s*=\s*\d+\s*/\s*\d+", p, re.I) for p in path):
                continue
            defined = _defined_pass_metric(path[-1], context) if path else ""
            if defined:
                path = path[:-1] + [defined + " / " + path[-1]]
            recovered = author.get((ti, cell["col"]))
            if recovered and path and not _is_metric(" / ".join(path)):
                path = [recovered["metric"]] + path
            bench_hint = _bench_in(path, benchmarks) or (in_caption[0] if len(in_caption) == 1 else "")
            if not _is_metric(caption) and context and bench_hint and bench_hint not in context_metrics:
                context_metrics[bench_hint] = _context_metric(context, bench_hint)
            inferred = cap_metric or (context_metrics.get(bench_hint, "") if not _is_metric(caption) else "")
            # 지표 없는 열은 평균·벤치마크 자체·캡션이 명시한 시행 설정에만 보충한다.
            if path and not _is_metric(" / ".join(path)) and inferred and (
                    (_MEAN_RE.fullmatch(path[-1].rstrip(".")) or all(_bench_re(bench_hint).fullmatch(p) for p in path)
                     or (cap_metric and re.fullmatch(r"[PT]@\d+", path[-1]) and path[-1] in caption))):
                path = [inferred] + path
            joined = " / ".join(path)
            if not _is_metric(joined) or not path:
                continue
            label_row = cell["row_label"]
            named = {pr_b for pr_b in benchmarks if _bench_re(pr_b).search(joined) or _bench_re(pr_b).search(label_row)}
            if len(named) > 1:
                continue                                    # 교차 데이터셋 설정(A → B) — 어느 벤치마크 값인지 하나로 못 정한다
            bench = _bench_in(path, benchmarks) or _bench_in([label_row], benchmarks) or (in_caption[0] if len(in_caption) == 1 else "")
            if not bench:
                continue
            bpat = _bench_re(bench)
            metric = clean_label(" / ".join(p for p in path if not bpat.fullmatch(clean_label(p)))) or clean_label(joined)
            model = model_label(re.sub(r"\s+", " ", bpat.sub("", label_row)).strip() or label_row)
            # 이 논문 행만 이름을 잇는다 — 비교 행 "+Foo-S" 를 "Base + Foo-S" 로 바꾸면 자기 변형 제외(이름 접두 검사)를 빠져나간다(Codex 검토 P2).
            base_row = _plus_base_row(t, cell["row"], blocks) if model.startswith("+") else None
            if base_row is not None and own_label(model, method):
                # "+Ours" 는 바로 위 기준 모델에 이 논문 방법을 붙인 행이다 — 이름만 "+Ours" 면 무엇에 붙였는지 모른다(2026-10-14 WARP-VLA:
                # GR00T N1.7 + Ours 66.5 와 π0.5 + Ours 78.3 이 둘 다 "+Ours" 였다).
                model = f"{model_label(arxiv_tables.row_label(t, base_row))} + {model.lstrip('+ ').strip()}"
            if len(re.findall(r"[A-Za-z]", model)) < 2:
                continue                                    # 이름 없는 행(빈칸·체크 표시뿐) — 누구의 값인지 모른다
            own = own_label(model, method)
            rows.append({"benchmark": bench, "metric": metric, "bench_key": norm_key(bench), "metric_key": metric_key(metric),
                         "direction": direction(joined), "value": cell["value"], "text": cell["text"], "model": model, "own": own,
                         # 표 순번을 넣는다 — 한 figure 에 표가 둘이면 파서가 같은 id 를 준다(Codex 재현: DS 표·SR 표가 같은 키로 덮였다).
                         "locator": f"{t.get('id')}#{ti}:r{cell['row']}c{cell['col']}", "caption": caption[:160], "_col": cell["col"],
                         **({"block_id": blocks[cell["row"]][0], "block": blocks[cell["row"]][1]} if blocks[cell["row"]][0] else {}),
                         **({"_base_row": base_row} if base_row is not None else {}),
                         # 지표 이름을 저자 문장에서 확인한 열 — 근거 문장을 남기고, 그 값을 말한 이 논문 칸은 대표값으로 앞세운다.
                         **({"metric_source": "author_text", "metric_evidence": recovered["sentence"],
                             "author_value": own and cell["row"] == recovered["row"]}
                            if recovered else {})})
        # 범주·fold 별 열은 쌓지 않는다. 같은 머리 아래(마지막 칸만 다른 열들)에 평균 열(Mean·Avg)이 있으면 **그 평균 열만**, 없는데 열이
        # MAX_COLUMNS_PER_KEY 보다 많으면 범주별 표로 보고 버린다(2026-09-30 실측: P³-SAM 표가 Fold-0/1/2/MEAN 을 따로 두었다).
        groups: dict[tuple[str, str], set[int]] = {}
        means: dict[tuple[str, str], bool] = {}
        for r in rows:
            parent = (r["bench_key"], norm_key(r["metric"].rsplit(" / ", 1)[0]) if " / " in r["metric"] else "")
            r["_parent"] = parent
            groups.setdefault(parent, set()).add(r["_col"])
            if " / " in r["metric"] and _MEAN_RE.search(r["metric"].rsplit(" / ", 1)[1]):
                means[parent] = True
        # 같은 행·같은 (벤치마크, 지표) 이름이 두 열에 있으면 머리가 모자란 것이다(실측: 1-shot·5-shot 아래 FB-IoU 가 둘 다 "FB-IoU").
        # 어느 열이 어느 조건인지 모르므로 **둘 다 버린다** — 틀린 칸을 채우느니 빈칸.
        seen: dict[tuple, int] = {}
        for r in rows:
            k = (r["locator"].split(":r")[0], r["locator"].split(":r")[1].split("c")[0], r["bench_key"], r["metric_key"])
            seen[k] = seen.get(k, 0) + 1
        rows = [r for r in rows
                if seen[(r["locator"].split(":r")[0], r["locator"].split(":r")[1].split("c")[0], r["bench_key"], r["metric_key"])] == 1]
        _mark_gains(rows)
        for r in rows:
            parent, col = r.pop("_parent"), r.pop("_col")
            last_is_mean = " / " in r["metric"] and bool(_MEAN_RE.search(r["metric"].rsplit(" / ", 1)[1]))
            if parent[1] and means.get(parent) and not last_is_mean:
                continue
            if parent[1] and not means.get(parent) and len(groups[parent]) > MAX_COLUMNS_PER_KEY:
                continue
            out.append(r)
    for result in out:
        location = re.search(r"#(\d+):r\d+c(\d+)$", result["locator"])
        if location:
            table = tables[int(location[1])]
            result.update(_table_metadata(table.get("caption") or "",
                                          arxiv_tables.column_path(table, int(location[2])),
                                          result["text"], result["metric"]))
    return out
