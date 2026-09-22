# 날짜 띠 앵커 선택지 조사 — 2026-09-21

## 범위와 전제

읽기 전용 조사. 쓰기는 이 파일만 허용하며 코드·테스트·PROGRESS는 수정하지 않는다. main, 조사 시작 HEAD `6a6304aa7183e8a63b12b70868485108d6d65e40`. 기존 작업 트리 변경은 다른 작업자의 것으로 보존한다. CLAUDE.md와 AGENTS.md 전체를 읽었다. `.env` 미열람, 운영 DB는 `mode=ro`, 전체 pytest·네트워크 재측정·커밋·push 없음.

사용자 제공 실측을 전제로 한다(이번 조사에서 재측정하지 않음): 9/21 arXiv 최신은 네 프로필 모두 9/17, 저널은 9/20까지. team_ai_advance·team_vision은 앵커 9/20 때문에 arXiv가 band 1, 내용 5/5 저널. team_agent·team_robot은 앵커 9/19라 arXiv band 0, robot은 arXiv 4편 유지. 9/20 team_agent는 5/5 arXiv. API에서도 9/17이 최신임을 확인한 자료다. PDF 수급을 순위에 넣는 해법은 제외한다.

## 1. 현재 계약과 18개 테스트

운영 코드는 `profile_scoring.py:553-570`에서 제외·무적중을 먼저 제거하고 적격 후보의 최신 날짜를 앵커로 잡는다. `rank_key`(`:296-315`)는 `(계층, max(0, 앵커−날짜)//3, −개념 폭, −날짜, −도메인, 논문 키)`다. 개념 폭과 도메인 적중 상한은 2, 결측 날짜 띠는 1000000이다(`:272-293`). 계층은 가중치를 0.1 구간으로 묶은 순위다(`:237-269`). `priority`는 설명용이다. 파일 머리의 옛 테스트 docstring보다 이 실행 코드가 근거다.

아래 번호는 이후 선택지 평가에서 재사용한다. 각 행은 `test_rank_contract.py`의 실제 assert를 읽은 결과다.

|번호|테스트(행)|못 박는 것|
|---|---|---|
|T1|계층이_최신성보다_먼저다 :33|6일 전 표적 계층이 최신 하위 계층의 결합·도메인 적중보다 먼저다.|
|T2|같은_계층이면_최신이_먼저다 :45|동일 폭에서 5일 더 최신인 논문이 도메인 적중 논문보다 먼저다.|
|T3|같은_날이면_적중_폭_그다음_도메인 :56|같은 날에는 2개념 > 1개념+도메인 > 1개념이다.|
|T4|날짜_결측은_같은_계층_뒤로_가지만_계층을_넘지_않는다 :66|같은 계층의 날짜 있음 > 연도만/결측 > 최신 하위 계층이며 연도 정밀도는 year, pub_day는 None이다.|
|T5|동률은_입력_순서가_아니라_키로_정한다 :81|완전 동률은 입력 순서와 무관하게 논문 키 순서다.|
|T6|연속_가중치는_0_1_구간으로_묶여_기존_계층은_그대로다 :88|1.07/1.0은 같은 계층, 1.0/0.6/0.4/0.35는 구별하고 0.72는 0.7 구간이다.|
|T7|계층은_적중_키워드와_원_가중치로_구한다 :103|반올림 점수 역조회 없이 원 가중치·누락 기본값 1.0으로 계층을 정한다.|
|T8|점수는_설명일_뿐_자격을_정하지_않는다 :115|가중치 0 키워드도 적중하면 적격, 무적중은 탈락, priority 필드는 유지한다.|
|T9|본문_링크_유무가_선정_순서를_바꾸지_않는다 :134|실제 scan_profile에서 링크 없는 DOI 상위 계층이 내용 1자리를 받고 arXiv 하위 계층은 reserve다.|
|T10|reserve_도_같은_계약_순서다 :165|score_and_rank 전체 목록이 계층→동일 폭 최신순이다. 이름과 달리 실제 reserve 분할 호출은 없다.|
|T11|병합은_더_이른_공개일을_남긴다 :175|selection._merge는 입력 방향과 무관하게 두 공개일 중 이른 날짜를 남긴다.|
|T12|띠_안에서는_개념_폭이_날짜를_이긴다 :185|앵커에서 2일 전 2개념이 최신 1개념보다 먼저이고 1개념끼리는 최신순이다.|
|T13|띠를_넘는_오래된_논문은_결합이_많아도_뒤로_간다 :194|앵커에서 3일 전 2개념은 최신 1개념 뒤다.|
|T14|띠의_기준은_절대_달력이_아니라_가장_최신_후보다 :201|후보 날짜 전체를 0~5일 평행 이동해도 2일 전 2개념→최신→1일 전 순서가 같다.|
|T15|표기_변형_둘은_한_개념이다 :211|robot/robotic 및 LLM 표기 변형을 한 개념으로 접어 실제 2개념이 먼저다.|
|T16|날짜_없음은_띠_뒤에_남고_계층은_넘지_않는다_v2 :225|결측 2개념은 날짜 있는 동일 계층 1개념 뒤지만 하위 계층 최신보다 앞이다.|
|T17|띠의_앵커는_무적중_최신_논문이_아니라_적격_후보의_최신이다 :233|무적중 최신 1편이 적격 1일 간격 두 논문의 띠를 갈라 순서를 뒤집으면 안 된다. 제외어 후보는 이 fixture가 직접 검증하지 않는다.|
|T18|개념_접기는_실제_변형_쌍만_접는다 :242|model-based/model은 구별하고 LLM-based/LLM만 접는다.|

