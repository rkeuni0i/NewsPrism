import llm, prompts
from state import TopicJudgement


def judge_topic(payload: dict) -> dict:
    """Send로 기사 1건씩 병렬 호출. 기준 사건과 같은 주제인지 판정."""
    a = payload["article"]
    try:
        j = llm.structured(
            prompts.JUDGE.format(anchor=payload["anchor"], outlet=a.outlet, title=a.title, body=a.body),
            TopicJudgement,
        )
    except llm.LLMAuthError:
        raise  # 인증 문제는 모든 호출이 실패하는 상태: 조용히 '제외'하지 않고 분석을 멈춘다
    except Exception as e:  # noqa: BLE001  기사 하나의 판정이 끝내 실패해도 전체 분석은 계속한다 (제외 목록에 사유가 보인다)
        j = TopicJudgement(same_topic=False, reason=f"판정 중 오류로 제외 ({type(e).__name__})")
    if j is None:  # 응답을 해석하지 못한 경우
        j = TopicJudgement(same_topic=False, reason="판정 응답을 해석하지 못해 제외")
    j.article_id = a.id
    return {"judgements": [j]}


def filter_articles(state: dict) -> dict:
    """판정 결과로 같은 주제 기사만 남김."""
    ok = {j.article_id for j in state["judgements"] if j.same_topic}
    return {"relevant": [a for a in state["articles"] if a.id in ok]}
