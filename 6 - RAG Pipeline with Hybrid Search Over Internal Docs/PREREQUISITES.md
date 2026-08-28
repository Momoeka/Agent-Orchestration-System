# Project 6 — RAG Pipeline with Hybrid Search: Prerequisites

Status: **not started** — Project 15 comes first. This file exists so the prerequisites are
captured while they are fresh; the full deep-dive will be written when we get here.

Project 6 is not a standalone demo in your portfolio. It is the **retrieval engine that
Project 15's agents use** — both as the Research specialist's `search_docs` tool and as the
long-term memory store (tier 3). Build it as a library with a thin API, not as a chatbot.

---

## 1. Knowledge you need before starting

| Topic | What you must be able to explain | Already on your resume? |
|---|---|---|
| Embeddings and cosine similarity | Text → vector; nearby vectors mean similar text; why dimensionality and normalisation matter | No — new |
| Dense retrieval | Embed the query, nearest-neighbour search in a vector store, top-k | No — new |
| Sparse retrieval (BM25) | Term-frequency scoring; why it beats embeddings on exact tokens (function names, error codes, IDs) | No — new |
| Reciprocal Rank Fusion (RRF) | `score = Σ 1 / (k + rank_i)` across result lists; why rank-based fusion avoids score-scale problems | No — new |
| Reranking | Cross-encoder scores (query, chunk) pairs jointly; run on top-20, keep top-5; precision jump | No — new |
| Chunking strategies | Fixed-size + overlap vs. structure-aware (headings) vs. semantic (topic boundaries); the trade-offs | Partly — you chunked documents for DocIntel |
| Grounded generation and citations | Answer only from context; cite chunk IDs; say "not in the corpus" when true | Partly — legal-CRM letter drafting is grounded generation |
| Citation verification (LLM-as-judge) | Does chunk [n] actually support the sentence it is attached to? | Yes — same pattern as your 75k-email eval harness |
| RAG evaluation | Faithfulness, answer correctness, retrieval relevance (recall@k), citation accuracy | Yes — you have built a golden-set harness before |

## 2. Tools and libraries

| Need | Choice | Notes |
|---|---|---|
| Language | Python 3.11+ | Same as Project 15 |
| Vector store | ChromaDB (file-based) | Shared with Project 15's memory tier. Qdrant if you want a server later |
| Sparse index | `rank_bm25` | Pure Python, fine for ≤ 100k chunks |
| Embeddings | **Local: `nomic-embed-text` or `bge-m3` via Ollama (CPU, $0, no rate limit)** | Fallback: Google AI Studio's free embedding endpoint. Same `ollama` service and same choice as Project 15's memory tier, so the two projects share one index format |
| Reranker | Local cross-encoder: `BAAI/bge-reranker-v2-m3` via `sentence-transformers` | Runs on CPU; ~50 ms per 20 pairs. Alternative: LLM rerank on the free `cheap` role from Project 15's `models.yaml` (Groq Llama 3.3 70B) |
| Generation / judge | Project 15's free role chains (`config/models.yaml`) | Supervisor chain for answers, reviewer chain (Gemini Flash) for citation verification — different family from the generator. $0 |
| Chunking | Your own splitter, or LangChain `RecursiveCharacterTextSplitter` | Write the fixed and heading-aware ones yourself — it's 60 lines and you'll be asked about it |
| Document loading | `pymupdf` (PDF), `markdown-it-py`, `beautifulsoup4` (HTML) | You have PyMuPDF from DocIntel |
| API | FastAPI | `POST /v1/ask`, `POST /v1/ingest`, `GET /v1/documents` |
| Eval | Your own harness (reuse Project 15's eval runner) + optionally RAGAS for cross-checking | Golden Q&A set of 50+ hand-written pairs |
| Containers | Docker Compose | API + ChromaDB + seed script |

## 3. Accounts and keys

- TokenRouter key (already in `tokens.txt` — move it to a `.env`, see Project 15's deep-dive §9)
- Nothing else. No GPU. A laptop with 16 GB RAM runs the reranker and a local embedder comfortably.

## 4. A corpus to index

Pick one **public, technical** documentation set so the demo is reproducible and BM25 has
exact tokens to shine on. Good options: the FastAPI docs, the Pydantic docs, the PostgreSQL
manual (a subset), or the MCP specification. Around 200–800 pages is enough. Do **not** use
employer documents.

## 5. What you already have that transfers

- **Golden-set discipline** — hand-written Q&A pairs, per-category accuracy, regression diffs.
- **Grounded drafting** — the legal-CRM AI workers draft from documents; the citation layer
  here is a formalisation of that.
- **Document pipeline** — DocIntel's PDF handling and chunk-and-merge.

## 6. What is genuinely new for you

1. Embeddings / vector search / BM25 / RRF / reranking — the whole retrieval stack.
2. Chunking-strategy comparison as an experiment (three strategies, same eval suite, a table).
3. Citation verification as a separate quality gate after generation.

Budget ~12 days. Days 1–6 are the retrieval engine; that is where the interview questions come from.