### 기록에 있는 설계 이유

**절대 달력을 버린 이유가 기록에 있다.** PROGRESS §8-149(`docs/PROGRESS.md:5974-5982`, 특히 :5981)는 절대 달력 경계가 하루 차이 후보를 갈라 놓기 때문에 스캔 최신 후보를 기준으로 삼았다고 명시한다. T14는 이 평행 이동 불변성을 고정한다. 여기서 절대 달력은 `day // 3` 같은 고정 구간이다. '실행 당일을 앵커로 삼는 방식'을 별도로 비교·기각한 실측 기록이라는 뜻은 아니다.

적격 후보로 좁힌 이유는 §8-150(`:5988-6000`, 특히 :5995): 무적중 최신 후보가 실제 경쟁 후보의 띠를 갈라 놓지 않게 하기 위해 정의를 명시하고 T17을 추가했다. `profile_scoring.py:189-201`과 일치한다. 같은 절 :5999에는 **S2 발표일 출처 보존은 미반영·보류**라고 남아 있다. 즉 날짜 의미 혼합 문제 자체는 이미 지적됐지만 해결됐다는 기록은 아니다.

§8-149의 18회 재생·90자리·16→43·평균 1.9일은 당시 기록이며 이번에 재측정하지 않았다. 링크 문지기 철회 근거는 §8-86 및 T9다.

조회 명령: `nl -ba test_rank_contract.py`, `nl -ba profile_scoring.py`, `grep -nE '날짜 띠|앵커|절대 달력|dateband' docs/PROGRESS.md`, Python Path로 해당 줄 범위 열람. `rg`는 설치되어 있지 않아 grep으로 대체했다.

## 2. 날짜 의미와 최근 2주 지연

### 코드가 실제로 읽는 필드

- arXiv MCP 검색은 `server.py:581-591` → 별칭 `:212` → `http_client.parse_arxiv_feed`다. 공용 파서는 Atom `published`를 그대로 넣는다(`http_client.py:309`); `updated`를 넣지 않는다. v1 제출 시각이라는 의미는 사용자 제공 확인 사실이다. 일일 경로는 `scan_search.py:63` → `find_new_papers.py:78-84`의 submittedDate 내림차순 검색 → 같은 파서다. 따라서 둘의 필드 해석은 같다.
- S2는 `_FIELDS`에 publicationDate를 요청하고(`s2_delta.py:102`), `_to_paper`에서 그 필드에 `T00:00:00Z`를 붙인다(`:118-131`). 일자만 있는 값에 자정을 붙인 것이지 자정에 발표됐음을 관측한 것이 아니다. 없으면 None이며 year 대체는 없다. 이 코드에는 online-first·인쇄 발행일·프리프린트 날짜를 구별하거나 최초 공개일로 환산하는 로직이 없다. S2 값이 개별 논문에서 정확히 어떤 사건의 날짜인지는 미확인이다.
- `publication_day`(`profile_scoring.py:218-234`)는 ISO 시각 두 형식 또는 YYYY-MM-DD를 파싱하면 day, 네 자리 연도만이면 year+None, 없음이면 missing+None, 나머지는 invalid+None이다. **day는 형식의 정밀도이지 날짜 의미/신뢰도의 보증이 아니다.** 시각은 `.date()`로 버리며 UTC로 재변환하지 않는다.
- `score_paper:524-534`가 pub_day/date_precision을 만들지만 `rank_key:304-308`은 published를 다시 파싱한다. 저장된 date_precision 문자열을 읽어 별도 출처 보정하지 않는다. year/missing/invalid는 전부 결측 띠로 가며 앵커 계산에서도 제외된다(`:569`).
- `selection._merge:34-66`은 source를 첫 비어 있지 않은 값으로 남기면서, 파싱 가능한 두 날짜 중 더 이른 published를 남긴다. retrieval_sources는 합집합이다. **source와 실제 채택 날짜의 출처가 일치한다는 보장이 없다.** S2 응답도 ArXiv ID를 가질 수 있다(`s2_delta.py:123`). 출처별 앵커를 구현할 때 arxiv_id/DOI 또는 source 하나로 날짜 의미를 단정하면 안 된다.
- 후보는 arXiv+S2 중복 제거(`scan_search.py:233`), 기존 요약/노출 제거 후 `fresh`를 채점한다(`:244-297`). 관측은 탈락 후보까지 기록한다(`:360-385`). `research_profile.py:1075`의 date_precision은 점수 결과에서 가져오므로 NULL이 곧 published 결측은 아니다.

### 측정 정의 및 실행 코드

요청한 2주를 **9/8~9/21 KST, 양끝 날짜 포함**으로 고정했다. scan_runs.started_at을 KST 날짜로 바꾸고 published는 순위 함수와 같은 방식으로 원 문자열의 날짜를 취해 뺀다. 즉 날짜 단위 후보 나이이며 정확한 경과 24시간이나 외부 API 인덱싱 지연이 아니다. scan 시작 기록은 검색 전 `scan_search.py:140`의 begin_scan이다. source를 기본 분류로 쓰며 DOI 접두 교차표도 확인한다. p90은 정렬 후 ceil(0.9*N)번째 값(최근접 순위), 중앙값은 짝수일 때 두 중앙값 평균이다.

