"""Composite confidence and the refusal decision (Architecture.md §3 step 6).

Heuristics are deliberate and documented; the weights and threshold are Phase 4 tuning
targets, not truths. Below the threshold, the caller returns a structured refusal — never a
best-effort guess (Rules.md §2).
"""

from __future__ import annotations

from librarian.types import Citation, Confidence, SearchHit

WEIGHT_RETRIEVAL = 0.35
WEIGHT_COVERAGE = 0.45
WEIGHT_COMPLETENESS = 0.20


def retrieval_confidence(hits: list[SearchHit]) -> float:
    """Agreement-based: a chunk both retrievers ranked is strong evidence; one-sided is
    weaker. (Raw dense/BM25 scores live on incomparable scales — agreement does not.)"""
    if not hits:
        return 0.0
    per_hit = [
        1.0 if (h.dense_rank is not None and h.sparse_rank is not None) else 0.6 for h in hits
    ]
    return sum(per_hit) / len(per_hit)


def citation_coverage(citations: list[Citation]) -> float:
    """Verified share of citations. No citations at all = 0 — an uncited answer earns no
    trust. Unverified (judge unavailable) counts as unsupported: fail toward honesty."""
    if not citations:
        return 0.0
    return sum(1 for c in citations if c.supported is True) / len(citations)


def build_confidence(
    hits: list[SearchHit], citations: list[Citation], completeness_1_5: int
) -> Confidence:
    retrieval = retrieval_confidence(hits)
    coverage = citation_coverage(citations)
    completeness = completeness_1_5 / 5.0
    composite = (
        WEIGHT_RETRIEVAL * retrieval
        + WEIGHT_COVERAGE * coverage
        + WEIGHT_COMPLETENESS * completeness
    )
    return Confidence(
        retrieval=round(retrieval, 4),
        citation_coverage=round(coverage, 4),
        completeness=round(completeness, 4),
        composite=round(composite, 4),
    )
