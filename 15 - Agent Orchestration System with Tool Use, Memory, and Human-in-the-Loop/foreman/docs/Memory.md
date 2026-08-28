# Memory — Foreman

Running log across coding sessions. **Read this first; update it last** (Rules.md §9). Newest entry at the top.

---

## 2026-08-28 — Phase 4: human-in-the-loop — DONE

### Built
- **Types** `packages/shared/types/approval.py`: `ApprovalLevel L1–L4`, `ApprovalKind tool_call|plan|escalation`, `ApprovalTrigger`, `ApprovalStatus pending → approved|modified|rejected|taken_over|expired`, `DecisionKind approve|modify|reject|take_over`, `ApprovalRequest` (the context package), `ApprovalDecision`.
- **Pausable agent loop** (`loop/agent_loop.py`): `run_agent_loop` returns `SubtaskResult | PausedLoop`. On a gate `approve` the loop serialises messages, pending calls, ready results, ledgers and budgets into a JSON-safe `LoopCheckpoint` and returns; `resume=LoopResume(checkpoint, decision)` re-enters at that exact turn — approve executes, modify re-validates the edited arguments against the tool schema then executes, reject returns an error tool result, take over becomes the result (`human_authored=True`). Several gated calls in one turn are decided one at a time. **A rejection is final:** `denied` (tool → reason) lives in the checkpoint and in `SubtaskResult.denied_tools`; `send_for` seeds every retry of that subtask with it; a repeated call is blocked before the gate ("a human already rejected …").
- **Graph**: state gains `pending_approvals` / `approval_decisions` (merge_dicts, `None` = cleared) and `SpecialistInput.resume` / `.denied`; nodes `await_approval` (L2), `approve_plan` (L3: approve · modify → validated plan, `set_plan` drops removed subtasks · reject → cancelled · take over → human deliverable), `escalate` (L4: approve → retry counts reset · modify → human `SubtaskResult` + verdict · take over · reject → cancelled); `review` accepts `human_authored` results without a model call and gets a `## Human decisions` section when `denied_tools` is set; `deliver` writes `cancelled`. Every interrupt node first calls `approvals.get_or_create` (dedupe keys `task:tool_call:sid:attempt:args_hash`, `task:plan:<sha16>`, `task:escalation:<n>`) so a replayed node never creates a second row, then `interrupt(request)`.
- **HITL package** `orchestrator/hitl/`: `escalation.py` (`config/escalation.yaml` → level, deadline, timeout decision; `on_timeout` may only be reject/cancel — validated), `approvals.py` (context package: request, plan with per-subtask status, completed subtasks with previews, the proposed call / plan / escalation options), `notify.py` (log + optional Slack webhook), `timeouts.py` (`expire_due` → `expired` with the timeout decision, returns the (task, approval) pairs to resume).
- **Persistence**: `approvals` table (Alembic `79e9c30b5c79`), `ApprovalStore` (`get_or_create`, atomic `record_decision` pending→decided, `decision_of`, `due`, `list`, `view`), `TaskStore.record_progress` (subtask rows + ledgers replaced from graph state when a task pauses, so the task view is truthful while people decide), `task_view` carries approvals + `pending_approval_id`; `tool_calls_not_executed` counts `ok IS NULL`.
- **Checkpoint serializer** `graph/serde.py`: explicit `allowed_msgpack_modules` allowlist of every model/enum that lands in state (LangGraph warns it will block unregistered types); used by the worker's `AsyncPostgresSaver` and every test saver.
- **Worker**: `execute_task(task_id, resume=decision)` → `Command(resume=…)`; on `__interrupt__` → `record_progress` + `awaiting_approval`; Celery tasks `foreman.resume_task(task_id, approval_id)` and beat `foreman.expire_approvals` (every 60 s; `make beat` — `celery worker -B` is refused on Windows).
- **API**: `GET /v1/approvals?status=`, `GET /v1/approvals/{id}`, `POST /v1/approvals/{id}/decide` (reject without a reason → 422; a second decision → 409; enqueues the resume), `GET /v1/outbox`.
- **Operator UI** `apps/review_ui/` (Streamlit, Design.md tokens): home metrics; Approvals — queue, context package, the four decisions with editable arguments / plan / take-over text, operator name recorded on the decision; Tasks — submit, plan, subtasks with verdicts, approvals, deliverable; Outbox. `make ui`.
- **Prompts**: supervisor / writing / reviewer / synthesis no longer say "never send". When the request names a recipient the plan includes the send step, the writing agent calls `actions_send_email` after writing the letter (the gate pauses it), and the reviewer accepts a `queued_for_human` result or a human-rejected call.

