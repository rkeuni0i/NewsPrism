import llm, prompts


def write_summary(state: dict) -> dict:
    bc = state.get("bias_check")
    fb = ""
    if bc and not bc.passed:
        fb = "\n[이전 초안의 지적 사항 — 반드시 고쳐라]\n- " + "\n- ".join(bc.issues) + "\n"
    s = llm.text(prompts.SUMMARY.format(feedback=fb, comparison=state["comparison"].model_dump_json(indent=1)))
    return {"summary": s, "retry_count": state.get("retry_count", 0) + (1 if bc else 0)}
