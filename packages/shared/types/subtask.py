from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from packages.shared.types.cost import CostEntry


class Specialist(StrEnum):
    RESEARCH = "research"
    ANALYSIS = "analysis"
    WRITING = "writing"
    CODE_EXEC = "code_exec"


class Complexity(StrEnum):
    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"


class SubtaskStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class Subtask(BaseModel):
    """One unit of delegated work, as produced by the supervisor's plan."""

    id: str
    description: str
    specialist: Specialist
    depends_on: list[str] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(default_factory=dict)
    expected_output: str = ""
    complexity: Complexity = Complexity.MODERATE


class SubmittedResult(BaseModel):
    """What the specialist model fills in when it calls ``submit_result`` to finish."""

    status: SubtaskStatus
    output: str = Field(description="The deliverable for this subtask, in full.")
    sources: list[str] = Field(
        default_factory=list,
        description="Where the facts came from: file paths, table names, URLs. Empty if none.",
    )
    self_confidence: float = Field(ge=0.0, le=1.0, description="0 = guessing, 1 = certain.")
    notes: str = Field(
        default="", description="Caveats, gaps, or anything the reviewer should know."
    )


class SubtaskResult(SubmittedResult):
    """``SubmittedResult`` plus what the loop knows: tools used, iterations, cost, errors."""

    subtask_id: str
    tools_used: list[str] = Field(default_factory=list)
    iterations: int = 0
    cost_entries: list[CostEntry] = Field(default_factory=list)
    fallback_used: bool = False
    error: str | None = None

    @property
    def total_cost_usd(self) -> float | None:
        known = [c.cost_usd for c in self.cost_entries if c.cost_usd is not None]
        return sum(known) if known else None

    @property
    def total_tokens(self) -> int:
        return sum(c.input_tokens + c.output_tokens for c in self.cost_entries)