### Verified
- Unit: **167 passed** — the decision matrix through the whole graph with fakes (`test_hitl_flow.py`: L2 approve / modify / invalid modify / reject / take over / worker restart / rejection survives a review retry / timeout decision; L3 approve / modify / invalid modify / reject / take over; L4 approve / modify / take over / reject), loop pause-resume (`test_loop_pause_resume.py`), policy + store + timeouts (`test_hitl_policy.py`), API (`test_api_approvals.py`), the Streamlit pages headlessly via `AppTest` (`test_review_ui.py`). `mypy --strict` clean (135 files), ruff clean.
- Integration on Postgres (`test_hitl_postgres_resume.py`, `test_graph_postgres_resume.py`): pause → fresh `AsyncPostgresSaver` + graph → decide → done, for approve and reject. 3 passed.
- **Live** (free tiers, $0; API + worker + beat + Streamlit + the 5 MCP servers), request "Review claim CLM-4471: list its loans …, draft a complaint letter to the lender, and send the letter by email to lender@example.test":
  - the plan gained a fourth subtask (writing: send); every task paused at L2 on `actions_send_email` with the full letter in the arguments (150–300 s to the pause, 26–33 LLM calls per task).
  - **approve** — worker killed while paused, a new worker started, decision via the API → resumed in 26 s (only D's remaining turn + review + synthesis; nothing replayed); outbox row `queued_for_human`; ledger `approve / ok`.
  - **modify** — recipient and subject edited → the outbox row carries the edited values; ledger reason "modify by sayed: …".
  - **reject** — nothing queued; the agent finished with "drafted but not sent"; the reviewer accepted it; `denied_tools` stored on the subtask.
  - **take over** — the human's text became D's result (`human_authored`), accepted without a model review; deliverable done; ledger "taken over by sayed", `ok` NULL.
  - **L3** (`require_human_review`) — paused at the plan with 1 LLM call already visible in the task view; approve → ran; the later L2 reject → done.
  - Beat's `expire_approvals` runs every minute (nothing due — deadlines are 24/48 h).

### Findings fixed during the live run
1. The Phase 2 prompts told the supervisor to plan a DRAFT and the writer never to send, so the first showcase task completed without ever proposing the email. The gate, not the plan, now decides whether an action happens.
2. After a rejection the agent asked again in the same loop; and once the reviewer rejected the "not sent" result, the fresh retry loop asked a third time. Fixed with the per-subtask denial list (checkpoint → result → retry seed) and the reviewer's `## Human decisions` section. Live: task 4 ended after three rejections with no fourth request; task 6 after two.
3. `celery worker -B` does not work on Windows → beat is a separate process.
4. LangGraph "Deserializing unregistered type … will be blocked in a future version" on every resume → `graph/serde.py`.
5. While paused the task view showed `planned` / 0 calls → `record_progress` on interrupt.

### Decisions
- L2 is a **pausable loop + `await_approval` node**, not `interrupt()` inside the loop: LangGraph re-executes a node on resume, which would replay the loop's model calls. The checkpoint is plain JSON in graph state.
- Timeouts never approve (validated in `EscalationPolicy`); reject requires a reason; decisions are single-shot; approval rows are idempotent by dedupe key.
- Take over at L2 replaces the *subtask* result; at L3/L4 it replaces the *deliverable*. Human results skip the model reviewer (`reviewer_model="human"`).
- Slack notification is optional (`SLACK_WEBHOOK_URL` empty → log only).

### Open / next → Phase 5 (memory)
- Timeout expiry is covered by unit tests only (24/48 h deadlines); a live check needs a throwaway `timeouts_hours`.
- The L4 "modify" editor is a plain text box; richer editing is Phase 7 UI work.
- Rotate the paid TokenRouter key (user's action; unused, `ENABLE_PAID_PROVIDERS=false`).

---

## 2026-08-28 — Phase 3: tools + gate — DONE

### Built
- **MCP servers** (mcp 2.x `MCPServer`, streamable HTTP, `ToolError` for every rejection):
  - `sandbox` (:7003) — `run_python` via the Docker SDK: `runner.py` builds the exact `containers.run` arguments (`network_mode=none`, `network_disabled`, read-only root + tmpfs `/work`, user 65534, `cap_drop=ALL`, `no-new-privileges`, mem/memswap/CPU/PID limits, `python -I -c`), waits with a clamped timeout, kills on timeout, caps output at 20k chars, always removes the container. Image `foreman-sandbox:latest` from `infra/sandbox/Dockerfile` (python:3.12-slim + pandas); `make sandbox-image`.
  - `web_search` (:7001) — `search` behind a `SearchBackend` protocol (fixture JSON by default, optional `ddgs`), `fetch` with an SSRF guard (`assert_public_http_url`: http(s) only, no credentials, no localhost/.local, IP-literal and DNS-resolved private/loopback/link-local/multicast rejected, every redirect hop re-checked, optional allow-list), byte cap, stdlib HTML→text with script/style stripping.
  - `actions` (:7005) — `send_email`, `create_calendar_event`, `call_api` validate and write an `outbox` row via `OutboxRepository`; there is no send path.
- **Policy** (`policy.yaml`): five servers, eleven tools with risk class, allow-lists, rate limits, timeouts.
- **Gate**: `decide()` (rules) and `decide_async()` (rules + classifier for `risky` only); `RiskClassifier` protocol with `LLMRiskClassifier` (cheap role, strict JSON, fail-closed on *any* error) and `StaticClassifier` for tests. Destructive never consults the classifier. `Decision.classified` flag; span attributes.
- **Rate limiting**: `RedisRateLimiter` (fixed window `INCR`+`EXPIRE`, fails closed on Redis errors) selected by `runtime.make_rate_limiter`, in-memory fallback with a warning.
- **Ledger**: `ToolEvent` per gated call (args hashed, never stored) → `SubtaskResult.tool_events` → state channel `tool_events` → `tool_invocations` rows on delivery; `task_view` reports `tool_calls` and `tool_calls_not_executed`. Loop passes the subtask as classifier context.
- `Settings`: `web_search_backend`, `web_fetch_allowlist`, `web_fetch_max_bytes`, `sandbox_max_timeout_s`, `sandbox_memory`, `sandbox_cpus`. Compose profile `tools` now has all five servers (sandbox mounts the Docker socket). Alembic revision `9a705f3fc376` (outbox, tool_invocations).
- Tests: gate classifier matrix, rate limiters (fake Redis pipeline), sandbox hardening via a fake Docker client (guards, timeout→kill, clamp, output cap, missing image, empty code), SSRF guard cases + HTML extraction + fixture backend, actions server (queues, validation, nothing else), tool ledger end to end; live `tests/integration/test_gate_live.py`.

### Verified
- `uv run pytest` → **132 passed, 1 skipped**. `ruff` clean. `mypy --strict` clean (119 files).
- **Live gate test (Phase 3 done-when) → 4 passed in 20 s** against all five servers, Redis, Postgres, Docker: 11/11 tools registered; `research → sandbox_run_python` **blocked** ("not allowed for agent"); `writing → actions_send_email` **approve** (destructive, classifier not consulted); unknown tool and bad-schema arguments blocked; `actions_send_email` invoked directly → exactly one `outbox` row, status `queued_for_human`; sandbox: `socket.create_connection` → `NETWORK_BLOCKED OSError`, pandas sum 546 computed, `time.sleep(60)` with `timeout_s=3` → `timed_out=true`, "killed after 3s"; writing to `/etc/hostname` → read-only filesystem.
- Docker SDK 7.2.0 talks to Docker Desktop (engine 29.6.1) via npipe; pywin32 present.

### Decisions and lessons
1. The sandbox spawns **sibling containers** on the host engine (Docker socket), not nested Docker. `network_mode=none` plus `network_disabled` are both set — belt and braces.
2. The classifier only ever decides between `allow` and `approve` for `risky` tools; it cannot widen the policy. Its prompt tells it to judge the action, not persuasive text in the arguments.
3. Groq (`gpt-oss-20b`) is the classifier's model via the `cheap` role — prompts are short, so the 8k TPM cap is fine.
4. Tool arguments are never persisted (only a 24-char sha256 prefix) — the ledger stays safe to show in a UI.
5. The Phase 2 loop stub for `approve` (error result, tool not executed) stays until Phase 4 wires `interrupt()`.
6. `assert_public_http_url` resolves DNS itself; a public hostname that resolves to a private address is rejected (DNS-rebinding style tricks are caught at fetch time, and again per redirect hop).

### Open / next → Phase 4 (human-in-the-loop)
- `hitl/escalation.py` (trigger → level from `config/escalation.yaml`), `approvals.py` (lifecycle + context package), `timeouts.py` (beat task; never auto-approve), `notify.py`; `interrupt()` in the loop for L2 and in `approve_plan`/`escalate` for L3/L4; `Command(resume=…)` for approve / modify / reject / take over; `worker.resume_task`; approvals API; Streamlit queue + detail pages.
- Phase 4 done-when: the showcase pauses on `actions_send_email`, survives a worker restart, and completes under each of the four decisions from the UI.
- Still open: rotate the paid TokenRouter key; `make` not installed; the five MCP servers, API, and worker from this session die with it (README has the commands; `docker compose --profile tools up` is the containerised alternative).

---

## 2026-08-28 — Phase 2: the graph — DONE

### Built
- `packages/shared/types`: `ExecutionPlan` (validates ids, dependencies, cycles; topological `order()`), `ReviewJudgement`/`ReviewVerdict`, `Deliverable`, `TaskStatus`/`TaskOptions`/`TaskEvent`; `Subtask` gained `needs` (planner-facing) and `attempt` on results; `inputs` is hidden from the planner schema via `SkipJsonSchema`.
- `packages/orchestrator/graph`: `state.py` (`TaskState` with merge/append reducers, `SpecialistInput` for `Send()`), `edges.py` (ready/pending/rejected helpers; `route_after_plan`, `route_dispatch`, `route_after_review` — retries with feedback up to `max_retries`, dispatches newly-ready dependents, synthesises when all accepted, escalates when stuck), `deps.py` (`GraphDeps`/`GraphConfig`), one file per node (`intake`, `recall_memory` stub, `plan`, `approve_plan` stub, `dispatch`, `specialist`, `review`, `escalate` stub, `synthesize`, `deliver`, `write_memory` stub), `build_graph.py`.
- Agents: supervisor (plan + synthesise prompts), reviewer, analysis, writing, code_exec; `agents/catalog.py`.
- LLM layer: `ChatLLM` protocol with `with_cost_sink()`; `ChainedLLM` gained **backoff rounds** (3 s / 8 s / 20 s) when every entry fails retryably; `OpenAICompatProvider` gained a **per-provider concurrency semaphore** (`limits.concurrency` in models.yaml); `strict_schema()` now also requires every property and strips defaults (OpenAI/Groq strict modes).
- Tier 2: `memory/db.py`, `memory/persistent.py` (`tasks`, `subtasks`, `llm_calls`, `audit_log`; `TaskStore` with `create_task`, `set_status`, `set_plan`, `finish_task`, `task_view`); Alembic (`alembic.ini`, `infra/migrations/`, revision `d03e998066fb`) with an `include_object` filter so autogenerate ignores the seed and LangGraph checkpoint tables.
- `packages/orchestrator/runtime.py` (build `GraphDeps` from settings), `worker.py` (Celery `foreman.run_task`; `execute_task` resumes from the Postgres checkpoint when one exists), `apps/api` (`POST /v1/tasks`, `GET /v1/tasks/{id}`, `/health`; API-key middleware; RFC 7807 errors; `create_app` factory — served with `uvicorn … --factory`), `scripts/run_task.py` (in-process, no broker), `packages/shared/asyncio_compat.py` (selector loop on Windows for psycopg async).
- Tests: plan validation + every edge (`test_plan_and_edges.py`); the full graph with scripted LLMs, empty tool registry, SQLite store and `MemorySaver` (`test_graph_flow.py`: A→B→C, parallel fan-out, retry-with-feedback, escalation after max retries, low-confidence and `require_human_review` fail closed, crash-then-resume, plan persisted early); API (`test_api.py`); chain backoff + assistant-message sanitisation (`test_chain_backoff_and_messages.py`); Postgres resume (`tests/integration/test_graph_postgres_resume.py`).

### Verified
- `uv run pytest` → **91 passed, 1 skipped** (2 live tests deselected). `ruff` clean. `mypy --strict` clean (109 files). `alembic upgrade head` applied.
- **Postgres resume integration test: passed** — reviewer crashes mid-task, a brand-new saver + graph resumes the thread, A is not re-run, task ends `done`.
- **Live acceptance (Phase 2 done-when) via `POST /v1/tasks` → Redis → Celery → graph:** task `f772fc7f…` `done` in 313 s. Plan of 6 subtasks (2 research, 2 analysis, 2 writing), **all accepted at 5/5**, F on attempt 2 after reviewer feedback. Deliverable "Summary of Lender Documents for Claim CLM-4471 and Draft Complaint Letter", confidence 0.99, 11 sources; loan table matches the DB row-for-row; rules A2 / R1–R4 applied correctly and the lender's "not upheld" flagged as conflicting with §D. 40 LLM calls persisted, 261k tokens, $0.
- **Jaeger:** 220 spans — 53 `llm.call` (33 Mistral, 12 TokenRouter Qwen, 7 Gemini, 1 Groq), **13 fallbacks, all recovered**, 23 `gate.decide` (all allow, all safe), 7 reviews, 36 specialist iterations.

### The first live run failed — and what it taught (all fixed)
1. **Assistant messages must be portable.** Re-sending Groq's assistant turn verbatim (it carries a `reasoning` field) to Mistral after a mid-loop fallback → HTTP 422. `assistant_message()` now emits only `role`/`content`/`tool_calls`. Regression test added.
2. **Groq's free tier is 8,000 tokens per minute** on `gpt-oss-120b`; a specialist call is ~9–10k tokens once a document and the schema are in history → HTTP 413 every time. Groq now serves only short-prompt roles (reviewer fallback, cheap). Specialist/supervisor chains: `mistral-medium-latest` → `qwen/qwen3.8-max-free` (TokenRouter) → `gemini-3.5-flash`.
3. **Parallel specialists burst past Mistral's rate limit.** Per-provider `concurrency` (Mistral 1, Gemini 1, TokenRouter 1, Groq 2) + chain backoff rounds. In the passing run Mistral still 429'd 13 times; every one fell through to Qwen and the task finished.
4. The failure itself was handled exactly as designed: reviewer rejected the empty results with precise feedback, three retries each, then `escalate` ended the task `failed` with the reason — persisted, no hang, no fail-open.

### Other decisions and notes
- Phase 2 stubs for HITL: `approve_plan` (low confidence or `require_human_review`) and `escalate` end the task `failed` with an explanatory error — fail closed until Phase 4's `interrupt()`.
- `deliver` runs for failed tasks too, so every outcome is persisted; `write_memory` only after `done`.
- `set_plan` is called from the `plan` node (via `asyncio.to_thread`) so the API shows the plan and `planned` subtasks while the task runs.
- LangGraph fan-out: `route_dispatch` / `route_after_review` return `Send(specialist_<name>, SpecialistInput)`; every specialist node edges into `review`, which runs once per superstep.
- The supervisor used all six allowed subtasks for the showcase request (two per specialist type). Fine for the demo; the Phase 6 evals should measure whether fewer, larger subtasks do better.
- `worker.py` / `run_task.py` / `conftest.py` set `WindowsSelectorEventLoopPolicy` — psycopg async needs it.
- Windows console is cp1252: print model output with `sys.stdout.reconfigure(encoding="utf-8")`.
- Docker Desktop stopped between sessions again; `make up` needs the engine running.

### Open / next → Phase 3 (tools + gate)
- MCP servers: `sandbox` (Docker per call, no network), `web_search` (provider interface + fixture backend), `actions` (outbox only); full `policy.yaml`; Redis token-bucket limiter; `gate/classifier.py` on the cheap role for `risky` tools; `tool_invocations` rows; `Dockerfile.mcp` build for all five.
- Still open: rotate the paid TokenRouter key; `qwen3:8b` pull only when needed; `make` not installed.
- Background processes from this session (API :8000, worker, MCP :7004/:7002) die with the session; README lists the commands.

---

## 2026-08-27 — Phase 1: skeleton + one agent — DONE

### Built
- `packages/shared`: `config.py` (the only env reader; `Settings.env_value()` resolves names from models.yaml), `errors.py` (taxonomy, all with the `Error` suffix), `types/` (Subtask, SubmittedResult/SubtaskResult, ToolCall/ToolResult/ToolSpec, CostEntry, LLMResponse/Usage, Decision).
- `packages/orchestrator/llm`: `openai_compat.py` (one provider class for every OpenAI-compatible endpoint; strict-schema injection; exception mapping), `roles.py` (models.yaml loader that rejects paid providers in default chains), `providers.py` (lazy pool), `chains.py` (per-role fallback: next entry on retryable/non-retryable error, one same-entry retry on schema failure; `CostEntry` with `fallback` flag; `llm.call` span per attempt).
- `packages/orchestrator/tracing`: `otel.py` (one global provider; explicit exporters via SimpleSpanProcessor, OTLP via Batch), `cost.py` (None when no price on file).
- `packages/tools/mcp_servers`: `database` (`schema`, `query` — sqlparse guard → SELECT-only role → LIMIT wrap → 15 s statement timeout), `files` (`list_dir`, `read_file`, `write_file` — canonical path confinement), shared `common.py` (mcp 2.x `MCPServer` + streamable HTTP), `infra/Dockerfile.mcp` + compose profile `tools`.
- `packages/tools/registry`: discovery via `mcp.Client`, policy merge that drops unlisted tools, `schemas_for(agent)`, `invoke()` with per-tool timeout; in-memory rate limiter (Redis in Phase 3).
- `packages/orchestrator/gate/decide.py`: unknown tool / wrong agent / unparseable args / schema mismatch / rate limit → block; safe → allow; risky and destructive → approve (classifier + interrupt arrive in Phases 3–4). `gate.decide` span with decision/risk.
- `packages/orchestrator/loop`: `agent_loop.py` (context → LLM → gate → registry → all results appended before the next call → `submit_result` tool ends the loop; nudges plain-text replies twice then fails), `budgets.py` (iterations, tokens, cost, wall-clock), `messages.py`.
- `packages/orchestrator/agents/research` (prompt + spec), `infra/seed/generate.py` (deterministic synthetic claims; planted injection doc in CLM-4302), `scripts/run_subtask.py`.
- Tests: confinement, SQL guard, registry + gate matrix, chains + roles (incl. "repo config is free-only"), agent loop (parallel results, blocked/approve never reach the registry, max iterations, invalid submit retry, nudge-then-fail, token budget), in-process MCP servers via `mcp.Client(server)`, import-scan for "one path to a tool".

### Verified
- `uv run pytest` → **65 passed, 1 skipped** (symlink test needs privileges on Windows), 1 deselected (live). `ruff check` clean. `mypy --strict` clean (70 files).
- Seed: 200 claims, 555 loans, 201 documents; `foreman_ro` refuses INSERT ("cannot execute INSERT in a read-only transaction").
- **Live acceptance run** (`scripts/run_subtask.py`, CLM-4471): `status=completed` in 3 iterations, 3 LLM calls on `mistral-medium-latest` (no fallback), 7,110 tokens, tools `db_schema → files_list_dir → db_query → files_read_file`, correct loan table + lender decision + checks, sources cited.
- **Jaeger trace**: 28 spans — `task` (7.6 s) → `agent.research` → 3 × `agent.research.iteration` → 3 × `llm.call` (provider/model/tokens), 4 × `gate.decide` (all allow, safe), 4 × `tool.*` (all ok), plus the mcp SDK's own `MCP send …` spans nested underneath.
- Compose: redis, postgres (healthy), chroma, jaeger, ollama up; `nomic-embed-text` pulled.

### Decisions and lessons
1. **mcp SDK is 2.x (2.1.1).** `FastMCP` → `MCPServer`; host/port/`stateless_http` go to `run()`; client is `mcp.Client(url_or_server)`; results expose `is_error` / `structured_content`; listed tools expose `input_schema`. Docs updated to the 2.x names.
2. **Tool rejections must raise `ToolError`.** A plain exception is wrapped as "Error executing tool X" and the reason is hidden from the client — so the guard and confinement reasons would never reach the model. Every rejection in the servers raises `ToolError`.
3. **Registry tool names use underscores** (`db_query`, `files_read_file`) because OpenAI-style function names forbid dots. `policy.yaml` is the source of truth; Architecture.md updated.
4. **Reasoning models need headroom**: with `max_tokens=20` gpt-oss / qwen3.8-max / gemini-3.5 return empty content. Smoke script uses ≥ 512. Groq's strict `json_schema` requires `additionalProperties: false` — `strict_schema()` injects it on every object node.
5. The mcp SDK emits its own OpenTelemetry spans into our provider. Kept — it shows the wire calls under each `tool.*` span.
6. The gate test's 2/min limit on `db_query` leaked into the loop tests and correctly blocked the third call; loop tests now use a loosened copy of the policy. Good accidental proof of fail-closed.
7. Docker Desktop must be running before `make up`; the `docker` client works even when the engine is down (Day 0 compose failure).
8. Exceptions renamed with the `Error` suffix (ruff N818); Rules.md §4 updated.
9. mypy: `sqlparse` ships no types → `disallow_untyped_calls=false` for the guard module only.

---

## 2026-08-27 — Day 0: keys verified, structure created

### Done
- Repo skeleton per `Architecture.md` §12 at `15 - …/foreman/`. `git init` on `main`; Phase 0 committed as `2beb84c`. The operator UI folder is `apps/review_ui` (importable name; docs say `review-ui`).
- Docs moved into `foreman/docs/`; diagrams copied to `foreman/docs/diagrams/` (originals remain one level up next to the deep-dive).
- Files: `pyproject.toml` (uv + hatchling, Python 3.12 via `.python-version`), `Makefile`, `.env`, `.env.example`, `.gitignore` (`.env` confirmed ignored), `.gitattributes` (LF), `README.md`, `config/models.yaml`, `config/escalation.yaml`, `infra/docker-compose.yml` (redis, postgres, chroma, ollama, jaeger), `infra/postgres-init/01-readonly-user.sql` (SELECT-only `foreman_ro` role), `scripts/smoke_provider.py`.
- `.env` written from the desktop `tokens.txt`; every value matched, then `tokens.txt` was deleted. All five keys re-verified afterwards by reading them back from `.env`.

### Key verification (curl: `/models`, then a tool-calling chat completion)

| Provider | Auth | Tool call | Working model ids | Notes |
|---|---|---|---|---|
| Mistral | 200 | ok | `mistral-large-latest`, `mistral-medium-latest`, `mistral-small-latest`; `ministral-14b-latest`, `magistral-*`, `codestral-*` also list `function_calling: true` | `mistral-medium-2505` is deprecated 2026-08-31 → use the `-latest` aliases |
| Groq | 200 | ok | `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.8-27b` | **`llama-3.3-70b-versatile` is retired (404)**. The key was labelled "grok" in tokens.txt; it is a Groq (`gsk_`) key |
| Google AI Studio | 200 (query param, Bearer, and `x-goog-api-key` all work) | ok | `gemini-3.5-flash`, `gemini-3.5-flash-lite` (3.6-flash, 3.7-flash, 3.1-pro-preview also listed); embeddings `gemini-embedding-2` | **`gemini-2.5-flash` and `gemini-flash-latest` → 404 "no longer available to new users"** |
| TokenRouter free key | 200 at `https://api.tokenrouter.com/v1` | ok on `qwen/qwen3.8-max-free` | free key sees 5 models; only `qwen/qwen3.8-max-free` is callable | `deepseek-v4-pro-0813-free` → 403 no access; `nemotron…:free` → 403 insufficient credit ($0.00); paid models → 403 |
| TokenRouter paid key | 200 | not tested (paid; `ENABLE_PAID_PROVIDERS=false`) | full catalogue; `anthropic/claude-*` entries advertise `supported_endpoint_types: ["anthropic"]` | native Anthropic API is reachable through tokenrouter.com if the paid path is ever enabled |

The service is **tokenrouter.com**. It is not api.tokenrouter.io (issues `tr_` keys; rejected ours) and not tokenrouter.me (404).

### `uv sync --dev` → exit 0 (Python 3.12.13 in `.venv`). `uv run scripts/smoke_provider.py --all` → exit 0

| provider | model | chat | tool | json (mode) | ms chat/tool/json |
|---|---|---|---|---|---|
| mistral | mistral-medium-latest | ok | ok | ok (json_schema) | 1317 / 454 / 919 |
| tokenrouter_free | qwen/qwen3.8-max-free | ok | ok | ok (json_schema) | 838 / 1025 / 2271 |
| groq | openai/gpt-oss-120b | ok | ok | ok (json_schema) | 1107 / 291 / 991 |
| gemini | gemini-3.5-flash | ok | ok | ok (json_schema) | 1711 / 1324 / 13238 |
| groq | qwen/qwen3.8-27b | ok | ok | ok (json_schema) | 359 / 388 / 514 |
| groq | openai/gpt-oss-20b | ok | ok | ok (json_schema) | 678 / 398 / 718 |
| mistral | ministral-14b-latest | ok | ok | ok (json_schema) | 360 / 508 / 2480 |

Lessons from the first (failing) run, now baked into the script: reasoning models need `max_tokens` ≥ ~500 even for one-word answers or they return empty content; Groq strict `json_schema` requires `additionalProperties: false`; `mistral-large-latest` timed out on every call (free-tier overload) and was moved out of the supervisor chain. Gemini's JSON call is slow (~13 s) — fine for the reviewer role, not for anything in a tight loop.

### Decisions
1. **Role chains** (`config/models.yaml`): see the Phase 2 entry for the current chains (Groq removed from specialist/supervisor after the TPM finding).
2. **Trace viewer: Jaeger all-in-one** (accepts OTLP on 4317/4318, UI on 16686). Langfuse deferred to an optional profile — it needs ClickHouse + MinIO + its own Postgres. `Architecture.md` §2 and `PRD.md` §10 updated.
3. **`make` is not installed** on this Windows machine. Either `winget install ezwinports.make` or run the Makefile commands directly (README lists them).
4. **Paid providers stay off.** TokenRouter paid key is in `.env` but unused; Anthropic key empty.
5. `uv` has Python 3.14 installed; the project pins 3.12 (`.python-version`) and uv fetches it on sync.