전체 후보와 실제 순위 대상(rank_pos IS NOT NULL)을 따로 집계한다. 반복 스캔·여러 프로필에 같은 논문이 나타나는 가중 효과를 확인하려고 기간 내 source/paper_key별 첫 관측도 제시한다. 첫 관측은 **이 기간 내 첫 관측**일 뿐 실제 최초 수집 시각은 아니다. 수치의 단위는 일이다.

아래 코드를 `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python - <<'PY'`로 실행했다(종료 표식 PY). DB 모듈을 import하지 않고 stdlib sqlite3만 쓰며 읽기 트랜잭션 하나에서 집계했다.

```python
import sqlite3, math, statistics
from collections import defaultdict, Counter
from datetime import datetime
from zoneinfo import ZoneInfo
c = sqlite3.connect('file:/home/mjh/paper-harness/data/papers.db?mode=ro', uri=True)
c.execute('PRAGMA query_only=ON')
c.execute('BEGIN')
c.row_factory = sqlite3.Row
rows = c.execute("""SELECT o.scan_id,o.profile_id,o.paper_key,o.source,o.published,
 o.date_precision,o.rank_pos,o.retrieval_sources,s.started_at
 FROM candidate_observations o JOIN scan_runs s ON s.scan_id=o.scan_id
 WHERE s.started_at >= '2026-09-07T15:00:00'
 AND s.started_at < '2026-09-21T15:00:00'
 ORDER BY s.started_at,o.scan_id,o.paper_key""").fetchall()
scans = c.execute("SELECT started_at,observations FROM scan_runs WHERE started_at >= '2026-09-07T15:00:00' AND started_at < '2026-09-21T15:00:00'").fetchall()
print('window = [2026-09-08 00:00, 2026-09-22 00:00) KST')
print('scan rows:',len(scans),'observations NULL:',sum(s['observations'] is None for s in scans))
print('observed scan time range UTC:',min(s['started_at'] for s in scans),max(s['started_at'] for s in scans))
print('joined rows:',len(rows),'unique papers:',len({r['paper_key'] for r in rows}))
valid=[]; missing=Counter(); stored=Counter(); cross=Counter()
for r in rows:
 stored[(r['source'],str(r['date_precision']))]+=1
 cross[(r['source'],'doi' if r['paper_key'].startswith('doi:') else 'arxiv' if r['paper_key'].startswith('arxiv:') else 'other')]+=1
 pub=None
 for fmt in ('%Y-%m-%dT%H:%M:%SZ','%Y-%m-%dT%H:%M:%S%z','%Y-%m-%d'):
  try: pub=datetime.strptime(r['published'] or '',fmt).date(); break
  except ValueError: pass
 if pub is None: missing[r['source']]+=1; continue
 scan=datetime.fromisoformat(r['started_at']).astimezone(ZoneInfo('Asia/Seoul')).date()
 valid.append((dict(r),scan,(scan-pub).days))
print('stored precision:',dict(sorted(stored.items())))
print('source x key:',dict(sorted(cross.items())))
print('unparseable published:',dict(missing),'negative lag:',sum(lag<0 for _,_,lag in valid))
def summarize(label, seq, key):
 groups=defaultdict(list)
 for r,day,lag in seq: groups[key(r,day)].append(lag)
 print(label,'[group, N, median, p90(nearest rank), min, max]')
 for k,vals in sorted(groups.items()):
  vals.sort(); print(k,len(vals),statistics.median(vals),vals[math.ceil(.9*len(vals))-1],vals[0],vals[-1])
summarize('all observations',valid,lambda r,d:r['source'] or 'unknown')
eligible=[v for v in valid if v[0]['rank_pos'] is not None]
summarize('rank_pos IS NOT NULL',eligible,lambda r,d:r['source'] or 'unknown')
seen=set(); first=[]
for v in valid:
 key=(v[0]['source'],v[0]['paper_key'])
 if key not in seen: first.append(v);seen.add(key)
summarize('first within window per source/paper',first,lambda r,d:r['source'] or 'unknown')
summarize('daily eligible',eligible,lambda r,d:(str(d),r['source'] or 'unknown'))
# 오늘 제공된 앵커 실측을 반복하지 않고, 이전 스캔의 반복 여부만 조사한다.
by_scan=defaultdict(dict)
for r,day,lag in eligible:
 if str(day)>='2026-09-21':continue
 src=r['source']
 if src in ('arxiv','s2'):
  g=by_scan[(r['scan_id'],str(day))];g[src]=min(g.get(src,lag),lag)
pairs=[(day,g['arxiv']-g['s2']) for (_,day),g in by_scan.items() if len(g)==2]
print('prior paired eligible scans:',len(pairs),'arxiv latest older:',sum(gap>0 for _,gap in pairs),'gap >=3:',sum(gap>=3 for _,gap in pairs))
print('prior latest-day gaps (arxiv lag - s2 lag):',dict(sorted(Counter(gap for _,gap in pairs).items())))
print('prior daily paired counts / positive / gap>=3:')
for day in sorted({d for d,_ in pairs}):
 vals=[g for d,g in pairs if d==day];print(day,len(vals),sum(g>0 for g in vals),sum(g>=3 for g in vals))
c.rollback();c.close()
```

실행 출력:

