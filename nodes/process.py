from concurrent.futures import ThreadPoolExecutor
from nodes.judge import judge_topic
from nodes.analyze import analyze_article


def process_article(payload: dict) -> dict:
    """기사 1건의 '같은 사건인지 판정'과 '기사별 분석'을 동시에 실행한다.
    예전에는 판정이 모두 끝난 뒤에 분석을 시작해서 LLM 단계가 하나 더 있었다. 판정에서 제외된 기사의 분석은 버려지므로
    분석 호출이 조금(제외 기사 수만큼) 늘지만, 그 대신 단계 하나만큼 빨라진다."""
    with ThreadPoolExecutor(2) as ex:
        fj = ex.submit(judge_topic, payload)
        fa = ex.submit(analyze_article, {"article": payload["article"]})
        out = dict(fj.result())
        out.update(fa.result())
    return out
