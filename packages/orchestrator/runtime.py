"""Assemble the production GraphDeps from settings: LLM chains, registry, gate, store, agents."""

from __future__ import annotations

from packages.orchestrator.agents.catalog import build_specialists
from packages.orchestrator.agents.reviewer.agent import build_reviewer_agent
from packages.orchestrator.agents.supervisor.agent import build_supervisor_agent, synthesize_prompt
from packages.orchestrator.gate.decide import Gate
from packages.orchestrator.graph.deps import GraphConfig, GraphDeps
from packages.orchestrator.llm.chains import ChainedLLM
from packages.orchestrator.llm.client import ChatLLM
from packages.orchestrator.llm.providers import ProviderPool
from packages.orchestrator.llm.roles import load_models_config
from packages.orchestrator.loop.budgets import Budget
from packages.orchestrator.memory.db import make_engine, make_session_factory
from packages.orchestrator.memory.persistent import TaskStore
from packages.shared.config import Settings
from packages.tools.registry.registry import ToolRegistry


def make_store(settings: Settings) -> TaskStore:
    return TaskStore(make_session_factory(make_engine(settings.database_url)))


def make_llm_factory(settings: Settings):  # type: ignore[no-untyped-def]
    config = load_models_config(
        settings.models_config_path, enable_paid=settings.enable_paid_providers
    )
    pool = ProviderPool(settings, config)
    chains: dict[str, ChatLLM] = {}

    def llm_for(role: str) -> ChatLLM:
        if role not in chains:
            chains[role] = ChainedLLM(
                role, config.role(role), pool, prices=config.list_prices_usd_per_mtok
            )
        return chains[role]

    return llm_for


async def build_runtime(settings: Settings, *, store: TaskStore | None = None) -> GraphDeps:
    registry = await ToolRegistry.discover(settings)
    return GraphDeps(
        llm_for=make_llm_factory(settings),
        registry=registry,
        gate=Gate(registry),
        store=store or make_store(settings),
        specialists=build_specialists(),
        supervisor=build_supervisor_agent(),
        synthesize_prompt=synthesize_prompt(),
        reviewer=build_reviewer_agent(),
        config=GraphConfig(
            plan_confidence_threshold=settings.plan_confidence_threshold,
            review_escalate_score=settings.review_escalate_score,
            specialist_budget=Budget(
                max_iterations=settings.max_iterations,
                max_cost_usd=settings.default_task_budget_usd,
            ),
        ),
    )
