# 주간 연구동향 심화 해설 — 시범 2회차 초안 (2026-10-24 작성)

계획 `docs/PLAN_research_assets_2026-10-24.md` v4. **코드 변경 없음.** 흐름은 `trend_chains` 가 저장된 일일 서술의 연결로 센 사슬(10/8~10/22)이고,
선행연구는 흐름 논문들이 **함께 인용한** S2 참고문헌(2편 이상)에서 골랐다. 초록·저장소 값은 2026-10-24 18:4x~19:0x 에 조회했다.
과업 목록(v3-2)은 흐름을 고를 때만 참고했고 메일에는 나오지 않는다. 숫자는 모두 해당 논문 초록의 **저자 보고값**이고, 서로 다른 설정이라 직접 비교하지 않는다.

## 재료와 근거
| 프로필 | 흐름(사슬) | 흐름 논문 | 초록 확인 | 공통 참고문헌(2편 이상) |
|---|---|---|---|---|
| team_robot | 미래 변화량을 통한 행동 생성 (5일 11편, 10/08~10/21) | 11 | 6 (나머지 5편은 제목만 메일에 실림) | 25+ — OpenVLA 6편, Diffusion Policy·π0·ACT·π0.5·WorldVLA 5편, RT-2·LIBERO·OpenVLA-OFT·CoT-VLA·GR-1 4편 |
| team_agent | 장기 실행의 워크플로 조정 (3일 5편, 10/08~10/13) | 5 | 3 (2편 제목만) | 9 — AutoGen 3편, Reflexion·MetaGPT·GAIA·AgentVerse·AFlow·AutoAgents·G-Designer·ScoreFlow 2편 |
| team_vision | 이상 합성의 생성 문맥 구성 (3일 5편) → **싣지 않음** | 5 | 2 (3편 제목만 — S2 제목 조회로 2편 찾음) | **0** — 4편의 참고문헌에 공통 항목 없음(서로 다른 분야). 대체 흐름 "계산 제약을 반영한 검사 구조"(4일 8편)는 S2 429 로 조회 미완 → 3회차 |

## 메일 본문 (Codex 검토 7건 수정 → 재검토 3건 수정 뒤 **2026-10-24 19:29 시험 수신 주소 한 곳 발송**, 반응 버튼 없음)

제목: [주간 연구동향 심화 · 시범 2회차] 이 흐름은 어디서 왔고 어디로 가나 (2026-10-24)

