"""실제 StateGraph·Send 병렬 경로. 코드 노드는 추가 모델 호출이 아니다."""

import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send


class ResearchState(TypedDict, total=False):
    run_id: str
    revision: int
    instruction: str
    plan: list
    assignment: dict
    results: Annotated[list, operator.add]
    draft: str


def build_graph(service):
    graph = StateGraph(ResearchState)
    graph.add_node("plan", service.plan)
    graph.add_node("dispatch", service.dispatch)
    graph.add_node("researcher", service.research)
    graph.add_node("review", service.review)
    graph.add_node("synthesize", service.synthesize)
    graph.add_node("evaluate", service.evaluate)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "dispatch")

    def fan_out(state):
        return [
            Send("researcher", {**state, "assignment": a, "results": []})
            for a in state.get("plan", [])
        ] or END

    graph.add_conditional_edges("dispatch", fan_out, ["researcher", END])
    graph.add_edge("researcher", "review")
    graph.add_edge("review", "synthesize")
    graph.add_edge("synthesize", "evaluate")
    graph.add_edge("evaluate", END)
    return graph.compile()
