# 연구 활용 후보 — 1주차 수동 시범 기록 (2026-10-24 착수)

계획 `docs/PLAN_research_assets_2026-10-24.md` v3-4. **코드 변경 없음** — 확인은 공개 API(GitHub·HF·Zenodo·S2)와 공식 문서 조회로, 시험 스크립트는 임시 폴더.
확인 주체 표기: **[API]** = Python 이 API 응답 필드로 확인(자동화 가능) · **[문서]** = 공식 문서·README 원문을 읽어 확인(문장 판정은 Claude — 사람 판단) ·
**[판단]** = Claude 해석. 숫자는 원문 그대로, 확인 못 한 것은 "미확인".

## 1. 따로 보고 — 발표 자료 확인 항목 (v26 덱 27·28장)

| 항목 | 확인 결과 | 출처 |
|---|---|---|
| **CSEM-MISD 조명 조건 수** | **108개의 서로 다른 조명 각도**("successively illuminated it from 108 distinct illumination angles"). 같은 고도(elevation)에 각도 12개씩, 같은 고도에서 이웃 각도 사이 방위각 차이 30도 → 고도 9단계 × 12 [문서]. 시료당 512×512 회색조 영상 108장 + 전경 마스크 + 손으로 그린 결함 마스크 [문서] | Zenodo 공식 기록 10.5281/zenodo.7410513(v0.2.0, 2021-09-16, CSEM·EPFL) [API] |
| CSEM-MISD 접근·라이선스 | 공개 접근(open) · **CC BY-NC-ND 4.0**(비상업·변경 금지) [API — Zenodo API `metadata.license.id`=cc-by-nc-nd-4.0, `access_right`=open, 10/24 16:4x 원응답]. 파일 gear 6.79 GB · washer 0.89 GB · screw 6.56 GB(합 14.2 GB) [API] | 같은 기록 |
| CSEM-MISD 구성 | 와셔(결함 70)·기어·나사(각 결함 35·정상 35·주석 없는 수백 개), 학습 32·시험 38 시료 [문서]. 영상은 8비트 회색조 png. 각 데이터셋에 **조명 방향 벡터(`light_vectors.csv`)와 밝기 값(`light_intensities.csv`, 0~127 척도)** 파일이 있다 — 밝기 값의 실제 분포·파장 조건은 미확인(파일을 받지 않음) [문서]. 방위각 30도는 설계 간격이고, 원문은 "방위각은 보정하지 않아 몇 도 어긋날 수 있다" 고 주의 [문서]. **정상(intact) 시료는 기어·나사 묶음에만** — 와셔는 결함 시료만 [문서]. (10/24 18시 교정: 처음엔 "밝기 서술 없음" 이라 적었다 — Codex 검토가 `light_intensities.csv` 를 짚었고 Claude 가 Zenodo 원문으로 확인) | 같은 기록 |
| **LIBAD 접근 조건** | 데이터는 HF `Evenrose/LIBAD` — **gated = auto**(HF API `/api/datasets/Evenrose/LIBAD` 원응답 `gated: "auto"`, `cardData.extra_gated_fields` 없음, 10/24 16:4x — HF 의 auto 는 로그인 후 접근 요청에 동의하면 자동 승인. Codex 는 이 값을 독립 재확인하지 못함) · 라이선스 cc-by-4.0 [API]. 코드 저장소 `github.com/evenrose/LIBAD`(BSD-3-Clause) [API], README 가 HF 데이터셋을 다운로드 경로로 안내 [문서]. 논문 본문에는 데이터 접근 조건·승인 절차 문장이 없다 [문서] | arXiv 2608.07958, GitHub API, HF API |
| LIBAD 구성(참고) | 모달리티 3(VIS·X-rayH·X-rayL), 벤치마크는 VIS+X-rayL. 744 패치(정상 361·이상 383), 공식 분할 10개 [문서] | arXiv 2608.07958 본문 |

해석: 외부 의견의 "LIBAD 데이터는 접근 조건 수락 필요" 는 맞다(gated). 다만 수동 승인 대기가 아니라 **동의 즉시 자동 승인**이다 — "바로 내려받아 실행 가능" 과는
다르고(로그인·동의 필요), "승인 대기" 와도 다르다. CSEM-MISD 는 **비상업 라이선스**라 과제(제조) 활용 시 사용 조건 확인이 필요하다 — 외부 의견에 없던 사실.
(사용자 확인 10/24: Zenodo·HF 페이지 기준 같은 값. 사용자가 덱 27·28장·구현계획 확인 목록에 반영 — CSEM-MISD 는 탐색 루프 연구 데모로만, 가공 데이터는 밖으로 내보내지 않는 선.)