```
이 메일은 시범입니다. 매일 오는 브리핑이 "무엇이 새로 나왔나" 라면, 이 메일은 지난 2~3주 브리핑에 이어서 나온 흐름 하나를 골라
"원래는 어떻게 했고, 무엇이 부족했고, 새 연구들이 무엇을 바꿨고, 아직 무엇이 남았나" 를 이어서 설명합니다.
- 숫자는 각 논문이 스스로 보고한 값입니다(논문마다 실험 조건이 달라 서로 비교할 수 없습니다).
- "(해석)" 표시는 에이전트의 해석입니다. 선행연구는 이 흐름의 논문들이 함께 인용한 것에서 골랐습니다.

■ 로봇·피지컬 AI — VLA 가 "다음에 무엇이 바뀔지" 를 배우기 시작했다
   (10/8~10/21 브리핑에 5일에 걸쳐 실린 11편)

원래는 — 지금 보이는 것에서 지금 할 동작으로
   VLA(카메라 영상과 말로 된 지시를 받아 로봇 동작을 내는 모델)의 출발점은 RT-2 입니다. 인터넷 규모로 학습한 시각·언어 모델을 로봇 궤적과 함께 다시 학습시키면서,
   로봇 동작을 글자 토큰처럼 적어 같은 모델이 말과 동작을 함께 내게 했습니다. OpenVLA 는 이를 공개 모델로 내놓았고(7B, 실제 로봇 시연 97만 건으로 학습),
   이후 연구들이 파인튜닝하는 출발점이 됐습니다. π0 는 사전학습된 시각·언어 모델 위에 동작을 연속 값으로 생성하는 flow matching 구조를 얹었습니다.
   동작을 만드는 방식에서는 Diffusion Policy(동작을 확산 과정으로 생성)와 ACT(동작 여러 단계를 묶어 한 번에 예측 — action chunking)가 기초로 쓰입니다.
   이번 흐름의 논문 11편 중 6편이 OpenVLA 를, 5편이 Diffusion Policy·π0·ACT 를 인용했습니다.

무엇이 부족했나
   이 모델들은 "지금 영상 → 지금 동작" 을 바로 배웁니다. 이번 흐름의 논문들은 같은 약점을 짚습니다.
   동작 정답만으로 학습하면 장면이 어떻게 바뀔지 예상하도록 명시적으로 유도하지 않는다(Evo-World), 시각·언어 모델 바탕에는 물리적 움직임에 대한 사전 지식이 부족하다(ACT³),
   미래 세계 상태에 대한 명시적 지도 없이 배운다(Forward Dynamics Models).

앞선 해법과 그 대가 — 미래 영상을 통째로 그리기
   그래서 먼저 나온 방법은 미래 장면을 그려 보게 하는 것이었습니다. GR-1 은 대규모 비디오 생성 사전학습 뒤 동작과 미래 영상을 함께 예측했고,
   CoT-VLA 는 동작 전에 목표가 될 미래 영상 프레임을 먼저 그립니다(시각적 생각의 사슬). WorldVLA 는 동작 모델과 미래 영상을 예측하는 세계 모델을 하나로 합쳤습니다.
   이번 흐름의 논문들은 이 방식의 대가를 이렇게 정리합니다. 미래 영상 전체를 만드는 것은 계산이 비싸고(PLaCA), 장면의 대부분인 움직이지 않는 겉모습까지 예측하느라
   동작과 관련된 움직임이 묻히며(Evo-World, VLDA), 실행할 때 예측 오류가 동작으로 번진다(Forward Dynamics Models).

이번 흐름 — 미래 정보를 동작 학습과 생성에 잇는 여러 방식
   · 10/13 Evo-World — 미래 영상 대신 광학 흐름(화면의 점들이 어디로 움직이는지)으로 미래 움직임만 예측하게 가르칩니다.
     Meta-World MT50 평균 91.1%(예측을 쓰지 않는 기준 대비 상대 13.0% 향상), 실물 AgileX 로봇에서 0.87B 파라미터로 비교 대상보다 나았다고 보고했습니다.
   · 10/13 Forward Dynamics Models — 미래 예측을 동작 특징에서만 출발하게 연결해(동작 → 미래, 반대 방향 없음), 학습 때는 실제 미래 상태가 동작 결정 모델의 학습을 이끌고
     실행 때는 미래 생성을 아예 건너뜁니다.
     쉽게 말해, 학습할 때만 "실제로 관측된 다음 장면" 을 정답으로 삼아 동작을 고르는 모델을 함께 가르치고, 로봇을 움직일 때는 그 예측 단계를 쓰지 않습니다. LIBERO 평균 99.4%, 실물 과제에서 기존 방식 대비 최대 13% 절대 향상을 보고했습니다.
   · 10/13 Beyond Actions — 동작 자체가 아니라 그 동작이 만들 "물리적 효과" 를 주된 예측 대상으로 삼습니다. DexJoCo 에서 π0.5 보다 11.82%p 높았다고 보고했습니다.
   · 10/14 ACT³ — 의미(시각·언어 모델)와 움직임(비디오 세계 모델) 표현을 동작 생성부가 각각 따로 참고하는 세 갈래 구조를 냈습니다.
   · 10/21 PLaCA — 화면을 작은 조각으로 나눠 조각마다 미래의 변화만 예측합니다. LIBERO-Long 79.0%(동작만 배우는 기준 대비 +5.0%p),
     2단계 학습에서 파라미터의 2.95% 만 갱신했다고 보고했습니다.
   · 10/21 VLDA — 미래 상태 전체 대신 작은 "델타(변화)" 변수를 이해와 동작 사이의 연결 고리로 씁니다.
   (같은 흐름에 제목으로만 실린 논문: Commit While Futures Agree, ViDAL, PLaW-VLA, What Matters for World Modeling in VLA Models?, ObjDream — 내용은 아직 확인하지 않았습니다.)

지금 어디에 와 있나 (해석)
   미래 정보를 쓰는 갈래가 나뉩니다. Forward Dynamics Models 는 미래 예측을 학습할 때의 감독으로만 쓰고
   실행에서는 뺍니다. PLaCA·VLDA 는 압축한 미래 변화(조각 단위 변화, 델타)를 실행 때도 동작 생성에 직접 넣습니다. Evo-World 는 광학 흐름 예측으로 학습을 돕고
   가벼운 모델(0.87B)로 실행 효율을 앞세웠습니다.
   평가 범위: 확인한 초록·공식 페이지 기준으로 시뮬레이션 벤치마크는 LIBERO·CALVIN·Meta-World·DexJoCo 등이고, 실물 로봇 실험은 Evo-World(AgileX),
   Forward Dynamics Models, ACT³, VLDA(공식 페이지: GALAXEA R1 Lite 장기 과제 4개 각 25회, 두 번째 플랫폼 AgileX Cobot Magic)에서 보고됐습니다.
   제목만 실린 5편의 실험 범위는 확인하지 못했습니다.
   광학 흐름·조각 단위 변화·효과·델타 중 어떤 "변화" 표현이 접촉이 많은 파지·조립에 맞는지는, 이번에 확인한 초록만으로는 같은 조건에서 비교한 결과를 찾지 못했습니다.
   (10/16 에 제목으로 실린 "What Matters for World Modeling in Vision-Language-Action Models?" 가 이런 비교를 다루는지는 확인 전입니다.)

함께 볼 연구·자료
   기초 논문   OpenVLA (arXiv 2406.09246, CoRL 2024) · π0 (2410.24164) · Diffusion Policy (2303.04137, RSS 2023)
               · ACT — Learning Fine-Grained Bimanual Manipulation with Low-Cost Hardware (2304.13705, RSS 2023) · GR-1 (2312.13139, ICLR) · CoT-VLA (2503.22020, CVPR 2025)
   공개 구현   OpenVLA github.com/openvla/openvla (MIT, 공식 체크포인트는 Hugging Face openvla 조직)
               · π0 계열 github.com/Physical-Intelligence/openpi (Apache-2.0) · Diffusion Policy github.com/real-stanford/diffusion_policy (MIT)
               · ACT github.com/tonyzhaozh/act (MIT) · GR-1 github.com/bytedance/GR-1 (Apache-2.0)
               · LeRobot github.com/huggingface/lerobot (Apache-2.0 — 공개 VLA 와 데이터를 한곳에서 다루는 도구)
   벤치마크    LIBERO github.com/Lifelong-Robot-Learning/LIBERO (MIT — 이번 흐름 논문 4편이 인용)
   이번 흐름 논문의 코드   VLDA 는 github.com/baidu-baige/LoongForge (Apache-2.0) 안에 구현했다고 밝혔습니다. 나머지 논문은 전용 공개 저장소를 확인하지 못했습니다.

■ AI 에이전트 — 질의별 절차 선택부터 실행 중 조정·세션 교대까지
   (10/8~10/13 브리핑에 3일에 걸쳐 실린 5편)

원래는 — 역할과 절차를 미리 짜 두기
   여러 LLM 에이전트가 함께 일하는 틀의 출발점은 AutoGen(대화하는 에이전트들로 응용을 짜는 공개 틀 — 이번 흐름 논문 3편이 인용)과
   MetaGPT(사람의 표준 작업 절차를 프롬프트에 넣어 역할을 나눔 — 에이전트를 그냥 이어 붙이면 잘못된 답이 연쇄되는 문제를 다룸)입니다.
   그다음에는 팀과 절차를 자동으로 만드는 연구가 나왔습니다. AutoAgents 는 과제 내용에 맞춰 필요한 전문 에이전트를 만들고, AgentVerse 는 팀 구성을 동적으로 조정하며,
   AFlow 는 워크플로를 코드로 표현해 여러 절차 후보를 탐색·평가하며 개선하고(몬테카를로 트리 탐색), G-Designer 는 과제에 맞는 에이전트 간 통신 구조를 그래프 신경망으로 설계했습니다.

무엇이 부족했나
   팀 구성을 동적으로 조정하는 연구(AgentVerse)도 있었지만, 이번 흐름의 논문들은 각자 다른 빈틈을 짚습니다.
   워크플로를 자동으로 최적화하려면 학습 단계에서 피드백을 모으는 데 API 비용이 크고, 좋은 워크플로를 다시 쓰지 못한다(CB-Orchestrator).
   하위 에이전트의 목표와 작업 틀을 만들 때 고정해 바뀌는 환경에 맞추지 못한다(Proactive Agentic Orchestration).
   한 세션이 길어질수록 추론의 강도와 진척이 함께 떨어지는 현상이 관찰되고, 맞춤 작업 틀의 효과는 바탕 모델이 좋아질수록 사라진다(Humanize MLE Agents).

이번 흐름 — 질의마다 고르고, 실행 도중 요청하고, 세션 사이에 교대한다
   · 10/12 CB-Orchestrator — 진화 탐색으로 후보 워크플로 묶음을 미리 만들고, 질의마다 어느 것을 쓸지 고르는 일을 문맥 밴딧(탐색과 활용을 저울질하는 선택) 문제로 풉니다.
     MATH 에서 학습 단계 API 토큰을 40.55% 이상 줄였다고 보고했습니다.
   · 10/13 Proactive Agentic Orchestration — 하위 에이전트가 실행 도중 언제든 조정자에게 작업 틀 조정을 요청할 수 있게 하고, 그 요청을 조정 정책 학습 신호로 씁니다.
     장기 과제 세 종류에서 가장 강한 비교 대상 대비 평균 상대 9.1% 향상을 보고했습니다.
   · 10/13 Humanize MLE Agents — 두 에이전트가 같은 작업 공간에서 세션을 번갈아 맡고, 세션마다 실험 수를 제한하며 세션 사이에 문맥을 지웁니다.
     MLE-bench 75개 Kaggle 과제에서 메달률 78.2%(공개 최강 비교 대상 +8.0점, 시간 예산 4분의 1)를 보고했습니다.
   (같은 흐름에 제목으로만 실린 논문: OOPMAS, DHCG — 내용은 아직 확인하지 않았습니다.)

지금 어디에 와 있나 (해석)
   세 논문은 결정을 바꾸는 시점이 서로 다릅니다. CB-Orchestrator 는 일을 시작할 때 질의마다 미리 만든 절차 중 하나를 고르고, Proactive Agentic Orchestration 은
   실행 도중 하위 에이전트의 요청으로 작업 틀을 바꾸며, Humanize MLE Agents 는 세션 사이에 담당 에이전트를 교대하고 문맥을 지웁니다.
   셋 다 질의·실행 상황·세션에 맞춰 작업 구성을 선택하거나 조정합니다.
   시험한 과제는 수학·연구 조사·디지털 비서·소프트웨어·Kaggle 이고, 물리 설비나 실시간 제약이 있는 환경은 다루지 않았습니다.
   실행 도중 바꾼 결정을 어떻게 기록하고 나중에 검증할지는 이 흐름의 초점이 아닙니다.

함께 볼 연구·자료
   기초 논문   AutoGen (arXiv 2308.08155) · MetaGPT (2308.00352) · AgentVerse (2308.10848, ICLR) · AFlow (2410.10762, ICLR)
               · G-Designer (2410.11782, ICML) · AutoAgents (2309.17288, IJCAI)
   공개 구현   AutoGen github.com/microsoft/autogen (코드 MIT — LICENSE-CODE, 문서 CC-BY-4.0) · MetaGPT github.com/FoundationAgents/MetaGPT (MIT)
               · AgentVerse github.com/OpenBMB/AgentVerse (Apache-2.0)
   벤치마크    GAIA (2311.12983 — 이번 흐름 논문 2편이 인용)
   이번 흐름 논문의 코드   Proactive Agentic Orchestration 은 github.com/XueyangFeng/POrchestra 에 코드를 공개했다고 밝혔습니다(라이선스 표기 없음).

■ 비전 검사 — 이번 회차는 싣지 않습니다
   지난 3주 브리핑에서 이어진 비전 흐름 중 "검사 자료 부족을 이상 합성으로 메우기" 는 적용 대상(의약품 정제·3D 프린팅·금속 부품)과 방법(생성 모델·생성 프롬프트 최적화)이
   다양했고, 참고문헌을 확보한 4편에서는 함께 인용한 문헌을 찾지 못했습니다. 나머지 1편은 조회되지 않아 이번 회차는 보류합니다. 다른 흐름(계산 제약 아래의 검사 구조)은 근거 조회가 끝나지 않아 다음 회차에 다룹니다.
```

