"""⑤⑨ SOTA 주장 — 논문이 **스스로** "state-of-the-art" 라고 말한 문장을 찾아 그대로 보여 준다(2026-09-16). 결정적, LLM 없음.

왜 이렇게만 하는가: 사용자는 "동향 브리핑이니까 관련된 SOTA 모델도 자연스럽게 알려 달라, 억지로 끼워 넣지는 말라"고 했다.
리더보드 자료원이 없다(Papers with Code 는 2026-09-16 확인 시 huggingface.co/papers 로 리다이렉트되고 API 가 없다). 그래서
우리가 말할 수 있는 것은 **논문이 주장한 것**뿐이고, 메일에도 "논문 자체 주장·미검증" 으로만 쓴다(CLAUDE.md 규칙 7 — 확인하지 않은
SOTA 를 사실로 표시하지 않는다). 주장 문장은 원문 문장 번호(S번호)를 달아 동향 서술의 근거로도 넘긴다 — 서술이 인용하면
독자가 대조 목록에서 그 문장을 볼 수 있다.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import sentence_grounding

MAX_CLAIMS = 3
MAX_SENTENCE_CHARS = 240

# 주장 표현 — **성능을 얻었다**는 서술이어야 한다. "state-of-the-art" 는 "SOTA 모델을 썼다·조사했다·분석했다"처럼 명사로도 쓰이고
# (실측 60편: DeepSeek 를 썼다, GR00T 를 분석했다, 기법을 조사했다 가 주장으로 잡혔다), "outperforms the CRNN" 같은 단순 비교는 SOTA
# 주장이 아니다. 그래서 achieve/attain/… + SOTA, SOTA + results/performance, outperform all/existing… 꼴만 잡는다(2026-09-16).
_SOTA = r"(?:state[- ]of[- ]the[- ]art|\bSOTA\b)"
_CLAIM_RE = re.compile(
    "(?:(?:achiev|attain|reach|obtain|set|establish|deliver|yield|report)\\w*\\s+(?:a\\s+)?(?:new\\s+)?(?:[\\w-]+\\s+){0,2}?" + _SOTA   # "achieves (a new single-model) SOTA"
    + "|" + _SOTA + "\\s+(?:[\\w-]+\\s+)?(?:results?|performance|accuracy|scores?|success rates?|mIoU|AUROC|F1|precision|recall)"   # "SOTA (BLEU) score"
    + "|(?:outperform|surpass|exceed|beat)\\w*\\s+(?:the\\s+|all\\s+|existing\\s+|prior\\s+|previous\\s+|current\\s+)*" + _SOTA      # "outperforms (the) SOTA"
    + "|new state of the art|first to achieve"
    + "|outperform(?:s|ed|ing)?\\s+(?:all|every|existing|prior|previous)\\b"
    + "|surpass(?:es|ed|ing)?\\s+(?:all|every|existing|prior|previous)\\b)", re.IGNORECASE)
# 이 논문의 주장이라는 단서 — 1인칭·제안 기법. "X achieves SOTA" 처럼 모델 이름이 주어인 문장은 놓치지만, "Recent methods achieve
# state-of-the-art results" 같은 배경 문장을 주장으로 싣는 것보다 낫다(실측: rPPG 논문의 배경 문장이 주장으로 잡혔다).
_OWN_RE = re.compile(r"\b(?:we|our|ours|the proposed|proposed (?:method|approach|model|framework|system)|this (?:paper|work|method|approach))\b",
                     re.IGNORECASE)
# 배경 표현은 **주장 명사를 꾸밀 때만** 배경이다 — "existing state-of-the-art methods", "Recent advancements in rPPG methods",
# "In prior work, we…". "compared to existing baselines" 는 우리 성과 문장의 비교 대상일 뿐이라 배경이 아니다(외부 검토 2026-09-16).
_BACKGROUND_RE = re.compile(r"\b(?:recent|existing|prior|previous|current|conventional|traditional)\b(?:\s+[\w-]+){0,3}?\s+"
                            r"(?:state[- ]of[- ]the[- ]art|SOTA|methods?|approaches|work|works|systems?|models?|techniques?|studies)\b"
                            r"|\b(?:pursuit of|methods that|approaches that)\b", re.IGNORECASE)
_NEGATION_RE = re.compile(r"\b(?:not|fail\w*|without|below|short of|far from|may degrade)\b.{0,40}\b(?:state[- ]of[- ]the[- ]art|SOTA)", re.IGNORECASE)
# 벤치마크 이름 — "on <대문자로 시작하는 이름>" 꼴. 전치사·쉼표·괄호에서 자른다.
_BENCH_RE = re.compile(r"\bon\s+(?:both\s+|all\s+)?(?:the\s+)?([A-Z][A-Za-z0-9]*(?:[- ][A-Za-z0-9]+){0,4})")
# "across three benchmarks, GenEval, HPSv2, and DPG" · "on four benchmarks: A, B, C" 꼴의 목록
_BENCH_LIST_RE = re.compile(r"\b(?i:across|on)\s+(?:[\w-]+\s+)?(?i:benchmarks?|datasets?)[,:]?\s+"
                            r"([A-Z][\w-]*(?:(?:\s*,\s*(?:and\s+)?|\s+and\s+|\s*&\s*)[A-Z][\w-]*)+)")
_BENCH_CUT = re.compile(r"\s+(?:by|with|for|in|at|under|across|using|against|compared|when|while|where|than|over|to|of|via|from)\b.*$",
                        re.IGNORECASE)
_BENCH_TRAIL = re.compile(r"\s+(?:benchmark|benchmarks|dataset|datasets|task|tasks|leaderboard|suite|split)s?$", re.IGNORECASE)
_BENCH_STOP = {"average", "both", "all", "several", "multiple", "various", "standard", "public", "real", "par", "top", "table", "figure",
               "section", "these", "this", "our", "a", "an", "the", "each", "every", "most", "many", "some", "two", "three", "four", "five"}


def _looks_like_name(name: str) -> bool:
    words = name.split()
    if not words or name.lower() in _BENCH_STOP or any(w.lower() in _BENCH_STOP for w in words):
        return False
    if len(words) >= 2:
        return all(w[0].isupper() or w[0].isdigit() for w in words)          # "MVTec AD", "Bridge V2", "Jetson Orin"
    w = words[0]
    return any(ch.isdigit() or ch.isupper() for ch in w[1:]) and len(w) >= 3   # "VisA", "LIBERO", "ImageNet-1K" — "Towel" 은 아니다


def _benchmarks(sentence: str) -> list[str]:
    out: list[str] = []
    for m in _BENCH_LIST_RE.finditer(sentence):
        for part in re.split(r"\s*(?:,|\band\b|&)\s*", m.group(1)):
            name = part.strip(" -")
            if name and _looks_like_name(name) and name not in out:
                out.append(name)
    for m in _BENCH_RE.finditer(sentence):
        chunk = _BENCH_CUT.sub("", m.group(1))
        for part in re.split(r"\s*(?:,|/|\band\b|&)\s*", chunk):
            name = _BENCH_TRAIL.sub("", part).strip(" -")
            if name and _looks_like_name(name) and name not in out:
                out.append(name)
    return out[:3]


def is_own_claim(sentence: str) -> bool:
    if not _CLAIM_RE.search(sentence) or _NEGATION_RE.search(sentence):
        return False
    if _BACKGROUND_RE.search(sentence) and not re.search(r"\b(?:outperform|surpass|exceed|beat)\w*", sentence, re.IGNORECASE):
        return False
    if re.search(r"\b(?:in|from)\s+(?:our\s+)?(?:prior|previous|earlier)\s+work\b", sentence, re.IGNORECASE):
        return False                          # 지난 연구의 성과다
    return bool(_OWN_RE.search(sentence))


def extract(text: str) -> list[dict]:
    """원문(또는 초록)에서 이 논문의 SOTA 주장 문장. [{index(1부터, 문장 번호), sentence, benchmarks}] — 벤치마크가 같은 문장은 하나만."""
    out: list[dict] = []
    seen_bench: set[tuple[str, ...]] = set()
    for i, sent in enumerate(sentence_grounding.segment_sentences(text), start=1):
        if len(sent) > 600 or not is_own_claim(sent):
            continue
        bench = tuple(_benchmarks(sent))
        if bench in seen_bench and bench:
            continue
        seen_bench.add(bench)
        out.append({"index": i, "sentence": sent if len(sent) <= MAX_SENTENCE_CHARS else sent[:MAX_SENTENCE_CHARS].rstrip() + "…",
                    "benchmarks": list(bench)})
        if len(out) >= MAX_CLAIMS:
            break
    return out


def claims_for(db: Path, arxiv_id: str | None, abstract: str | None = None) -> tuple[list[dict], str]:
    """(주장 목록, 출처) — 출처는 'text'(원문, S번호 유효) 또는 'abstract'(초록, S번호 없음) 또는 ''."""
    if arxiv_id:
        try:
            with sqlite3.connect(db) as con:
                row = con.execute("SELECT text_path, abstract FROM papers WHERE arxiv_id=?", (arxiv_id,)).fetchone()
        except sqlite3.Error:
            row = None
        if row and row[0]:
            try:
                return extract(Path(row[0]).read_text(encoding="utf-8")), "text"
            except (OSError, UnicodeDecodeError):
                pass
        if row and row[1] and not abstract:
            abstract = row[1]
    if abstract:
        claims = extract(abstract)
        for c in claims:
            c["index"] = None                     # 초록 문장 번호는 원문 S번호가 아니다
        return claims, "abstract"
    return [], ""


def evidence_packets(claims: list[dict]) -> list[dict]:
    """동향 서술 근거(`_evidence`) 형식 — 원문 S번호가 있는 주장만. 서술이 인용하면 대조 목록에 이 문장이 실린다."""
    return [{"id": f"S{c['index']:04d}", "text": f"(논문 자체의 SOTA 주장 — 미검증) {c['sentence']}"}
            for c in claims if c.get("index")]


def mail_line(claims: list[dict]) -> str:
    """메일 한 줄. 주장이 없으면 빈 문자열 — 억지로 넣지 않는다."""
    if not claims:
        return ""
    bench = [b for c in claims for b in c["benchmarks"]]
    bench = list(dict.fromkeys(bench))[:3]
    head = "SOTA 주장(논문 자체 주장 · 미검증)"
    if bench:
        head += ": " + ", ".join(bench)
    quote = claims[0]["sentence"]
    if len(quote) > 160:
        quote = quote[:160].rstrip() + "…"
    return f"{head} — “{quote}”"
