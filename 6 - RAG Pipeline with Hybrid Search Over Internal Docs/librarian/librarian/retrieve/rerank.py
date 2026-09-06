"""Reranking: a cross-encoder scores (query, chunk) pairs jointly on the fused top-20 and
keeps the best (Phases.md, Phase 2). The heavy model is optional (`uv sync --group rerank`);
when it cannot load, retrieval degrades to fused order — logged, never silent (Rules.md §3)."""

from __future__ import annotations

import logging
from typing import Protocol

from librarian.config import Settings
from librarian.types import SearchHit

log = logging.getLogger(__name__)


class Reranker(Protocol):
    def rerank(self, query: str, hits: list[SearchHit]) -> list[SearchHit]: ...


class NoReranker:
    """Keeps fused order. The explicit degraded mode, also useful in evals as a baseline."""

    def rerank(self, query: str, hits: list[SearchHit]) -> list[SearchHit]:
        return hits


class CrossEncoderReranker:
    def __init__(self, model_name: str) -> None:
        from sentence_transformers import CrossEncoder  # heavy import, optional dependency

        self.model_name = model_name
        self._model = CrossEncoder(model_name)

    def rerank(self, query: str, hits: list[SearchHit]) -> list[SearchHit]:
        if not hits:
            return hits
        scores = self._model.predict([(query, h.text) for h in hits])
        rescored = [
            h.model_copy(update={"rerank_score": float(s)})
            for h, s in zip(hits, scores, strict=True)
        ]
        return sorted(rescored, key=lambda h: (-(h.rerank_score or 0.0), h.chunk_id))


def build_reranker(settings: Settings) -> Reranker:
    if settings.reranker == "none":
        return NoReranker()
    try:
        return CrossEncoderReranker(settings.reranker_model)
    except ImportError:
        log.warning(
            "sentence-transformers not installed (uv sync --group rerank); "
            "degrading to fused order"
        )
    except OSError as e:
        log.warning("reranker model %s failed to load (%s); degrading", settings.reranker_model, e)
    return NoReranker()
