"""The whole graph with fakes: scripted LLMs per role, an empty tool registry, a SQLite store, and
LangGraph's in-memory checkpointer. Covers the Phase 2 done-when (A → B → C end to end, resume
after a crash) plus retry-with-feedback, escalation, and the fail-closed plan-approval stub."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver

from packages.orchestrator.agents.base import AgentSpec
from packages.orchestrator.gate.decide import Gate
from packages.orchestrator.graph.build_graph import build_graph
from packages.orchestrator.graph.deps import GraphConfig, GraphDeps
from packages.orchestrator.graph.state import initial_state
from packages.orchestrator.memory.db import create_all, make_engine, make_session_factory
from packages.orchestrator.memory.persistent import TaskStore
from packages.shared.types.cost import CostEntry
from packages.shared.types.llm import LLMResponse, Usage
from packages.shared.types.task import TaskOptions
from packages.shared.types.tools import ToolCall
from packages.tools.registry.registry import ToolRegistry

PLAN_ABC: dict[str, Any] = {
    "subtasks": [
        {
            "id": "A",
            "description": "find loans",
            "specialist": "research",
            "depends_on": [],
            "needs": [],
            "expected_output": "table",
            "complexity": "simple",
        },
        {
            "id": "B",
            "description": "check affordability",
            "specialist": "analysis",
            "depends_on": ["A"],
            "needs": ["loans"],
            "expected_output": "findings",
            "complexity": "moderate",
        },
        {
            "id": "C",
            "description": "draft letter",
            "specialist": "writing",
            "depends_on": ["A", "B"],
            "needs": ["findings"],
            "expected_output": "letter",
            "complexity": "moderate",
        },
    ],
    "confidence": 0.9,
    "sensitive_actions": [],
    "rationale": "facts, then analysis, then writing",
}


class FakeChain:
    """A ChatLLM for one role. `handler(messages, schema_name) -> content | ToolCall list`."""

    def __init__(
        self,
        role: str,
        handler: Callable[[list[dict[str, Any]], str], Any],
        log: list[dict[str, Any]],
    ) -> None:
        self.role = role
        self._handler = handler
        self._log = log
        self._on_cost: Callable[[CostEntry], None] | None = None

    def with_cost_sink(self, on_cost: Callable[[CostEntry], None] | None) -> FakeChain:
        clone = FakeChain(self.role, self._handler, self._log)
        clone._on_cost = on_cost
        return clone

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: Any = None,
        response_schema: Any = None,
        schema_name: str = "Response",
        **_: Any,
    ) -> LLMResponse:
        self._log.append(
            {"role": self.role, "schema": schema_name, "messages": json.loads(json.dumps(messages))}
        )
        out = self._handler(messages, schema_name)
        if self._on_cost:
            self._on_cost(
                CostEntry(
                    provider="fake", model="m", role=self.role, input_tokens=3, output_tokens=2
                )
            )
        if isinstance(out, list):
            return LLMResponse(
                content=None,
                tool_calls=out,
                provider="fake",
                model="m",
                usage=Usage(input_tokens=3, output_tokens=2),
            )
        return LLMResponse(
            content=out, provider="fake", model="m", usage=Usage(input_tokens=3, output_tokens=2)
        )


class Scenario:
    """Builds GraphDeps with scripted behaviour and records every model call."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        plan: dict[str, Any] | None = None,
        reviewer_script: Callable[[str, int], dict[str, Any]] | None = None,
        crash_reviewer_once: bool = False,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.plan = plan or PLAN_ABC
        self.reviewer_script = reviewer_script or (
            lambda sid, attempt: {"accept": True, "score": 5, "issues": [], "feedback": ""}
        )
        self.crashes_left = 1 if crash_reviewer_once else 0
        self.specialist_calls: list[tuple[str, str, int]] = []  # (agent, subtask_id, attempt)

        engine = make_engine(f"sqlite:///{tmp_path / 'store.db'}")
        create_all(engine)
        self.store = TaskStore(make_session_factory(engine))
        registry = ToolRegistry.from_listing({}, {"servers": {}, "tools": {}}, {})
        self.deps = GraphDeps(
            llm_for=self.llm_for,
            registry=registry,
            gate=Gate(registry),
            store=self.store,
            specialists={
                n: AgentSpec(name=n, role="specialist", system_prompt=f"you are {n}")
                for n in ("research", "analysis", "writing", "code_exec")
            },
            supervisor=AgentSpec(name="supervisor", role="supervisor", system_prompt="plan"),
            synthesize_prompt="synthesise",
            reviewer=AgentSpec(name="reviewer", role="reviewer", system_prompt="review"),
            config=GraphConfig(plan_confidence_threshold=0.6, max_retries=2),
        )

    # ---- role handlers ----

    def _supervisor(self, messages: list[dict[str, Any]], schema_name: str) -> str:
        if schema_name == "ExecutionPlan":
            return json.dumps(self.plan)
        assert schema_name == "Deliverable"
        body = messages[-1]["content"]
        return json.dumps(
            {"title": "Final", "body": body[-400:], "sources": ["loans"], "confidence": 0.8}
        )

    def _specialist_for(self, agent: str) -> Callable[[list[dict[str, Any]], str], Any]:
        def handler(messages: list[dict[str, Any]], schema_name: str) -> Any:
            user = messages[1]["content"]
            sid = user.split("## Subtask ", 1)[1].split("\n", 1)[0].strip()
            attempt = 1 + sum(1 for a, s, _ in self.specialist_calls if a == agent and s == sid)
            self.specialist_calls.append((agent, sid, attempt))
            pred = (
                "with " + ", ".join(sorted(k for k in self._pred_keys(user)))
                if "predecessor_outputs" in user
                else "alone"
            )
            return [
                ToolCall(
                    id=f"sub-{sid}-{attempt}",
                    name="submit_result",
                    arguments={
                        "status": "completed",
                        "output": f"out {sid} ({pred})",
                        "sources": ["loans"],
                        "self_confidence": 0.9,
                        "notes": "",
                    },
                )
            ]

        return handler

    @staticmethod
    def _pred_keys(user_text: str) -> list[str]:
        block = user_text.split("```json", 1)[1].split("```", 1)[0]
        return list(json.loads(block).get("predecessor_outputs", {}).keys())

    def _reviewer(self, messages: list[dict[str, Any]], schema_name: str) -> str:
        if self.crashes_left:
            self.crashes_left -= 1
            raise RuntimeError("simulated worker crash")
        payload = json.loads(
            messages[-1]["content"]
            .split("## Specialist result\n```json\n", 1)[1]
            .split("```", 1)[0]
        )
        return json.dumps(self.reviewer_script(payload["subtask_id"], payload["attempt"]))

    def llm_for(self, role: str) -> FakeChain:
        if role == "supervisor":
            return FakeChain(role, self._supervisor, self.calls)
        if role == "reviewer":
            return FakeChain(role, self._reviewer, self.calls)
        return FakeChain(role, self._dispatch_specialist, self.calls)

    def _dispatch_specialist(self, messages: list[dict[str, Any]], schema_name: str) -> Any:
        agent = messages[0]["content"].removeprefix("you are ")
        return self._specialist_for(agent)(messages, schema_name)

    # ---- running ----

    async def run(
        self,
        request: str = "summarise and draft",
        *,
        checkpointer: Any = None,
        resume: bool = False,
        thread: str | None = None,
        options: TaskOptions | None = None,
    ) -> tuple[dict[str, Any], str]:
        saver = checkpointer or MemorySaver()
        graph = build_graph(self.deps, checkpointer=saver)
        row = (
            self.store.create_task(user_id="u1", request=request, options=options or TaskOptions())
            if not thread
            else None
        )
        task_id = thread or row.id  # type: ignore[union-attr]
        config = {"configurable": {"thread_id": task_id}}
        state = None if resume else initial_state(task_id, "u1", request, options or TaskOptions())
        final = await graph.ainvoke(state, config)
        return final, task_id


