import operator
from typing import Annotated, TypedDict
from pydantic import BaseModel, Field


class Article(BaseModel):
    id: str
    outlet: str
    title: str
    body: str
    url: str = ""
    published: str = ""
    body_quality: str = "full"  # full(본문 충분) | partial | snippet(제목+요약뿐: 유료벽/추출 실패)


class TopicJudgement(BaseModel):
    """기사가 기준 사건과 같은 주제인지 판정."""
    article_id: str = ""
    same_topic: bool = Field(description="기준 사건과 같은 사건을 다루면 true")
    reason: str = Field(description="판정 근거 한 문장")


class Evidence(BaseModel):
    claim: str
    quote: str = Field(description="기사 본문에서 그대로 가져온 근거 문장")


class ArticleAnalysis(BaseModel):
    article_id: str = ""
    outlet: str = ""
    body_quality: str = "full"
    main_claim: str
    emphasized_facts: list[Evidence]
    omitted_context: list[str] = Field(description="다른 기사엔 있을 법하나 이 기사에 없는 맥락 (추정 시 비워둠)")
    cited_sources: list[str]
    tone: str


class CommonFact(BaseModel):
    fact: str
    outlets: list[str] = Field(description="이 사실을 실제로 보도한 언론사 이름 전부")


class Position(BaseModel):
    holder: str = Field(description="이 입장을 가진 주체 (예: 노동계, 경영계, 정부, 야당). 언론사 자신의 의견이 아니라 기사 속 당사자")
    claim: str = Field(description="그 주체의 주장 한 문장. 기사에 나온 내용만")
    outlets: list[str] = Field(description="이 입장을 기사에서 전한 언론사 이름 전부")


class Issue(BaseModel):
    topic: str = Field(description="쟁점을 중립적인 의문문 한 문장으로 (예: '인상 폭은 적정한가?')")
    positions: list[Position] = Field(description="서로 다른 입장 2개 이상. 한 입장뿐이면 쟁점이 아니므로 만들지 않는다")


class Comparison(BaseModel):
    common_facts: list[CommonFact]
    highlights: list[str] = Field(default_factory=list, description="화면에서 굵게 보여줄 핵심어 8~12개. 아래 문장들에 글자 그대로 나온 표현만")
    issues: list[Issue] = Field(default_factory=list, description="사건을 둘러싸고 당사자들 사이에 입장이 갈리는 주요 쟁점 1~4개")
    divergences: list[str] = Field(description="'언론사 A는 ~, 언론사 B는 ~' 형태의 시각 차이")
    partial_facts: list[str] = Field(description="한두 곳만 다룬 사실 (언론사 명시)")
    questions: list[str] = Field(description="독자가 직접 확인해볼 질문 2~3개. 시각 차이 문장을 복사하지 말고 의문문으로 작성")


class ClaimVerdict(BaseModel):
    claim: str = Field(description="요약문에서 뽑은 독립적인 사실 주장 하나")
    stated_as_fact: bool = Field(description="요약이 단정적 사실로 서술했으면 true, '~에 따라/~는 ~라고 보도'처럼 출처 귀속이면 false")
    verdict: str = Field(description="supported | unsupported | contradicted")
    supporting_outlets: list[str]
    quote: str = Field(description="뒷받침하는 기사 제목 또는 본문 문장 (글자 그대로)")
    unsupported_outlets: list[str] = Field(default_factory=list, description="주장 문장에 이름이 나온 언론사 중, 실제로는 그 내용을 보도하지 않은 곳")


class ClaimCheck(BaseModel):
    claims: list[ClaimVerdict]


class Rubric(BaseModel):
    lexical_ok: bool = Field(description="평가·감정·낙인 어휘가 없는가")
    attribution_ok: bool = Field(description="갈리는 쟁점이 출처(언론사) 병기로 서술되고 한쪽 옳음을 판정하지 않는가")
    selection_ok: bool = Field(description="특정 입장의 정보만 부각/누락되지 않았는가")
    issues: list[str]


