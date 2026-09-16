# 재생 보고 — 탐색 차선 어휘 위생 (2026-09-13)

고정 픽스처: `data/fixtures/exploration_pool_2026-09-13_7d.json` (로컬 전용, gitignore)
창 2026-09-06T00:00:00+00:00 ~ 2026-09-13T00:00:00+00:00 · 탈락 풀 **550편** · 프로필 `team_ai_advance` · 픽스처 sha256 `1c1b1aeaa7d586d8…`
전 = 고치기 전 코드(`trend_report._ngrams` 정확 토큰 일치) · 후 = `term_hygiene` 적용. 같은 풀, 같은 프로필, 같은 규칙.

## 요약

| | 전 | 후 |
|---|---|---|
| 후보 전체 | 393 | **309** |
| 상위 3 (제안기에 가는 것) | `state-of-the-art performance`(support), `computational overhead`(domain), `previous studies`(seed) | `computer vision`(support), `computational overhead`(domain), `percentage points`(seed) |
| 상위 3 중 상투구 | **2 / 3** | 0 / 3 |
| `state estimation` | 후보 아님(`state` 낱말 목록에 죽음) | **도메인 차선 20위 안에 복귀** |
| `computational overhead` | 도메인 1위 | 도메인 1위 (검토 의견대로 유지) |

## 버린 사유 집계 (후, 로컬 진단 — LLM 에 안 나간다)

| 사유 | 건수 | 뜻 |
|---|---|---|
| `edge_stop` | 90,911 | 양 끝이 불용어·연구 동사·평가형 형용사 |
| `min_papers` | 77,299 | 3편 미만 |
| `boilerplate_word` | 1,532 | 낱말 목록(정규화 뒤: studies→study, open-source→open+source) |
| `hard_noise` | 676 | URL·저장소 조각 · 깨진 하이픈(`low-`) |
| `umbrella_generic` | 641 | 낱말 전부가 우산어 |
| `known_overlap` | 276 | 현재 키워드·도메인·제외어와 포함 관계 |
| `boilerplate_phrase` | 90 | 담화 구절(state of the art · previous work · orders of magnitude…) |
| `subsumed` | 77 | 더 긴 조합에 그대로 들어 있음 |
| `known_variant` | 1 | 기존 키워드의 표기 변형 → 매처 진단으로 |

## 차선별 상위 20 — 전 · 후

