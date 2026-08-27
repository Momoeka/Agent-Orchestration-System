"""The four guards every agent loop must have (Rules.md §2.8): iterations, tokens, cost, wall-clock."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from packages.shared.errors import BudgetExceededError
from packages.shared.types.cost import CostEntry


@dataclass(frozen=True)
class Budget:
    max_iterations: int = 15
    max_total_tokens: int = 150_000
    max_cost_usd: float | None = 1.0
    max_wall_seconds: float = 300.0


@dataclass
class BudgetTracker:
    budget: Budget
    iterations: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    started: float = field(default_factory=time.monotonic)

    def start_iteration(self) -> int:
        self.iterations += 1
        if self.iterations > self.budget.max_iterations:
            raise BudgetExceededError(f"max iterations ({self.budget.max_iterations}) reached")
        self.check_wall()
        return self.iterations

    def record(self, entry: CostEntry) -> None:
        self.total_tokens += entry.input_tokens + entry.output_tokens
        if entry.cost_usd is not None:
            self.total_cost_usd += entry.cost_usd
        if self.total_tokens > self.budget.max_total_tokens:
            raise BudgetExceededError(
                f"token budget exceeded: {self.total_tokens} > {self.budget.max_total_tokens}"
            )
        if self.budget.max_cost_usd is not None and self.total_cost_usd > self.budget.max_cost_usd:
            raise BudgetExceededError(
                f"cost budget exceeded: ${self.total_cost_usd:.4f} > ${self.budget.max_cost_usd:.2f}"
            )

    def check_wall(self) -> None:
        elapsed = time.monotonic() - self.started
        if elapsed > self.budget.max_wall_seconds:
            raise BudgetExceededError(f"wall-clock budget exceeded: {elapsed:.0f}s")
