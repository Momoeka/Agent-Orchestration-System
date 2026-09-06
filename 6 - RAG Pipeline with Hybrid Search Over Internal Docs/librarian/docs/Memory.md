# Memory — Librarian

Running log across coding sessions. Read this first; update it last. Newest entry at the top.

---

## 2026-09-06 — Diagrams (docs/diagrams/, Foreman's visual style)

Three hand-written SVGs + PNG exports (headless Chrome renders them): 01 architecture
overview (three panels + the four eval guarantees), 02 hybrid retrieval as **the real traced
query** from the Phase 2 live smoke (dense#5 + sparse#3 → RRF 0.7/65 + 0.3/63 = 0.0155 →
hybrid#2), 03 the three chunkers over one document with trade-offs and the bake-off strip.
README shows 01; Architecture.md links all three. Re-render:
`chrome --headless=new --screenshot=<png> --window-size=1600,<h> <svg>`.

---

## 2026-09-05 — Phase 2: the hybrid retrieval engine

### Built
- `config.py` (Settings — the only env reader; retrieval knobs default to PRD values and are
  tuned in Phase 4, not by taste) · `errors.py` (Retryable/NonRetryable, Foreman's taxonomy).
- `llm/embeddings.py`: one `OpenAICompatEmbedder` for Ollama and Gemini; its `space_id` names
  the vector space and hence the Chroma collection — no silent fallback between models.
- `index/`: `store.py` (SQLite ChunkStore — the single source of truth), `dense.py` (Chroma;
  local `PersistentClient` by default so a fresh clone needs zero infra, `HttpClient` when
  `CHROMA_HOST` is set; collection per space), `sparse.py` (BM25, `\w+` tokens keep
  `response_model`-style identifiers whole; rebuilt from the store = in sync by construction),
  `indexer.py` (ingest pipeline with per-chunk dedup at cosine > 0.95, catching intra- and
  cross-document repeats).
- `retrieve/`: `fuse.py` (weighted RRF, rank-based on purpose — dense and BM25 scores are
  incomparable scales), `rerank.py` (cross-encoder behind `uv sync --group rerank`, degrading
  loudly to fused order when absent), `retriever.py` (the facade: modes hybrid/dense/sparse so
  the PRD's hybrid-beats-dense claim is measurable; every `SearchHit` carries its rank and
  score at each stage).
- Scripts: `fetch_corpus.py` (shallow-clone FastAPI docs → `data/corpus`), `seed.py`
  (resumable-ish: deterministic chunk ids + dedup make re-runs safe), `search.py` (CLI demo,
  `--compare` prints hybrid vs dense side by side).

### Verified
- Unit **23 passed** · ruff · mypy strict. Highlights: RRF math checked by hand
  (`1/62 + 1/61` for the doubly-ranked id), dedup skips repeats within and across documents
  (store count == dense count == 1), BM25 puts the `response_model` chunk first where the
  bag-of-words dense fake would not, per-stage fields land on `SearchHit` in every mode.
- **Live against Ollama** (60 FastAPI docs, heading strategy): 431 chunks in 180 s, 3 near-
  duplicates skipped, store count == dense count. Query *"how do I return a custom
  JSONResponse with a status_code"*: hybrid top-3 are the right sections with heading paths;
  the "Returning a custom `Response`" chunk sat at dense#5 but sparse#3 and fusion lifted it
  to hybrid#2 — the PRD's hybrid thesis visible on the first real query.

### Decisions
1. **Local Chroma by default** (file-based PersistentClient): a reviewer cloning the repo needs
   Ollama and nothing else; `CHROMA_HOST` switches to the shared server that Foreman's compose
   runs.
2. The cross-encoder lives behind an optional dependency group; `uv sync` stays light and the
   degrade path (fused order) is explicit and logged, per Rules.md §3.

---

## 2026-09-05 — Full spec set written (root Rules.txt compliance)

- `PRD.md` (problem, users, MVP scope, the §7 metric targets that gate "done", risks, open
  decisions), `Architecture.md` (component map, ask-flow, folder structure, config, the
  Foreman `search_docs` contract), `Rules.md` (stack use/avoid — notably **no LangChain
  runtime**: the chunkers/RRF/grounding are hand-written on purpose; non-negotiables like one
  chunk store feeding both indexes and a different-family citation verifier), `Design.md`
  (evidence-first dashboard: no answer without citations, confidence always visible,
  hybrid-vs-dense as a first-class toggle).
- Order note: scaffold + Phase-1 code landed a few hours before the docs; the docs encode the
  same decisions, nothing was retrofitted to match code accidents.

---

## 2026-09-05 — Phase 1 started: scaffold, loaders, three chunkers

### Built
- Scaffold: uv + Python 3.12, ruff, mypy strict, pytest — same toolchain as Foreman.
- `librarian/types.py`: `Document` (markdown-flavoured plaintext; headings survive as `#` lines
  in **every** format so one heading chunker serves md/html/pdf alike; `page_offsets` map chunk
  offsets back to PDF pages for citations) and `Chunk` (offsets into the parent text, strategy,
  heading path, page).
- `ingest/loader.py`: md/txt/html/pdf → `Document`. HTML drops script/style/nav/header/footer,
  keeps heading levels; nested block elements are not double-extracted. PDF records page starts.
- `ingest/chunkers.py`: **fixed** (size + overlap, whitespace-aligned breaks), **heading**
  (split on markdown headings, greedy-pack sections, oversized section fixed-split but keeps its
  path, preamble before the first heading kept), **semantic** (adjacent-sentence cosine below a
  floor = topic boundary; size cap so one topic cannot swallow the document; takes any
  `Embedder` protocol — Ollama in Phase 2, a fake in tests).

### Decisions
1. **Name: Librarian.** Foreman runs the crew; Librarian finds the right page and cites it.
2. **Corpus: the FastAPI docs** — public markdown, 500+ pages, dense with exact tokens
  (function names, config keys) where BM25 should beat embeddings; fetched by a seed script in
  Phase 2, never vendored into git.
3. **Library-first** (per PREREQUISITES.md): no chatbot; a thin FastAPI layer in Phase 5 and a
  clean Python API for Foreman's `search_docs` tool.
4. Chunker invariants are tested, not assumed: full coverage (no text lost), offsets faithful
  (`chunk.text == doc.text[start:end]`), sizes bounded, heading paths correct. The *choice* of
  strategy is Phase 4's bake-off, with numbers.

### Open / next
- Phase 2: Ollama embedder (+ Gemini fallback) satisfying the `Embedder` protocol, ChromaDB
  store (one collection per embedding model — Foreman's lesson), BM25 index, RRF fusion,
  cross-encoder reranker, dedup at insert (cosine > 0.95), corpus seed script.
