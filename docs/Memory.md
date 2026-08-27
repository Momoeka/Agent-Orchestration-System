# Memory — Foreman

Running log across coding sessions. **Read this first; update it last** (Rules.md §9). Newest entry at the top.

---

## 2026-08-27 — Day 0: keys verified, structure created

### Done
- Repo skeleton per `Architecture.md` §12 at `15 - …/foreman/`. `git init` on `main`; nothing committed yet. The operator UI folder is `apps/review_ui` (importable name; docs say `review-ui`).
- Docs moved into `foreman/docs/`; diagrams copied to `foreman/docs/diagrams/` (originals remain one level up next to the deep-dive).
- Files: `pyproject.toml` (uv + hatchling, Python 3.12 via `.python-version`), `Makefile`, `.env`, `.env.example`, `.gitignore` (`.env` confirmed ignored), `README.md`, `config/models.yaml`, `config/escalation.yaml`, `infra/docker-compose.yml` (redis, postgres, chroma, ollama, jaeger — `docker compose config` valid), `infra/postgres-init/01-readonly-user.sql` (SELECT-only `foreman_ro` role), `scripts/smoke_provider.py`.
- `.env` written from the desktop `tokens.txt`; every value matched, then `tokens.txt` was deleted. All five keys re-verified afterwards by reading them back from `.env`.
- `uv sync --dev` started in the background; outcome recorded under "Open" below until it finishes.

### Key verification (curl: `/models`, then a tool-calling chat completion)

| Provider | Auth | Tool call | Working model ids | Notes |
|---|---|---|---|---|
| Mistral | 200 | ok | `mistral-large-latest`, `mistral-medium-latest`, `mistral-small-latest`; `ministral-14b-latest`, `magistral-*`, `codestral-*` also list `function_calling: true` | `mistral-medium-2505` is deprecated 2026-08-31 → use the `-latest` aliases |
| Groq | 200 | ok | `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.8-27b` | **`llama-3.3-70b-versatile` is retired (404)**. The key was labelled "grok" in tokens.txt; it is a Groq (`gsk_`) key |
| Google AI Studio | 200 (query param, Bearer, and `x-goog-api-key` all work) | ok | `gemini-3.5-flash`, `gemini-3.5-flash-lite` (3.6-flash, 3.7-flash, 3.1-pro-preview also listed); embeddings `gemini-embedding-2` | **`gemini-2.5-flash` and `gemini-flash-latest` → 404 "no longer available to new users"** |
| TokenRouter free key | 200 at `https://api.tokenrouter.com/v1` | ok on `qwen/qwen3.8-max-free` | free key sees 5 models; only `qwen/qwen3.8-max-free` is callable | `deepseek-v4-pro-0813-free` → 403 no access; `nemotron…:free` → 403 insufficient credit ($0.00); paid models → 403 |
| TokenRouter paid key | 200 | not tested (paid; `ENABLE_PAID_PROVIDERS=false`) | full catalogue; `anthropic/claude-*` entries advertise `supported_endpoint_types: ["anthropic"]` | native Anthropic API is reachable through tokenrouter.com if the paid path is ever enabled |

The service is **tokenrouter.com**. It is not api.tokenrouter.io (issues `tr_` keys; rejected ours) and not tokenrouter.me (404).

### Decisions
1. **Role chains** (`config/models.yaml`): supervisor `mistral-large-latest` → `qwen/qwen3.8-max-free` (tokenrouter_free) → `openai/gpt-oss-120b` (groq); specialist `mistral-medium-latest` → `gpt-oss-120b`; reviewer `gemini-3.5-flash` → `qwen/qwen3.8-27b` (groq); cheap `gpt-oss-20b` → `ministral-14b-latest`; embedding `nomic-embed-text` (ollama) → `gemini-embedding-2`. Every chain crosses at least two providers.
2. **Trace viewer: Jaeger all-in-one** (accepts OTLP on 4317/4318, UI on 16686). Langfuse deferred to an optional profile — it needs ClickHouse + MinIO + its own Postgres. `Architecture.md` §2 and `PRD.md` §10 updated.
3. **`make` is not installed** on this Windows machine. Either `winget install ezwinports.make` or run the Makefile commands directly (README lists them).
4. **Paid providers stay off.** TokenRouter paid key is in `.env` but unused; Anthropic key empty.
5. `uv` has Python 3.14 installed; the project pins 3.12 (`.python-version`) and uv fetches it on sync.

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

Lessons from the first (failing) run, now baked into the script: reasoning models (gpt-oss, qwen3.8-max, gemini-3.5) need `max_tokens` ≥ ~500 even for one-word answers or they return empty content; Groq strict `json_schema` requires `additionalProperties: false` on the schema; `mistral-large-latest` timed out on every call (free-tier overload) and was moved out of the supervisor chain. Gemini's JSON call is slow (~13 s) — fine for the reviewer role, not for anything in a tight loop.

### Open / next
- [x] `uv sync --dev`; smoke table above is green for every chain entry.
- [ ] `docker compose -f infra/docker-compose.yml up -d`; then `docker exec foreman-ollama-1 ollama pull nomic-embed-text` and `… pull qwen3:8b`.
- [ ] Rotate the TokenRouter paid key in its dashboard (it sat in plain text on OneDrive).
- [ ] Fill `list_prices_usd_per_mtok` in `models.yaml` (Phase 1) so the cost ledger can report avoided cost.
- [ ] First commit once the smoke table is green: `phase-0: skeleton, infra, provider smoke tests`.
- [ ] **Phase 1 begins:** `packages/shared/types`, `llm/openai_compat.py` + `llm/chains.py` + `llm/roles.py`, `tracing/otel.py`, database + files MCP servers, registry, agent loop, research agent, `infra/seed` generator, `run_subtask` CLI.
