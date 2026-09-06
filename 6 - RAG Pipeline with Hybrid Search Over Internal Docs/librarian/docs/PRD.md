# PRD — Librarian: RAG Pipeline with Hybrid Search Over Internal Docs

BASWE guide Project 6, adapted to this portfolio's constraints (see §8). Companion docs:
`Architecture.md` (how), `Rules.md` (boundaries), `Phases.md` (order), `Design.md` (UI),
`Memory.md` (build log — read first each session).

## 1. Summary

A production-grade retrieval-augmented generation system: it ingests a documentation corpus,
indexes every chunk **twice** (dense vectors + BM25 keywords), fuses both result lists, reranks
with a cross-encoder, and answers questions **only from retrieved context**, with inline
citations that a second model verifies. An eval harness with a hand-written golden set decides
every architecture argument with numbers.

## 2. Problem

Most RAG demos are a single PDF behind a LangChain quickstart: dense-only retrieval that misses
exact tokens (function names, config keys, error codes), no citation checking, and confident
answers when the corpus has none. Technical documentation is exactly where those failures bite.
The interesting engineering — hybrid retrieval, chunking trade-offs, verified grounding,
honest "not in the corpus" — is what this project builds and measures.

## 3. Users

- **Foreman's agents** (primary): the research specialist calls Librarian as its `search_docs`
  tool; grounded, cited chunks are what an agent can safely act on.
- **A developer** asking questions about the indexed docs through the dashboard or API.
- **A hiring reviewer** running `docker compose up`, asking questions, and reading the eval
  numbers behind the claims.

## 4. Goals and non-goals

Goals:
1. Hybrid retrieval that measurably beats dense-only on a technical corpus.
2. Grounded answers whose citations are verified claim-by-claim, and an honest structured
   answer when retrieval confidence is too low.
3. A chunking-strategy bake-off (fixed vs heading vs semantic) settled by the eval suite.
4. A clean library API that Foreman can mount without importing any UI or server code.

Non-goals: multi-tenant auth, incremental crawling, conversation history/chat memory,
fine-tuning, GPU anything, agentic multi-step retrieval (that is Foreman's job).

## 5. Scope — MVP features

Per phase (details in `Phases.md`): multi-format ingestion (md/txt/html/pdf) with three
switchable chunkers and near-duplicate skip → dense (Chroma) + sparse (BM25) indexes built
from one chunk store → RRF fusion with configurable weights → cross-encoder rerank top-20→5 →
grounded generation with `[n]` citations → per-claim citation verification by a different model
family → composite confidence with a refusal path → golden-set eval harness with baseline
diffs → FastAPI (`/v1/ask`, `/v1/search`, `/v1/ingest`, `/v1/documents`) + Streamlit dashboard
→ compose stack with a seed script.

## 6. Demo scenario — the FastAPI docs

Corpus: the FastAPI documentation (public markdown, 500+ pages, dense with exact tokens where
BM25 shines). Demo: ingest the corpus; ask an easy lookup ("what does `response_model` do?"),
a multi-hop question, and a question the docs cannot answer (watch it refuse with structure,
not hallucinate); show a citation the verifier rejects; flip hybrid → dense-only and show the
recall difference on an exact-token query.

## 7. Success metrics

| Metric | Target |
|---|---|
| Faithfulness (all claims grounded in retrieved context) | ≥ 0.90 |
| Citation accuracy (verified claim-citation pairs) | ≥ 0.85 |
| Answer correctness (judge vs golden answer) | ≥ 0.80 |
| Retrieval recall@5 (golden chunk retrieved) | ≥ 0.85 |
| Hybrid vs dense-only on exact-token queries | hybrid strictly better |
| No-answer questions answered with a refusal (never a fabrication) | 100% |
| `POST /v1/ask` p50 latency, local CPU | ≤ 10 s |
| Cost of the default configuration | $0 |

## 8. Constraints and assumptions

- **$0 by default**: local embedder (`nomic-embed-text` via Ollama) and local CPU reranker;
  free chat tiers via role chains; `gpt-6-astra` (Experiential Labs) as the paid opt-in with
  the same loader semantics as Foreman (paid chain entries dropped unless
  `ENABLE_PAID_PROVIDERS=true`).
- **Library-first**: retrieval logic importable as plain Python; the API is a thin layer;
  Foreman shares the ChromaDB + `nomic-embed-text` index format.
- Public corpus only; nothing from any employer. Corpus fetched by a seed script, never
  committed to git. Laptop, 16 GB RAM, no GPU.

## 9. Risks

- Free-tier judges are rate-limited → the harness must be JSONL-resumable (Foreman's lesson).
- The semantic chunker may not earn its complexity → the bake-off is allowed to kill it.
- Cross-encoder first-load (~1 GB download) → cache it in the compose volume; degrade to
  LLM-rerank on the cheap role if the model cannot load.
- BM25 in memory caps corpus size (~100k chunks) → acceptable for the MVP; documented.

## 10. Open decisions

1. Reranker: `BAAI/bge-reranker-v2-m3` vs LLM-rerank on the cheap role — decided by eval
   accuracy and latency in Phase 4.
2. RRF weights (start 0.7 dense / 0.3 sparse) — tuned against the golden set.
3. How Foreman mounts Librarian (HTTP tool vs MCP server wrapper) — resolved in Phase 6.