## 2. 후보 11건 — 주장 단위 기록

판정 주체: **규칙** = API 필드·정확 일치로 기계 판정 가능 · **Claude** = 원문을 읽고 의미 판정(사람 판단 — 자동화 완료로 세지 않음). 실행·다운로드는 하지 않았다(실행 미확인).
수작업 시간: 후보별로 재지 않았다(미측정) — 1절·2절·3절 확인 전체가 약 15분(16:43~16:58, Claude 도구 호출 기준).

| 후보·자료 ID | 주장 | 근거(URL·필드·원문 위치) | 수집 | 판정 | 결과 | 자동화·수동 유지 방안 |
|---|---|---|---|---|---|---|
| ZEN `gh:gorules/zen` | 공식 배포 | repo API(소유 gorules, homepage gorules.io) | API | 규칙 | 프로젝트 자체 저장소 | 비논문 경로: homepage ↔ 저장소 |
| ZEN | 라이선스 | repo API `license.spdx_id`=MIT | API | 규칙 | MIT | 자동 |
| ZEN | Python 예제 있음 | `bindings/python/README.md` 42·49~58행(`pip install zen-engine`, `zen.ZenEngine()`, `evaluate`) | API(하위 README) | Claude | 있음(실행 미확인) | 하위 폴더 README 탐색 규칙 + 코드 블록 언어 태그(```python) 판정은 규칙화 가능 |
| eventsourcing `gh:pyeventsourcing/eventsourcing` | 공식 배포·문서(양방향) | repo API homepage=eventsourcing.readthedocs.io · 문서 첫 화면 `href="https://github.com/pyeventsourcing/eventsourcing"`(18시 재확인) | API+문서 조회 | 규칙 | **양방향 확인** — 비논문 통과 경로 충족 | 자동(두 링크 정확 일치) |
| eventsourcing | 라이선스 | BSD-3-Clause | API | 규칙 | 확인 | 자동 |
| eventsourcing | SQLite·PostgreSQL 지원 | 패키지 파일 `sqlite.py`·`postgres.py`(contents API), README 108~117행 SQLite 예제 | API | 규칙(파일명)+Claude(예제) | 있음 | 파일명 규칙은 자동, "예제" 판정은 Claude |
| vLLM SO `doc:docs.vllm.ai/…/structured_outputs` | 문서 존재·내용 | HTTP 200, 제목 "Structured Outputs - vLLM", JSON schema 서술 | 문서 조회 | Claude | 있음 | 문서 도달 단계 수: 미시험 |
| vLLM `gh:vllm-project/vllm` | 라이선스 | Apache-2.0 | API | 규칙 | 확인 | 자동 |
| vLLM | 서빙 요구 조건 | 이 문서에서 자동 추출 못함 | — | — | 미확인 | 설치 문서 별도 조회 필요 |
| AssetOpsBench `gh:IBM/AssetOpsBench` | 공식 저장소↔논문 | README 25행: Paper arXiv 2506.03828 · HF Dataset 링크 | API | Claude(자기 논문 문맥 판정) | 양쪽 연결 후보(논문→저장소 방향 미확인) | 논문 본문 링크 확인 필요 |
| AssetOpsBench | 라이선스·데이터 접근 | repo Apache-2.0, HF `ibm-research/AssetOpsBench` gated=False, license apache-2.0 | API | 규칙 | 공개 | 자동 |
| AssetOpsBench | 시나리오 수·구성·실행 경로 | README "141+ Scenarios", "5 + 1 Domain agents + utility server", 61~62행 "Try a scenario **(to be enabled)**", 66행 Colab 링크 | API(README) | Claude | 숫자는 README 그대로. **시나리오 실행 명령은 아직 비활성.** Colab 노트북은 LangChain·Watsonx.ai 일반 LLM 에이전트 예제(셀 7 `WATSONX_APIKEY`·`WATSONX_PROJECT_ID` 필요) — **벤치마크 실행 경로가 아니다**(18시 노트북 원문 확인) | "(to be enabled)"·노트북 성격 판정은 Claude |
| Anomalib 문서 `doc:anomalib.readthedocs.io/…/get_started/anomalib.html` | 문서 존재·도달 | HTTP 200, 제목 "Anomalib in 15 Minutes", Engine·fit·predict. 저장소 homepage(anomalib.readthedocs.io/en/latest/)에 `href="markdown/get_started/anomalib.html"` — **한 단계로 도달**(18시 확인) | 문서 조회 | 규칙(링크)+Claude(내용) | 있음 | 자동(한 단계 링크) |
| Anomalib `gh:open-edge-platform/anomalib` | 이전된 저장소 | openvinotoolkit/anomalib 조회가 open-edge-platform/anomalib 로 리다이렉트 | API | 규칙 | 같은 저장소 | 자동(리다이렉트 추적) |
| LIBAD | 1절 | 1절 | API+문서 | 규칙(gated·license)+Claude(본문 문장) | 1절 | gated 값은 자동, 본문의 접근 조건 문장 부재는 Claude |
| CSEM-MISD `zenodo:7410513`(상위 기록 5513768) | 1절 | 1절 | Zenodo API | 규칙(license·access·files)+Claude(조명 서술) | 1절 | **Zenodo 는 예정 출처(S1~S5)에 없다** |
| BoTorch 튜토리얼 `doc:botorch.org/docs/tutorials/closed_loop_botorch_only` | 문서 존재·내용 | HTTP 200, SingleTaskGP·qLogNoisyExpectedImprovement | 문서 조회 | Claude | 있음 | 부모 발견 ≠ 문서 도달 |
| BoTorch `gh:meta-pytorch/botorch` | 이전된 저장소·라이선스 | pytorch/botorch → meta-pytorch/botorch, MIT, homepage botorch.org | API | 규칙 | 확인 | 자동 |
| LeRobot `gh:huggingface/lerobot` | 공식·문서·라이선스 | Apache-2.0, homepage huggingface.co/docs/lerobot | API | 규칙 | 확인 | 자동 |
| LeRobot | 하드웨어 없이 시작 | 설치 문서: "Simulations Install environment packages: aloha (gym-aloha), or pusht (gym-pusht)" | 문서 조회 | Claude | 시뮬레이션 경로 있음 | 문장 판정은 Claude |
| LeRobot | 환경 조건 | 설치 문서 "PyTorch CUDA variant (Linux only)": **Linux 에서 uv 로 소스 설치할 때** torch·torchvision 이 CUDA 12.8 인덱스(드라이버 하한 570.86)로 고정, 다른 CUDA(cu126·cu130)로 바꾸는 명령 안내. macOS·Windows 는 PyPI 기본 | 문서 조회 | Claude | 원문 인용(조건 한정) | 정규식으로 위치, 적용 조건은 Claude — 처음엔 "Linux 고정" 으로 넓게 적었다(Codex 지적) |
| SmolVLA `hf-model:lerobot/smolvla_base` | 체크포인트·라이선스·접근 | HF API: gated=False, license apache-2.0, `model.safetensors` 파일 있음, arxiv 2506.01844 | API | 규칙 | 파일 확인 | 자동 |
| SmolVLA 문서 `doc:huggingface.co/docs/lerobot/smolvla` | 학습 조건 | "Training the model for 20k steps will roughly take ~4 hrs on a single A100 GPU." · "If you don't have a gpu device, you can train using our notebook"(Google Colab 노트북) | 문서 조회 | Claude | **학습 소요 예시이지 필수 조건 아님** | 정규식은 A100 을 조건으로 잘못 뽑는다(시험 T4) — Claude 판정 필요 |
| robomimic `gh:ARISE-Initiative/robomimic` | 라이선스·문서 | MIT, 문서 Getting Started(HTTP 200) | API+문서 | 규칙+Claude | 확인 | — |
| robomimic | 실행 조건 | 문서: 예제는 robosuite v1.5.1 필요, rollout 을 끄면 robosuite 없이 실행 가능 | 문서 조회 | Claude | 원문 인용 | Claude |

## 3. 시험 자료 — 실제 후보와 다른 목록, 추천·분모에서 제외 (`recommendable=false`)

판정 결과는 이번 주엔 Claude 가 부록 4절 규칙(관계 종류·근거 등급)을 손으로 적용한 결과다 — 자동 판정기가 아니다. 주입 주장은 시험용이며, 해당 자료가 그렇게 주장한다는 뜻이 아니다.

| fixture_id | source_asset_id | 주입 주장 | 정답 | 판정 근거 [API·문서] | 판정 결과 |
|---|---|---|---|---|---|
| F-01 | `gh:niejunnan25/OpenVLA` | 이름이 OpenVLA 이므로 OpenVLA 공식 구현이다 | 제3자 재현(`implements` 주장만) | 설명: OpenVLA 재현·LIBERO 파인튜닝 개선, 라이선스 없음, README 가 공식 저장소를 clone | 공식 승격 거부 |
| F-02 | `hf-model:moritzknaust/openvla-7b` | `arxiv:2406.09246` 태그가 있으므로 공식 체크포인트다 | `cites` | 공식 README 의 체크포인트 목록에 없음, base_model 메타 없음 | 거부 |
| F-03 | `hf-dataset:Evenrose/LIBAD` | 페이지가 공개이므로 파일을 즉시 내려받을 수 있다 | 로그인·동의 후 자동 승인(gated auto) | HF API `gated: auto` | 교정 |
| F-04 | `doc:huggingface.co/docs/lerobot/smolvla` | SmolVLA 는 A100 GPU 가 필요하다 | 학습 소요 예시, 필수 조건 아님. GPU 없으면 노트북 경로 | 문서 원문 두 문장 | 교정 |
| F-05 | `gh:M-3LAB/awesome-industrial-anomaly-detection` | 이 목록 저장소가 PatchCore 공식 구현이다(Codex 제안) | 논문 목록(인용·소개) | 설명 "Paper list and datasets…", 라이선스 없음 | 거부 |
| F-06 | `gh:hcw-00/PatchCore_anomaly_detection` | 논문 링크가 있으므로 공식 구현이다(Codex 제안) | 비공식 구현(코드는 있음 — "없음" 으로 만들지 않음) | 설명·README "Unofficial implementation of PatchCore", Apache-2.0 | 거부, 제3자 구현으로 보존 |
| F-07 | `hf-model:TheBloke/Llama-2-7B-Chat-GGUF` | arXiv 태그가 있으므로 Meta 원본 공식 체크포인트다(Codex 제안) | 제3자 양자화본(`quantized_from`) | 태그 `base_model:quantized:meta-llama/Llama-2-7b-chat-hf`, license llama2 | 거부, 관계 보존 |
| C-01 (정상·복잡) | OpenVLA: arXiv 2406.09246 · `gh:openvla/openvla`(MIT) · HF 체크포인트 | — | 공식 체크포인트 7개 | README 가 HF 링크 7개. 그중 `openvla-7b-v01` 링크는 401, 실제 ID `openvla/openvla-v01-7b`(200) | 링크 오타를 표시하고 공식 유지 — 깨진 링크로 버리면 오판 |
| C-02 (정상·복잡) | SmolVLA: arXiv 2506.01844 · LeRobot 안(전용 저장소 없음) · `hf-model:lerobot/smolvla_base` · 데이터셋 SVLA SO100 PickPlace | — | `lerobot/smolvla_base` = 공식 사전학습 체크포인트(`checkpoint_for`), 데이터셋 = 참고 예시(`dataset_for`), 사용자 파인튜닝 결과 = 별개 | **프로젝트 문서(huggingface.co/docs/lerobot/smolvla)가 자기 사전학습 모델로 `lerobot/smolvla_base` 를 직접 지정**(학습 명령 `--policy.path=lerobot/smolvla_base`) — 조직명 일치가 아니라 이 지정이 근거(Codex 지적으로 근거 교정) | "논문↔전용 저장소" 규칙만이면 오판 — 문서 지정 경로 필요 |

## 4. 재발견 시험 결과 (2026-10-24 16:46~16:53, 질의 44 + 보완 14 = 58 — 일반 과업어 50·제품명 8)

**이 숫자는 "이번 질의·예산에서 웹이 고른 11건을 다시 찾은 결과" 다 — 과업 전체의 발견 성능도, API 경로의 상한도 아니다.** S2(참고문헌)·S5(우리 DB)·문서 도달은 시험하지 않았고,
API 가 찾은 **다른** 유용 후보의 정밀도는 2주차에 평가한다. 오류: 결과 파일의 **최종 실패 0건**(S2·HF·GitHub) — S2 재시도 중 오류·429 횟수는 기록하지 않아 미실측.
고정 시각: 보완 질의·규칙(v3-4-0)은 16:50 에 계획서에 적었고 본시험 집계는 16:51 에 처음 봤다 — 로그에 질의별·열람 시각이 없어 문서로 입증하지는 못한다.
집계 규칙(18시 교정): HF 는 **모델·데이터셋 따로, 종류 안 순위**, 일치는 **전체 ID 정확 일치**(조직 접두 일치는 "관련 조직 자료" 로 따로). 처음 스크립트는 HF 를 합친 목록 순위(22위)로 세고 `lerobot/` 접두로 조직 전체를 LeRobot 으로 셌다.
실행 예외: 계획 질의 "규칙 기반 의사결정 엔진" 은 영어 "rule-based decision engine" 으로 바꿔 실행했다(후보 적중 없음 — 결과에 영향 없음).

| 후보 | 일반 과업어 — 예정 경로(S1 S2 인용 순·S3 HF) | 일반 과업어 — 시험 확장(S4 GitHub 검색) | 제품명 검색어 | 대상 조회(이름·ID) |
|---|---|---|---|---|
| GoRules ZEN | 없음 | "business rules engine python" **1위** | — | GitHub API ✔ |
| eventsourcing | 없음 | 없음 | — | GitHub API ✔ |
| vLLM SO 문서 | 없음 | 없음 | — | 문서 URL ✔(부모 GitHub ✔) |
| AssetOpsBench | 없음 | "asset operations agent" **1위** | — | GitHub·HF API ✔ |
| Anomalib 문서 | 없음 | 없음 | "anomalib": S1 1위·S4 1위(부모 발견. 시작 문서는 homepage 에서 한 단계 링크 — 18시 확인, 시험 스크립트에서는 미시행) | ✔ |
| LIBAD | 없음 | 없음 | — | GitHub·HF API ✔ |
| CSEM-MISD | 없음 | 없음 | — | **Zenodo API 만**(예정 출처 밖) |
| BoTorch 튜토리얼 | 없음 | 없음 | "BoTorch": S1 2위·S4 1위(부모 발견, 튜토리얼 도달 미시험) | ✔ |
| LeRobot | 없음 (관련 조직 자료만: "pick and place" HF 데이터셋 2위 `lerobot/utokyo_xarm_pick_and_place` — 후보 재발견으로 세지 않음) | 없음 | "LeRobot": S4 1위, S1 논문 2위(arXiv 2602.22818 — README 가 가리키는 논문), HF 모델 1위는 `lerobot/smolvla_base`(LeRobot 도구 자체 아님) | ✔ |
| SmolVLA | 없음 | 없음 | — | HF API ✔ |
| robomimic | "imitation learning manipulation": S1 5위 = **부모 논문**(arXiv 2108.03298) — 후보는 튜토리얼이고 **튜토리얼 도달은 미시험** | 없음 | — | ✔ |

요약(분모 11, 일반 과업어만 — 문서 도달과 논문 발견을 합치지 않음):

| 관측 대상 | 상위 5 | 상위 10 | 상위 20 |
|---|---:|---:|---:|
| S1(S2 인용 순): robomimic **부모 논문** | 1 | 1 | 1 |
| S3(HF): 후보 전체 ID 정확 일치 | 0 | 0 | 0 |
| S4(GitHub, 시험 확장): ZEN·AssetOpsBench 저장소 | 2 | 2 | 11~20위 미수집(10개만 받음) |
| 후보 문서까지 도달 | 미시험 | 미시험 | 미시험 |

- 제품명 검색어: 부모 프로젝트 3건(Anomalib·BoTorch·LeRobot) + LeRobot 논문 — 이름을 아는 검색이라 발견으로 세지 않는다.
- 대상 조회: 11/11 (CSEM-MISD 는 Zenodo — 예정 출처 밖).
- 웹 추가 효과: 11건 모두 웹(외부 의견)에서 왔다. **이번 질의·예산에서** 일반 과업어 API 경로는 부모 논문 1건, GitHub 시험 확장은 2건을 다시 찾았다.
  "웹 없이는 못 찾는다" 는 이 시험이 입증한 상한이 아니다 — API 가 찾은 다른 자료의 유용성(2주차)과 함께 판단한다.

관찰(설계 근거):
- HF `search` 는 ID 문자열 검색이라 문장형 과업어는 0건이 많다(보완 질의 14개 모두 HF 0건) — 자료 부재가 아니다.
- S2 인용 순 정렬은 일반 과업어에 엉뚱한 고인용 논문(의학·심리학)을 올린다 — 분야 필터(fieldsOfStudy)·연도·구문 조합이 필요하다.
- GitHub 키워드 검색은 v2 에서 "awesome 목록이 위로 와서 약함" 으로 뺐지만, **도구형 과업어**("business rules engine python", "asset operations agent")에서는 공식 저장소가 1위였다 — 경로 재검토 근거.
- 문서·튜토리얼은 부모 발견 뒤 도달을 따로 봐야 한다 — Anomalib 시작 문서는 homepage 에서 한 단계로 닿는다(18시 확인). vLLM·BoTorch·robomimic 문서의 도달 단계 수는 미시험.

### 4.1 AG-03 칸 (결측·지연된 입력에서 여러 모델 판단 결합)
실습 자료: **찾지 못함.** 보완 질의 3개(asynchronous decision fusion·missing classifier outputs·timeout-aware ensemble)는 S2·GitHub 결과가 반환됐으나(예: S2 "Ensemble based systems in decision making") 실습 적합성을 확인한 자료는 확보하지 못함, HF 0건.
배경 후보(**미확인**, 관련성은 Claude 판단): "ModDrop"(TPAMI, 모달리티를 빼고 학습하는 강건 결합 — S2 "missing modality" 1위), "Multimodal Machine Learning: A Survey and Taxonomy"
(arXiv 1705.09406 — 결합 방식 분류, S2 "late fusion" 1위), "A survey of multiple classifier systems as hybrid systems"(2014 — 판단 결합, S2 "decision-level fusion" 5위).
서지 확인(16:56): 다중 분류기 서베이 = Information Fusion 2014, DOI 10.1016/j.inffus.2013.04.006 [API] ✔ · ModDrop = S2 기록 제목이 깨져 있어("TRANSACTIONS ON PATTERN ANALYSIS… ModDrop") 제목 조회로 못 찾음 — 미확인 ·
1705.09406 = S2 429(요청 과다)로 미확인. S2 키 없이 연속 조회하면 429 가 난다 — 자동화 때는 키·간격 필요.
목록 저장소 `Jian-Lang/awesome-modality-missing-learning` 은 자료가 아니라 목록이다(시험 F-05 와 같은 종류). → (10/24 사용자 결정) **AG-03 은 실습 칸에서 빼고 배경 읽기 목록 대상으로 옮긴다.** 이 시험이 입증한 것은 **"이번 검색·확인 범위에서 실습 기준을 충족한 자료를 확보하지 못했다"** 까지다 —
반환된 후보(본시험 GitHub 의 구현 저장소들 등)를 하나씩 검토·탈락시킨 기록이 없어 "도구가 없는 영역" 은 입증되지 않았다(Codex 지적). 사용자 판단(정형화된 도구가 드문 영역)은 가설로 둔다. 배경 후보 3편 서지 확인은 2주차.

## 5. 사람이 판단한 부분 (자동화하려면 무엇이 필요한가)
- 문서 문장 판정(예: "(to be enabled)", "rollout 을 끄면 robosuite 없이", "GPU 없으면 노트북") — README·문서 원문을 읽고 의미를 판단. 정규식으로는 위치만 찾고 판정은 사람(또는 검증 모델).
- 저장소 이전(openvinotoolkit→open-edge-platform, pytorch→meta-pytorch) — GitHub API 가 리다이렉트로 따라가 줘서 자동 처리 가능 [API].
- 비논문 후보의 양방향 확인(저장소 homepage ↔ 문서의 저장소 링크) — eventsourcing 은 18시에 역링크까지 확인. 두 URL 정확 일치라 자동화 가능.
- 하위 폴더 README(ZEN Python 예제) — 루트 README 만 보면 "Python 예제 없음" 으로 오판. 바인딩 폴더 탐색 규칙 필요.
- 라이선스의 의미(CC BY-NC-ND = 비상업) — 이름은 API 로, 과제 활용 가능 여부는 사람이 판단(자동 판정 안 함 — 계획 부록 4.2).

## 6. 시범 메일 1회차 (Codex 검토 2회 — 17:50 검토 6건 수정 · 재검토 "발송 가능" — 뒤 **2026-10-24 18:06 시험 수신 주소 한 곳 발송**, 반응 버튼 없음)

제목: [연구 활용 후보 · 시범 1회차] 지금 과업의 병목을 줄여 줄 자료 (2026-10-24)

```
이 메일은 시범입니다. 매일 오는 최신 동향 브리핑과 별개로, 지금 하고 있는 과업마다 "막힌 곳을 줄여 줄 자료" 를 골랐습니다.
- 사실 칸(라이선스·접근·문서에 적힌 조건)은 공식 저장소·문서·API 로 확인한 것입니다. 설치·실행은 하지 않았습니다(실행 미확인). 현장 장비 호환성도 미확인입니다.
- "다음 행동"·"확인할 결과" 는 에이전트의 제안입니다(해석).
- 써 보셨으면 이 메일에 "써봄 / 안 씀 / 못 씀(이유)" 로 짧게 답해 주세요. 답이 없으면 '미확인' 으로 둡니다.
- 비전·로봇 과업은 초안입니다 — 그 프로필을 받는 분이 맞는지 확인 전입니다.

■ AI 에이전트 (과업 확정)

▸ [실습] GoRules ZEN — 결정표(JSON 규칙)를 Python 에서 불러 실행하는 규칙 엔진
  과업 AG-01 결정표 엔진 구현 · 병목: JSON 규칙 작성·호출 방식 선정
  자료: github.com/gorules/zen (MIT) · Python 사용법은 bindings/python/README.md
  사실: `pip install zen-engine` → `zen.ZenEngine()` → `decision.evaluate({...})` 예제가 문서에 있음 · 실행 미확인 · GPU·외부 데이터 필요 여부는 문서에서 확인하지 않음
  다음 행동(해석): 다섯 상황 결정표 중 하나를 ZEN 의 JSON 결정 형식으로 옮겨, 같은 입력에 지금 구현과 같은 판단이 나오는지 비교
  확인할 결과(해석): 규칙 표현이 우리 상황 정의를 다 담는가, 규칙 변경 시 코드 수정이 줄어드는가

▸ [참고·실습] Python eventsourcing — 이벤트 저장·상태 재구성 라이브러리
  과업 AG-02 판단·요청·응답 이벤트 원장과 재생 · 병목: 이벤트 순서·기한 만료·버전 재현
  자료: github.com/pyeventsourcing/eventsourcing (BSD-3-Clause) · 문서 eventsourcing.readthedocs.io
  사실: `pip install eventsourcing`, SQLite 로 실행하는 예제(README), PostgreSQL 모듈도 패키지에 있음 · 실행 미확인
  다음 행동(해석): 도입이 아니라 비교용 — 이 라이브러리의 이벤트 저장·재구성 방식과 자체 SQLite 원장 설계를 나란히 놓고, 순서·버전 처리에서 빠진 것이 없는지 점검
  확인할 결과(해석): 자체 원장에 추가할 규칙(순서 번호·스냅숏·버전 필드 등)이 나오는가

▸ [배경·평가] IBM AssetOpsBench — 산업 자산 운영·정비 에이전트 평가 틀
  과업 AG-05 원인 가설·추가 확인 항목 평가 · 병목: 원인 정답·평가 기준 부족
  자료: github.com/IBM/AssetOpsBench (Apache-2.0) · 논문 arXiv 2506.03828 · 데이터 huggingface.co/datasets/ibm-research/AssetOpsBench (공개)
  사실: README 기준 시나리오 "141+", 도메인 에이전트 5개 + 공용 유틸리티 서버 1개, KDD 2026 채택(README 표기) · **README 의 시나리오 실행 명령에 "(to be enabled)" 표시 — 벤치마크 실행 경로는 미확인**
        · README 가 연결한 Colab 은 벤치마크 실행이 아니라 LangChain·Watsonx.ai 일반 에이전트 예제(Watsonx 자격 증명 필요)
  다음 행동(해석): 시나리오와 평가 기준이 어떻게 짜였는지 읽고, 원인 가설·추가 확인 항목을 평가할 우리 시나리오 형식의 본보기로 삼을지 판단
  확인할 결과(해석): 우리 고장 시나리오를 같은 형식(질문·정답·평가 기준)으로 적을 수 있는가

  (AG-03 결측·지연 입력의 판단 결합: 이번 검색·확인 범위에서 실습 기준을 충족한 자료를 찾지 못해, 배경 읽기 목록으로 다룹니다 — 배경 논문 후보 3편은 다음 회차에 서지 확인 뒤 싣습니다.)
  (AG-04 구조화 출력: vLLM Structured Outputs 문서(docs.vllm.ai)가 후보로 확인됐습니다 — 후속 과업이라 다음 회차에 다룹니다.)

■ 비전 검사 (과업 초안)

▸ [실습] Anomalib — 이상탐지 모델 학습·추론 라이브러리
  과업 VI-01 공개 영상 이상탐지 기준선 · 병목: 전처리·라벨 구조·평가 코드
  자료: github.com/open-edge-platform/anomalib (Apache-2.0) · 시작 문서 "Anomalib in 15 Minutes"(anomalib.readthedocs.io)
  사실: 문서에 설치 → 학습(fit) → 추론(predict) 흐름 · 실행 미확인
  다음 행동(해석): 공개 데이터 하나로 문서 흐름을 그대로 돌려 점수·평가 출력 형식을 확인
  확인할 결과(해석): 우리 영상 폴더 구조·라벨을 이 형식으로 바꾸는 데 필요한 일

▸ [실습] CSEM-MISD — 조명 방향을 바꿔 찍은 금속 부품 결함 데이터셋
  과업 VI-02 조명 후보별 영상 품질 평가, VI-03 제한 예산 탐색 · 병목: 지표 선택, 같은 예산에서 공정 비교
  자료: Zenodo 10.5281/zenodo.7410513 (v0.2.0, CSEM·EPFL)
  사실: **108개의 서로 다른 조명 각도**(같은 고도에 12개씩, 이웃 방위각 설계 간격 30도), 시료당 512×512 8비트 회색조 108장 + 결함 마스크 · 공개 접근 · 약 14.2 GB(파일 3개)
        · **라이선스 CC BY-NC-ND 4.0(비상업·변경 금지) — 과제 활용 전 사용 조건 확인 필요**
        · 조명 방향 벡터와 밝기 값(0~127 척도) 파일을 함께 제공 — 밝기 값의 실제 분포·파장 조건은 미확인. 방위각은 보정되지 않아 몇 도 어긋날 수 있음(원문 주의)
        · 정상 시료는 기어·나사 묶음에만(각 35) — 오경보 확인은 이 두 묶음으로, 와셔는 결함 시료만
  다음 행동(해석): 같은 부품의 108개 조명 영상에서 대비·포화 지표를 계산해 결함 마스크 영역이 잘 보이는 조명을 고르는 실험 — 격자 탐색과 베이지안 최적화를 같은 "촬영 횟수" 예산으로 비교하는 가상 장비로 쓸 수 있는지
  확인할 결과(해석): 지표가 결함 가시성과 맞는가, 108개 중 몇 번의 평가로 좋은 조명에 닿는가

▸ [실습] BoTorch 닫힌 고리(closed-loop) 튜토리얼 — 후보 선택·평가·갱신 반복
  과업 VI-03 제한된 평가 예산의 탐색(격자·베이지안 최적화) · 병목: 후보 선택·종료 조건·공정 비교
  자료: botorch.org/docs/tutorials/closed_loop_botorch_only · 저장소 github.com/meta-pytorch/botorch (MIT)
  사실: 튜토리얼에 SingleTaskGP·qLogNoisyExpectedImprovement 로 반복 최적화하는 코드 · 실행 미확인
  다음 행동(해석): 튜토리얼의 목적함수 자리에 위 조명 데이터의 지표 계산을 넣어 같은 예산의 격자 탐색과 비교
  확인할 결과(해석): 같은 평가 횟수에서 격자 대비 얼마나 빨리 좋은 조명에 닿는가

  (참고 LIBAD — 전지 전극 다중모달 이상탐지: 데이터 huggingface.co/datasets/Evenrose/LIBAD 는 HF 로그인 후 동의하면 자동 승인(cc-by-4.0), 코드 github.com/evenrose/LIBAD(BSD-3-Clause).)

■ 로봇·피지컬 AI (과업 초안)

▸ [실습] LeRobot — 데이터 수집·학습·평가를 잇는 로봇 학습 도구
  과업 RB-01 공개 파지 예제 이해, RB-02 모델·데이터 관계 · 병목: 실행 환경·구성 요소, 호환성
  자료: github.com/huggingface/lerobot (Apache-2.0) · 문서 huggingface.co/docs/lerobot
  사실: 설치 문서에 시뮬레이션 환경(gym-aloha, gym-pusht) 설치 경로 · Linux 에서 uv 로 소스 설치하면 기본 CUDA 12.8 PyTorch(다른 CUDA 로 바꾸는 방법 안내, macOS·Windows 는 기본 설치) · 실행 미확인 · 실물 로봇 없이 시작하는 경로 = 시뮬레이션
  다음 행동(해석): 실물 로봇 없이 시뮬레이션 환경 하나로 설치 → 공개 데이터셋 확인 → 학습·평가 명령까지 따라가 보기
  확인할 결과(해석): 우리 GPU·운영체제에서 설치가 되는가, 데이터 형식이 어떻게 생겼는가

▸ [실습] SmolVLA — LeRobot 의 공개 VLA 기초 모델과 파인튜닝 안내
  과업 RB-02 모델·데이터 관계, RB-03 파인튜닝 절차 · 병목: 데이터 형식·학습 비용
  자료: 문서 huggingface.co/docs/lerobot/smolvla · 모델 huggingface.co/lerobot/smolvla_base (apache-2.0, 공개, 가중치 파일 있음) · 논문 arXiv 2506.01844
  사실: 문서 원문 "Training the model for 20k steps will roughly take ~4 hrs on a single A100 GPU", GPU 가 없으면 (Colab) 노트북으로 학습하는 경로 안내 · 참고 데이터셋 SVLA SO100 PickPlace(에피소드 50, 큐브 위치 5)
  다음 행동(해석): 참고 데이터셋 구조를 보고, 우리 파지 시연을 같은 형식으로 모으려면 무엇이 필요한지 정리
  확인할 결과(해석): 우리 로봇 관측·행동 형식이 이 데이터 형식과 맞는가(장비 호환성 미확인)

▸ [배경·실습] robomimic — 모방학습 연구 틀과 재현 절차
  과업 RB-03 모방학습 절차, RB-04 평가 방법 · 병목: 학습 비용·평가 기준
  자료: github.com/ARISE-Initiative/robomimic (MIT) · 문서 robomimic.github.io/docs (Getting Started)
  사실: 예제는 robosuite v1.5.1 이 필요하지만 rollout 을 끄면 robosuite 없이 실행 가능(문서 원문) · 실행 미확인
  다음 행동(해석): 행동 복제(BC) 예제로 학습·평가 로그 구조를 보고, 우리 정책 평가표(성공률·실패 유형)의 본보기로 삼을지 판단
  확인할 결과(해석): 평가 지표와 실험 기록 방식 중 가져올 것
```
