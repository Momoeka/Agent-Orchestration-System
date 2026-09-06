# Librarian — build phases

Adapted from the BASWE guide (Project 6) for this portfolio's constraints: $0 by default (local
embedder and reranker, free chat tiers, `gpt-6-astra` as the paid opt-in), a public corpus
(FastAPI docs), and **library-first** — Foreman (Project 15) will mount this engine as its
`search_docs` tool and shares the ChromaDB + `nomic-embed-text` index format.

## Phase 1 — Ingestion and chunking
- Multi-format loader: markdown, text, HTML, PDF → normalised `Document` (plaintext + metadata:
  source path, title, section headings, page numbers). Raw kept beside processed for re-indexing.
- Three switchable chunkers, hand-written: **fixed** (size + overlap, whitespace-aligned),
  **heading** (structure-aware: split on markdown headings, pack sections, keep the heading path),
  **semantic** (topic boundaries via embedding similarity between neighbouring sentences).
  Every chunk records which strategy produced it.
- Dedup at insert: cosine > 0.95 against existing chunks → skip and flag.
- Tests: loaders on fixtures, chunker invariants (no text lost, sizes bounded, headings attached).

## Phase 2 — Hybrid retrieval
- Dense: embed query (`nomic-embed-text` via Ollama; Gemini free endpoint as fallback),
  ChromaDB top-k (k=10). One collection per embedding model — Foreman's lesson, same reason.
- Sparse: BM25 (`rank_bm25`) over the same chunks; indexes stay in sync by construction
  (both are built from the chunk store, never separately).
- Fusion: Reciprocal Rank Fusion with configurable weights (default 0.7 dense / 0.3 sparse).
- Rerank: cross-encoder `BAAI/bge-reranker-v2-m3` (CPU) on top-20 → keep top-5.
- Tests: RRF math on hand-built rank lists; retrieval smoke on a tiny indexed corpus.

## Phase 3 — Generation and citations
- Grounded prompt: answer only from numbered context blocks, cite `[n]`, say "not in the corpus"
  when true. Chat via role chains in `config/models.yaml` (same loader semantics as Foreman:
  paid entries dropped unless `ENABLE_PAID_PROVIDERS=true`).
- Citation verification: each (claim, cited chunk) pair to an LLM judge **from a different
  family** than the generator; unsupported citations flagged, never silently kept.
- Confidence: retrieval score, citation coverage, completeness → composite; below threshold →
  structured "what I found / what I could not" instead of an answer.

## Phase 4 — Evaluation
- Golden set: 50+ hand-written Q&A pairs over the FastAPI docs — lookups, multi-hop,
  no-answer-in-corpus, ambiguous.
- Metrics per run: answer correctness (judge), faithfulness, retrieval relevance (recall@k),
  citation accuracy. JSONL-resumable runner, baseline + diff — reuse Foreman's harness patterns.
- Chunking bake-off: the same suite across all three strategies → a table that picks the winner.

## Phase 5 — API and dashboard
- FastAPI: `POST /v1/ask`, `POST /v1/ingest`, `GET /v1/documents`.
- Streamlit: answer with citations, ranked chunks, confidence breakdown, hybrid vs dense-only
  toggle.
- docker-compose: API + ChromaDB + seed script that indexes the corpus.

## Phase 6 — Portfolio polish
- Demo recording (< 4 min): ingest, easy/hard/no-answer questions, citation verification
  catching a hallucination, hybrid vs dense-only side by side.
- Case study led by the eval numbers; hook Librarian into Foreman as `search_docs`.
