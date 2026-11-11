"""실제 키워드로 수집부터 리포트까지. 사용: python run_live.py "키워드" [출력파일]"""
import sys, io
from graph import build
kw = sys.argv[1]
out = build().invoke({"query": kw})
txt = "기준 사건: " + out["anchor"] + "\n판정: " + str([(j.article_id, j.same_topic) for j in out["judgements"]]) + "\n\n" + out["report"]
open(sys.argv[2] if len(sys.argv) > 2 else "last_report.md", "w", encoding="utf-8").write(txt)
print("saved")
