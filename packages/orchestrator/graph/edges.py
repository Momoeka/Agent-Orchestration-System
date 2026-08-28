"""Conditional edges (Architecture.md §4.3). Plain functions over typed state — an LLM never
decides which node runs next (Rules.md §2.4).

Rejected results are re-sent to the same specialist with the reviewer's feedback until
``max_retries`` rejections; then the task escalates. Subtasks whose dependencies are all accepted
are dispatched, in parallel when independent. When nothing can run and not everything is
accepted, the task escalates rather than hanging.
"""

from __future__ import annotations

from langgraph.types import Send

from packages.orchestrator.graph.state import SpecialistInput, TaskState
from packages.shared.types.plan import ExecutionPlan
from packages.shared.types.review import ReviewVerdict
from packages.shared.types.subtask import Subtask, SubtaskResult

NODE_DISPATCH = "dispatch"
NODE_APPROVE_PLAN = "approve_plan"
NODE_SYNTHESIZE = "synthesize"
NODE_ESCALATE = "escalate"


def specialist_node_name(specialist: str) -> str:
    return f"specialist_{specialist}"


# ---------- helpers over typed state ----------


def _plan(state: TaskState) -> ExecutionPlan:
    plan = state.get("plan")
    if plan is None:
        raise ValueError("no plan in state")
    return ExecutionPlan.model_validate(plan)


def _results(state: TaskState) -> dict[str, SubtaskResult]:
    return {
        k: SubtaskResult.model_validate(v) for k, v in (state.get("subtask_results") or {}).items()
    }


def _verdicts(state: TaskState) -> dict[str, ReviewVerdict]:
    return {
        k: ReviewVerdict.model_validate(v) for k, v in (state.get("review_verdicts") or {}).items()
    }


def is_accepted(
    subtask_id: str, results: dict[str, SubtaskResult], verdicts: dict[str, ReviewVerdict]
) -> bool:
    r, v = results.get(subtask_id), verdicts.get(subtask_id)
    return r is not None and v is not None and v.attempt == r.attempt and v.accept


def is_pending_review(
    subtask_id: str, results: dict[str, SubtaskResult], verdicts: dict[str, ReviewVerdict]
) -> bool:
    r, v = results.get(subtask_id), verdicts.get(subtask_id)
    return r is not None and (v is None or v.attempt < r.attempt)


def needs_review(
    results: dict[str, SubtaskResult], verdicts: dict[str, ReviewVerdict]
) -> list[SubtaskResult]:
    return [r for sid, r in results.items() if is_pending_review(sid, results, verdicts)]


def ready_subtasks(
    plan: ExecutionPlan, results: dict[str, SubtaskResult], verdicts: dict[str, ReviewVerdict]
) -> list[Subtask]:
    """Not yet run (no result at all) and every dependency accepted."""
    out = []
    for s in plan.subtasks:
        if s.id in results:
            continue
        if all(is_accepted(d, results, verdicts) for d in s.depends_on):
            out.append(s)
    return out


def rejected_subtasks(
    plan: ExecutionPlan, results: dict[str, SubtaskResult], verdicts: dict[str, ReviewVerdict]
) -> list[tuple[Subtask, ReviewVerdict]]:
    out = []
    for s in plan.subtasks:
        r, v = results.get(s.id), verdicts.get(s.id)
        if r is not None and v is not None and v.attempt == r.attempt and not v.accept:
            out.append((s, v))
    return out


def predecessor_outputs(subtask: Subtask, results: dict[str, SubtaskResult]) -> dict[str, str]:
    return {d: results[d].output for d in subtask.depends_on if d in results}


def send_for(state: TaskState, subtask: Subtask, *, attempt: int, feedback: str | None) -> Send:
    payload: SpecialistInput = {
        "task_id": state["task_id"],
        "subtask": subtask,
        "predecessor_outputs": predecessor_outputs(subtask, _results(state)),
        "feedback": feedback,
        "attempt": attempt,
    }
    return Send(specialist_node_name(subtask.specialist.value), payload)


# ---------- the edges ----------


def make_route_after_plan(confidence_threshold: float):  # type: ignore[no-untyped-def]
    def route_after_plan(state: TaskState) -> str:
        if state.get("plan") is None or state.get("status") == "failed":
            return NODE_ESCALATE
        plan = _plan(state)
        if (
            plan.confidence < confidence_threshold
            or plan.sensitive_actions
            and state["options"].require_human_review
        ):
            return NODE_APPROVE_PLAN
        if state["options"].require_human_review:
            return NODE_APPROVE_PLAN
        return NODE_DISPATCH

    return route_after_plan


def route_dispatch(state: TaskState) -> list[Send] | str:
    """After the dispatch node: fan out every ready subtask; nothing ready means a broken plan."""
    plan = _plan(state)
    results, verdicts = _results(state), _verdicts(state)
    ready = ready_subtasks(plan, results, verdicts)
    if not ready:
        return NODE_ESCALATE
    return [send_for(state, s, attempt=1, feedback=None) for s in ready]


def make_route_after_review(max_retries: int):  # type: ignore[no-untyped-def]
    def route_after_review(state: TaskState) -> list[Send] | str:
        plan = _plan(state)
        results, verdicts = _results(state), _verdicts(state)
        retry_counts = state.get("retry_counts") or {}

        sends: list[Send] = []
        for subtask, verdict in rejected_subtasks(plan, results, verdicts):
            if retry_counts.get(subtask.id, 0) > max_retries:
                return NODE_ESCALATE
            attempt = results[subtask.id].attempt + 1
            sends.append(send_for(state, subtask, attempt=attempt, feedback=verdict.feedback))

        for subtask in ready_subtasks(plan, results, verdicts):
            sends.append(send_for(state, subtask, attempt=1, feedback=None))

        if sends:
            return sends
        if all(is_accepted(s.id, results, verdicts) for s in plan.subtasks):
            return NODE_SYNTHESIZE
        return NODE_ESCALATE  # nothing runnable, not finished: a dependency can never be met

    return route_after_review
