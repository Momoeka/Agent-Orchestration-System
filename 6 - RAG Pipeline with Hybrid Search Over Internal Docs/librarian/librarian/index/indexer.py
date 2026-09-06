"""Ingestion pipeline: load → chunk → embed → dedup → store + dense index (Phases.md, Phase 1-2).

Dedup happens here, against the dense index (cosine above the threshold = near-duplicate,
skipped and counted), chunk by chunk so intra-document duplicates are caught too.
"""

from __future__ import annotations

from pydantic import BaseModel

from librarian.index.dense import DenseIndex
from librarian.index.store import ChunkStore
from librarian.ingest.chunkers import Embedder, chunk_document
from librarian.types import Document, Strategy


class IngestReport(BaseModel):
    doc_id: str
    source: str
    chunks_added: int
    duplicates_skipped: int


class Indexer:
    def __init__(
        self,
        store: ChunkStore,
        dense: DenseIndex,
        embedder: Embedder,
        *,
        dedup_threshold: float = 0.95,
    ) -> None:
        self._store = store
        self._dense = dense
        self._embedder = embedder
        self._threshold = dedup_threshold

    def ingest(
        self,
        doc: Document,
        *,
        strategy: Strategy,
        size: int = 1200,
        overlap: int = 200,
    ) -> IngestReport:
        chunks = chunk_document(
            doc, strategy, size=size, overlap=overlap, embedder=self._embedder
        )
        vectors = self._embedder.embed([c.text for c in chunks]) if chunks else []
        self._store.upsert_document(doc)
        added = 0
        skipped = 0
        for chunk, vector in zip(chunks, vectors, strict=True):
            near = self._dense.nearest(vector)
            if near is not None and near[1] > self._threshold and near[0] != chunk.chunk_id:
                skipped += 1
                continue
            self._store.upsert_chunks([chunk])
            self._dense.add([chunk], [vector])
            added += 1
        return IngestReport(
            doc_id=doc.doc_id, source=doc.source, chunks_added=added, duplicates_skipped=skipped
        )
