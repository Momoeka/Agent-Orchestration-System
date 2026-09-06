# Memory — Librarian

Running log across coding sessions. Read this first; update it last. Newest entry at the top.

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