| 차선 | # | 전 | 후 |
|---|---|---|---|
| support | 1 | `state-of-the-art performance` 12 | `computer vision` 10 |
|  | 2 | `computer vision` 10 | `web science` 8 |
|  | 3 | `collected through` 9 | `classroom action research` 7 |
|  | 4 | `web science` 8 | `logistic regression` 7 |
|  | 5 | `case studies` 7 | `semi-structured interviews` 7 |
|  | 6 | `classroom action research` 7 | `random forest` 6 |
|  | 7 | `examines how` 7 | `risk stratification` 6 |
|  | 8 | `further research` 7 | `control group` 6 |
|  | 9 | `future research` 7 | `existing benchmarks` 6 |
|  | 10 | `logistic regression` 7 | `question answering` 6 |
|  | 11 | `semi-structured interviews` 7 | `regression models` 6 |
|  | 12 | `random forest` 6 | `retrospective cohort` 6 |
|  | 13 | `risk stratification` 6 | `robotic manipulation` 6 |
|  | 14 | `carried out` 6 | `world bank` 6 |
|  | 15 | `consistently improves` 6 | `climate change` 5 |
|  | 16 | `control group` 6 | `empirical evidence` 5 |
|  | 17 | `existing benchmarks` 6 | `focus group discussions` 5 |
|  | 18 | `question answering` 6 | `kemmis and mctaggart` 5 |
|  | 19 | `regression models` 6 | `language modeling` 5 |
|  | 20 | `retrospective cohort` 6 | `model selection` 5 |
| domain | 1 | `computational overhead` 3 | `computational overhead` 3 |
|  | 2 | `embodied intelligence` 3 | `embodied intelligence` 3 |
|  | 3 | `prediction horizon` 3 | `prediction horizon` 3 |
|  | 4 | `aerial vehicle` 3 | `aerial vehicle` 3 |
|  | 5 | `among children` 3 | `autonomous driving` 3 |
|  | 6 | `autonomous driving` 3 | `computational efficiency` 3 |
|  | 7 | `comparative studies` 3 | `computationally expensive` 3 |
|  | 8 | `computational efficiency` 3 | `core components` 3 |
|  | 9 | `computationally expensive` 3 | `end-to-end latency` 3 |
|  | 10 | `end-to-end latency` 3 | `extreme gradient boosting` 3 |
|  | 11 | `extreme gradient boosting` 3 | `generation pipeline` 3 |
|  | 12 | `falls short` 3 | `machine learning algorithms` 3 |
|  | 13 | `generation pipeline` 3 | `middle-income countries` 3 |
|  | 14 | `information about` 3 | `operating conditions` 3 |
|  | 15 | `jointly models` 3 | `prediction model` 3 |
|  | 16 | `jointly optimizes` 3 | `safety constraints` 3 |
|  | 17 | `low- and middle-income` 3 | `sample size` 3 |
|  | 18 | `machine learning algorithms` 3 | `space requiring` 3 |
|  | 19 | `middle-income countries` 3 | `spearman rho` 3 |
|  | 20 | `models are increasingly` 3 | `state estimation` 3 |
| seed | 1 | `previous studies` 7 | `percentage points` 6 |
|  | 2 | `all three` 6 | `public health` 6 |
|  | 3 | `percentage points` 6 | `united states` 6 |
|  | 4 | `public health` 6 | `error rate` 5 |
|  | 5 | `united states` 6 | `inference latency` 5 |
|  | 6 | `error rate` 5 | `success rates` 5 |
|  | 7 | `inference latency` 5 | `task success` 5 |
|  | 8 | `often rely` 5 | `complex real-world` 4 |
|  | 9 | `success rates` 5 | `graphical user interface` 4 |
|  | 10 | `task success` 5 | `multi-agent system` 4 |
|  | 11 | `complex real-world` 4 | `nonlinear dynamics` 4 |
|  | 12 | `graphical user interface` 4 | `distribution shifts` 3 |
|  | 13 | `multi-agent system` 4 | `generation process` 3 |
|  | 14 | `nonlinear dynamics` 4 | `primary outcomes` 3 |
|  | 15 | `two distinct` 4 | `real-world settings` 3 |
|  | 16 | `aims explore` 3 | `root cause` 3 |
|  | 17 | `article concludes` 3 | `visually grounded` 3 |
|  | 18 | `distribution shifts` 3 | `ground truth` 4 |
|  | 19 | `future studies` 3 | `operating characteristic curve` 4 |
|  | 20 | `generation process` 3 | `receiver operating characteristic` 4 |

## 상위 20 에서 사라진 것 (23)
`aims explore`, `all three`, `among children`, `article concludes`, `carried out`, `case studies`, `collected through`, `comparative studies`, `consistently improves`, `examines how`, `falls short`, `further research`, `future research`, `future studies`, `information about`, `jointly models`, `jointly optimizes`, `low- and middle-income`, `models are increasingly`, `often rely`, `previous studies`, `state-of-the-art performance`, `two distinct`

전부 담화 조각이다. 기술 용어는 하나도 없다.

## 상위 20 에 새로 든 것 (23)
`climate change`, `core components`, `empirical evidence`, `focus group discussions`, `ground truth`, `kemmis and mctaggart`, `language modeling`, `model selection`, `operating characteristic curve`, `operating conditions`, `prediction model`, `primary outcomes`, `real-world settings`, `receiver operating characteristic`, `robotic manipulation`, `root cause`, `safety constraints`, `sample size`, `space requiring`, `spearman rho`, `state estimation`, `visually grounded`, `world bank`

세 부류다. ① 기술 용어(`state estimation` · `robotic manipulation` · `language modeling` · `model selection` · `safety constraints` · `ground truth` · `receiver operating characteristic`) ② **도메인 밖의 진짜 구절**(`world bank` · `climate change` · `focus group discussions` · `kemmis and mctaggart`) — S2 씨앗 잡음이고 위생 계층의 일이 아니다(단일 씨앗 진단·도메인 축이 다룬다) ③ 조각 둘(`space requiring` · `empirical evidence`) — 규칙으로 설명되지 않아 **일부러 안 막았다**(외부 검토: 상위 3만 보고 하나씩 지우는 whack-a-mole 을 하지 않는다).

## 알고 감수한 대가
- 평가형 형용사(`strong` · `promising` · `consistent` …)를 가장자리 금지어로 넣어 `strong scaling`(HPC 용어)이 죽는다. 이 프로필에서는 실측 0편.
- `art` 는 낱말 목록에 남겼다 — 띄어 쓴 "state of the art" 의 `art performance` 조각을 잡기 위해서. `art` 단독이 기술 용어인 경우는 이 분야에 없다.

