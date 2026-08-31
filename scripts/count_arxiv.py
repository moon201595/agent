"""arXiv totalResults 만 읽어 후보 수를 센다 — 요청 1회, 페이징 없음."""
import sys, time, urllib.parse, urllib.request, re

WINDOW = "submittedDate:[202608210617 TO 202608310213]"
UA = {"User-Agent": "paper-harness/keyword-sizing (mailto:answnsgur030@naver.com)"}

def total(terms):
    q = "(" + " OR ".join(f'all:"{k}"' if " " in k else f"all:{k}" for k in terms) + ")"
    url = ("https://export.arxiv.org/api/query?" + urllib.parse.urlencode({
        "search_query": f"{q} AND {WINDOW}", "start": 0, "max_results": 1}))
    for attempt in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                body = r.read().decode()
            m = re.search(r"<opensearch:totalResults[^>]*>(\d+)<", body)
            return int(m.group(1)) if m else -1
        except Exception as e:
            wait = 20 * (attempt + 1)
            print(f"    (재시도 {attempt+1}/5, {wait}s 대기: {type(e).__name__})", file=sys.stderr)
            time.sleep(wait)
    return -1
