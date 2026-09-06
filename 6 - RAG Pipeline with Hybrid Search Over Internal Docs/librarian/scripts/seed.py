"""Ingest the corpus into the chunk store and dense index (Phases.md, Phase 2).

Usage:  uv run python scripts/seed.py [--limit N] [--strategy heading|fixed|semantic]
Re-running is safe: chunk ids are deterministic and near-duplicates are skipped.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from librarian.config import get_settings
from librarian.index.dense import DenseIndex, build_client
from librarian.index.indexer import Indexer
from librarian.index.store import ChunkStore
from librarian.ingest.loader import load_dir
from librarian.llm.embeddings import build_embedder
from librarian.types import Strategy


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="ingest at most N documents (0 = all)")
    ap.add_argument("--strategy", default="", help="override CHUNK_STRATEGY for this run")
    args = ap.parse_args()

    settings = get_settings()
    strategy = Strategy(args.strategy or settings.chunk_strategy)
    docs = load_dir(settings.corpus_dir)
    if not docs:
        print(f"no documents under {settings.corpus_dir}; run scripts/fetch_corpus.py first")
        return 1
    if args.limit:
        docs = docs[: args.limit]

    embedder = build_embedder(settings)
    store = ChunkStore(settings.store_path)
    # scoped to the strategy being seeded: dedup must never compare across strategies
    dense = DenseIndex(build_client(settings), space_id=embedder.space_id, strategy=strategy.value)
    indexer = Indexer(store, dense, embedder, dedup_threshold=settings.dedup_threshold)

    started = time.perf_counter()
    added = skipped = resumed = 0
    for i, doc in enumerate(docs, 1):
        if store.has_chunks(doc.doc_id, strategy):  # already seeded: a cut-off run resumes
            resumed += 1
            continue
        report = indexer.ingest(
            doc, strategy=strategy, size=settings.chunk_size, overlap=settings.chunk_overlap
        )
        added += report.chunks_added
        skipped += report.duplicates_skipped
        if i % 25 == 0 or i == len(docs):
            print(f"  {i}/{len(docs)} docs · {added} chunks · {skipped} dupes skipped")
    elapsed = time.perf_counter() - started
    counts = store.counts()
    print(
        f"done in {elapsed:.1f}s — {counts['documents']} documents, {counts['chunks']} chunks "
        f"({strategy} strategy; {resumed} docs already seeded), "
        f"dense collection '{dense.collection_name}' has {dense.count()}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
