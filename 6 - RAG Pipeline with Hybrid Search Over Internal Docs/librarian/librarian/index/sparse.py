"""Sparse index: BM25 over the chunk store. Rebuilt from the store on load — in sync by
construction, never fed separately (Rules.md §2). ``\\w+`` tokenisation keeps the exact tokens
BM25 exists for: ``response_model``, ``HTTP_404_NOT_FOUND``, error codes."""

from __future__ import annotations

import re
from collections.abc import Sequence

from rank_bm25 import BM25Okapi

from librarian.types import Chunk

_TOKEN = re.compile(r"\w+")


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text)]


class SparseIndex:
    def __init__(self, chunks: Sequence[Chunk]) -> None:
        self._ids = [c.chunk_id for c in chunks]
        self._bm25 = BM25Okapi([tokenize(c.text) for c in chunks]) if chunks else None

    def query(self, text: str, k: int) -> list[tuple[str, float]]:
        """Top-k as ``(chunk_id, bm25 score)``, best first; zero-score rows are dropped."""
        if self._bm25 is None:
            return []
        scores = self._bm25.get_scores(tokenize(text))
        ranked = sorted(zip(self._ids, scores, strict=True), key=lambda p: (-p[1], p[0]))
        return [(cid, float(s)) for cid, s in ranked[:k] if s > 0.0]

    def __len__(self) -> int:
        return len(self._ids)
