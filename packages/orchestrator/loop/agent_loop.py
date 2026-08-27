"""The agent loop (diagram 03, Architecture.md §5.1).

    build context → LLM → tool calls? → gate → registry → results appended → LLM … → submit_result

The model finishes by calling the ``submit_result`` tool, whose arguments are validated as
``SubmittedResult``. Every other tool call goes through ``Gate.decide`` and only then
``ToolRegistry.invoke``. All parallel results are appended before the next model call. Guards:
iterations, tokens, cost, wall-clock (``Budget``).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import structlog
from pydantic import ValidationError

from packages.orchestrator.agents.base import AgentSpec
from packages.orchestrator.gate.decide import Gate
from packages.orchestrator.llm.chains import ChainedLLM
from packages.orchestrator.llm.openai_compat import strict_schema
from packages.orchestrator.loop.budgets import Budget, BudgetTracker
from packages.orchestrator.loop.messages import (
    assistant_message,
    render_subtask,
    system_message,
    tool_messages,
    user_message,
)
from packages.orchestrator.tracing.otel import span, tool_span
from packages.shared.errors import BudgetExceededError, RetryableError
from packages.shared.types.cost import CostEntry
from packages.shared.types.gate import GateAction
from packages.shared.types.llm import LLMMessage
from packages.shared.types.subtask import SubmittedResult, Subtask, SubtaskResult, SubtaskStatus
from packages.shared.types.tools import ToolCall, ToolResult
from packages.tools.registry.registry import ToolRegistry

log = structlog.get_logger(__name__)

SUBMIT_TOOL = "submit_result"
MAX_NUDGES = 2


def submit_tool_schema() -> dict:  # type: ignore[type-arg]
    schema = SubmittedResult.model_json_schema()
    schema.pop("title", None)
    return {
        "type": "function",
        "function": {
            "name": SUBMIT_TOOL,
            "description": (
                "Finish the subtask. Call this exactly once, when you have the deliverable. "
                "Put the full deliverable in `output`; list every source you relied on."
            ),
            "parameters": strict_schema(schema),
        },
    }


@dataclass
class LoopDeps:
    llm: ChainedLLM
    registry: ToolRegistry
    gate: Gate
    budget: Budget = field(default_factory=Budget)
    llm_timeout_s: float = 90.0


async def run_agent_loop(agent: AgentSpec, subtask: Subtask, deps: LoopDeps) -> SubtaskResult:
    tracker = BudgetTracker(deps.budget)
    cost_entries: list[CostEntry] = []
    tools_used: list[str] = []
    nudges = 0

    def on_cost(entry: CostEntry) -> None:
        cost_entries.append(entry)
        tracker.record(entry)

    # The chain records cost through this callback; bind it for the life of this loop.
    llm = ChainedLLM(
        deps.llm.role, deps.llm.config, deps.llm._pool, prices=deps.llm._prices, on_cost=on_cost
    )

    messages: list[LLMMessage] = [
        system_message(agent.system_prompt),
        user_message(render_subtask(subtask)),
    ]
    tools = deps.registry.schemas_for(agent.name) + [submit_tool_schema()]

    def finish(
        submitted: SubmittedResult, *, iterations: int, error: str | None = None
    ) -> SubtaskResult:
        return SubtaskResult(
            **submitted.model_dump(),
            subtask_id=subtask.id,
            tools_used=sorted(set(tools_used)),
            iterations=iterations,
            cost_entries=cost_entries,
            fallback_used=any(c.fallback for c in cost_entries),
            error=error,
        )

    def failed(reason: str, *, iterations: int) -> SubtaskResult:
        return finish(
            SubmittedResult(
                status=SubtaskStatus.FAILED, output="", self_confidence=0.0, notes=reason
            ),
            iterations=iterations,
            error=reason,
        )

    with span(f"agent.{agent.name}", subtask_id=subtask.id, agent=agent.name):
        iteration = 0
        while True:
            try:
                iteration = tracker.start_iteration()
            except BudgetExceededError as e:
                return failed(str(e), iterations=iteration)

            with span(f"agent.{agent.name}.iteration", subtask_id=subtask.id, iteration=iteration):
                try:
                    response = await llm.chat(messages, tools=tools, timeout_s=deps.llm_timeout_s)
                except BudgetExceededError as e:
                    return failed(str(e), iterations=iteration)
                except RetryableError as e:
                    return failed(f"all model providers failed: {e}", iterations=iteration)

                messages.append(assistant_message(response))

                if not response.tool_calls:
                    nudges += 1
                    if nudges > MAX_NUDGES:
                        return failed(
                            "model stopped without calling submit_result", iterations=iteration
                        )
                    messages.append(user_message("You must finish by calling `submit_result`."))
                    continue

                submitted: SubmittedResult | None = None
                results: list[ToolResult] = []
                other_calls: list[ToolCall] = []
                for call in response.tool_calls:
                    if call.name == SUBMIT_TOOL:
                        try:
                            if call.parse_error:
                                raise ValueError(call.parse_error)
                            submitted = SubmittedResult.model_validate(call.arguments)
                            results.append(
                                ToolResult(tool_call_id=call.id, name=call.name, content="accepted")
                            )
                        except (ValidationError, ValueError) as e:
                            results.append(
                                ToolResult(
                                    tool_call_id=call.id,
                                    name=call.name,
                                    is_error=True,
                                    content=f"submit_result rejected; fix and call again: {str(e)[:600]}",
                                )
                            )
                    else:
                        other_calls.append(call)

                executed = await asyncio.gather(
                    *(_execute(agent, call, deps, tools_used) for call in other_calls)
                )
                results.extend(executed)
                messages.extend(tool_messages(results))  # every result before the next model call

                if submitted is not None:
                    log.info(
                        "agent.submitted",
                        agent=agent.name,
                        subtask_id=subtask.id,
                        iterations=iteration,
                    )
                    return finish(submitted, iterations=iteration)


async def _execute(
    agent: AgentSpec, call: ToolCall, deps: LoopDeps, tools_used: list[str]
) -> ToolResult:
    decision = deps.gate.decide(agent.name, call)
    if decision.action == GateAction.BLOCK:
        return ToolResult(
            tool_call_id=call.id,
            name=call.name,
            is_error=True,
            content=f"denied: {decision.reason}",
        )
    if decision.action == GateAction.APPROVE:
        # Phase 4 wires interrupt()/resume here. Until then the call does not run — fail closed.
        return ToolResult(
            tool_call_id=call.id,
            name=call.name,
            is_error=True,
            content=f"requires human approval ({decision.reason}); approval flow not available in this phase — "
            "choose an approach that does not need this tool",
        )
    spec = deps.registry.get(call.name)
    server = spec.server if spec else "unknown"
    with tool_span(server=server, tool=call.name, agent=agent.name) as s:
        s.set_attribute("args", json.dumps(call.arguments, default=str)[:1000])
        result = await deps.registry.invoke(call)
        s.set_attribute("ok", not result.is_error)
        s.set_attribute("result_size", len(result.content))
        s.set_attribute("latency_ms", result.latency_ms)
    tools_used.append(call.name)
    return result
