"""LLM 없이 그래프 배선만 검증 (가짜 LLM)."""
import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import llm
from state import *

def fake_structured(prompt, schema, temperature=0.0):
    if schema is TopicJudgement:
        return TopicJudgement(same_topic="노선 개편안" not in prompt, reason="stub")
    if schema is ArticleAnalysis:
        return ArticleAnalysis(main_claim="m", emphasized_facts=[], omitted_context=[], cited_sources=[], tone="t")
    if schema is Comparison:
        return Comparison(common_facts=[CommonFact(fact="c",outlets=["a","b","c"]),CommonFact(fact="z",outlets=["a"])], divergences=["d"], partial_facts=["p"], questions=["q?"])
    if schema is ClaimCheck:
        return ClaimCheck(claims=[])
    if schema is Rubric:
        fake_structured.n += 1
        ok = fake_structured.n >= 2
        return Rubric(lexical_ok=ok, attribution_ok=True, selection_ok=True, issues=[] if ok else ["x"])
fake_structured.n = 0
llm.structured = fake_structured
llm.text = lambda p, temperature=0.3: "요약"

from graph import build
d = json.load(open("samples/sample_01.json", encoding="utf-8"))
out = build().invoke({"query": d["query"], "anchor": d["anchor"], "articles": [Article(**a) for a in d["articles"]]})
print("relevant:", [a.id for a in out["relevant"]])
print("analyses:", len(out["analyses"]), "retry:", out["retry_count"], "passed:", out["bias_check"].passed)
print(out["report"][-200:])
