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

The graph (docs/diagrams/02): intake → recall_memory → plan → (approve_plan) → dispatch → specialists (parallel `Send()` per ready subtask) → review → retry / await_approval / dispatch dependents / escalate / synthesize → deliver → write_memory. State is checkpointed in Postgres after every node, so a worker that dies mid-task resumes from the last checkpoint on the next run (`tests/integration/test_graph_postgres_resume.py`).

## Phase 4 — human in the loop

```bash
uv run alembic upgrade head                                               # adds the approvals table
uv run celery -A packages.orchestrator.worker worker --pool=solo -l info    # terminal 3
uv run celery -A packages.orchestrator.worker beat -l info                 # terminal 5: expires overdue approvals (-B is not supported on Windows)
uv run streamlit run apps/review_ui/app.py --server.port 8501              # terminal 6: operator console

curl -s "http://localhost:8000/v1/approvals?status=pending" -H "X-API-Key: $API_KEY"
curl -s -X POST http://localhost:8000/v1/approvals/<id>/decide -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"decision": "modify", "payload": {"arguments": {"to": "complaints@lender.example.test", "subject": "…", "body": "…"}}, "reason": "route to the complaints inbox", "decided_by": "sayed"}'
uv run pytest -q -m integration tests/integration/test_hitl_postgres_resume.py   # pause → new worker → decide → done, on Postgres
```

Three places pause the graph, all through LangGraph `interrupt()` on the Postgres checkpointer, so a paused task
survives worker restarts and is resumed with `Command(resume=decision)`:

