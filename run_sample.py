import json, sys, io
from state import Article
from graph import build

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
path = sys.argv[1] if len(sys.argv) > 1 else "samples/sample_01.json"
d = json.load(open(path, encoding="utf-8"))
state = {"query": d["query"], "anchor": d["anchor"], "articles": [Article(**a) for a in d["articles"]]}
out = build().invoke(state)
print("판정:", [(j.article_id, j.same_topic, j.reason) for j in out["judgements"]])
print("재시도:", out.get("retry_count", 0), "| 편향통과:", out["bias_check"].passed if "bias_check" in out else None)
print()
print(out["report"])
