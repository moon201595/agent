# 연구 참고 자료 — 2주차 수동 시험: 지난 4주 카드에서 자료 이름 발견 (2026-10-25)

계획 `docs/PLAN_research_assets_2026-10-24.md` v5-5. **코드 변경 없음** — 임시 스크립트(`scratchpad/pilot/w2_extract.py` 요약 칸 추출, `w2_fulltext.py` 원문 절 대조)와 공개 API·arXiv·프로젝트 페이지 조회.
분담: Claude = 추출·정식 확인·적합 판단·경로 비교 · Codex = 관계·이름 독립 채점(표본 50건, 원문 문장 대조) · 보고서 독립 검토(수치 재현·결론 강도, 지적 8건 — 전부 근거 확인 뒤 반영).

## 대상
team_robot·team_agent 의 9/28~10/23 발송 18회차, 카드 각 90편. ④ 요약이 있는 카드 로봇 75·에이전트 68편(나머지는 초록만 처리된 카드 — 원문이 저장돼 있지 않아 이번엔 추출 안 함).
추출 칸: ④ 요약의 `실험 설정·데이터셋`(→ dataset: 평가 또는 사용), `실험 설정·비교 대상`(→ compared_with), `방법 상세·모델 규모·백본`(→ used), `연관 연구`(→ cites),
code_ladder official(→ implements, 본문 링크일 뿐). 한 논문 안 반복은 한 번으로 셌다.
보강 대조: 같은 카드의 **원문 본문**(참고문헌 절 앞까지)에서 **미리 정한** 자료 이름 30개가 어느 절에 나오는지 셌다 — 새 이름을 원문에서 뽑는 시험이 아니라 출현 위치 대조다.
절 판정은 앞쪽 가장 가까운 절 제목으로 가른 휴리스틱이고 정밀도는 재지 않았다(LeRobot 4편만 문장을 직접 읽어 확인).

