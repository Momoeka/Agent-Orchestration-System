# Architecture — Librarian

Diagrams (SVG + PNG, style shared with Foreman): [`diagrams/01-architecture-overview`](diagrams/01-architecture-overview.svg) ·
[`diagrams/02-hybrid-retrieval`](diagrams/02-hybrid-retrieval.svg) (a real traced query) ·
[`diagrams/03-chunking-strategies`](diagrams/03-chunking-strategies.svg).

## 1. Overview

```
ingest                          index                       ask
────────────────────────        ─────────────────────       ────────────────────────────────
md/txt/html/pdf                 ChunkStore (source of       question
   │ loader.py                  truth: every chunk +           │ embed query (Ollama)
   ▼                            metadata)                      ▼
Document (headed plaintext,        │            │           dense top-10 ─┐
 page offsets)                     ▼            ▼                         ├─ RRF fuse ─ top-20
   │ chunkers.py                Chroma        BM25          sparse top-10 ─┘      │
   ▼                            (dense)       (sparse)                            ▼
Chunks (fixed|heading|          one collection per          cross-encoder rerank → top-5
 semantic, offsets,             embedding model;                    │
 heading path, page)            both always built                   ▼
   │ dedup (cosine > 0.95)      from the ChunkStore         numbered context blocks
   ▼                                                                │ generator (role chain)
 stored + indexed                                                   ▼
                                                            answer + [n] citations
                                                                    │ verifier (other family)
                                                                    ▼
                                                            confidence ⇒ answer | refusal
```

Everything left of "ask" is `librarian/` the library; the API and dashboard are thin layers
on top of it. Foreman imports the same retrieval entry point the API serves.

## 2. Components

| Layer | Module | Responsibility |
|---|---|---|
| Ingestion | `librarian/ingest/loader.py` | md/txt/html/pdf → `Document` (headed plaintext, page offsets) |
| Chunking | `librarian/ingest/chunkers.py` | fixed / heading / semantic → `Chunk` (offsets, heading path, page, strategy) |
| Chunk store | `librarian/index/store.py` | the single source of truth both indexes are built from (SQLite, file-based) |
| Ingestion pipeline | `librarian/index/indexer.py` | load → chunk → embed → dedup (cosine > 0.95 vs the dense index) → store + index |
| Dense index | `librarian/index/dense.py` | Chroma; **one collection per embedding model** (a fallback embedder is a different vector space) |
| Sparse index | `librarian/index/sparse.py` | BM25 (`rank_bm25`) rebuilt from the chunk store; in sync by construction |
| Embeddings | `librarian/llm/embeddings.py` | `nomic-embed-text` via Ollama; Gemini free endpoint fallback; satisfies the `Embedder` protocol |
| Fusion | `librarian/retrieve/fuse.py` | Reciprocal Rank Fusion, configurable weights |
| Rerank | `librarian/retrieve/rerank.py` | cross-encoder top-20 → top-5; LLM-rerank fallback |
| Retrieval facade | `librarian/retrieve/retriever.py` | `search(query, k, mode)` — the one entry point (API, dashboard, Foreman) |
| Generation | `librarian/answer/generate.py` | grounded prompt, numbered context, `[n]` citations |
| Verification | `librarian/answer/verify.py` | claim-citation pairs to a judge from a different family |
| Confidence | `librarian/answer/confidence.py` | retrieval score + citation coverage + completeness → composite; refusal below threshold |
| Ask pipeline | `librarian/answer/pipeline.py` | `ask()`: retrieve → generate → verify → decide; `build_pipeline()` wires the stack from settings |
| LLM access | `librarian/llm/` | role chains from `config/models.yaml`, same loader semantics as Foreman |
| Evals | `evals/` | golden set, runner (JSONL-resumable), metrics, baseline diff, chunking bake-off |
| API | `apps/api/` | FastAPI: routes → services; no retrieval logic here |
| Dashboard | `apps/dashboard/` | Streamlit; talks only to the API |

## 3. Request flow (`POST /v1/ask`)

1. Embed the query; dense top-k and BM25 top-k run against the same chunk corpus.
2. RRF merges the two ranked lists (`score = Σ w_i / (60 + rank_i)`); top-20 survive.
3. The reranker scores (query, chunk) pairs jointly; top-5 become numbered context blocks.
4. The generator answers **only** from those blocks, citing `[n]`; "not in the corpus" is a
   valid and expected answer.
5. The verifier checks each claim-citation pair; unsupported citations are flagged.
6. Confidence = f(retrieval scores, citation coverage, completeness). Below threshold, the
   response is a structured refusal: what was found, what was not, which documents to check.
7. Response: answer, citations (chunk id, source, heading path, page), confidence breakdown,
   flags, timings. `POST /v1/search` stops after step 3 and returns chunks — that is what
   Foreman's `search_docs` uses.

## 4. Folder structure

```
librarian/
  ingest/        loader, chunkers
  index/         store (chunk source of truth), dense (Chroma), sparse (BM25)
  retrieve/      fuse (RRF), rerank, retriever (facade)
  answer/        generate, verify, confidence
  llm/           providers (openai-compat), roles (models.yaml loader), embeddings
  types.py       Document, Chunk, retrieval/answer models
  config.py      Settings — the only env reader
evals/           golden_tasks/, runner, metrics, report, reports/
apps/api/        FastAPI (routes → services)
apps/dashboard/  Streamlit
config/          models.yaml
infra/           docker-compose, seed script
scripts/         fetch_corpus, seed, smoke
tests/           unit (no network, fakes) · integration (stack + real models, marked)
docs/            PRD, Architecture, Rules, Phases, Design, Memory
```

## 5. Configuration (`.env`)

```
OLLAMA_BASE_URL=http://localhost:11434/v1     # embedder (shared with Foreman's compose)
GEMINI_API_KEY=                               # embedding fallback + judge chain
MISTRAL_API_KEY= / GROQ_API_KEY=              # generator/judge free chains
ENABLE_PAID_PROVIDERS=false                   # gpt-6-astra opt-in (EXPLABS_API_KEY, EXPLABS_BASE_URL)
CHROMA_HOST/PORT                              # shared Chroma instance
CORPUS_DIR=./data/corpus                      # fetched by scripts/fetch_corpus, gitignored
```

## 6. Foreman integration (Phase 6)

`retriever.search()` is the contract: `(query, k, mode) → [SearchHit{text, source,
heading_path, page, score}]`. Foreman mounts it either as an HTTP tool against `/v1/search`
or wrapped in an MCP server matching its registry conventions — decided in Phase 6
(PRD §10.3). Index compatibility is guaranteed by sharing the embedding model and the
collection-per-model rule.
