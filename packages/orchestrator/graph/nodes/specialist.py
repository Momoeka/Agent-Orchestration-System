"""One node per specialist. Receives a `SpecialistInput` from Send(), runs the agent loop, and
merges its result into the shared state."""

from __future__ import annotations

from typing import Any

from packages.orchestrator.agents.base import AgentSpec
from packages.orchestrator.graph.deps import GraphDeps
from packages.orchestrator.graph.state import SpecialistInput, event
from packages.orchestrator.loop.agent_loop import LoopDeps, run_agent_loop
from packages.shared.types.subtask import Subtask


def make_specialist_node(agent: AgentSpec, deps: GraphDeps):  # type: ignore[no-untyped-def]
    async def specialist(payload: SpecialistInput) -> dict[str, Any]:
        subtask = Subtask.model_validate(payload["subtask"])
        inputs = dict(subtask.inputs)
        if payload.get("predecessor_outputs"):
            inputs["predecessor_outputs"] = payload["predecessor_outputs"]
        if payload.get("feedback"):
            inputs["reviewer_feedback"] = payload["feedback"]
        enriched = subtask.model_copy(update={"inputs": inputs})

        loop_deps = LoopDeps(
            llm=deps.llm_for(agent.role),
            registry=deps.registry,
            gate=deps.gate,
            budget=deps.config.specialist_budget,
            llm_timeout_s=deps.config.llm_timeout_s,
        )
        result = await run_agent_loop(agent, enriched, loop_deps, attempt=payload["attempt"])
        return {
            "subtask_results": {subtask.id: result},
            "cost_ledger": result.cost_entries,
            "tool_events": result.tool_events,
            "events": [
                event(
                    "subtask_done",
                    f"{subtask.id} ({agent.name}) attempt {result.attempt}: {result.status.value}",
                    node=f"specialist_{agent.name}",
                    subtask_id=subtask.id,
                    attempt=result.attempt,
                    status=result.status.value,
                    tools_used=result.tools_used,
                    iterations=result.iterations,
                )
            ],
        }

    return specialist
