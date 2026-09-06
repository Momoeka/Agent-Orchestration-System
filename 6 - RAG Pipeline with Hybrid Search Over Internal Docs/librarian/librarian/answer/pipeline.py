"""ask(): retrieve → generate → verify → decide (Architecture.md §3). The one entry point the
API, dashboard and evals call for a full answer; Foreman's `search_docs` stops at retrieval."""

from __future__ import annotations

import time

from librarian.answer.confidence import build_confidence
from librarian.answer.generate import generate, is_refusal
from librarian.answer.verify import extract_citations, normalize_citation_brackets, verify
from librarian.config import Settings
from librarian.errors import RetryableError
from librarian.index.dense import DenseIndex, build_client
from librarian.index.sparse import SparseIndex
from librarian.index.store import ChunkStore
from librarian.llm.embeddings import build_embedder
from librarian.llm.providers import Chat, build_chat
from librarian.retrieve.rerank import build_reranker
from librarian.retrieve.retriever import Mode, Retriever
from librarian.types import Answer, Confidence


class AnswerPipeline:
    def __init__(
        self,
        retriever: Retriever,
        generator: Chat,
        judge: Chat,
        *,
        context_k: int = 5,
        max_tokens: int = 1200,
        threshold: float = 0.55,
    ) -> None:
        self._retriever = retriever
        self._generator = generator
        self._judge = judge
        self._context_k = context_k
        self._max_tokens = max_tokens
        self._threshold = threshold

    def ask(self, question: str, *, mode: Mode = "hybrid") -> Answer:
        started = time.perf_counter()
        hits = self._retriever.search(question, k=self._context_k, mode=mode)
        if not hits:
            return Answer(
                question=question,
                text="NOT IN CORPUS\nRetrieval returned nothing for this question — "
                "the index may be empty or the topic entirely absent.",
                refusal=True,
                confidence=Confidence(),
                elapsed_s=round(time.perf_counter() - started, 2),
            )
        try:
            text = generate(
                self._generator, question, hits, max_tokens=self._max_tokens
            )
        except RetryableError:
            return Answer(
                question=question,
                text="NOT IN CORPUS\nEvery model in the generator chain is unavailable "
                "right now; retrieval found candidate passages (attached) but no answer "
                "was written. Try again shortly.",
                refusal=True,
                hits=hits,
                confidence=Confidence(),
                elapsed_s=round(time.perf_counter() - started, 2),
            )
        text = normalize_citation_brackets(text)  # displayed text must match parsed [n]
        generator_used = self._generator.last_used

        if is_refusal(text):
            return Answer(
                question=question,
                text=text,
                refusal=True,
                hits=hits,
                generator=generator_used,
                confidence=build_confidence(hits, [], 3),
                elapsed_s=round(time.perf_counter() - started, 2),
            )

        citations = extract_citations(text, hits)
        citations, completeness = verify(self._judge, question, text, hits, citations)
        confidence = build_confidence(hits, citations, completeness)
        return Answer(
            question=question,
            text=text,
            refusal=confidence.composite < self._threshold,
            citations=citations,
            confidence=confidence,
            hits=hits,
            generator=generator_used,
            judge=self._judge.last_used,
            elapsed_s=round(time.perf_counter() - started, 2),
        )


def build_pipeline(settings: Settings, *, strategy: str | None = None) -> AnswerPipeline:
    """Wire the whole stack from settings — used by scripts, the API, and the eval runner.

    ``strategy`` picks which chunking strategy's index view to search (the bake-off compares
    them); default is CHUNK_STRATEGY from settings.
    """
    from librarian.types import Strategy

    active = Strategy(strategy or settings.chunk_strategy)
    embedder = build_embedder(settings)
    store = ChunkStore(settings.store_path)
    retriever = Retriever(
        store,
        DenseIndex(build_client(settings), space_id=embedder.space_id, strategy=active.value),
        SparseIndex(store.all_chunks(active)),
        embedder,
        build_reranker(settings),
        dense_k=settings.dense_k,
        sparse_k=settings.sparse_k,
        fuse_keep=settings.fuse_keep,
        rrf_k=settings.rrf_k,
        weight_dense=settings.weight_dense,
        weight_sparse=settings.weight_sparse,
    )
    return AnswerPipeline(
        retriever,
        build_chat(settings, "generator"),
        build_chat(settings, "judge"),
        context_k=settings.answer_context_k,
        max_tokens=settings.answer_max_tokens,
        threshold=settings.answer_confidence_threshold,
    )
