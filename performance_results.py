"""④⑤ 성능 결과 — 논문이 보고한 (벤치마크·지표·값)을 뽑는다. 결정적, LLM·네트워크 없음(표 HTML 은 호출부가 준다).

2026-09-30 방향 전환(사용자): "SOTA 라는 말이 있을 때만" 보면 최신 논문 대부분을 놓친다(최근 123편 중 주장 7편). 그래서 **모든 최종 후보**에서
결과를 뽑아 관측 DB(`frontier_store`)에 쌓고, 성능 경계를 민 후보를 찾는다. `sota_claims` 는 버리지 않고 우선순위 신호 하나로 쓴다.

두 갈래이고 **쓰임이 다르다**:
- **표 결과**(`table_results`) — arXiv HTML 표의 행(방법) × 열(벤치마크·지표) 교차 셀. 구조가 확인된 값이라 **수치 비교에 쓴다**.
- **문장 근거**(`performance_evidence`) — 이 논문 자신의 결과를 말한 원문 문장. 구조가 없어 **숫자 비교는 하지 않고** 원문 주장으로만 보인다
  (사용자 결정 2026-09-30: 표를 못 읽었다고 주장을 버리지는 않는다, 숫자 비교만 안 한다).
"""
from __future__ import annotations

import re

import sentence_grounding

MAX_BENCHMARKS = 2
MAX_QUOTE_CHARS = 200

# 지표 — 약어는 대소문자를 가린다("AP"·"SR" 은 소문자면 다른 낱말이다), 구절은 가리지 않는다.
_METRIC_ABBR = re.compile(
    r"(?<!\w)(AUROC|AUPRO|AUPR|mAP(?:@[\d.:]+)?|AP(?:50|75)?|mIoU|FB-IoU|IoU|F1|PSNR|SSIM|LPIPS|FID|BLEU|ROUGE(?:-[L12])?|EM|"
    r"SR|DS|RC|WER|CER|MRR|nDCG(?:@\d+)?|Dice|PCK|ADE|FDE|EPE|MAE|RMSE|NDS|HOTA|MOTA)(?![\w-])")
_METRIC_PHRASE = re.compile(
    r"\b(accuracy|success(?: rates?)?|driving score|route completion|error rates?|collision rates?|top-1|top-5|recall@\d+|pass@\d+|"
    r"precision|recall|f1[- ]score|exact match|win rate)\b", re.IGNORECASE)
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


def method_name(title: str | None) -> str:
    """제목 앞 `FIVE-VLA: …` 의 기법 이름. 없으면 빈 문자열 — 지어내지 않는다."""
    head = (title or "").split(":", 1)[0].strip()
    return head if ":" in (title or "") and 2 <= len(head) <= 30 and len(head.split()) <= 3 else ""


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
    return list(dict.fromkeys(b for b in out if b))[:30]


def table_results(tables: list[dict], *, method: str, benchmarks: list[str]) -> list[dict]:
    """표의 교차 셀마다 (벤치마크, 지표, 값, 행). own=True 는 이 논문 행(제목의 기법 이름·"Ours"·"proposed").

    [{benchmark, metric, bench_key, metric_key, direction, value, text, model, own, locator, caption}]
    벤치마크는 **열 머리 → 행 이름 → (후보가 하나만 있을 때) 캡션** 순으로, 원문에서 뽑은 후보 이름과 일치할 때만 정한다 — 모르는 벤치마크를
    지어내지 않는다. 실측(2026-09-30): P³-SAM 표는 데이터셋 이름이 열이 아니라 **행** 앞 칸에 있다(`Surface Defects-4i Ours ResNet50`)."""
    import arxiv_tables
    out: list[dict] = []
    for ti, t in enumerate(tables):
        if not t.get("grid"):
            continue
        caption = t.get("caption") or ""
        cap_metric = metric_token(caption)
        if _ABLATION_RE.search(caption):
            continue                                        # 절제 실험 표 — 변형 행은 경쟁 결과가 아니다(행 이름도 "✓" 뿐이다)
        in_caption = [b for b in benchmarks if _bench_re(b).search(caption)]
        rows: list[dict] = []
        for cell in arxiv_tables.find_cells(t, lambda _lbl: True, lambda _path: True):
            path = [p for p in cell["column_path"] if p]
            if path and not _is_metric(" / ".join(path)) and cap_metric and _MEAN_RE.search(path[-1]):
                path = [cap_metric] + path          # 지표가 캡션에만 있고 열은 범주·평균인 표 — 평균 열만 캡션 지표로 읽는다(Codex 검토)
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
            model = re.sub(r"\s+", " ", bpat.sub("", label_row)).strip() or label_row
            if len(re.findall(r"[A-Za-z]", model)) < 2:
                continue                                    # 이름 없는 행(빈칸·체크 표시뿐) — 누구의 값인지 모른다
            own = bool((method and re.search(r"(?<![\w-])" + re.escape(method) + r"(?![\w-])", model, re.IGNORECASE))
                       or _OURS_RE.search(model))
            rows.append({"benchmark": bench, "metric": metric, "bench_key": norm_key(bench), "metric_key": metric_key(metric),
                         "direction": direction(joined), "value": cell["value"], "text": cell["text"], "model": model, "own": own,
                         # 표 순번을 넣는다 — 한 figure 에 표가 둘이면 파서가 같은 id 를 준다(Codex 재현: DS 표·SR 표가 같은 키로 덮였다).
                         "locator": f"{t.get('id')}#{ti}:r{cell['row']}c{cell['col']}", "caption": caption[:160], "_col": cell["col"]})
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
        for r in rows:
            parent, col = r.pop("_parent"), r.pop("_col")
            last_is_mean = " / " in r["metric"] and bool(_MEAN_RE.search(r["metric"].rsplit(" / ", 1)[1]))
            if parent[1] and means.get(parent) and not last_is_mean:
                continue
            if parent[1] and not means.get(parent) and len(groups[parent]) > MAX_COLUMNS_PER_KEY:
                continue
            out.append(r)
    return out
