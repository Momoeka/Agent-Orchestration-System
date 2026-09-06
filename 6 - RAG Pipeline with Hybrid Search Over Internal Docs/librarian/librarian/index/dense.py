"""Dense index on ChromaDB. One collection per vector space (Rules.md §2): the collection name
embeds the embedder's ``space_id``, so switching embedding models can never mix spaces."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import chromadb
from chromadb.api import ClientAPI

from librarian.config import Settings
from librarian.types import Chunk


def build_client(settings: Settings) -> ClientAPI:
    if settings.chroma_host:
        return chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
    settings.chroma_path.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(settings.chroma_path))


class DenseIndex:
    """A view over the collection, optionally scoped to one chunking strategy.

    All strategies share the collection (one per vector space); the ``strategy`` scope keeps
    queries — and, critically, dedup — inside one strategy: without it, seeding a second
    strategy would find the first strategy's chunks as near-duplicates and skip everything.
    """

    def __init__(self, client: ClientAPI, space_id: str, *, strategy: str | None = None) -> None:
        self.collection_name = f"librarian-{space_id}"
        self.strategy = strategy
        self._where: dict[str, Any] | None = {"strategy": strategy} if strategy else None
        self._coll = client.get_or_create_collection(
            self.collection_name, metadata={"hnsw:space": "cosine"}
        )

    def add(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        if not chunks:
            return
        if len(chunks) != len(vectors):
            raise ValueError(f"{len(chunks)} chunks but {len(vectors)} vectors")
        embeddings: list[Sequence[float] | Sequence[int]] = [v for v in vectors]
        self._coll.add(
            ids=[c.chunk_id for c in chunks],
            embeddings=embeddings,
            metadatas=[
                {"doc_id": c.doc_id, "source": c.source, "strategy": c.strategy.value}
                for c in chunks
            ],
        )

    def query(self, vector: list[float], k: int) -> list[tuple[str, float]]:
        """Top-k as ``(chunk_id, cosine similarity)``, best first, within this view's scope."""
        n = min(k, self.count())
        if n == 0:
            return []
        queries: list[Sequence[float] | Sequence[int]] = [vector]
        res: dict[str, Any] = dict(
            self._coll.query(query_embeddings=queries, n_results=n, where=self._where)
        )
        ids = res["ids"][0]
        distances = res["distances"][0]
        return [(cid, 1.0 - float(d)) for cid, d in zip(ids, distances, strict=True)]

    def nearest(self, vector: list[float]) -> tuple[str, float] | None:
        top = self.query(vector, 1)
        return top[0] if top else None

    def count(self) -> int:
        return int(self._coll.count())
