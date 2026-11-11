# '중립 요약'의 정의와 검증 방법

## 정의
이 프로젝트에서 중립은 "모든 입장을 같은 분량으로 쓴다"가 아니라 **세 층을 분리해서 쓰는 것**이다.

| 층 | 규칙 | 위반 예 |
| --- | --- | --- |
| ① 사실 층 | 검증 가능한 진술만 단정한다. 단정하려면 수집한 기사 과반이 뒷받침해야 한다. | 1~2곳만 쓴 내용을 "모두가 동의"로 서술 |
| ② 쟁점 층 | 언론사마다 갈리는 지점은 **누가 그렇게 말했는지** 출처를 붙여 병기하고, 어느 쪽이 옳은지 판정하지 않는다. | "정부의 일방적 결정", "당연한 조치" |
| ③ 표현 층 | 평가·감정·낙인 어휘를 쓰지 않는다. | "강행", "졸속", "논란의 중심" |

하지 않는 것: 기사의 진위 판정, 언론사 정치 성향 라벨링, 양쪽 주장의 기계적 균형(거짓 균형).

## 논문 근거
- **어휘 편향 vs 정보 편향**: Lei & Huang, ACL 2025, *Multi-document Summarization through Multi-document Event Relation Graph Reasoning in LLMs: a case study in Framing Bias Mitigation* (arXiv 2506.12978). 중립화는 표현(lexical)과 정보 선택(informational) 두 차원에서 평가 → 루브릭 ③, ①②.
- **요약문 자체의 프레이밍 평가**: *Frame In, Frame Out: Measuring Framing Bias in LLM-Generated News Summaries* (arXiv 2505.05406). 평가를 생성과 분리.
- **관점을 하나로 뭉개는 문제**: AutoJourn (arXiv 2607.18983; 검색 결과의 2차 요약만 확인, 원문 미확인). 쟁점 층에서 입장별 출처 병기.
- **원자적 사실 단위 검증**: Wei et al., *Long-form factuality in large language models* (SAFE, arXiv 2403.18802). 응답을 독립 사실로 분해해 각각 근거로 검증 → `check_bias`의 사실 근거 검증.

## 검증 구현 (`nodes/check_bias.py`)
1. **사실 근거 검증(SAFE 변형)**: 요약을 원자적 주장으로 분해 → 각 주장을 수집 기사 본문에 대해 supported / unsupported / contradicted로 판정, 근거 문장과 뒷받침 언론사를 기록. 근거 문장이 실제 본문에 없으면 supported를 인정하지 않음(코드 검사). 단정형 주장이 과반 미만이면 위반.
   - 한계: **폐쇄 세계 검증**이다. 수집한 기사끼리 서로 맞다는 것이지 외부 사실과 맞다는 증명이 아니다. 외부 검색 검증(SAFE 원형)은 확장 과제.
2. **루브릭 편향 검사**: 어휘 / 출처 병기 / 정보 선택 편중을 항목별 통과·미통과로 채점.
3. 둘 중 하나라도 미통과면 지적 사항을 넣어 재작성(최대 2회).

## 본문이 막힌 기사
유료벽은 우회하지 않는다. 본문 길이로 `full / partial / snippet`을 기록하고, `snippet` 기사는 제목·요약만 쓰며 '빠뜨림' 주장과 어조 판단에서 제외한다.