## 결과표 (사용자 지정 지표)
| 지표 | team_robot | team_agent | 메모 |
|---|---|---|---|
| 발견율 — 이름 후보를 하나 이상 뽑은 카드 | 75/90 (83%) | 68/90 (76%) | 요약 있는 카드 전부. **유효한 추천 자료가 나온 카드 비율은 아니다**(이름 후보·구현 URL 기준). 초록만 처리된 15·22편은 0 |
| 관계 정확도(표본 50, Codex 채점) | 전체 45/50 (90%) — dataset 16/16, used 12/12, compared_with 10/12, cites 7/10 | | dataset 칸의 실제 관계는 평가 12·사용 4. 연관 연구 칸에 실제 비교군·사용 자료가 섞임(cites 로 축소). implements 는 표본 없음 |
| 이름 정확도(같은 표본) | 42/50 (84%) | | 오류: 조사로 두 이름이 합쳐짐, 도메인 단어("Business"), 실험 조건 기호("B3"·"No Memory"·"Vanilla Base"), 저자-연도만 |
| 정식 확인율(상위 후보 8개) | 5개 중 직접 양방향 3(LIBERO-Plus·RoboTwin 2.0·GR00T), 프로젝트 페이지 경유 2(DROID·RoboMME) | 3개 중 직접 양방향 2(AgentDojo·ScienceWorld), 프로젝트 페이지 경유 1(ALFWorld) | 직접 양방향 = README 가 논문을, 논문(초록 페이지 또는 HTML 본문)이 저장소를 가리킴. 경유 = 논문 → 저자 프로젝트 페이지 → 저장소 + README → 논문 |
| 추천 적합률 — **Claude 의 수동 판정** | 5 중 5 | 3 중 1(AgentDojo) | 신원 확인된 후보 8개 대상. 사용자·팀원 평가는 미확인. 근거는 아래 후보표 |
| 고유 발견 | 1주차 웹 원결과에 **없던** 것: RoboMME · 원결과에는 있었으나 선정 안 된 것: LIBERO-Plus(GitHub 4위·S2 6위)·RoboTwin 2.0(S2 13위)·GR00T(N1 논문 S2 3위 — N1.5/N1.7 자체는 아님)·DROID(S2 20위) · 웹 선정 이름 5개(LeRobot·openpi·SmolVLA·LIBERO·robomimic)는 **모두 카드 원문에도 있다** — 웹이 더한 것은 설치·튜토리얼 문서 페이지 | 1주차 웹 원결과에 없던 것: AgentDojo·ScienceWorld·ALFWorld · 웹에서만: ZEN·eventsourcing(이번 에이전트 저장 원문 68편에서 식별 안 됨) · vLLM 은 카드 원문 4편이 서빙 엔진으로 사용 — 웹이 더한 것은 구조화 출력 기능 문서 · 둘 다: AssetOpsBench·LangGraph | 웹 = 1주차 선정 11건 + 2회차 9건, 원결과는 `search_test.json`·`search_supp.json`. **경로 고유 발견과 선정 차이를 구분한다** — 로봇 4개는 웹에도 떴지만 사용 근거 없이 묻혀 있었고, 카드 경로가 사용 편수로 끌어올렸다 |
| 신규성 | 상위 후보 8개 모두 **이전 참고자료 선정 항목**(시범 1·2회차)에 없던 자료 | | 일일 메일 본문에서 처음 언급됐는지는 따로 보지 않았다. LIBERO·openpi·SmolVLA·LeRobot 은 시범에서 이미 소개 |
| 자료 다양성 | 요약 칸 = 벤치마크·모델 위주, 도구는 소수(OpenPI 비교 1·robomimic 데이터셋 1·JAX/Isaac Sim 1) · 원문에는 LeRobot·openpi 코드베이스 사용이 더 있다 | 요약 칸 = 벤치마크 + 바탕 LLM · 원문에는 vLLM·LangGraph·MCP 사용도 있다 | 유형별 개수·비율은 세지 않았다(미확인) |
| 추천 범위 밖 후보(요약 칸 상위 30) | 13개 — 부품·백본 9(DiT·PaLI-Gemma·Qwen3-VL·ResNet-18·SigLIP·DINOv2·Gemma-300M·U-Net·DINOv3), 장비 2(AgiBot G1·Astribot-S1), 주행 데이터 2(Bench2Drive·DriveLM) · 별도로 LIBERO 하위 스위트 4개는 LIBERO 로 합쳐야 함 | 26개 — 바탕 LLM 25(Qwen·GPT·DeepSeek·Kimi·Llama·Claude·Gemma…), 방법 1(ReAct) · 남은 4개가 벤치마크(AgentDojo·ALFWorld·ScienceWorld·GAIA) | 상위 30 = 사용·데이터셋·구현 관계 편수 → 전체 관계 편수 → 이름 순(`w2_extract.py`). 표본 밖 오류(조건 기호·도메인 단어·저자-연도)는 채점 표본에서 확인 |
| 추출 누락 사례(잘못 탈락 후보) | **LeRobot — 원문에서 구현 기반 사용 3편 + 비교 기준 구현으로 실행 1편, 요약 칸 0편** · openpi 원문 5 vs 요약 칸 3 · GR00T 21 vs 13 · LIBERO 32 vs 25 · RLBench·ManiSkill 0 | MCP 원문 10 vs 요약 칸 2 · WebArena 4 vs 1 · LangGraph 3 vs 1 · WebShop 3 vs 1 | **잘못 탈락률은 미확인** — 정상 후보 정답 목록과 선정·탈락 기록이 없다. 위는 미리 정한 이름 30개의 추출 누락 사례다(LeRobot 외 원문 수치는 절 휴리스틱, 미검증). 표기 변형으로 편수가 갈린 것도 있다(RoboTwin2·"RoboTwin 2.0 (Chen et al., 2025)"·"LIBERO와 LIBERO-Plus"). 비교로만 나온 자료(OpenVLA-OFT 비교 6편)는 사용·평가 편수를 자격 조건으로 쓰면 탈락한다 |

