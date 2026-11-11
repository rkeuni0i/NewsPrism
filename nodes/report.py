def build_report(state: dict) -> dict:
    c = state["comparison"]
    rel = state["relevant"]
    out = [f"# 뉴스 비교 리포트: {state['query']}", "",
           f"비교 기사 {len(rel)}건: " + ", ".join(f"{a.outlet}{'(제목·요약만)' if a.body_quality == 'snippet' else ''}" for a in rel), "",
           "## 중립 요약", state["summary"], "",
           "## ① 모두가 동의하는 사실", *[f"- {x.fact} ({len(x.outlets)}곳: {', '.join(x.outlets)})" for x in c.common_facts], "",
           "## ② 주요 쟁점",
           *[f"- {i.topic}" + "".join(f"\n  - {p.holder}: {p.claim} ({', '.join(p.outlets)})" for p in i.positions) for i in c.issues],
           *([f"- {x}" for x in c.divergences] if not c.issues else []), "",
           "### 일부만 다룬 사실", *[f"- {x}" for x in c.partial_facts], "",
           "## ③ 함께 읽어볼 포인트"]
    out += [f"- {x}" for x in c.questions]
    excluded = [j for j in state["judgements"] if not j.same_topic]
    if excluded:
        out += ["", "## 제외된 기사 (다른 주제)", *[f"- {j.article_id}: {j.reason}" for j in excluded]]
    bc = state["bias_check"]
    if not bc.passed:
        out += ["", f"> 편향 검증 미통과 상태로 출력됨: {bc.issues}"]
    return {"report": "\n".join(out)}


def no_report(state: dict) -> dict:
    arts = state.get("articles", [])
    failed = [j for j in state.get("judgements", []) if "오류로 제외" in j.reason or "해석하지 못해" in j.reason]
    if failed:  # 사건이 달라서가 아니라 판정 호출이 실패한 경우
        return {"report": f"수집한 기사 {len(arts)}건 중 {len(failed)}건은 일시적인 LLM 오류로 같은 사건인지 판정하지 못했어요. 잠시 뒤 '다시 분석하기'를 눌러 주세요."}
    outlets = ", ".join(dict.fromkeys(a.outlet for a in arts)) or "없음"
    return {"report": f"같은 사건으로 판정된 기사가 {len(state.get('relevant', []))}건뿐이라 비교할 수 없습니다. "
                      f"(수집한 기사 {len(arts)}건, 언론사: {outlets}) 다른 표현의 키워드로 검색해 보세요."}
