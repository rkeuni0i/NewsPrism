"""주요 사건 카드용 짧은 요약. 본문을 읽어 LLM으로 두 문장 요약하고 결과를 캐시한다."""
import llm, prompts
from tools import news_search as ns


def summarize(item: dict) -> str:
    key = item.get("url") or item.get("gurl") or item["title"]
    hit = ns._cache_get("summary", key)
    if hit is not None:
        return hit["text"]
    art = ns.build_articles([item])[0]
    if art.body_quality == "snippet":  # 본문을 못 얻으면 LLM으로 요약하지 않는다 (캐시하지 않아 다음에 재시도)
        desc = art.body[len(art.title) + 2:].strip()  # build_articles가 "제목. 요약문"으로 만든 뒤쪽
        return desc if len(desc) >= 30 else ""
    text = llm.text(prompts.TOPIC_SUMMARY.format(title=art.title, body=art.body[:4000]), temperature=0.2)
    ns._cache_put("summary", key, {"text": text})
    return text
