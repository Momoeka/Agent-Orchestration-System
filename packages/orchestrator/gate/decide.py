"""The permission gate (Architecture.md §6.3). Every tool call passes through `Gate.decide` before the
registry may invoke it. Fail closed: anything not positively allowed is blocked or sent for approval.

Phase 1 implements the rule-based steps. Phase 3 adds the LLM classifier for `risky` tools (which
today always resolves to `approve`) and a Redis-backed limiter.
"""

from __future__ import annotations

import time

from jsonschema import Draft202012Validator
from jsonschema import ValidationError as JsonSchemaError

from packages.orchestrator.tracing.otel import gate_span
from packages.shared.types.gate import Decision, GateAction
from packages.shared.types.tools import RiskClass, ToolCall
from packages.tools.registry.ratelimit import RateLimiter
from packages.tools.registry.registry import ToolRegistry


class Gate:
    def __init__(self, registry: ToolRegistry, limiter: RateLimiter | None = None) -> None:
        self._registry = registry
        self._limiter = limiter or RateLimiter()

    def decide(self, agent: str, call: ToolCall) -> Decision:
        started = time.perf_counter()
        with gate_span(tool=call.name, agent=agent) as span:
            decision = self._decide(agent, call)
            decision.latency_ms = int((time.perf_counter() - started) * 1000)
            span.set_attribute("decision", decision.action.value)
            span.set_attribute("reason", decision.reason[:200])
            if decision.risk is not None:
                span.set_attribute("risk", decision.risk.value)
            return decision

    def _decide(self, agent: str, call: ToolCall) -> Decision:
        spec = self._registry.get(call.name)
        if spec is None:
            return Decision(action=GateAction.BLOCK, reason=f"unknown tool '{call.name}'")
        if agent not in spec.agents:
            return Decision(
                action=GateAction.BLOCK,
                reason=f"tool '{call.name}' is not allowed for agent '{agent}'",
                risk=spec.risk,
            )
        if call.parse_error:
            return Decision(action=GateAction.BLOCK, reason=call.parse_error, risk=spec.risk)
        try:
            Draft202012Validator(spec.input_schema).validate(call.arguments)
        except JsonSchemaError as e:
            return Decision(
                action=GateAction.BLOCK,
                reason=f"arguments do not match the tool schema: {e.message}",
                risk=spec.risk,
            )
        if not self._limiter.allow(f"{agent}:{call.name}", spec.rate_per_min):
            return Decision(
                action=GateAction.BLOCK,
                reason=f"rate limit of {spec.rate_per_min}/min reached for '{call.name}'; retry later",
                risk=spec.risk,
            )
        if spec.risk == RiskClass.SAFE:
            return Decision(action=GateAction.ALLOW, reason="safe tool", risk=spec.risk)
        if spec.risk == RiskClass.DESTRUCTIVE:
            return Decision(
                action=GateAction.APPROVE,
                reason="destructive tool always requires human approval",
                risk=spec.risk,
            )
        # risky: the classifier arrives in Phase 3; until then, fail closed.
        return Decision(
            action=GateAction.APPROVE, reason="risky tool; human approval required", risk=spec.risk
        )
