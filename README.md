# Agent Orchestration System

Portfolio work from the BASWE "15 AI Engineering Projects" track, built by Sayed Mohammad Firdousi. Everything runs locally on free model tiers ($0), with synthetic data only.

## Project 15 — Foreman: agent orchestration with tool use, memory and human-in-the-loop

- **Code:** [`15 - …/foreman`](15%20-%20Agent%20Orchestration%20System%20with%20Tool%20Use,%20Memory,%20and%20Human-in-the-Loop/foreman/) — a supervisor plans, specialist agents act through MCP tools behind a permission gate, a reviewer from a different model family checks every result, and a human is pulled in at three levels (a gated tool call, a low-confidence plan, repeated failure) with approve / modify / reject / take-over. LangGraph with Postgres checkpoints, Celery worker, FastAPI, Streamlit operator console, OpenTelemetry → Jaeger.
- **Start here:** [foreman/README.md](15%20-%20Agent%20Orchestration%20System%20with%20Tool%20Use,%20Memory,%20and%20Human-in-the-Loop/foreman/README.md) to run it; [foreman/docs/](15%20-%20Agent%20Orchestration%20System%20with%20Tool%20Use,%20Memory,%20and%20Human-in-the-Loop/foreman/docs/) for the PRD, architecture, phase plan, UI design and the build log (`Memory.md`).
- **Design explainer:** [PROJECT-15-DEEP-DIVE.md](15%20-%20Agent%20Orchestration%20System%20with%20Tool%20Use,%20Memory,%20and%20Human-in-the-Loop/PROJECT-15-DEEP-DIVE.md) with the diagrams in [diagrams/](15%20-%20Agent%20Orchestration%20System%20with%20Tool%20Use,%20Memory,%20and%20Human-in-the-Loop/diagrams/).
- **History:** one commit per phase (`phase-0` skeleton → `phase-4` human-in-the-loop); phases 5–8 (memory tiers, evals and observability, hardening, showcase) follow.

## Project 6 — RAG pipeline with hybrid search over internal docs

The retrieval engine Project 15 will use. Planned; see [PREREQUISITES.md](6%20-%20RAG%20Pipeline%20with%20Hybrid%20Search%20Over%20Internal%20Docs/PREREQUISITES.md).

## Working rules

[Rules.txt](Rules.txt) — the constraints every build session follows: local-only work, no secrets in the repository (keys live in an untracked `.env`), synthetic data only, free tiers by default, small modular files with tests.
