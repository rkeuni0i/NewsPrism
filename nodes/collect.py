import re
from concurrent.futures import ThreadPoolExecutor
from pydantic import BaseModel, Field
import llm
from tools import news_search as ns


class AnchorOut(BaseModel):
    anchor: str = Field(description="후보 중 가장 많은 언론사가 다룬 하나의 구체적 사건을 한두 문장으로 서술 (누가/무엇을/언제)")


class QueriesOut(BaseModel):
    queries: list[str] = Field(description="같은 사건을 다룬 다른 언론사 기사를 찾기 위한 짧은 검색어 2~3개")


ANCHOR_PROMPT = """아래는 키워드 '{query}'로 검색된 기사 제목들이다. 이 중 **가장 많은 기사가 다루는 하나의 구체적 사건**을 골라
기준 사건을 한두 문장으로 서술하라. 제목에 없는 내용은 만들지 마라.

{titles}
"""

QUERY_PROMPT = """다음 기사 제목(또는 키워드)과 **같은 사건**을 다룬 다른 언론사 기사를 뉴스 검색에서 찾으려 한다.
검색어 2~3개를 만들어라.
- 각 검색어는 2~5단어로 짧게. 핵심 인물·기관·사건어만 남기고 따옴표·괄호·[속보] 같은 말머리는 빼라.
- 서로 다른 표현으로 만들어라 (제목 문장을 그대로 반복하지 말 것).
- 제목에 없는 내용을 추가하지 마라.

제목: {query}
"""

MIN_OUTLETS = 5  # 이 수보다 적은 언론사가 모이면 검색어를 바꿔 더 찾는다


def clean_query(q: str) -> str:
    """검색 엔진이 한 기사만 돌려주지 않도록 말머리([속보])·따옴표를 제거."""
    q = re.sub(r"\[[^\]]*\]|[“”\"'‘’]", " ", q)
    return re.sub(r"\s+", " ", q).strip() or q


def _relevance(query: str, title: str) -> float:
    """검색어(원래 헤드라인)의 글자 쌍 중 후보 제목에도 있는 비율. 같은 사건 기사일수록 높다."""
    x, y = ns._bigrams(clean_query(query)), ns._bigrams(title)
    return len(x & y) / len(x) if x else 0.0


MAX_ARTICLES = 12  # 비교할 최대 언론사 수 (판정·분석 LLM 호출이 이만큼 늘어난다)
REL_OK = 0.25      # 제목 유사도가 이 이상이면 '같은 사건일 가능성이 큰 후보'


def select_items(pool: list[dict], query: str, seed_outlets: set, n: int = MAX_ARTICLES) -> list[dict]:
    """언론사마다 이 사건과 가장 비슷한 기사 1건을 고른 뒤, 언론사를 중요도 순으로 n곳 고른다.
    우선순위: ① 주요 사건 카드가 이미 같은 사건으로 묶어 준 언론사 ② 제목이 사건과 비슷한 기사 ③ 주요 언론사 ④ 제목 유사도.
    (이전에는 검색 결과 순서대로 잘라서, 카드에 표기된 KBS·JTBC 같은 언론사가 빠지고 군소 매체가 들어갔다)"""
    best: dict[str, tuple[float, dict]] = {}
    for it in pool:  # 같은 언론사의 여러 기사 중 사건과 가장 비슷한 기사
        o = it.get("outlet")
        if not o or o in ns.PHOTO_ONLY:
            continue
        r = _relevance(query, it["title"])
        if o not in best or r > best[o][0]:
            best[o] = (r, it)
    ranked = sorted(best.items(), key=lambda kv: (
        0 if kv[0] in seed_outlets else 1,
        0 if kv[1][0] >= REL_OK else 1,
        0 if kv[0] in ns.MAJOR_OUTLETS else 1,
        -kv[1][0]))
    return [it for _, (_, it) in ranked[:n]]


def _dedupe(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for it in items:
        k = it.get("url") or it.get("gurl") or it["title"]
        if k not in seen:
            seen.add(k); out.append(it)
    return out


def _n_outlets(items: list[dict]) -> int:
    return len({it["outlet"] for it in items if it.get("outlet")})


def gather(query: str, seeds: list[dict]) -> list[dict]:
    """후보 기사 모으기: ① 이미 같은 사건으로 묶인 기사(seeds) ② 정리한 제목으로 검색
    ③ 그래도 언론사가 모자라면 LLM이 만든 짧은 검색어로 추가 검색."""
    pool = list(seeds) + ns.search(clean_query(query), limit=40)
    if _n_outlets(pool) < MIN_OUTLETS:
        qs = ns._cache_get("queries", query)  # 같은 기사에는 처음 만든 검색어를 다시 쓴다: 결과가 일정하고 호출도 줄어든다
        if qs is None:
            try:
                qs = llm.structured(QUERY_PROMPT.format(query=query), QueriesOut).queries[:3]
                ns._cache_put("queries", query, qs)
            except Exception:  # noqa: BLE001  검색어 생성 실패해도 수집은 계속 (실패는 저장하지 않는다)
                qs = []
        with ThreadPoolExecutor(3) as ex:
            for res in ex.map(lambda q: ns.search(q, limit=40), qs):
                pool += res
    return _dedupe(pool)


def collect_articles(state: dict) -> dict:
    """후보 모으기 → 언론사 중복 제거 → 본문 추출 → 기준 사건(anchor) 도출.
    candidates는 판정 에이전트가 거를 것이므로 넉넉히(10건) 가져온다."""
    seeds = state.get("seeds") or []
    pool = gather(state["query"], seeds)
    items = select_items(pool, state["query"], {s["outlet"] for s in seeds if s.get("outlet")}, MAX_ARTICLES)
    articles = ns.build_articles(items)
    if seeds:
        # 주요 사건 카드에서 시작한 분석: 헤드라인이 이미 하나의 사건을 가리키므로 LLM으로 기준 사건을 다시 만들지 않는다 (LLM 단계 1개 절약)
        anchor = clean_query(state["query"])
    else:
        titles = "\n".join(f"- [{a.outlet}] {a.title}" for a in articles)
        anchor = llm.structured(ANCHOR_PROMPT.format(query=state["query"], titles=titles), AnchorOut).anchor
    return {"articles": articles, "anchor": anchor}
