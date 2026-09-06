"""Chunk store, dense and sparse indexes, embedder naming, settings."""

from __future__ import annotations

import uuid
from pathlib import Path

import chromadb
import pytest

from librarian.config import Settings
from librarian.errors import NonRetryableError
from librarian.index.dense import DenseIndex
from librarian.index.sparse import SparseIndex, tokenize
from librarian.index.store import ChunkStore, document_stub
from librarian.llm.embeddings import OpenAICompatEmbedder, build_embedder
from librarian.types import Chunk, Strategy


def settings(**kw: str) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg, arg-type]


def chunk(cid: str, text: str, doc: str = "d1") -> Chunk:
    return Chunk(
        chunk_id=cid,
        doc_id=doc,
        source="s.md",
        strategy=Strategy.FIXED,
        index=0,
        text=text,
        start=0,
        end=len(text),
        heading_path=["H"],
        page=None,
    )


def test_store_roundtrip_and_counts(tmp_path: Path) -> None:
    store = ChunkStore(tmp_path / "chunks.db")
    store.upsert_document(document_stub("d1", "s.md", "markdown", "T", "body"))
    store.upsert_chunks([chunk("c1", "alpha"), chunk("c2", "beta")])
    got = store.get_chunk("c1")
    assert got is not None and got.text == "alpha" and got.heading_path == ["H"]
    assert store.get_chunk("nope") is None
    assert [c.chunk_id for c in store.all_chunks()] == ["c1", "c2"]
    assert store.counts() == {"documents": 1, "chunks": 2}
    docs = store.documents()
    assert docs[0]["doc_id"] == "d1" and docs[0]["chunks"] == 2
    # upsert is idempotent, not duplicating
    store.upsert_chunks([chunk("c1", "alpha v2")])
    assert store.counts()["chunks"] == 2
    updated = store.get_chunk("c1")
    assert updated is not None and updated.text == "alpha v2"
    store.close()


def fresh_dense() -> DenseIndex:
    # EphemeralClient is process-wide (Foreman's lesson): unique collection per test
    return DenseIndex(chromadb.EphemeralClient(), space_id=f"test-{uuid.uuid4().hex[:8]}")


def test_dense_add_query_similarity_order() -> None:
    dense = fresh_dense()
    assert dense.query([1.0, 0.0], k=3) == [] and dense.count() == 0
    dense.add(
        [chunk("a", "x"), chunk("b", "y"), chunk("c", "z")],
        [[1.0, 0.0], [0.8, 0.2], [0.0, 1.0]],
    )
    top = dense.query([1.0, 0.0], k=2)
    assert [cid for cid, _ in top] == ["a", "b"]
    assert top[0][1] == pytest.approx(1.0, abs=1e-5)  # cosine similarity, not distance
    near = dense.nearest([0.0, 1.0])
    assert near is not None and near[0] == "c"
    with pytest.raises(ValueError, match="vectors"):
        dense.add([chunk("d", "w")], [])


def test_sparse_exact_tokens_win() -> None:
    chunks = [
        chunk("a", "The response_model parameter controls output serialisation."),
        chunk("b", "Model responses are shaped by configuration and validation."),
        chunk("c", "Completely unrelated text about cats."),
    ]
    sparse = SparseIndex(chunks)
    top = sparse.query("what does response_model do", k=3)
    assert top[0][0] == "a" and top[0][1] > 0
    assert "c" not in [cid for cid, _ in top]  # zero-score rows dropped
    assert SparseIndex([]).query("anything", k=5) == []
    assert tokenize("HTTP_404_NOT_FOUND!") == ["http_404_not_found"]


def test_embedder_space_id_and_build() -> None:
    e = OpenAICompatEmbedder(provider_id="ollama", model="nomic-embed-text", base_url="http://x")
    assert e.space_id == "ollama-nomic-embed-text"
    weird = OpenAICompatEmbedder(provider_id="gemini", model="models/embed 2.0", base_url="http://x")
    assert weird.space_id == "gemini-models-embed-2.0"

    s = settings()
    assert build_embedder(s).provider_id == "ollama"
    with pytest.raises(NonRetryableError, match="GEMINI_API_KEY"):
        build_embedder(settings(embedding_provider="gemini"))
    with pytest.raises(NonRetryableError, match="Unknown"):
        build_embedder(settings(embedding_provider="voodoo"))


def test_settings_read_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEIGHT_DENSE", "0.5")
    monkeypatch.setenv("RERANKER", "none")
    s = settings()
    assert s.weight_dense == 0.5 and s.reranker == "none"
    assert s.enable_paid_providers is False  # paid stays off unless flipped on purpose
