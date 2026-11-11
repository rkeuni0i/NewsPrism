from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
from state import GraphState
from nodes.collect import collect_articles
from nodes.judge import filter_articles
from nodes.process import process_article
from nodes.compare import compare_articles
from nodes.summarize import write_summary
from nodes.check_bias import check_bias, route_after_bias
from nodes.report import build_report, no_report
from nodes.single import single_factcheck, single_research, write_single, check_single, route_after_single, build_single


def start_route(state):
    """샘플처럼 articles가 미리 주어지면 수집을 건너뜀."""
    return "judge_fan" if state.get("articles") else "collect_articles"


def judge_fan(state):
    return {}


def fan_out_judge(state):
    """기사마다 '판정 + 분석'을 동시에 실행."""
    return [Send("process_article", {"article": a, "anchor": state["anchor"]}) for a in state["articles"]]


def route_after_filter(state):
    rel = state["relevant"]
    if len(rel) == 1 and rel[0].body_quality != "snippet":  # 단독 보도: 비교 대신 팩트체크 후 기사 새로 작성
        return "single_factcheck"
    if len(rel) < 2:  # 같은 사건 기사가 없거나, 1건이지만 본문을 못 얻어 팩트체크도 할 수 없음
        return "no_report"
    return "compare_articles"  # 분석은 process_article에서 이미 끝나 있다


def build():
    g = StateGraph(GraphState)
    g.add_node("collect_articles", collect_articles)
    g.add_node("judge_fan", judge_fan)
    g.add_node("process_article", process_article)
    g.add_node("filter_articles", filter_articles)
    g.add_node("compare_articles", compare_articles)
    g.add_node("write_summary", write_summary)
    g.add_node("check_bias", check_bias)
    g.add_node("build_report", build_report)
    g.add_node("no_report", no_report)
    g.add_node("single_factcheck", single_factcheck)
    g.add_node("single_research", single_research)
    g.add_node("write_single", write_single)
    g.add_node("check_single", check_single)
    g.add_node("build_single", build_single)

    g.add_conditional_edges(START, start_route, ["collect_articles", "judge_fan"])
    g.add_edge("collect_articles", "judge_fan")
    g.add_conditional_edges("judge_fan", fan_out_judge, ["process_article"])
    g.add_edge("process_article", "filter_articles")
    g.add_conditional_edges("filter_articles", route_after_filter, ["compare_articles", "no_report", "single_factcheck"])
    g.add_edge("single_factcheck", "single_research")
    g.add_edge("single_research", "write_single")
    g.add_edge("write_single", "check_single")
    g.add_conditional_edges("check_single", route_after_single, ["write_single", "build_single"])
    g.add_edge("build_single", END)
    g.add_edge("compare_articles", "write_summary")
    g.add_edge("write_summary", "check_bias")
    g.add_conditional_edges("check_bias", route_after_bias, ["write_summary", "build_report"])
    g.add_edge("build_report", END)
    g.add_edge("no_report", END)
    return g.compile()
