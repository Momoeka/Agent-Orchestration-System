"""Phase 2 stub for the L4 escalation point. Phase 4 replaces the body with ``interrupt()``.

Reached when a subtask was rejected more than ``max_retries`` times, when the plan cannot make
progress, or when planning failed. Ends the task with a precise reason — fail closed.
"""

from __future__ import annotations

from typing import Any

from packages.orchestrator.graph.state import TaskState, event
from packages.orchestrator.tracing.otel import span
from packages.shared.types.review import ReviewVerdict


def _reason(state: TaskState) -> str:
    if state.get("error"):
        return str(state["error"])
    verdicts = {
        k: ReviewVerdict.model_validate(v) for k, v in (state.get("review_verdicts") or {}).items()
    }
    rejected = [
        f"{k} ({v.score}/5: {'; '.join(v.issues)[:120]})"
        for k, v in verdicts.items()
        if not v.accept
    ]
    if rejected:
        return "rejected after retries: " + ", ".join(rejected)
    return "no runnable subtask and the plan is not complete"


async def escalate(state: TaskState) -> dict[str, Any]:
    reason = _reason(state)
    with span("hitl.interrupt", task_id=state["task_id"], level="L4", trigger="repeated_failure"):
        return {
            "status": "failed",
            "error": f"escalation required (human take-over arrives in Phase 4): {reason}",
            "events": [event("escalated", reason, node="escalate", level="L4")],
        }