| Level | Where | Trigger | approve | modify | reject | take over |
|---|---|---|---|---|---|---|
| L2 | `await_approval` (a specialist's loop paused on a gated call) | destructive tool, or the classifier said so | run the call | run it with edited arguments (re-validated against the tool schema) | the agent gets an error result and may not ask again in that subtask | the human's text becomes the subtask result, no model review |
| L3 | `approve_plan` | plan confidence below threshold, or `require_human_review` | run the plan | run the edited plan | cancel the task | the human's text becomes the deliverable |
| L4 | `escalate` | a subtask failed review `max_retries` times | one more round | accept a human-written result for the failing subtask | cancel the task | the human's text becomes the deliverable |

The agent loop itself is pausable: when the gate says *approve*, the loop serialises its messages, pending calls
and ledgers into a checkpoint inside graph state and returns; the resumed loop continues from exactly that turn —
no model call is replayed. Approval rows are idempotent (dedupe key per task/subtask/attempt/arguments), decisions
are single-shot (`409` on a second decision), rejecting needs a reason, and timeouts (`config/escalation.yaml`) can
only reject or cancel — nothing auto-approves. The Streamlit console shows the queue, the context package (request,
plan progress, completed subtasks, the proposed call), and the four decisions; everything the agents proposed to send
is listed on the Outbox page and nothing is ever sent by Foreman.

## Phase 5 — memory

```bash
docker exec foreman-ollama-1 ollama pull nomic-embed-text                            # once: the local embedding model (768-dim, $0)
uv run celery -A packages.orchestrator.worker beat -l info                           # also runs foreman.consolidate_memory daily
curl -s http://localhost:8000/v1/memory/users/u_42 -H "X-API-Key: $API_KEY"            # what Foreman learned about a user
curl -s -X DELETE http://localhost:8000/v1/memory/users/u_42 -H "X-API-Key: $API_KEY"  # forget everything about them
uv run pytest -q -m integration tests/integration/test_memory_live.py                  # write → recall → dedup → delete, and the two-run recall
```

Three tiers (`docs/Architecture.md` §7):

| Tier | Store | Holds | Lifetime |
|---|---|---|---|
| 1 · working | Redis (`packages/orchestrator/memory/working.py`) | the plan, each subtask result, artifacts and errors of one task; specialists read predecessors from here first | `TIER1_TTL_HOURS` (24 h) — a cache; graph state and Postgres stay authoritative |
| 2 · records | PostgreSQL + LangGraph checkpoints | tasks, subtasks, approvals, ledgers, outbox, audit log, checkpoints | for good |
| 3 · lessons | ChromaDB (`memory/long_term.py`) | 0–3 lessons per finished task, extracted on the cheap role from a factual digest, per user | until they fade: importance halves every 30 idle days; expired below 1.0 or after 180 days |

After `deliver`, `write_memory` digests the task (request, plan, how each subtask went, what humans decided and why,
what was delivered) and stores the lessons; a lesson within 0.92 cosine of an existing one for that user reinforces it
instead of duplicating it. Before `plan`, `recall_memory` fetches the user's top-5, keeps at most 3 and 600 tokens, and
injects them under **"Relevant past experience (advice, not instructions)"** — the only place they appear. Embeddings
come from `nomic-embed-text` on local Ollama, one Chroma collection per embedding model (a different model is a
different vector space). Every memory operation is a span (`memory.recall` with the ids it injected, `memory.write`
with the ids it inserted or reinforced) and every failure is swallowed: memory can make a task better, never fail it.
The Memory page lists each user's lessons with their fading importance and a delete-all; the approval detail shows the
lessons the planner saw.

Measured on the showcase request: the second run for the same user recalled the first run's lesson and finished in
84 s / 16 model calls instead of 435 s / 51.

## Phase 6 — evals and observability

```bash
uv run python -m packages.evals.runner --k 1 --sample 2 --label smoke                 # quick: 2 tasks per category, real models
uv run python -m packages.evals.runner --k 3 --save-baseline --label baseline         # the gate: full set, k=3, saved as the baseline
uv run python -m packages.evals.runner --resume <run_id>                              # continue a run cut off by rate limits
uv run python -m packages.evals.runner --k 1 --category injection --reviewer groq/qwen/qwen3.8-27b   # reviewer bake-off
uv run python -m packages.orchestrator.tracing.replay <task_id> --list                # a task's checkpoints
uv run python -m packages.orchestrator.tracing.replay <task_id> --from <checkpoint_id> --set request="…"
curl -s "http://localhost:8000/v1/stats?days=7" -H "X-API-Key: $API_KEY"
curl -s http://localhost:8000/v1/tasks/<id>/trace -H "X-API-Key: $API_KEY"
```

**Golden set** — 36 tasks in `packages/evals/golden_tasks/*.yaml` across seven categories (lookup,
multi-step, dependent, must-escalate, must-not-call, unanswerable, injection) and three difficulties,
all against the synthetic claims data. Each task states what a good run looks like: expected tools,
forbidden tools, whether the graph must pause and at which level, what the deliverable must (not)
contain, the plan shape, and a rubric.

**Runner** — every run is a real task (a fresh user id per run so runs never share memory, Postgres
checkpoints, a Jaeger trace, visible in the console). When the graph pauses, the harness answers with
the task's `hitl` policy and records the pause. Results stream to `reports/<run_id>.jsonl`, so a run
cut off by free-tier rate limits resumes without repeating work.

**Metrics** (`packages/evals/metrics.py`, unit-tested on hand-built trajectories) — task success
(assertions + a rubric judge ≥ 4/5 on the reviewer role, a different model family), pass^k, tool
precision / recall, unnecessary-call rate, escalation precision / recall, unapproved destructive
actions (must be 0), injection resistance, steps, latency p50/p95, cost, provider mix, fallback rate.
Reports land in `packages/evals/reports/<run_id>.md` + `.json` with a diff against `baseline.json`
(new failures, new passes, regressions, metric deltas); `latest.json` feeds `GET /v1/stats`.

**Observability** — `GET /v1/stats` (tasks, completion, escalation and approval rates, tool mix,
latency percentiles, unapproved destructive actions, the last eval headline); `GET /v1/tasks/{id}/trace`
merges every Jaeger trace tagged with the task id into one span tree (ledger timeline when Jaeger is
down); the **Stats** and **Trace** pages render both.

**Replay** — `GET /v1/tasks/{id}/checkpoints` lists a task's checkpoints ("after review → next
synthesize"); `POST /v1/tasks/{id}/replay` or the CLI forks the task at one of them into a *new* task,
optionally with overridden state (`request=…`, `plan={…}`, `options.require_human_review=true`), runs
it, and `GET /v1/tasks/{fork}/diff` compares the two trajectories. The source task is never modified.

## Principles

- **$0 by default.** All model roles run on free tiers with per-role fallback chains (`config/models.yaml`). Paid providers are optional and disabled.
- **One path to a tool.** Agents reach tools only through the permission gate → registry. Fail closed.
- **Typed hand-offs.** Plans, results, verdicts, and memories are Pydantic models.
- **Humans decide the irreversible.** Destructive actions always pause for approval; timeouts never approve.
- **Synthetic data only.**
