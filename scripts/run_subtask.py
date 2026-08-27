"""Run the Research agent on one ad-hoc subtask against the live MCP servers (Phases.md, Phase 1).

    uv run scripts/run_subtask.py "List the loans for claim CLM-4471 and summarise the lender's response"

Requires: the compose stack up, the seed generated, and the database + files MCP servers running
(`make mcp-db` and `make mcp-files` in two terminals, or `make mcp-up`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import structlog

from packages.orchestrator.agents.research.agent import build_research_agent
from packages.orchestrator.gate.decide import Gate
from packages.orchestrator.llm.chains import ChainedLLM
from packages.orchestrator.llm.providers import ProviderPool
from packages.orchestrator.llm.roles import load_models_config
from packages.orchestrator.loop.agent_loop import LoopDeps, run_agent_loop
from packages.orchestrator.loop.budgets import Budget
from packages.orchestrator.tracing.otel import configure_tracing, span
from packages.shared.config import get_settings
from packages.shared.types.subtask import Specialist, Subtask
from packages.tools.registry.registry import ToolRegistry


async def main(text: str, expected: str, max_iterations: int) -> int:
    settings = get_settings()
    provider = configure_tracing(settings)
    config = load_models_config(
        settings.models_config_path, enable_paid=settings.enable_paid_providers
    )
    pool = ProviderPool(settings, config)
    agent = build_research_agent()
    llm = ChainedLLM(
        agent.role, config.role(agent.role), pool, prices=config.list_prices_usd_per_mtok
    )

    registry = await ToolRegistry.discover(settings)
    print(f"tools registered: {registry.names()}", file=sys.stderr)
    if not registry.allowed_for(agent.name):
        print(
            "no tools available to the research agent — are the MCP servers running?",
            file=sys.stderr,
        )
        return 2

    deps = LoopDeps(
        llm=llm,
        registry=registry,
        gate=Gate(registry),
        budget=Budget(max_iterations=max_iterations),
    )
    subtask = Subtask(
        id="adhoc-1", description=text, specialist=Specialist.RESEARCH, expected_output=expected
    )

    with span("task", task_id="adhoc", user_id="cli"):
        result = await run_agent_loop(agent, subtask, deps)
    provider.force_flush()

    print(json.dumps(result.model_dump(exclude={"cost_entries"}), indent=2, default=str))
    print(
        f"\n{len(result.cost_entries)} LLM calls | {result.total_tokens} tokens | "
        f"cost {'$' + format(result.total_cost_usd, '.4f') if result.total_cost_usd is not None else 'n/a (free tier)'} | "
        f"fallback used: {result.fallback_used} | tools: {result.tools_used}",
        file=sys.stderr,
    )
    return 0 if result.status != "failed" else 1


if __name__ == "__main__":
    structlog.configure(processors=[structlog.processors.KeyValueRenderer(key_order=["event"])])
    ap = argparse.ArgumentParser()
    ap.add_argument("text", help="the subtask, in plain language")
    ap.add_argument(
        "--expected", default="A compact, sourced summary.", help="expected output description"
    )
    ap.add_argument("--max-iterations", type=int, default=10)
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.text, args.expected, args.max_iterations)))
