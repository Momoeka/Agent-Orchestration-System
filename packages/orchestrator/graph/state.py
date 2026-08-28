"""The shared graph state (Architecture.md §4.1) and the payload a specialist branch receives.

Reducers: dict channels merge by key (a retried subtask's newer result/verdict overwrites the old
one); list channels append. Values are Pydantic models so the checkpointer round-trips them.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from packages.shared.types.cost import CostEntry
from packages.shared.types.deliverable import Deliverable
from packages.shared.types.gate import ToolEvent
from packages.shared.types.plan import ExecutionPlan
from packages.shared.types.review import ReviewVerdict
from packages.shared.types.subtask import Subtask, SubtaskResult
from packages.shared.types.task import TaskEvent, TaskOptions


def merge_dicts(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any]:
    return {**(a or {}), **(b or {})}


class TaskState(TypedDict, total=False):
    task_id: str
    user_id: str
    request: str
    options: TaskOptions
    recalled_memories: list[dict[str, Any]]
    plan: ExecutionPlan | None
    plan_confidence: float
    subtask_results: Annotated[dict[str, SubtaskResult], merge_dicts]
    review_verdicts: Annotated[dict[str, ReviewVerdict], merge_dicts]
    retry_counts: Annotated[dict[str, int], merge_dicts]
    cost_ledger: Annotated[list[CostEntry], operator.add]
    tool_events: Annotated[list[ToolEvent], operator.add]
    events: Annotated[list[TaskEvent], operator.add]
    final_output: Deliverable | None
    status: str
    error: str | None


class SpecialistInput(TypedDict):
    """What `Send()` hands to a specialist node."""

    task_id: str
    subtask: Subtask
    predecessor_outputs: dict[str, str]
    feedback: str | None
    attempt: int


def initial_state(task_id: str, user_id: str, request: str, options: TaskOptions) -> TaskState:
    return TaskState(
        task_id=task_id,
        user_id=user_id,
        request=request,
        options=options,
        recalled_memories=[],
        plan=None,
        plan_confidence=0.0,
        subtask_results={},
        review_verdicts={},
        retry_counts={},
        cost_ledger=[],
        tool_events=[],
        events=[],
        final_output=None,
        status="running",
        error=None,
    )


def event(kind: str, message: str, *, node: str = "", **data: Any) -> TaskEvent:
    return TaskEvent(kind=kind, message=message, node=node, data=data)
