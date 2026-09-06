"""The search facade (Architecture.md §3 steps 1-3): every caller — API, dashboard, Foreman's
`search_docs`, the eval harness — goes through `Retriever.search`. Modes exist so hybrid vs
dense-only is a measured comparison, not a belief."""

from __future__ import annotations

from typing import Literal

from librarian.index.dense import DenseIndex
from librarian.index.sparse import SparseIndex
from librarian.index.store import ChunkStore
from librarian.ingest.chunkers import Embedder
from librarian.retrieve.fuse import rrf
from librarian.retrieve.rerank import Reranker
from librarian.types import SearchHit

Mode = Literal["hybrid", "dense", "sparse"]


class Retriever:
    def __init__(
        self,
        store: ChunkStore,
        dense: DenseIndex,
        sparse: SparseIndex,
        embedder: Embedder,
        reranker: Reranker,
        *,
        dense_k: int = 10,
        sparse_k: int = 10,
        fuse_keep: int = 20,
        rrf_k: int = 60,
        weight_dense: float = 0.7,
        weight_sparse: float = 0.3,
    ) -> None:
        self._store = store
        self._dense = dense
        self._sparse = sparse
        self._embedder = embedder
        self._reranker = reranker
        self._dense_k = dense_k
        self._sparse_k = sparse_k
        self._fuse_keep = fuse_keep
        self._rrf_k = rrf_k
        self._weights = {"dense": weight_dense, "sparse": weight_sparse}

    def search(self, query: str, *, k: int = 5, mode: Mode = "hybrid") -> list[SearchHit]:
        dense_hits: list[tuple[str, float]] = []
        sparse_hits: list[tuple[str, float]] = []
        if mode in ("hybrid", "dense"):
            vector = self._embedder.embed([query])[0]
            dense_hits = self._dense.query(vector, self._dense_k)
        if mode in ("hybrid", "sparse"):
            sparse_hits = self._sparse.query(query, self._sparse_k)

        if mode == "hybrid":
            fused = rrf(
                {"dense": [c for c, _ in dense_hits], "sparse": [c for c, _ in sparse_hits]},
                weights=self._weights,
                k=self._rrf_k,
            )[: self._fuse_keep]
        else:
            fused = [(cid, score) for cid, score in (dense_hits or sparse_hits)][: self._fuse_keep]

        dense_at = {cid: (rank, score) for rank, (cid, score) in enumerate(dense_hits, 1)}
        sparse_at = {cid: (rank, score) for rank, (cid, score) in enumerate(sparse_hits, 1)}
        hits: list[SearchHit] = []
        for cid, fused_score in fused:
            chunk = self._store.get_chunk(cid)
            if chunk is None:  # index ahead of store would be a bug; skip defensively
                continue
            d = dense_at.get(cid)
            s = sparse_at.get(cid)
            hits.append(
                SearchHit(
                    chunk_id=chunk.chunk_id,
                    doc_id=chunk.doc_id,
                    source=chunk.source,
                    text=chunk.text,
                    heading_path=chunk.heading_path,
                    page=chunk.page,
                    dense_rank=d[0] if d else None,
                    dense_score=d[1] if d else None,
                    sparse_rank=s[0] if s else None,
                    sparse_score=s[1] if s else None,
                    rrf_score=fused_score if mode == "hybrid" else None,
                )
            )
        return self._reranker.rerank(query, hits)[:k]