async def test_three_step_plan_runs_to_a_deliverable(tmp_path: Path) -> None:
    sc = Scenario(tmp_path)
    final, task_id = await sc.run()

    assert final["status"] == "done", final.get("error")
    assert final["final_output"].title == "Final"
    assert [c[1] for c in sc.specialist_calls] == ["A", "B", "C"]
    assert final["subtask_results"]["B"].output == "out B (with A)"
    assert final["subtask_results"]["C"].output == "out C (with A, B)"
    assert all(v.accept for v in final["review_verdicts"].values())
    kinds = [e.kind for e in final["events"]]
    for kind in (
        "started",
        "planned",
        "dispatch",
        "subtask_done",
        "reviewed",
        "synthesized",
        "delivered",
    ):
        assert kind in kinds
    view = sc.store.task_view(task_id)
    assert (
        view is not None and view["status"] == "done" and view["final_output"]["title"] == "Final"
    )
    assert {s["id"]: s["status"] for s in view["subtasks"]} == {
        "A": "accepted",
        "B": "accepted",
        "C": "accepted",
    }
    assert (
        view["llm_calls"] == len(final["cost_ledger"]) == 1 + 3 + 3 + 1
    )  # plan + 3 specialists + 3 reviews + synth


async def test_independent_subtasks_run_before_their_dependent(tmp_path: Path) -> None:
    plan = json.loads(json.dumps(PLAN_ABC))
    plan["subtasks"][1]["depends_on"] = []  # A and B independent; C needs both
    sc = Scenario(tmp_path, plan=plan)
    final, _ = await sc.run()
    assert final["status"] == "done"
    order = [c[1] for c in sc.specialist_calls]
    assert set(order[:2]) == {"A", "B"} and order[2] == "C"
    assert final["subtask_results"]["C"].output == "out C (with A, B)"


