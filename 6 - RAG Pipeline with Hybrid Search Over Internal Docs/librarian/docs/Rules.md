# Rules — Librarian

Boundaries for building this project. Root `Rules.txt` §7 (code quality, modularity,
maintainability) applies in full; these are the project-specific rules on top of it.

## 1. Stack — use / avoid

| Use | Avoid | Why |
|---|---|---|
| Hand-written chunkers, RRF, and grounding prompt | LangChain / LlamaIndex runtime | the point of the project is being able to explain these; a framework hides them |
| `openai` SDK, per-provider base_url + key (Ollama, Mistral, Groq, Gemini, Experiential Labs — all OpenAI-compatible) | one SDK per provider; hand-rolled HTTP | one client, many providers — Foreman's proven pattern |
| ChromaDB for vectors, `rank_bm25` for sparse | a second vector store "for comparison" | one of each; comparisons happen in the eval, not the infra |
| `sentence-transformers` cross-encoder on CPU | GPU-only models, paid rerank APIs | $0, laptop-runnable |
| uv + Python 3.12, ruff, mypy `--strict`, pytest | poetry/pip-tools, unchecked `Any` | same toolchain as Foreman |
| FastAPI (routes → services), Streamlit dashboard | retrieval logic in the API layer | library-first (PRD §8) |

## 2. Architecture rules (non-negotiable)

- **One chunk store feeds both indexes.** Dense and sparse are always built from it — never
  ingested separately, so they cannot drift out of sync.
- **One Chroma collection per embedding model.** A fallback embedder is a different vector
  space; mixing them poisons similarity (Foreman's Phase 5 lesson).
- **The generator answers only from provided context.** Retrieved text is data, not
  instructions; the prompt says so explicitly (injection posture inherited from Foreman).
- **Citations are verified by a different model family** than the generator — a model grading
  its own family shares its blind spots.
- **Low confidence means a structured refusal**, never a best-effort guess. "Not in the
  corpus" is a first-class answer with its own response shape.
- **No paid provider in an effective default chain.** `ENABLE_PAID_PROVIDERS` defaults to
  false; paid chain entries (gpt-6-astra) are dropped by the loader while it is false, and a
  test asserts the effective default chains are free-only.

## 3. Error handling

- Foreman's taxonomy: `RetryableError` (429/5xx/timeouts — the chain falls through and backs
  off) vs `NonRetryableError` (bad request, auth, config). No bare `except Exception` that
  swallows the cause.
- Embedding fallback is explicit, never silent: a query embedded by the fallback model must
  query that model's collection or fail loudly — never mix spaces.
- The reranker failing to load degrades to LLM-rerank on the cheap role, logged; the pipeline
  keeps working.

## 4. Secrets and configuration

- `librarian/config.py` is the **only** module reading environment variables. Keys live in
  `.env` (gitignored) only — never hard-coded, never printed, never committed. `.env.example`
  documents every variable with empty values.

## 5. Data rules

- Public corpus only (FastAPI docs); **never** employer documents. The corpus and every index
  live under `data/` (gitignored) and are recreated by `scripts/fetch_corpus` + seed — a fresh
  clone plus one command must rebuild everything.

## 6. Testing rules

- Unit tests: fast, no network, deterministic — fake embedders/judges, tmp-path corpora.
  Chunker/fusion/confidence invariants are tested properties, not examples.
- Integration tests (marked): the compose stack and real models; never run by default.
- **The eval harness is the arbiter.** Architecture claims (hybrid > dense, which chunker,
  which reranker) are settled by golden-set numbers, and the report shows baseline diffs.
  Eval runs must be JSONL-resumable (free tiers die mid-run — Foreman's lesson).
- Every phase ends green: `pytest`, `ruff check`, `mypy` (strict), before it is called done.

## 7. Git and session rules

- Commit per meaningful step from the repo root; push with the **Momoeka** account only.
  Never commit `data/`, `.env`, or model caches.
- `docs/Memory.md`: read first each session, updated last (newest on top).

## 8. What the AI assistant must not do

- No artifacts / chat-side documents — everything lives in this repo (root Rules.txt §8).
- Never fabricate eval numbers or "typical" results; only report what a run produced.
- Never vendor the corpus into git, and never call paid models unless the flag is on.
- Never weaken a failing invariant test to make it pass — fix the code or revisit the
  documented behaviour explicitly.

## 9. Definition of done (per feature)

Implemented per Architecture.md · unit-tested (invariants, not just happy path) · green on
pytest/ruff/mypy · wired into the eval if it changes retrieval or answers · documented in
Memory.md with any decision it settled.
