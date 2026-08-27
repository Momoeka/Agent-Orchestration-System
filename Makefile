# Foreman — developer entry points. On Windows use Git Bash, or run the commands directly.
COMPOSE := docker compose -f infra/docker-compose.yml

.PHONY: sync up down ps logs smoke smoke-all test lint typecheck fmt eval demo

sync:            ## create the venv and install all deps (uv provisions Python 3.12 itself)
	uv sync --all-extras --dev

up:              ## start the local infrastructure (redis, postgres, chroma, ollama, jaeger)
	$(COMPOSE) up -d
	@echo "Jaeger UI: http://localhost:16686"

down:
	$(COMPOSE) down

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f --tail=100

ollama-pull:     ## pull the local embedding + offline dev models (CPU)
	docker exec foreman-ollama-1 ollama pull nomic-embed-text
	docker exec foreman-ollama-1 ollama pull qwen3:8b

smoke:           ## smoke-test one provider: make smoke P=groq M=llama-3.3-70b-versatile
	uv run scripts/smoke_provider.py --provider $(P) --model $(M)

smoke-all:       ## smoke-test every chat entry in config/models.yaml
	uv run scripts/smoke_provider.py --all

test:            ## unit tests (integration/network tests are excluded by default)
	uv run pytest -q

test-live:       ## integration tests against the compose stack, seed, MCP servers and live models
	uv run pytest -q -m "integration and network" tests/integration

seed:            ## generate the synthetic claims data (tables + documents)
	uv run python -m infra.seed.generate

mcp-db:          ## run the database MCP server locally on :7004
	MCP_PORT=7004 uv run python -m packages.tools.mcp_servers.database

mcp-files:       ## run the files MCP server locally on :7002
	MCP_PORT=7002 uv run python -m packages.tools.mcp_servers.files

run-subtask:     ## run the research agent on one subtask: make run-subtask T="..."
	uv run scripts/run_subtask.py "$(T)"

lint:
	uv run ruff check .

fmt:
	uv run ruff format .

typecheck:
	uv run mypy

eval:            ## Phase 6+: run the golden task set (k=3) and write a report
	uv run python -m packages.evals.runner --k 3

demo:            ## Phase 8: seed data and run the showcase task end to end
	uv run python -m infra.seed.generate && uv run python -m scripts.demo