### 사용 vs 인용 — 근거 강도가 실제로 갈린다
| 자료 | 평가·사용(편) | 비교(편) | 인용(편) | 해석 |
|---|---|---|---|---|
| LIBERO | 18 | — | 7 | 반복 평가 벤치마크 |
| LIBERO-Plus | 8 | — | 1 | 같은 과제에 외란 7종 — 강건성 평가 |
| π0.5 | 12 | 19 | — | 바탕 모델로도, 비교 기준으로도 |
| OpenVLA | 2 | 11 | 8 | 주로 비교·인용 — "지금 쓰는 구현" 근거는 약함 |
| RoboTwin 2.0 | 6 | — | 1 | 평가 벤치마크·데이터 생성기 |
| GR00T N1.5/N1.7 | 3/5 | 2/2 | 1/— | 바탕 모델 |
| AgentDojo | 5 | — | 3 | 프롬프트 주입 공격·방어 평가 |
(편수는 요약 추출 칸 기준 — 관계 정확도 90% 표본 수준의 오차가 있고, 표기 변형이 합쳐지지 않아 실제보다 적게 잡힌 것이 있다.)

## 후보표 (상위, 바탕 LLM·백본·장비 제외)
| 후보 | 근거(편) | 정식 확인 | 라이선스·제약 | 적합 판단(Claude) |
|---|---|---|---|---|
| LIBERO-Plus | 평가 8 | 직접 양방향 — README "official implementation … 'In-depth Robustness Analysis for VLA'"(arXiv 2510.13626) · 논문 HTML 이 github.com/sylvestf/LIBERO-plus | 저장소 라이선스 표기 없음 | 적합 — 파지 실험의 강건성(외란) 평가 관점 |
| RoboTwin 2.0 | 평가 6 | 직접 양방향 — README·arXiv 2506.18088 초록 페이지 | MIT | 적합 — 도메인 무작위화 데이터 생성·양팔 벤치마크 |
| GR00T N1.5/N1.7 (NVIDIA) | 사용 5~7 | 직접 양방향 — **원 논문은 N1**(2503.14734 HTML) ↔ NVIDIA/Isaac-GR00T · N1.5/N1.7 제공은 현재 README 가 근거 | 코드 Apache-2.0(가중치 조건 미확인) · README: N1.7 은 LeRobot 의 groot 정책으로도 제공 | 적합 — 공개 바탕 모델 |
| DROID | 평가·사용 5 | 경유 — 논문 → droid-dataset.github.io → github.com/droid-dataset/droid(플랫폼 코드)·droid_policy_learning · README → arXiv 2403.12945 | **데이터 CC-BY 4.0**(논문 본문: "We open-source the full DROID dataset under CC-BY 4.0 license") · 플랫폼 코드 라이선스 표기 없음 · 실제 다운로드 조건 미확인 | 적합 — 실환경 조작 데이터(openpi 의 π0.5-DROID 체크포인트가 카드에서 쓰임) |
| RoboMME | 평가 4 | 경유 — 카드 참고문헌에서 arXiv 2603.04639 ("RoboMME: Benchmarking and Understanding Memory for Robotic Generalist Policies") → 초록 페이지 → robomme.github.io → github.com/RoboMME/robomme_benchmark · README → 같은 논문 | Apache-2.0(벤치마크·정책 학습 저장소) · 데이터 HF `Yinpei/robomme_data_h5` apache-2.0, 로그인 불필요 · arXiv 주석 "Accepted to ICML 2026" | 적합 — 기억 의존 조작 과제 16개, π0.5 기반. 지난 4주 카드 4편이 이 벤치마크로 평가 |
| AgentDojo | 평가 5 | 직접 양방향 — README ↔ arXiv 2406.13352 초록 페이지 | MIT | 적합 — 도구 쓰는 에이전트의 공격·방어 평가(허용 행동 검사와 접점) |
| ScienceWorld | 평가 4 | 직접 양방향 — README ↔ 논문 HTML | Apache-2.0 | 약함 — 초등 과학 과제 텍스트 환경 |
| ALFWorld | 평가 4 | 경유 — 초록 페이지 → alfworld.github.io → github.com/alfworld/alfworld · README → arXiv 2010.03768 | MIT | 약함 |

