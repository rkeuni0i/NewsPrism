import json, llm, prompts
from state import Comparison


def compare_articles(state: dict) -> dict:
    # 판정과 분석을 동시에 돌리므로, 같은 사건으로 판정된 기사의 분석만 비교에 쓴다
    rel_ids = {a.id for a in state["relevant"]}
    analyses = [a for a in state["analyses"] if a.article_id in rel_ids]
    data = json.dumps([a.model_dump() for a in analyses], ensure_ascii=False, indent=1)
    cmp = llm.structured(prompts.COMPARE.format(analyses=data), Comparison)
    # 과반 미만 보도 사실은 '공통'에서 내려 '일부만 다룬 사실'로 이동 (과대 일반화 방지)
    need = len(analyses) // 2 + 1
    keep = []
    for f in cmp.common_facts:
        if len(set(f.outlets)) >= need:
            keep.append(f)
        else:
            cmp.partial_facts.append(f"{', '.join(f.outlets)}: {f.fact}")
    cmp.common_facts = keep
    return {"comparison": cmp}