```text
window = [2026-09-08 00:00, 2026-09-22 00:00) KST
scan rows: 41 observations NULL: 3
observed scan time range UTC: 2026-09-11T04:44:53+00:00 2026-09-20T21:10:09.804496+00:00
joined rows: 27720 unique papers: 6148
stored precision: {('arxiv', 'None'): 4627, ('arxiv', 'day'): 8136, ('s2', 'None'): 13200, ('s2', 'day'): 1757}
source x key: {('arxiv', 'other'): 12763, ('s2', 'doi'): 10956, ('s2', 'other'): 4001}
unparseable published: {} negative lag: 0
all observations [group, N, median, p90(nearest rank), min, max]
arxiv 12763 5 9 1 12
s2 14957 5 8 0 12
rank_pos IS NOT NULL [group, N, median, p90(nearest rank), min, max]
arxiv 8136 5.0 8 1 12
s2 1757 6 8 0 12
first within window per source/paper [group, N, median, p90(nearest rank), min, max]
arxiv 2110 3.0 7 1 10
s2 4537 5 8 0 12
daily eligible [group, N, median, p90(nearest rank), min, max]
('2026-09-11', 'arxiv') 428 5.0 9 1 9
('2026-09-11', 's2') 21 8 9 4 9
('2026-09-12', 'arxiv') 792 4.0 5 2 7
('2026-09-12', 's2') 131 5 7 1 7
('2026-09-13', 'arxiv') 132 4.5 5 3 6
('2026-09-13', 's2') 180 6.0 7 1 7
('2026-09-14', 's2') 266 6.0 8 2 8
('2026-09-15', 's2') 149 7 9 0 9
('2026-09-16', 'arxiv') 1247 5 8 1 10
('2026-09-16', 's2') 240 7.0 8 1 10
('2026-09-17', 'arxiv') 956 5.0 8 2 9
('2026-09-17', 's2') 84 7.0 8 1 9
('2026-09-18', 'arxiv') 1189 4 8 1 10
('2026-09-18', 's2') 103 7 10 1 10
('2026-09-19', 'arxiv') 1124 4.0 9 2 10
('2026-09-19', 's2') 151 4 8 1 10
('2026-09-20', 'arxiv') 1084 5.0 10 3 11
('2026-09-20', 's2') 195 5 10 2 11
('2026-09-21', 'arxiv') 1184 6.0 10 4 12
('2026-09-21', 's2') 237 6 10 1 12
prior paired eligible scans: 28 arxiv latest older: 13 gap >=3: 0
prior latest-day gaps (arxiv lag - s2 lag): {-3: 1, -2: 1, -1: 7, 0: 6, 1: 12, 2: 1}
prior daily paired counts / positive / gap>=3:
2026-09-11 1 0 0
2026-09-12 4 4 0
2026-09-13 1 1 0
2026-09-16 7 3 0
2026-09-17 3 2 0
2026-09-18 4 1 0
2026-09-19 4 1 0
2026-09-20 4 1 0
```

### 결과 해석

|집계|arXiv N / 중앙값 / p90|S2 N / 중앙값 / p90|
|---|---|---|
|전체 관측|12,763 / 5 / 9|14,957 / 5 / 8|
|순위 대상|8,136 / 5 / 8|1,757 / 6 / 8|
|기간 내 논문별 첫 관측(source별)|2,110 / 3 / 7|4,537 / 5 / 8|

- **arXiv가 상시 N일 더 늦다고 결론 낼 수 없다.** 전체 중앙값은 둘 다 5일이며 순위 대상에서는 오히려 S2 중앙값이 1일 크다. 이 분포는 검색 창·반복 수집·필터·키워드 변경의 영향을 받는 후보 나이이지 출처 자체의 배포 지연 측정이 아니다. 임의의 고정 보정 N을 이 수치로 정하면 안 된다.
- **최신 끝부분의 시차는 오늘만 생긴 현상이 아니다.** 오늘을 제외하고 양 출처에 순위 대상이 있는 28스캔 중 13스캔에서 S2 최신 날짜가 더 새로웠다(12회 1일, 1회 2일). 반대는 9회, 동일은 6회다. 해당 28스캔에는 3일 이상 차이가 없었다. 사용자 제공 오늘 자료의 3일 차이는 이 보존 범위의 이전 스캔보다 크다. 다만 1~2일 차이도 후보가 band 경계에 놓이면 하위 후보 순위에 영향을 줄 수 있다.
- **'상시 arXiv 불리'도 '완전한 우연'도 입증되지 않는다.** 9/12·13에는 양쪽 최신의 차이가 모두 S2 우세였고, 9/16~20에는 방향이 섞였다. 주말과의 일관성은 보이지만 표본이 짧고 스캔 수·프로필도 불균등해 원인을 분리하지 못한다. 이번에 외부 API를 다시 호출하지 않았다.
- 요청 범위는 14일이지만 실제 scan_runs는 KST 9/11~21의 41개뿐이고 observations NULL이 3개다. 9/8~10 및 저장 실패분은 관측 없음이며 0건 성공으로 세지 않는다. daily eligible에 9/14·15 arXiv가 없는 것도 출처 전체에 신규 논문이 없었다는 뜻이 아니다.
- 파싱 불가능 published와 음수 나이는 이 표본에서 각각 0이다. 저장 date_precision NULL인 행도 모두 날짜 파싱은 가능했다. 따라서 precision NULL을 날짜 결측으로 필터링하면 많은 관측을 잘못 버린다.
- source=s2 14,957행 중 doi: 키는 10,956행이고 나머지는 4,001행이다. **S2=저널로 확정하지 않는다.** 위 교차표의 other는 비 DOI라는 의미다. `research_profile.paper_key:700-706`는 arXiv ID를 접두 없이 반환하므로 코드의 arxiv: 분기는 이 저장소의 arXiv 식별 방식이 아니다. source 기반 통계에는 영향이 없으며 other를 전부 저널/전부 arXiv로 재분류하지 않았다.
- 전체 고유 paper_key는 6,148개, source별 첫 관측 행 수의 합은 6,647이다. 동일 논문이 관측에 따라 다른 source로 기록될 수 있으므로 두 집계의 모집단이 다르다. 두 출처의 순수 배포 지연 추정으로 해석하면 안 된다.

