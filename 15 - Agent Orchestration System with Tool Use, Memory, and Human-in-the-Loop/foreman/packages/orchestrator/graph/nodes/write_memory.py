"""Phase 2 stub — Phase 5 extracts lessons and writes them to ChromaDB (Architecture.md §7.3)."""

from __future__ import annotations

from typing import Any

from packages.orchestrator.graph.state import TaskState, event
from packages.orchestrator.tracing.otel import span


async def write_memory(state: TaskState) -> dict[str, Any]:
    with span("memory.write", task_id=state["task_id"], count=0):
        return {
            "events": [
                event("memory", "long-term memory write arrives in Phase 5", node="write_memory")
            ]
        }
