# NewsPrism (뉴스프리즘)

> **같은 사건을 다룬 여러 언론사 기사를 LangGraph 멀티에이전트로 비교 분석하여 사실과 시각 차이를 투명하게 보여주는 서비스**

---

## 1. 개요

NewsPrism은 단일 사건에 대한 다양한 언론사의 보도를 수집하고 병렬로 심층 분석합니다.
단순한 기사 요약을 넘어 **"모두가 동의하는 사실"**과 **"언론사별 시각(프레임) 차이"**, 그리고 **"쟁점 및 질문"**을 분리해 제공함으로써 뉴스 소비자가 편향되지 않은 입체적 시각을 가질 수 있도록 돕습니다.

---

## 2. 핵심 기능

- **실시간 뉴스 검색 & 수집**: Google News RSS(기본) 및 SerpApi 연동을 통해 다양한 언론사 기사 자동 수집 및 본문 추출
- **기준 사건 동질성 판정 (`judge_topic`)**: 키워드가 같더라도 다른 사건이나 무관한 후속 기사를 필터링
- **언론사별 병렬 분석 (`analyze_article`)**: 주장, 강조 사실, 정보 출처, 어조를 언론사별로 병렬 분석
- **입체적 비교 (`compare_articles`)**: 과반 이상 언론사가 일치하는 공통 사실과 언론사 간 시각 차이 분류
- **엄격한 중립 요약 & 편향 검증 루프 (`check_bias`)**: 
  - 사실 층 / 쟁점 층 / 표현 층의 3단계 중립 원칙 적용
  - 원자적 사실 대조 및 루브릭 기반 편향 검증 미통과 시 자동 재작성(최대 2회) 피드백 루프 수행
- **반응형 웹 UI (FastAPI)**: 직관적인 검색, 토픽 탐색, 분석 상태 실시간 폴링, 반응형 리포트 UI 제공

---

## 3. 에이전트 파이프라인 (LangGraph)

```text
키워드 / 주요 기사
  └─ collect_articles      기사 수집 → 언론사 중복 제거(최대 10곳) → 본문 추출 → 기준 사건(Anchor) 도출
      └─ judge_topic × N   [병렬] 각 기사가 기준 사건과 동일한 사건인지 판정
          └─ filter_articles  유효 기사 필터링 (동일 사건 2건 미만 시 중단)
              └─ analyze_article × M   [병렬] 언론사별 핵심 주장 / 강조 사실 / 인용 출처 / 어조 분석
                  └─ compare_articles   공통 사실(과반) · 시각 차이 · 단독 보도 사실 · 균형 질문 도출
                      └─ write_summary  3층 원칙 기반 중립 요약 작성
                          └─ check_bias ──(피드백 / 재작성 루프 최대 2회)──┐
                              │ (통과)                                   │
                              ▼                                         │
                         build_report ◀─────────────────────────────────┘
```

---

## 4. '중립'의 정의 (3층 원칙)

| 층위 | 규칙 | 검증 기준 |
| :--- | :--- | :--- |
| **① 사실 층** | 검증 가능한 진술만 단정하며, 수집 기사 **과반**이 일치할 때만 공통 사실로 채택 | 특정 언론사 단독 주장을 전체 사실로 일반화 금지 |
| **② 쟁점 층** | 입장이 갈리는 지점은 **출처(언론사)를 명기**하며 중재자 입장에서 서술 | 특정 입장에 편향된 가치판단 배제 |
| **③ 표현 층** | 평가·감정·낙인성 어휘 철저 배제 | "강행", "졸속", "논란" 등의 유도성 수식어 지양 |

---

## 5. 기술 스택

- **Backend**: Python 3.10+, FastAPI, Uvicorn, Pydantic v2
- **Agent Orchestration**: LangGraph
- **LLM**: Google Gemini (Google Cloud Vertex AI, `gemini-3.8-flash`)
- **Scraping / Parsing**: Trafilatura, Feedparser, Requests
- **Frontend**: HTML5, Vanilla JavaScript, CSS3 (FastAPI Static 배포)
- **Dependency Management**: `uv` or `pip`

---

## 6. 설치 및 실행 방법

### 1) 사전 준비
- Python 3.10 이상
- Google Cloud 프로젝트 및 Vertex AI 접근 권한 (`gcloud auth application-default login`)

### 2) 저장소 클론 및 가상환경 설정
```bash
git clone https://github.com/YOUR_USERNAME/NewsPrism.git
cd NewsPrism

# uv를 사용하는 경우
uv sync

# 또는 venv + pip 사용하는 경우
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r pyproject.toml
```

### 3) 환경 변수 설정
`.env.example`을 복사하여 `.env`를 생성하고 환경에 맞게 수정합니다:
```bash
cp .env.example .env
```

`.env` 설정 예시:
```env
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GOOGLE_CLOUD_LOCATION=us-central1
GEMINI_MODEL=gemini-3.8-flash

# (선택) 더 풍부한 검색 결과를 원할 경우 SerpApi 키 입력 가능
# SERPAPI_API_KEY=your_serpapi_key_here
```

### 4) 서비스 실행
```bash
uvicorn app:app --reload --port 8000
```
실행 후 브라우저에서 `http://localhost:8000`에 접속합니다.

---

## 7. 디렉토리 구조

```text
NewsPrism/
├── app.py               # FastAPI 서버 및 API 엔드포인트
├── graph.py             # LangGraph 그래프 워크플로우 정의
├── state.py             # Pydantic 상태(State) 스키마 모델
├── llm.py               # Google GenAI / Vertex AI 호출 및 리트라이 클라이언트
├── prompts.py           # 각 노드별 프롬프트 템플릿
├── db.py                # 리포트 및 진행 상태 저장소 (SQLite)
├── categories.py        # 카테고리별 주요 토픽 관리
├── nodes/               # LangGraph 개별 노드 모듈
│   ├── collect.py       # 기사 수집 노드
│   ├── judge.py         # 사건 동질성 판정 노드
│   ├── analyze.py       # 언론사별 분석 노드
│   ├── compare.py       # 사실/시각 비교 노드
│   ├── summarize.py     # 중립 요약 노드
│   ├── check_bias.py    # 사실 근거 및 편향 검증 노드
│   └── report.py        # 최종 리포트 생성 노드
├── tools/
│   └── news_search.py   # Google News RSS / SerpApi 기사 검색 및 크롤링 툴
├── static/
│   └── index.html       # 웹 프론트엔드 단일 페이지
├── docs/                # 중립성 가이드 및 아키텍처 문서
└── samples/             # 테스트용 샘플 데이터 및 결과물
```

---

## 8. 라이선스

이 프로젝트는 교육 및 연구 목적으로 제작되었습니다.