## 3. 선택지 비교

아래 실패 판정은 **현재 테스트의 입력과 assert를 읽은 정적 분석**이다. 정책을 구현하거나 돌연변이를 실행하지 않았으므로 실행 결과로서의 실패/통과는 모두 **미실측**이다. 테스트를 통과할 것으로 보인다는 말과 기존 계약을 바꾸지 않는다는 말은 다르다. T1~T18은 1절 표의 번호다. T9 이외의 순위 fixture는 대부분 source가 없고 `_paper`가 arxiv_id만 만든다(`test_rank_contract.py:25-30`). 따라서 혼합 출처 날짜 계약에 큰 구멍이 있다.

### (a) 날짜 출처별 앵커

**정의:** 적격 후보를 날짜의 출처/의미가 같은 그룹으로 묶고 `A_g=max(day)`를 구해 `band=(A_g-day)//3`로 비교한다. 튜플의 계층·개념 폭·공개일·도메인·키 순서는 유지한다. 구현 시 published를 바꾸지 않는다. arXiv와 S2 모두 자기 그룹의 최신일을 기준으로 삼는다.

장점: 다른 날짜 체계의 최신 1편이 들어와 기존 arXiv 그룹 내부의 band가 통째로 밀리는 현상을 끊는다. 반대 방향도 대칭적으로 적용한다. 계층 우선, 적격 후보만 앵커 계산, 일괄 날짜 이동 불변성, PDF 무관성, 단일 정렬과 reserve 순서를 유지할 수 있다. 출처별 최소 자리를 보장하지 않는다.

단점: 소수·오래된 후보만 남은 출처도 자체 최신이 band 0이므로 절대 나이가 큰 논문이 넓은 개념 폭으로 올라올 수 있다. '최신성'을 **출처 내 상대적 최신성**으로 재정의하는 정책 변경이다. 공통 원시 공개일로 마지막 동률을 가르면 같은 band·폭에서는 여전히 더 최신 날짜의 저널이 앞설 수 있다. 따라서 날짜 의미 혼합을 전부 해소하는 안은 아니며 가장 큰 문제인 두 번째 키의 출처 간 시차를 줄이는 안이다. 아예 해당 출처 후보가 없는 검색 실패도 복구하지 않는다.

**깨지는 테스트:** source 미지정 fixture를 같은 unknown 그룹으로 두는 구현이라면 T1~T18 중 필수적으로 깨지는 테스트는 없다(정적 예상). T14·T17의 현재 입력도 한 그룹이므로 그대로 성립한다. 다만 주석의 '전체 적격 후보 중 최신 하나'라는 전역 앵커 계약(`profile_scoring.py:189,568`)은 바뀐다. 그룹 앵커를 무적중/제외 전 계산하면 T17이 깨진다. T9는 계층이 먼저라 유지된다. source별 이름만으로 arXiv를 강제 우대하거나 뒤에서 quota를 붙이는 것은 이 안이 아니다.

**구현 난이도: 중간, 날짜 provenance까지 보존하면 중상(설계 판단).** 앵커 map만 만드는 수정은 작지만 `selection._merge`가 날짜와 source를 따로 보존하는 문제가 있다. 선택된 날짜의 출처를 수집→병합→관측/재생까지 전달해야 일관된다. 병합 날짜를 더 최신으로 바꾸면 T11이 깨지므로 이른 공개일 규칙은 유지한다. 실제 공수와 정책 변경 후 메일 결과는 미실측이다.

### (b) 공통 앵커 유지 + 폭 확대 또는 arXiv 날짜 보정

**폭 확대:** 3일을 4일 이상으로 넓히면 사용자 제공 사례의 9/17과 9/20이 같은 band에 들어간다. 출처 분류 없이 변경할 수 있고 PDF와 무관하다. 그러나 서로 다른 날짜 의미를 그대로 비교하며 경계만 옮긴다. 다음 긴 휴지기/날짜 오차에서 재발하고, 모든 출처의 오래된 결합 논문에도 영향이 간다.

**깨지는 테스트:** 폭을 4일 이상으로 두면 T13의 3일 전 2개념 논문이 band 0으로 올라 최신 1개념을 이기므로 assert가 깨진다. T12는 2일 차이라 유지, T14도 상대 앵커라 유지한다. T2는 폭이 넓어져도 동일 개념 폭에서 날짜가 가르므로 유지된다. 이 fixture 집합에서는 나머지 필수 실패는 보이지 않는다. T12 docstring의 '폭이 3일이 아닌 것'은 과한 표현이다. 4일 확대는 T12 자체가 아니라 T13이 잡는다.

