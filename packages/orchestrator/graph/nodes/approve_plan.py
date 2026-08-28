"""Phase 2 stub for the L3 escalation point. Phase 4 replaces the body with ``interrupt()``.

Until a human can approve a plan, a plan that needs approval ends the task — fail closed rather
than run a low-confidence or sensitive plan unattended (Rules.md §2.2).
"""

from __future__ import annotations

from typing import Any

from packages.orchestrator.graph.state import TaskState, event
from packages.orchestrator.tracing.otel import span


async def approve_plan(state: TaskState) -> dict[str, Any]:
    with span("hitl.interrupt", task_id=state["task_id"], level="L3", trigger="plan"):
        reason = (
            "plan requires human approval "
            f"(confidence {state.get('plan_confidence', 0.0):.2f}); "
            "the approval flow arrives in Phase 4"
        )
        return {
            "status": "failed",
            "error": reason,
            "events": [event("escalated", reason, node="approve_plan", level="L3")],
        }