## 검증
- `test_term_hygiene.py` 7건(가드표 · 띄어쓴/하이픈 SOTA 동일 판정 · 표시 문자열 보존 · all-token 우산어 · 세 소비자 동일 판정 · 진단이 제안기 입력에 안 실림 · 픽스처 재생 대조). 전체 936 통과.
- 돌연변이 6건 전부 검출: `state` 복귀(5 실패) · 가장자리 검사를 정규화 토큰으로(3) · 단수화 제거(1) · 구절 짧은 낱말 제거 삭제(1) · SOTA 구절 삭제(2) · 우산어 all→any(3).

## 2026-09-14 추가 재생

같은 고정 픽스처 550편과 같은 프로필·규칙으로 `data`·`time`·`times`를 가장자리 목록에서
제거한 전후를 재생했다. 아래는 각 축의 상위 20이며 괄호는 지지 편수다.

| 축 | 제거 전 | 제거 후 |
|---|---|---|
| support | `computer vision`(10), `web science`(8), `classroom action research`(7), `logistic regression`(7), `semi-structured interviews`(7), `random forest`(6), `risk stratification`(6), `control group`(6), `existing benchmarks`(6), `question answering`(6), `regression models`(6), `retrospective cohort`(6), `robotic manipulation`(6), `world bank`(6), `climate change`(5), `empirical evidence`(5), `focus group discussions`(5), `kemmis and mctaggart`(5), `language modeling`(5), `model selection`(5) | `computer vision`(10), `web science`(8), `classroom action research`(7), `logistic regression`(7), `semi-structured interviews`(7), `synthetic data`(7), `random forest`(6), `risk stratification`(6), `control group`(6), `existing benchmarks`(6), `question answering`(6), `regression models`(6), `retrospective cohort`(6), `robotic manipulation`(6), `world bank`(6), `climate change`(5), `real-world data`(5), `empirical evidence`(5), `focus group discussions`(5), `kemmis and mctaggart`(5) |
| domain | `computational overhead`(3), `embodied intelligence`(3), `prediction horizon`(3), `aerial vehicle`(3), `autonomous driving`(3), `computational efficiency`(3), `computationally expensive`(3), `core components`(3), `end-to-end latency`(3), `extreme gradient boosting`(3), `generation pipeline`(3), `machine learning algorithms`(3), `middle-income countries`(3), `operating conditions`(3), `prediction model`(3), `sample size`(3), `space requiring`(3), `spearman rho`(3), `state estimation`(3), `test set`(3) | `computational overhead`(3), `embodied intelligence`(3), `prediction horizon`(3), `aerial vehicle`(3), `autonomous driving`(3), `computational efficiency`(3), `computationally expensive`(3), `core components`(3), `end-to-end latency`(3), `extreme gradient boosting`(3), `generation pipeline`(3), `machine learning algorithms`(3), `middle-income countries`(3), `operating conditions`(3), `prediction model`(3), `safety constraints`(3), `sample size`(3), `space requiring`(3), `spearman rho`(3), `state estimation`(3) |
| seed | `public health`(6), `united states`(6), `error rate`(5), `inference latency`(5), `task success`(5), `complex real-world`(4), `graphical user interface`(4), `multi-agent system`(4), `nonlinear dynamics`(4), `generation process`(3), `primary outcomes`(3), `real-world settings`(3), `root cause`(3), `safety constraints`(3), `visually grounded`(3), `ground truth`(4), `operating characteristic curve`(4), `receiver operating characteristic`(4), `report generation`(4), `shapley additive explanations`(4) | `public health`(6), `united states`(6), `error rate`(5), `inference latency`(5), `task success`(5), `complex real-world`(4), `data collection`(4), `graphical user interface`(4), `inference time`(4), `multi-agent system`(4), `nonlinear dynamics`(4), `generation process`(3), `primary outcomes`(3), `real-world settings`(3), `root cause`(3), `visually grounded`(3), `ground truth`(4), `operating characteristic curve`(4), `receiver operating characteristic`(4), `report generation`(4) |

`data` 제거로 새로 올라온 `synthetic data`·`real-world data`·`data collection`, `time` 제거로
새로 올라온 `inference time`은 단위·데이터·지연을 나타내는 기술 표현이었다. 세 축의 상위 20에
새 담화 상투구는 없었다. 반면 `analysis`까지 제거한 별도 재생에서는 support 상위 20에
담화 조각 `analysis reveals`(5)가 새로 올라와 `analysis`는 유지했다(2026-09-14 외부 검토).
