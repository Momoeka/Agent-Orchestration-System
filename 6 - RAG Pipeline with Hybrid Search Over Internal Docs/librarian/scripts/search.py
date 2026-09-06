"""Search the indexed corpus from the command line — the Phase 2 demo.

Usage:  uv run python scripts/search.py "how do I set a response_model" [--k 5] [--mode hybrid]
        --compare   run hybrid and dense side by side for the same query
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from librarian.config import get_settings
from librarian.index.dense import DenseIndex, build_client
from librarian.index.sparse import SparseIndex
from librarian.index.store import ChunkStore
from librarian.llm.embeddings import build_embedder
from librarian.retrieve.rerank import build_reranker
from librarian.retrieve.retriever import Mode, Retriever


def show(hits: list, header: str) -> None:  # type: ignore[type-arg]
    print(f"\n=== {header}")
    for i, h in enumerate(hits, 1):
        where = " > ".join(h.heading_path) or Path(h.source).name
        stages = (
            f"dense#{h.dense_rank or '-'} sparse#{h.sparse_rank or '-'} "
            f"rrf={h.rrf_score:.4f}" if h.rrf_score is not None else
            f"dense#{h.dense_rank or '-'} sparse#{h.sparse_rank or '-'}"
        )
        rerank = f" rerank={h.rerank_score:.3f}" if h.rerank_score is not None else ""
        print(f"[{i}] {where}  ({stages}{rerank})")
        print(f"    {h.text[:220].replace(chr(10), ' ')}...")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--mode", default="hybrid", choices=["hybrid", "dense", "sparse"])
    ap.add_argument("--compare", action="store_true", help="hybrid vs dense side by side")
    args = ap.parse_args()

    settings = get_settings()
    embedder = build_embedder(settings)
    store = ChunkStore(settings.store_path)
    if store.counts()["chunks"] == 0:
        print("index is empty; run scripts/seed.py first")
        return 1
    retriever = Retriever(
        store,
        DenseIndex(build_client(settings), space_id=embedder.space_id),
        SparseIndex(store.all_chunks()),
        embedder,
        build_reranker(settings),
        dense_k=settings.dense_k,
        sparse_k=settings.sparse_k,
        fuse_keep=settings.fuse_keep,
        rrf_k=settings.rrf_k,
        weight_dense=settings.weight_dense,
        weight_sparse=settings.weight_sparse,
    )
    if args.compare:
        for mode in ("hybrid", "dense"):
            show(retriever.search(args.query, k=args.k, mode=cast(Mode, mode)), mode)
    else:
        show(retriever.search(args.query, k=args.k, mode=cast(Mode, args.mode)), args.mode)
    return 0


if __name__ == "__main__":
    sys.exit(main())