## 검증 기록 (주장 → 근거)
- 기초 논문의 서술(RT-2 동작 토큰, OpenVLA 7B·97만 시연, π0 flow matching, Diffusion Policy, ACT action chunking, GR-1 동작+미래 영상, CoT-VLA 미래 프레임 먼저, WorldVLA 통합,
  AutoGen·MetaGPT SOP·AgentVerse 동적 구성·AFlow MCTS·G-Designer GNN·AutoAgents) — S2 batch 초록(18:5x) [API·Claude 요약].
- 흐름 논문의 진단·방법·숫자 — 운영 DB 의 초록 원문(`papers.abstract`) [Claude 요약, 숫자는 원문 그대로].
- 학회명은 S2 `venue` 값만(연도는 S2 `year` 와 같은 경우만 붙였다 — OpenVLA CoRL 2024·Diffusion Policy RSS 2023·ACT RSS 2023·CoT-VLA CVPR 2025).
- 인용 편수 — `round2_refs.json`(흐름 논문의 S2 참고문헌을 paperId 로 모아 센 것) [API·규칙].
- 저장소·라이선스 — GitHub API `license.spdx_id`(AutoGen 은 감지값 CC-BY-4.0 이지만 LICENSE-CODE 첫 줄 "MIT License" 확인), 이전된 이름(geekan/MetaGPT → FoundationAgents/MetaGPT) [API].
- WorldVLA 저장소는 alibaba-damo-academy/RynnVLA-002 로 이전·라이선스 표기 없음 → 링크 생략. OpenVLA-OFT 는 공통 인용 4편이지만 이번 해설에 넣지 않았다(분량).
- 제목만 실린 논문(로봇 5·에이전트 2)은 내용 주장을 하지 않았다.
- VLDA 실물 실험 — 공식 프로젝트 페이지 baidu-baige.github.io/LoongForge/projects/vlda.html(20시 확인: "Four long-horizon tasks on a GALAXEA R1 Lite, 25 trials each, plus a second platform, AgileX Cobot Magic").
- Codex 재검토(같은 스레드) 3건 — 로봇 소제목·공통점 단정, 에이전트 제목·공통점 단정, 비전 보류 사유의 범위 — Claude 가 제안 문구대로 고침(재검토 2회 상한, 새 사실 없음).
- Codex 2회차 검토(task-mvm90lk7-z1kzos) 7건 반영: 실행 시 예측 제거를 흐름 전체로 묶음 → 갈래 구분, 실물 실험 목록에 VLDA·"대부분" 삭제, 질의별 선택·실행 중 변경·세션 교대 구분, HMA 관찰을 원인으로 바꾼 것, Evo-World "명시적 유도 안 함", 비교 부재 단정 → 확인 범위 한정, FDM·AFlow 풀이.
- 별견: code_ladder 에 깨진 공식 URL — POrchestra `XueyangFeng/POrchestra.1IntroductionTo`, Humanize MLE `deepseek-ai/deepseek-harnessCited`(PDF 추출에서 글자가 붙음, official 로 기록). 운영 ⑦ 단계 결함 후보 — 별도 보고.