## 자동화에 주는 교훈 (11/16 근거)
1. **요약 칸 추출은 된다** — 요약 있는 카드 전부에서 이름 후보가 나오고, 표본 관계 정확도 90%. 다만 이름 정제가 필요하다(조사 분리, 조건 기호·도메인 단어·저자-연도 거르기, 다중 근거 태그 파싱).
2. **"연관 연구" 칸을 일괄 cites 로 두면 실제 비교·사용을 잃는다** — 실험 문장의 역할을 따로 봐야 한다(표본 10 중 3).
3. **편수 순위를 그대로 쓰면 상위가 부품·바탕 모델로 찬다**(상위 30 중 로봇 13, 에이전트 26) — 자료 유형(벤치마크·데이터·모델·도구·부품·장비)으로 나눈 뒤 순위를 매긴다. 유형만으로 일괄 제외하지 않고 관련성·추가 정보 가치를 따로 본다.
   "팀이 이미 아는 바탕 모델"인지는 이번에 재지 않았다.
4. **카드 본문에서 이름 근처 링크를 그 자료의 공식 링크로 쓰면 틀린다** — 근처 URL 은 대개 그 논문 자신의 코드(LIBERO-Plus 근처에 MambaAct, RoboTwin 근처에 RARA). GR00T 처럼 맞는 경우도 있다.
5. **정식 확인은 양방향으로 가능하고, 프로젝트 페이지 한 단계를 따라가야 한다** — 8개 중 직접 양방향 5, 프로젝트 페이지 경유 3(DROID·ALFWorld·RoboMME). 초록 페이지만 보면 직접 연결은 2개뿐이다(HTML 본문 필요).
6. **이름 → 저장소 연결 단계가 필요하다** — LIBERO-Plus 저장소는 Claude 가 이름으로 **추측한 주소**가 맞았던 것이다. RoboMME 처럼 카드의 참고문헌에서 그 자료의 논문 ID 를 찾고 → 논문 페이지 → (프로젝트 페이지) → 저장소로 가는 길이 재현 가능한 방법이다.
7. **④ 요약 칸만으로는 도구·코드베이스 사용을 놓칠 수 있다** — LeRobot 은 원문에서 구현 기반 사용 3편("π0.5 publicly released via LeRobot", "using the LeRobot implementation", "built on LeRobot")과
   비교 기준 구현 실행 1편이 있는데 요약 칸에는 0편이다. 요약 칸에서도 일부 도구는 나왔으므로(OpenPI·robomimic 등) "구현 도구 전용 칸이 없다"는 원인 중 하나일 가능성이다.
   → **다음 시험에서 원문 방법·실험 절(참고문헌 분리)을 주력 입력 후보로 평가한다** — 새 이름 추출 정확도·비용·추천 품질은 아직 재지 않았다.
8. **두 경로의 몫** — 로봇: 웹이 선정한 이름은 모두 카드 원문에 있었다(웹이 더한 것은 설치·튜토리얼 문서). 반대로 카드 후보 4개는 웹 원결과에도 떴지만 사용 근거 없이 묻혀 있었다.
   에이전트: 카드 원문 68편에서 식별되지 않은 엔지니어링 도구(ZEN·eventsourcing)는 웹(과업 검색)이 있어야 찾는다.
9. **근거 강도는 자격 조건이 아니라 우선순위다** — 사용·평가를 필수로 두면 비교로만 6편에 나온 OpenVLA-OFT 같은 비교 기준 후보가 사라진다(그 후보의 최종 적합성까지 확인한 것은 아니다).
10. 놓침: 초록만 처리된 카드(37편)는 원문이 저장돼 있지 않다(로봇 15·에이전트 22). 초록의 데이터셋·벤치마크 이름 추출은 다음에 시험.

## 검증하지 못한 것
- 추출 전체의 정확도(채점은 표본 50건), implements 관계(표본 없음). Codex 보고서 검토는 채점 결과를 재집계하고 근거 발췌 50개가 원문에 있는지 확인했지만 50건을 새로 채점하지는 않았다.
- 원문 절 대조의 정밀도 — 절 판정 휴리스틱이라 "방법·실험 절" 언급이 곧 사용이라는 뜻이 아니다(예: MCP 를 주제로 다룬 논문, 실험 절의 비교 서술). 문장을 읽어 확인한 것은 LeRobot 4편뿐.
- 잘못 탈락률·유형별 다양성 비율·사용자/팀원의 적합성 평가.
- 후보의 가중치 조건(GR00T), DROID 데이터의 실제 다운로드 조건, LIBERO-Plus·DROID 플랫폼 코드의 라이선스.
- 과업 검색 경로(보조)와 팀 등록 경로는 이번 시험 범위 밖.