async def test_rejected_subtask_is_retried_with_feedback(tmp_path: Path) -> None:
    def reviewer(sid: str, attempt: int) -> dict[str, Any]:
        if sid == "A" and attempt == 1:
            return {
                "accept": False,
                "score": 2,
                "issues": ["no sources"],
                "feedback": "add the loan table as a source",
            }
        return {"accept": True, "score": 5, "issues": [], "feedback": ""}

    sc = Scenario(tmp_path, reviewer_script=reviewer)
    final, _ = await sc.run()
    assert final["status"] == "done"
    assert [c for c in sc.specialist_calls if c[1] == "A"] == [
        ("research", "A", 1),
        ("research", "A", 2),
    ]
    second_call = [
        c
        for c in sc.calls
        if c["role"] == "specialist" and "## Subtask A" in c["messages"][1]["content"]
    ][1]
    assert "reviewer_feedback" in second_call["messages"][1]["content"]
    assert "add the loan table" in second_call["messages"][1]["content"]
    assert final["retry_counts"] == {"A": 1}
    assert final["subtask_results"]["A"].attempt == 2 and final["review_verdicts"]["A"].attempt == 2


async def test_escalates_after_max_retries(tmp_path: Path) -> None:
    sc = Scenario(
        tmp_path,
        reviewer_script=lambda sid, attempt: {
            "accept": False,
            "score": 1,
            "issues": ["wrong"],
            "feedback": "redo",
        },
    )
    final, task_id = await sc.run()
    assert final["status"] == "failed"
    assert "escalation required" in final["error"]
    assert [c[2] for c in sc.specialist_calls if c[1] == "A"] == [1, 2, 3]
    assert [c[1] for c in sc.specialist_calls].count("B") == 0  # never dispatched
    assert sc.store.task_view(task_id)["status"] == "failed"  # type: ignore[index]


async def test_low_confidence_plan_fails_closed_until_hitl_exists(tmp_path: Path) -> None:
    plan = {**PLAN_ABC, "confidence": 0.2}
    sc = Scenario(tmp_path, plan=plan)
    final, task_id = await sc.run()
    assert final["status"] == "failed" and "human approval" in final["error"]
    assert sc.specialist_calls == []
    assert sc.store.task_view(task_id)["error"] == final["error"]  # type: ignore[index]


async def test_require_human_review_option_routes_to_approval(tmp_path: Path) -> None:
    sc = Scenario(tmp_path)
    final, _ = await sc.run(options=TaskOptions(require_human_review=True))
    assert final["status"] == "failed" and "human approval" in final["error"]


async def test_crash_mid_task_then_resume_from_checkpoint(tmp_path: Path) -> None:
    sc = Scenario(tmp_path, crash_reviewer_once=True)
    saver = MemorySaver()
    with pytest.raises(RuntimeError, match="simulated worker crash"):
        await sc.run(checkpointer=saver)
    # The task row created in the failed run is the only one in the store.
    with sc.store._sessions() as s:  # noqa: SLF001 — test-only peek
        from packages.orchestrator.memory.persistent import TaskRow

        task_id = s.query(TaskRow).one().id
    assert [c[1] for c in sc.specialist_calls] == ["A"]  # A ran once before the crash

    final, _ = await sc.run(checkpointer=saver, resume=True, thread=task_id)
    assert final["status"] == "done", final.get("error")
    assert [c[1] for c in sc.specialist_calls] == ["A", "B", "C"]  # A was NOT re-run
    assert sc.store.task_view(task_id)["status"] == "done"  # type: ignore[index]


async def test_plan_is_persisted_as_soon_as_it_exists(tmp_path: Path) -> None:
    sc = Scenario(tmp_path)
    final, task_id = await sc.run()
    view = sc.store.task_view(task_id)
    assert view is not None and view["plan"]["confidence"] == 0.9
    assert [s["id"] for s in view["subtasks"]] == ["A", "B", "C"]
    assert {s["specialist"] for s in view["subtasks"]} == {"research", "analysis", "writing"}
