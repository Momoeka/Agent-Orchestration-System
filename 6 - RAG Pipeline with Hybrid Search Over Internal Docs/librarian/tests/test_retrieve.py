"""RRF math, reranker fallback, the indexer's dedup, and the search facade end to end."""

from __future__ import annotations

import uuid

import chromadb
import pytest

from librarian.config import Settings
from librarian.index.dense import DenseIndex
from librarian.index.indexer import Indexer
from librarian.index.sparse import SparseIndex, tokenize
from librarian.index.store import ChunkStore
from librarian.retrieve.fuse import rrf
from librarian.retrieve.rerank import NoReranker, build_reranker
from librarian.retrieve.retriever import Retriever
from librarian.types import Document, Format, SearchHit, Strategy


class HashEmbedder:
    """Deterministic bag-of-words vectors: same tokens → same vector; shared tokens → close."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            v = [0.0] * 32
            for tok in tokenize(t):
                v[hash(tok) % 32] += 1.0
            norm = sum(x * x for x in v) ** 0.5 or 1.0
            out.append([x / norm for x in v])
        return out


def test_rrf_math_and_weights() -> None:
    fused = rrf({"dense": ["a", "b"], "sparse": ["b", "c"]}, k=60)
    scores = dict(fused)
    assert scores["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert scores["a"] == pytest.approx(1 / 61) and scores["c"] == pytest.approx(1 / 62)
    assert [cid for cid, _ in fused] == ["b", "a", "c"]
    # weights shift the winner
    weighted = rrf(
        {"dense": ["a"], "sparse": ["b"]}, weights={"dense": 0.1, "sparse": 1.0}, k=60
    )
    assert weighted[0][0] == "b"
    with pytest.raises(ValueError, match="positive"):
        rrf({"dense": ["a"]}, k=0)


def test_reranker_none_and_degrade() -> None:
    hits = [
        SearchHit(chunk_id="a", doc_id="d", source="s", text="t1"),
        SearchHit(chunk_id="b", doc_id="d", source="s", text="t2"),
    ]
    def settings(**kw: str) -> Settings:
        return Settings(_env_file=None, **kw)  # type: ignore[call-arg, arg-type]

    assert NoReranker().rerank("q", hits) == hits
    assert isinstance(build_reranker(settings(reranker="none")), NoReranker)
    # cross-encoder not installed in the unit env → degrades to NoReranker, never raises
    degraded = build_reranker(settings(reranker="cross-encoder"))
    assert degraded.rerank("q", hits)[:1] == hits[:1]


def build_world() -> tuple[ChunkStore, DenseIndex, HashEmbedder, Indexer]:
    store = ChunkStore()
    dense = DenseIndex(chromadb.EphemeralClient(), space_id=f"t-{uuid.uuid4().hex[:8]}")
    embedder = HashEmbedder()
    return store, dense, embedder, Indexer(store, dense, embedder, dedup_threshold=0.95)


def doc(doc_id: str, text: str) -> Document:
    return Document(doc_id=doc_id, source=f"{doc_id}.md", format=Format.MARKDOWN, text=text)


def test_indexer_dedup_skips_repeats_within_and_across_documents() -> None:
    store, dense, _, indexer = build_world()
    repeated = "alpha beta gamma delta " * 4  # fixed chunks with identical token bags
    r1 = indexer.ingest(doc("d1", repeated), strategy=Strategy.FIXED, size=23, overlap=0)
    assert r1.chunks_added == 1 and r1.duplicates_skipped >= 2
    # a second document with the same content adds nothing
    r2 = indexer.ingest(doc("d2", repeated), strategy=Strategy.FIXED, size=23, overlap=0)
    assert r2.chunks_added == 0 and r2.duplicates_skipped >= 1
    assert store.counts()["chunks"] == dense.count() == 1


def test_search_modes_end_to_end() -> None:
    store, dense, embedder, indexer = build_world()
    corpus = {
        "cats": "# Cats\n\nThe cat sat on the mat. Cats nap on mats all day long.\n",
        "api": "# API\n\nThe response_model parameter controls the output schema shape.\n",
        "cooking": "# Soup\n\nSimmer the broth gently and season the soup with thyme.\n",
    }
    for name, text in corpus.items():
        indexer.ingest(doc(name, text), strategy=Strategy.HEADING, size=500)
    sparse = SparseIndex(store.all_chunks())
    retriever = Retriever(store, dense, sparse, embedder, NoReranker())

    hybrid = retriever.search("cat on the mat", k=2, mode="hybrid")
    assert hybrid and hybrid[0].doc_id == "cats"
    top = hybrid[0]
    assert top.rrf_score is not None and top.dense_rank is not None and top.sparse_rank == 1
    assert top.heading_path == ["Cats"] and top.score == top.rrf_score

    exact = retriever.search("response_model", k=1, mode="sparse")
    assert exact[0].doc_id == "api" and exact[0].dense_rank is None
    assert exact[0].score == exact[0].sparse_score

    dense_only = retriever.search("simmer the broth and season the soup", k=1, mode="dense")
    assert dense_only[0].doc_id == "cooking"
    assert dense_only[0].rrf_score is None and dense_only[0].sparse_rank is None

    assert retriever.search("cats", k=1) != []  # default mode works
