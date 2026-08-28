"""Reviewer node: scores every result that has not been reviewed at its current attempt.

Runs once per superstep after the specialists (fan-in). Uses the reviewer role chain — a different
model family from the specialists. A reviewer outage is a rejection with feedback, never an
acceptance (fail closed).
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from pydantic import ValidationError

from packages.orchestrator.graph.deps import GraphDeps
from packages.orchestrator.graph.edges import needs_review
from packages.orchestrator.graph.state import TaskState, event
from packages.orchestrator.llm.chains import extract_json
from packages.orchestrator.loop.messages import system_message, user_message
from packages.orchestrator.tracing.otel import span
from packages.shared.errors import RetryableError, SchemaValidationError
from packages.shared.types.cost import CostEntry
from packages.shared.types.plan import ExecutionPlan
from packages.shared.types.review import ReviewJudgement, ReviewVerdict
from packages.shared.types.subtask import Subtask, SubtaskResult

log = structlog.get_logger(__name__)


def render_review(subtask: Subtask, result: SubtaskResult) -> str:
    spec = subtask.model_dump(exclude={"inputs"})
    res = result.model_dump(exclude={"cost_entries"})
    return (
        "## Subtask specification\n```json\n"
        + json.dumps(spec, indent=2, default=str)
        + "\n```\n\n## Specialist result\n```json\n"
        + json.dumps(res, indent=2, default=str)
        + "\n```\n\nJudge the result as JSON."
    )


def make_review_node(deps: GraphDeps):  # type: ignore[no-untyped-def]
    async def review(state: TaskState) -> dict[str, Any]:
        plan = ExecutionPlan.model_validate(state["plan"]).by_id()
        results = {
            k: SubtaskResult.model_validate(v)
            for k, v in (state.get("subtask_results") or {}).items()
        }
        verdicts = {
            k: ReviewVerdict.model_validate(v)
            for k, v in (state.get("review_verdicts") or {}).items()
        }
        pending = needs_review(results, verdicts)

        costs: list[CostEntry] = []
        llm = deps.llm_for(deps.reviewer.role).with_cost_sink(costs.append)
        new_verdicts: dict[str, ReviewVerdict] = {}
        retry_increments: dict[str, int] = {}
        events = []
        retry_counts = state.get("retry_counts") or {}

        for result in pending:
            subtask = plan[result.subtask_id]
            with span(
                "node.review",
                task_id=state["task_id"],
                subtask_id=subtask.id,
                attempt=result.attempt,
            ) as s:
                messages = [
                    system_message(deps.reviewer.system_prompt),
                    user_message(render_review(subtask, result)),
                ]
                model_used = ""
                try:
                    resp = await llm.chat(
                        messages,
                        response_schema=ReviewJudgement.model_json_schema(),
                        schema_name="ReviewJudgement",
                        max_tokens=1500,
                        timeout_s=deps.config.llm_timeout_s,
                    )
                    judgement = ReviewJudgement.model_validate(extract_json(resp.content))
                    model_used = f"{resp.provider}/{resp.model}"
                except (RetryableError, SchemaValidationError, ValidationError) as e:
                    log.warning("review.unavailable", subtask_id=subtask.id, error=str(e)[:300])
                    judgement = ReviewJudgement(
                        accept=False,
                        score=1,
                        issues=["reviewer unavailable"],
                        feedback=f"The reviewer could not assess this result ({str(e)[:160]}). "
                        "Re-submit with every claim clearly sourced.",
                    )
                verdict = ReviewVerdict(
                    **judgement.model_dump(),
                    subtask_id=subtask.id,
                    attempt=result.attempt,
                    reviewer_model=model_used,
                )
                new_verdicts[subtask.id] = verdict
                s.set_attribute("accept", verdict.accept)
                s.set_attribute("score", verdict.score)
                if not verdict.accept:
                    retry_increments[subtask.id] = retry_counts.get(subtask.id, 0) + 1
                    retry_counts = {**retry_counts, **retry_increments}
                events.append(
                    event(
                        "reviewed",
                        f"{subtask.id} attempt {result.attempt}: "
                        f"{'accepted' if verdict.accept else 'rejected'} ({verdict.score}/5)",
                        node="review",
                        subtask_id=subtask.id,
                        attempt=result.attempt,
                        accept=verdict.accept,
                        score=verdict.score,
                        issues=verdict.issues,
                    )
                )

        return {
            "review_verdicts": new_verdicts,
            "retry_counts": retry_increments,
            "cost_ledger": costs,
            "events": events,
        }

    return review
