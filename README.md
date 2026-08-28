# Foreman

An agent orchestration system: a supervisor plans, specialist agents act through gated MCP tools, a reviewer checks every result, three tiers of memory make it improve over time, and a human is pulled in whenever confidence is low or an action is irreversible. Every decision is traced and replayable.

**Status:** Day 0 — skeleton, infrastructure, and provider smoke tests. See `docs/Phases.md`.

## Documents

| Doc | Purpose |
|---|---|
| `docs/PRD.md` | What is being built, for whom, and how success is measured |
| `docs/Architecture.md` | The build specification: graph, agents, tools, memory, HITL, tracing, API, layout |
| `docs/Rules.md` | Boundaries for anyone (human or AI) writing code here |
| `docs/Phases.md` | The 14-day plan with "done when" lines |
| `docs/Design.md` | Operator UI visual spec |
| `docs/Memory.md` | Running log across coding sessions — read first, update last |
| `docs/diagrams/` | Architecture, graph, agent loop, memory, HITL, and one-task sequence diagrams |

## Quick start (Day 0)

```bash
cp .env.example .env            # then fill in the free-provider keys
make sync                       # uv creates .venv with Python 3.12 and installs deps
make up                         # redis, postgres, chroma, ollama, jaeger
make ollama-pull                # nomic-embed-text (embeddings) + qwen3:8b (offline dev)
make smoke-all                  # every chat model in config/models.yaml: chat / tool call / JSON schema
```

`make` is not installed by default on Windows. Either `winget install ezwinports.make`, or run the equivalents directly:
`uv sync --dev` · `docker compose -f infra/docker-compose.yml up -d` · `uv run scripts/smoke_provider.py --all`.

Jaeger trace UI: http://localhost:16686

## Phase 1 — run the research agent on one subtask

```bash
uv run python -m infra.seed.generate                       # 200 synthetic claims + documents under data/workspace
MCP_PORT=7004 uv run python -m packages.tools.mcp_servers.database   # terminal 1
MCP_PORT=7002 uv run python -m packages.tools.mcp_servers.files      # terminal 2
uv run scripts/run_subtask.py "For claim CLM-4471: list every loan from the database, then read claims/CLM-4471/lender_response.md and summarise the lender's decision."
```

Then open Jaeger and look at the newest `foreman` trace: `task → agent.research → iterations → llm.call / gate.decide / tool.*`.

Checks: `uv run pytest` (unit) · `uv run ruff check .` · `uv run mypy` · `make test-live` (against the running stack and live models).

## Phase 2 — run a whole task through the graph

```bash
uv run alembic upgrade head                                            # tier-2 tables (tasks, subtasks, llm_calls, audit_log)
uv run celery -A packages.orchestrator.worker worker --pool=solo -l info   # terminal 3
uv run uvicorn apps.api.main:create_app --factory --port 8000             # terminal 4

curl -s -X POST http://localhost:8000/v1/tasks -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"request": "Summarise the lender documents for claim CLM-4471 and draft the complaint letter. Cite the rules in ruleset.md.", "user_id": "u_42"}'
curl -s http://localhost:8000/v1/tasks/<task_id> -H "X-API-Key: $API_KEY"   # plan, subtasks with verdicts, deliverable, cost
```

Or in-process without Celery: `uv run scripts/run_task.py "<request>"`.

## Phase 3 — the full tool layer and the gate

```bash
make sandbox-image                                                      # once: the image run_python executes in
MCP_PORT=7001 uv run python -m packages.tools.mcp_servers.web_search      # search (fixture backend) + SSRF-guarded fetch
MCP_PORT=7003 uv run python -m packages.tools.mcp_servers.sandbox         # run_python in a hardened throwaway container
MCP_PORT=7005 uv run python -m packages.tools.mcp_servers.actions         # send_email / calendar / call_api -> outbox only
uv run pytest -q -m integration tests/integration/test_gate_live.py       # the Phase 3 done-when, live
```

Every tool call goes through the gate before the registry may run it: unknown tool, wrong agent, bad
arguments, or rate limit → **block**; `safe` → allow; `destructive` → **a human, always**; `risky` → a
small LLM classifier decides whether a human needs to look, and any failure of that classifier means
**approve**. Each decision is recorded in `tool_invocations` (arguments hashed, never stored). The
actions server cannot send anything: its only effect is a row in `outbox`.

The graph (docs/diagrams/02): intake → recall_memory → plan → dispatch → specialists (parallel `Send()` per ready subtask) → review → retry / dispatch dependents / synthesize → deliver → write_memory. State is checkpointed in Postgres after every node, so a worker that dies mid-task resumes from the last checkpoint on the next run (`tests/integration/test_graph_postgres_resume.py`).

## Principles

- **$0 by default.** All model roles run on free tiers with per-role fallback chains (`config/models.yaml`). Paid providers are optional and disabled.
- **One path to a tool.** Agents reach tools only through the permission gate → registry. Fail closed.
- **Typed hand-offs.** Plans, results, verdicts, and memories are Pydantic models.
- **Humans decide the irreversible.** Destructive actions always pause for approval; timeouts never approve.
- **Synthetic data only.**
