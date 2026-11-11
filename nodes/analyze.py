import llm, prompts
from state import ArticleAnalysis


def analyze_article(payload: dict) -> dict:
    a = payload["article"]
    try:
        r = llm.structured(prompts.ANALYZE.format(quality=a.body_quality, outlet=a.outlet, title=a.title, body=a.body), ArticleAnalysis)
    except llm.LLMAuthError:
        raise
    except Exception:  # noqa: BLE001  기사 하나의 분석이 끝내 실패해도 전체 분석은 계속한다 (그 기사만 비교에서 빠진다)
        return {"analyses": []}
    if r is None:  # 응답을 해석하지 못한 경우도 마찬가지
        return {"analyses": []}
    r.article_id, r.outlet, r.body_quality = a.id, a.outlet, a.body_quality
    if a.body_quality == 'snippet':
        r.omitted_context = []  # 본문이 없는 기사로 '빠뜨림'을 주장하지 않음
    # 환각 방지: 본문에 없는 근거 문장은 제외
    r.emphasized_facts = [e for e in r.emphasized_facts if e.quote.strip() and e.quote.strip() in a.body]
    return {"analyses": [r]}
