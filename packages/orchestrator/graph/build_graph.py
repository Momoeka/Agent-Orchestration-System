"""Wire the nodes and edges into the LangGraph state machine (diagram 02, Architecture.md §4).

START → intake → recall_memory → plan ─┬→ dispatch ─(Send per ready subtask)→ specialist_* → review
                                       └→ approve_plan → END                                  │
review ─┬→ (Send: retries / newly ready) → specialist_* → review …                             │
        ├→ synthesize → deliver → write_memory → END                                          │
        └→ escalate → deliver → END   (failed tasks are still persisted)                       ┘
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from packages.orchestrator.graph.deps import GraphDeps
from packages.orchestrator.graph.edges import (
    NODE_APPROVE_PLAN,
    NODE_DISPATCH,
    NODE_ESCALATE,
    NODE_SYNTHESIZE,
    make_route_after_plan,
    make_route_after_review,
    route_dispatch,
    specialist_node_name,
)
from packages.orchestrator.graph.nodes.approve_plan import approve_plan
from packages.orchestrator.graph.nodes.deliver import make_deliver_node
from packages.orchestrator.graph.nodes.dispatch import dispatch
from packages.orchestrator.graph.nodes.escalate import escalate
from packages.orchestrator.graph.nodes.intake import intake
from packages.orchestrator.graph.nodes.plan import make_plan_node
from packages.orchestrator.graph.nodes.recall_memory import recall_memory
from packages.orchestrator.graph.nodes.review import make_review_node
from packages.orchestrator.graph.nodes.specialist import make_specialist_node
from packages.orchestrator.graph.nodes.synthesize import make_synthesize_node
from packages.orchestrator.graph.nodes.write_memory import write_memory
from packages.orchestrator.graph.state import TaskState


def build_graph(deps: GraphDeps, checkpointer: Any = None) -> CompiledStateGraph:  # type: ignore[type-arg]
    g: StateGraph = StateGraph(TaskState)  # type: ignore[type-arg]

    g.add_node("intake", intake)
    g.add_node("recall_memory", recall_memory)
    g.add_node("plan", make_plan_node(deps))
    g.add_node(NODE_APPROVE_PLAN, approve_plan)
    g.add_node(NODE_DISPATCH, dispatch)
    specialist_nodes = []
    for name, agent in deps.specialists.items():
        node_name = specialist_node_name(name)
        specialist_nodes.append(node_name)
        g.add_node(node_name, make_specialist_node(agent, deps))
    g.add_node("review", make_review_node(deps))
    g.add_node(NODE_SYNTHESIZE, make_synthesize_node(deps))
    g.add_node(NODE_ESCALATE, escalate)
    g.add_node("deliver", make_deliver_node(deps))
    g.add_node("write_memory", write_memory)

    g.add_edge(START, "intake")
    g.add_conditional_edges(
        "intake", _route_after_intake, {"plan": "recall_memory", "fail": "deliver"}
    )
    g.add_edge("recall_memory", "plan")
    g.add_conditional_edges(
        "plan",
        make_route_after_plan(deps.config.plan_confidence_threshold),
        [NODE_DISPATCH, NODE_APPROVE_PLAN, NODE_ESCALATE],
    )
    g.add_edge(NODE_APPROVE_PLAN, "deliver")
    g.add_conditional_edges(NODE_DISPATCH, route_dispatch, [*specialist_nodes, NODE_ESCALATE])
    for node_name in specialist_nodes:
        g.add_edge(node_name, "review")
    g.add_conditional_edges(
        "review",
        make_route_after_review(deps.config.max_retries),
        [*specialist_nodes, NODE_SYNTHESIZE, NODE_ESCALATE],
    )
    g.add_edge(NODE_ESCALATE, "deliver")
    g.add_edge(NODE_SYNTHESIZE, "deliver")
    g.add_conditional_edges("deliver", _route_after_deliver, {"memory": "write_memory", "end": END})
    g.add_edge("write_memory", END)

    return g.compile(checkpointer=checkpointer, name="foreman")


def _route_after_intake(state: TaskState) -> str:
    return "fail" if state.get("status") == "failed" else "plan"


def _route_after_deliver(state: TaskState) -> str:
    return "memory" if state.get("status") == "done" else "end"
