# Memory — Foreman

Running log across coding sessions. **Read this first; update it last** (Rules.md §9). Newest entry at the top.

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
- Compose: redis, postgres (healthy), chroma, jaeger up. Ollama image pull was still running at the end of the session; `nomic-embed-text` pull follows it.

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

### Open / next → Phase 2 (graph)
- `graph/state.py`, `edges.py`, `nodes/` (intake, recall stub, plan, dispatch with `Send`, review, synthesize, deliver, write_memory stub), `build_graph.py` with `PostgresSaver`; supervisor + reviewer agents; remaining specialists as copies of research; `worker.py` (Celery `run_task`); `apps/api` with `POST /v1/tasks`, `GET /v1/tasks/{id}`; tasks/subtasks/llm_calls repositories + Alembic.
- Phase 2 done-when: a 3-subtask task with A → B → C runs end to end; kill the worker mid-task, restart, it completes from the checkpoint.
- Still open from Day 0: rotate the paid TokenRouter key; `qwen3:8b` pull only when needed; `make` not installed (README lists direct commands).
- Live integration test exists (`tests/integration/test_research_agent_live.py`); run with `make test-live` while the two MCP servers are up.

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
1. **Role chains** (`config/models.yaml`): supervisor `mistral-medium-latest` → `qwen/qwen3.8-max-free` (tokenrouter_free) → `openai/gpt-oss-120b` (groq); specialist `mistral-medium-latest` → `gpt-oss-120b`; reviewer `gemini-3.5-flash` → `qwen/qwen3.8-27b` (groq); cheap `gpt-oss-20b` → `ministral-14b-latest`; embedding `nomic-embed-text` (ollama) → `gemini-embedding-2`. Every chain crosses at least two providers.
2. **Trace viewer: Jaeger all-in-one** (accepts OTLP on 4317/4318, UI on 16686). Langfuse deferred to an optional profile — it needs ClickHouse + MinIO + its own Postgres. `Architecture.md` §2 and `PRD.md` §10 updated.
3. **`make` is not installed** on this Windows machine. Either `winget install ezwinports.make` or run the Makefile commands directly (README lists them).
4. **Paid providers stay off.** TokenRouter paid key is in `.env` but unused; Anthropic key empty.
5. `uv` has Python 3.14 installed; the project pins 3.12 (`.python-version`) and uv fetches it on sync.