class BiasCheck(BaseModel):
    passed: bool
    issues: list[str]
    lexical_ok: bool = True
    attribution_ok: bool = True
    selection_ok: bool = True
    grounded_ok: bool = True
    repairs: list[str] = Field(default_factory=list)  # LLM 재작성 없이 코드로 고친 내용 (화면에 표시)


class SourceClaim(BaseModel):
    """단독 보도 기사에서 뽑은 사실 주장 하나."""
    claim: str = Field(description="기사가 사실로 전하는 주장 한 문장")
    quote: str = Field(description="그 주장의 근거가 되는 본문 문장. 본문에서 글자 그대로 복사")
    source_type: str = Field(description="공식 자료·기록 | 당사자 발언 | 익명 관계자 | 전문가 발언 | 기자 취재 내용 | 수치·통계 중 하나")
    concern: str = Field(default="", description="다른 곳에서 교차 확인이 필요한 이유 한 문장. 필요 없으면 빈 문자열")


class SourceCheck(BaseModel):
    claims: list[SourceClaim]
    queries: list[str] = Field(default_factory=list, description="다른 언론사 보도로 확인하고 배경을 찾기 위한 뉴스 검색어 3~4개")


class Cite(BaseModel):
    outlet: str = Field(description="근거가 되는 자료의 언론사 이름 (자료 목록의 이름 그대로)")
    quote: str = Field(description="그 자료의 제목 또는 본문에서 글자 그대로 복사한 문장")


class ClaimCrossCheck(BaseModel):
    index: int = Field(description="원 기사 주장의 번호(1부터)")
    status: str = Field(description="교차 확인됨 | 일부 확인 | 확인 불가 | 다른 정보 있음 중 하나")
    note: str = Field(description="판정 이유 한 문장. 어느 쪽이 맞다고 단정하지 말고 차이만 적는다")
    cites: list[Cite] = Field(default_factory=list)


class ContextPoint(BaseModel):
    kind: str = Field(description="배경 | 다른 관점 중 하나")
    point: str = Field(description="독자가 알면 좋은 배경·맥락, 또는 다른 관점 한 문장")
    cite: Cite


class CrossCheck(BaseModel):
    checks: list[ClaimCrossCheck]
    context: list[ContextPoint] = Field(default_factory=list)


class SingleArticle(BaseModel):
    headline: str
    report: str
    crosscheck: str
    context: str = ""
    perspectives: str = ""
    takeaways: list[str] = Field(default_factory=list)


class GraphState(TypedDict, total=False):
    single_claims: list[dict]  # 단독 보도 팩트체크 결과 (주장, 근거 문장, 출처 유형, 본문 근거 여부)
    single_article: str  # 단독 보도를 바탕으로 새로 쓴 기사 (검증 대상)
    single_queries: list[str]  # 교차 확인 자료를 찾기 위한 검색어
    single_sources: list  # 교차 확인에 쓴 다른 언론사 기사
    single_context: list[dict]  # 배경·다른 관점 항목 (인용문이 자료에서 확인된 것만)
    single_doc: dict  # 새 기사의 구성(제목, 보도 내용, 교차 확인, 배경, 다른 관점, 생각해 볼 점)
    single_note: str  # 코드가 붙이는 안내(단독 보도, 교차 확인 필요 부분). 검증 대상이 아니다
    query: str
    seeds: list[dict]  # 이미 같은 사건으로 묶여 있는 기사(주요 사건 카드에서 시작할 때)
    articles: list[Article]
    anchor: str  # 같은 주제 판정 기준
    judgements: Annotated[list[TopicJudgement], operator.add]
    relevant: list[Article]
    analyses: Annotated[list[ArticleAnalysis], operator.add]
    comparison: Comparison
    summary: str
    bias_check: BiasCheck
    claims: list[ClaimVerdict]
    retry_count: int
    report: str