**고정 N일 보정:** 원본 published는 그대로 두고 정렬용 날짜만 arXiv에 +N하는 방식을 가정한다. '원래 앵커를 그대로 둠'과 '보정 날짜로 공통 앵커도 다시 계산함'을 구별해야 한다.

- 원래 앵커를 고정한 채 arXiv의 나이만 N일 줄이면, 모든 후보가 arXiv인 경우에도 차이가 압축된다. arxiv_id로 fixture를 arXiv로 인식하고 N≥1이면 T13의 3일 전 논문은 band 0이 되어 실패한다. source 문자열만 보고 보정한다면 source 없는 fixture에는 적용되지 않아 **기존 테스트가 이 결함을 못 잡을 수 있다**.
- 보정 날짜로 앵커도 다시 계산하면 단일 출처 전체에 같은 N을 더한 효과가 상쇄된다. source 미지정 처리까지 일관되면 T1~T18 필수 실패는 없다(정적 예상). 대신 출처 간 보정이라는 새 정책은 기존 fixture로 검증되지 않는다. 보정값을 published에 덮어쓰거나 병합 날짜 선택을 바꾸면 T11 또는 날짜 설명 계약을 훼손할 수 있다.

장점은 단순하고 정책 파라미터가 명확하다는 점이다. 단점은 이번 DB 분포가 일정한 arXiv 지연 N을 뒷받침하지 않고, 주말·평일·검색 지연을 구분하지 못한다는 점이다. 특정 출처에만 근거 없는 가점을 주는 고정 N은 **권고하지 않는다**. 관측된 지연을 보정한다는 이름만으로 날짜 의미가 같아지지는 않는다.

**구현 난이도: 폭 확대 낮음, 보정 낮음~중간.** 공통 앵커의 기준 날짜와 설명/원시 날짜 분리까지 명시해야 한다. 정책 버전은 두 경우 모두 바뀌어야 한다.

### (c) 내용 자리를 출처별로 배분

요청된 비교 대상으로만 평가하며 **채택 대상에서 제외한다**. 5자리 중 arXiv 최소 n석을 강제하면 출처 구성은 예측하기 쉬워진다. 그러나 날짜 비교 문제를 고치지 않고 이미 정한 관련성 순서를 출처로 뒤집는다. 본문 수급 때문에 이 quota를 쓰면 9/11에 걷어낸 문지기를 출처라는 대리 변수로 되살린 것이다.

**깨지는 테스트:** `max_items=1`에도 최소 arXiv 1석을 보장하도록 축소 적용하면 T9의 링크 없는 상위 DOI가 밀려 실패한다. 단, quota를 정확히 5자리에서만 적용하는 구현은 T9가 1자리라 통과할 수 있다. 이는 안전하다는 뜻이 아니라 테스트 공백이다. T1·T10은 score_and_rank만 호출하므로 quota를 scan_profile의 자르기 뒤에 넣으면 실제 계층 위반/reserve 왜곡을 못 잡는다. quota를 정렬 자체에 넣으면 혼합 출처의 계층 역전 가능성이 있지만 T1의 두 fixture는 모두 arxiv_id를 가지므로 그 테스트가 반드시 실패한다고 말할 수 없다.

**구현 난이도: 표면은 낮음, 계약을 유지하려면 높음/목표 충돌.** 상위 계층을 밀지 않는 계층 내 quota여도 날짜·개념 폭 계약은 바뀌며 후보 부족·reserve 대체 처리까지 설계가 필요하다. 현재 `scan_search.py:297-304`의 단일 정렬→앞부분/나머지 분할 계약과 맞지 않는다.

### (d) 띠를 개념 폭 뒤로 이동

**정의:** `(계층, −개념 폭, band, −날짜, −도메인, 키)`로 바꾼다. 장점은 최근 단일 적중이 며칠 전 결합 논문을 무조건 밀어내는 현상을 완화하고 출처에 직접 가점을 주지 않는 것이다. 단점은 날짜 의미를 고치지 않고 최신성 우선순위를 전체적으로 낮추는 것이다. 검색 창 안에서 아무리 오래됐어도 2개념이 1개념을 항상 이긴다. §8-149에서 '3일 띠 안에서만' 허용한 완화를 전체로 확장한다.

**깨지는 테스트:** T13은 3일 전 2개념이 앞으로 가므로 실패. 단순 자리 교환은 T16도 결측 2개념이 날짜 있는 동일 계층 1개념을 이겨 실패한다. `(계층, 날짜 결측 여부, −개념 폭, band, …)`로 결측 우선순위만 보존하면 T16은 유지되지만 T13은 여전히 실패한다. T4는 결측과 날짜 있음의 폭이 같아 단순 교환의 결측 문제를 못 잡는다. T14·T17은 기대 순서가 계속 같아 통과 예상이지만 앵커 정책을 제대로 감시하는 능력은 약해진다. T9는 계층 우선이라 유지된다.

**구현 난이도: 낮음.** 코드 수정은 작지만 '관련성과 최신성'의 합의 변경 범위는 크다. 본 문제에 대한 권고는 아니다.

### (e) 공통 날짜 의미로 전환: 최초 발견일/실제 최초 공개일

