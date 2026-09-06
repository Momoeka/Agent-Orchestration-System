"""Reciprocal Rank Fusion (Phases.md, Phase 2).

Rank-based on purpose: dense cosine and BM25 scores live on incomparable scales, so fusing by
rank position sidesteps score normalisation entirely. ``score(id) = Σ_source w_s / (k + rank)``
with 1-based ranks; ties break by id for determinism.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

DEFAULT_RRF_K = 60


def rrf(
    rankings: Mapping[str, Sequence[str]],
    *,
    weights: Mapping[str, float] | None = None,
    k: int = DEFAULT_RRF_K,
) -> list[tuple[str, float]]:
    """Fuse named ranked lists of ids into one list of ``(id, score)``, best first.

    ``rankings`` maps a source name ("dense", "sparse") to its ids in rank order.
    Missing weights default to 1.0. An id absent from a source simply earns nothing there.
    """
    if k <= 0:
        raise ValueError(f"rrf k must be positive, got {k}")
    weights = weights or {}
    scores: dict[str, float] = {}
    for source, ids in rankings.items():
        w = weights.get(source, 1.0)
        for rank, item in enumerate(ids, start=1):
            scores[item] = scores.get(item, 0.0) + w / (k + rank)
    return sorted(scores.items(), key=lambda p: (-p[1], p[0]))