출처마다 다른 published 대신 모든 출처의 '이 시스템에서 최초 발견한 날'을 별도 필드로 저장하여 공통 앵커를 만드는 대안이다. 원시 published는 표시·병합을 위해 보존한다. 장점은 공통 관측 사건을 비교하고 출처명으로 우대하지 않는다는 점이다. 단점은 검색 실패 복구·새 키워드·새 프로필이 가져온 오래된 논문도 오늘 발견됐다는 이유로 새로워지고, 재관측일을 쓰면 매번 회춘한다는 점이다. 개인화 프로필별 최초 발견과 시스템 전체 최초 발견도 다른 정책이다. 기존 관측 이전 이력은 복원할 수 없다.

**깨지는 테스트:** first_seen 없는 현재 fixture를 published로 fallback하면 T1~T18을 모두 유지할 수 있지만 새 동작을 하나도 검증하지 못한다. 반대로 없는 first_seen을 모두 같은 날짜로 넣으면 T13이 실패하고, 결측 published의 취급도 바꾸면 T4·T16이 깨질 수 있다. 원본 published와 merge 규칙을 보존하면 T11은 유지된다. 정확한 실패 집합은 fallback 사양에 의존한다(실행 미실측).

실제 최초 공개일(v1/online-first 등)을 출처와 무관하게 검증해 맞추는 것이 의미적으로 더 직접적인 방법이다. 그러나 현재 S2 원응답 필드·관측 스키마만으로 그 사건을 복원할 근거가 없고, 외부 메타데이터 수집/검증과 결측 정책이 필요하다. **구현 난이도: 최초 발견일 중상, 검증된 최초 공개일 높음.** 후자는 장기 설계 방향이고 이번 문제의 즉시 해법으로 권하지 않는다.

## 4. 권고: (a), 단 날짜 출처 보존을 포함한다

**(a)를 권한다.** 'source 문자열별 앵커만 급히 나누기'가 아니라 **채택한 published의 출처를 보존하고, 같은 날짜 체계의 적격 후보끼리 상대 나이를 계산하는 안**이다. 이는 현재 전역 앵커 계약을 의도적으로 개정하는 설계 제안이며, 구현·정책 적용은 하지 않았다.

권고 사양은 다음과 같다.

1. 수집 시 arXiv Atom published와 S2 publicationDate의 출처를 구별해 보존한다. 이름은 예를 들어 `published_origin`이며 **현재 존재하는 필드가 아니라 제안**이다. source/retrieval_sources(어디서 검색했는가), arxiv_id/doi(무슨 논문인가), published_origin(채택한 날짜를 어디서 얻었는가)은 구분한다. S2를 일괄 '저널 확정'으로 부르지 않는다.
2. 병합은 T11의 이른 날짜 원칙을 유지하되 날짜와 그 출처를 함께 선택한다. 날짜가 같으면 출처 선택도 입력 순서와 무관한 고정 규칙을 정한다. provenance가 없는 과거 행에는 추정 arXiv 우대를 넣지 않고 unknown 그룹을 명시한다. 관측 저장·재생·정책 버전에 이 구분이 남아야 한다. 기존 DB에 새 provenance가 있는 것처럼 소급해서 성능을 주장하지 않는다.
3. 기존 자격/기존 노출 필터를 통과한 정렬 대상에서 그룹별 최신 날짜를 계산한다. 결측 날짜는 앵커에서 빠지고 같은 계층의 날짜 있는 후보 뒤에 남는다. 그룹 분리는 계층을 앞지를 수 없으며, 단일 rank_key 정렬에서 내용/목록/reserve를 자른다. unknown이 다른 그룹의 앵커를 움직이지 않도록 정의한다.
4. 띠 폭 3일, 개념 폭 상한 2, 계층 우선, 원시 공개일 동률 가르개는 이번 개정 범위에서는 유지한다. 마지막 공개일 비교에도 서로 다른 의미가 남는 점은 숨기지 않는다. **새 공통 최초 공개일을 복원한 것이 아니라 출처별 날짜 척도로 상대 나이를 정규화한 것**이다.

왜 이 안인가: 이번 현상의 급격한 변화는 적격 최신 저널 1~2편이 다른 출처의 후보 전체를 band 밖으로 밀어낸 데 있다(사용자 제공 실측). (a)는 그 결합을 직접 끊는다. 고정 N일은 관측 분포로 정당화되지 않고, 폭 확대는 경계를 미루며, quota는 계약을 우회하고, 폭 우선은 최신성 계약을 더 크게 바꾼다. 최초 발견일은 검색 사정까지 새로움으로 바꿔버린다.

**문지기 부활이 아닌 이유:** PDF 유무/수급 가능성, journal 여부에 대한 감점, arXiv 보장 좌석을 사용하지 않는다. 두 날짜 그룹에 같은 계산을 적용한다. 상위 계층의 링크 없는 S2 후보는 하위 계층 arXiv보다 여전히 앞이고 T9를 유지해야 한다. 같은 계층에서 S2가 자기 그룹 기준으로 더 최신이거나 같은 band에서 더 넓은 개념을 맞히면 S2가 이긴다. 목적은 'arXiv 복구 n편'이 아니라 서로 다른 날짜 체계가 상대 출처의 띠를 움직이지 않게 하는 것이다.

**남는 위험과 검증 한계:** 그룹 최신이라는 이유만으로 실제로는 오래된 논문이 band 0이 될 수 있다. 후보가 적거나 검색이 부분 실패한 출처에서 특히 그렇다. 이것은 문지기는 아니지만 최신성 정의 변경의 실제 비용이다. 현재 검색 창 제한을 유지해야 하고(`scan_search.py:142-148`, `s2_delta.py:105-108`), 그 제한만으로 모든 오래된 후보 승격을 막는다고 주장하지 않는다. 주어진 9/21 상황에서 arXiv의 band 불이익이 사라지는 것은 산식상 예측이지만, 어떤 5편이 나갈지·본문 확보 비율이 좋아질지·장기 만족도가 좋아질지는 **미실측**이다. 오늘 arXiv가 반드시 몇 편 뽑힐 것이라는 수치는 제시하지 않는다.

구현 전후 비교에서는 두 가지를 분리해야 한다. 기존 후보로 source별 앵커를 근사 재생하는 것은 영향 탐색이고, 날짜 provenance를 실제 보존한 후보로 재생하는 것이 권고 사양의 검증이다. 전자를 후자의 측정치로 보고하면 안 된다.

## 5. 회귀 테스트 제안(작성하지 않음)

### 1) `test_다른_날짜_출처의_최신일은_상대_출처의_띠를_밀지_않는다`

**운영 함수:** `profile_scoring.score_and_rank`. 같은 상위 계층의 arXiv 9/17 2개념과 S2 9/20 1개념을 넣는다(날짜 출처를 명시). 양쪽 모두 자기 그룹의 최신으로 band 0이므로 arXiv 2개념이 앞이어야 한다. 날짜 출처를 맞바꿔 S2 9/17 2개념 대 arXiv 9/20 1개념으로도 실행하고 이번에는 S2가 앞이어야 한다. 추가로 9/22 무적중/제외 후보를 각 그룹에 넣어도 적격 두 후보의 순서는 같아야 한다. 동일 그룹만 있을 때 9/20 1개념 대 9/17 2개념은 기존 T13대로 최신 1개념이 이겨야 한다.

**무엇을 망가뜨리면 실패하는가:** 전역 최신 앵커를 다시 사용하면 첫 혼합 사례의 9/17 2개념이 band 1로 밀려 실패한다. arXiv라는 이름에만 가점을 주면 출처를 바꾼 대칭 사례가 실패한다. 무적중/제외를 앵커에 넣으면 잡음 추가 사례가 실패한다. 모든 날짜를 band 0으로 만들거나 개념 폭을 band 앞으로 옮기면 동일 출처 3일 경계 사례가 실패한다. 기대 band를 테스트 안에서 재계산해 자체 검증하지 않고 운영 함수의 반환 순서를 assert한다.

### 2) `test_병합한_날짜의_출처가_검색_출처나_입력_순서에_바뀌지_않는다`

**운영 함수:** `selection._merge`(또는 동일 논문의 실제 dedupe 경로) 후 `score_and_rank`. 동일 논문을 arXiv 9/18과 S2 9/16으로 넣어 병합한다. 논문은 2개념이고 두 입력 방향 모두 published=9/16, 날짜 출처=S2여야 한다. 여기에 같은 계층의 arXiv 9/18 1개념, S2 9/19 1개념을 추가한다. 병합 논문은 S2 앵커 9/19에서 band 1이므로 두 band 0 단일 개념 후보 뒤여야 한다. source 필드나 arxiv_id가 arXiv라는 이유로 arXiv 앵커 9/18을 적용하면 병합 논문이 band 0이 되어 잘못 선두로 올라간다.

**무엇을 망가뜨리면 실패하는가:** 날짜만 병합하고 날짜 출처를 갱신하지 않는 것, source/arxiv_id로 날짜 그룹을 다시 추측하는 것, 첫 입력의 출처로 그룹을 고정하는 것, 병합에서 더 최신 날짜를 선택하는 것이 모두 실패해야 한다. 두 입력 방향의 전체 순위와 보존 published/provenance를 함께 assert한다. 필드 이름은 구현 시 정하되 테스트는 실제 운영 병합·정렬 함수를 부른다.

T9의 링크 없는 상위 계층 우선과 T4/T16의 결측 규칙은 기존 테스트를 그대로 유지한다. 새로운 테스트로 기존 테스트를 대체하거나 기대값만 바꾸어 통과시키는 제안이 아니다.

## 조사 완료 범위

- 1~5절을 순서대로 나누어 저장했다. 코드·테스트 구현은 하지 않았다.
- 실행한 것은 파일/이력 식별 정보 조회와 위 mode=ro DB 집계다. pytest는 단일 파일 실행도 **실행하지 않았다**. 이번 산출물은 정적 계약 분석이므로 테스트 통과/실패 실측을 주장하지 않는다. 전체 pytest, 외부 API 재호출, 메일 발송, 운영 DB 쓰기, 커밋·push는 하지 않았다.
- 최초 읽기 명령 `cat CLAUDE.md AGENTS.md`, `git status --short`, `git rev-parse HEAD`, `git branch --show-current`의 결과로 규칙·기존 변경·main·HEAD를 확인했다. 경로 검색 중 존재하지 않는 scan_store.py/candidate_pool.py에 대한 grep 오류는 있었으며 실제 담당 경로는 scan_search.py/research_profile.py로 확인했다.
- 근거 줄 번호는 조사 시점 작업 트리 기준이다. server.py·batch_summarize.py 동시 수정 경고를 존중했고 두 파일을 수정하지 않았다. batch_summarize.py 내부를 읽거나 그 변경을 검토했다고 주장하지 않는다. 다른 작업자의 PROGRESS 변경도 보존했다.
