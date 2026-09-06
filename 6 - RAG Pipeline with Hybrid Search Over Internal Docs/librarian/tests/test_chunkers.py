"""Chunker invariants: nothing lost, sizes bounded, offsets faithful, headings attached."""

from __future__ import annotations

from itertools import pairwise

import pytest

from librarian.ingest.chunkers import chunk_document, cosine
from librarian.types import Chunk, Document, Format, Strategy

DOC_TEXT = """# Guide

Intro paragraph that sets the scene for everything below.

## Install

Run the installer. It downloads packages. The cache directory can be moved.

## Configure

Set the API key in the environment. Never commit the key. Restart after changing it.

### Advanced

Timeouts are in seconds. Retries use exponential backoff with jitter to avoid thundering herds.
"""


def doc(text: str = DOC_TEXT, pages: list[int] | None = None) -> Document:
    return Document(
        doc_id="d1",
        source="guide.md",
        format=Format.MARKDOWN,
        title="Guide",
        text=text,
        page_offsets=pages or [],
    )


def covers_everything(d: Document, chunks: list[Chunk]) -> None:
    assert chunks, "no chunks produced"
    assert chunks[0].start == 0 and chunks[-1].end == len(d.text)
    for a, b in pairwise(chunks):
        assert b.start <= a.end, f"gap between {a.index} and {b.index}"
    for c in chunks:
        assert c.text == d.text[c.start : c.end]


def test_fixed_bounds_overlap_and_coverage() -> None:
    d = doc(text=" ".join(f"word{i}" for i in range(600)) + "\n")
    chunks = chunk_document(d, Strategy.FIXED, size=500, overlap=100)
    covers_everything(d, chunks)
    assert all(c.n_chars <= 500 for c in chunks)
    for a, b in pairwise(chunks):
        assert a.end - b.start in range(0, 101)  # overlap kept
    # whitespace-aligned splits: no chunk starts mid-word
    for c in chunks[1:]:
        assert d.text[c.start - 1].isspace()


def test_fixed_rejects_bad_params() -> None:
    with pytest.raises(ValueError, match="overlap"):
        chunk_document(doc(), Strategy.FIXED, size=100, overlap=100)


def test_heading_paths_and_coverage() -> None:
    d = doc()
    chunks = chunk_document(d, Strategy.HEADING, size=180)
    covers_everything(d, chunks)
    paths = {tuple(c.heading_path) for c in chunks}
    # the short Install section packs into the Guide preamble chunk (greedy packing);
    # its heading stays visible in that chunk's text, and deeper sections start their own
    assert ("Guide", "Configure") in paths
    assert ("Guide", "Configure", "Advanced") in paths
    packed = next(c for c in chunks if tuple(c.heading_path) == ("Guide",))
    assert "## Install" in packed.text
    # a tighter budget gives Install its own chunk and path
    small = chunk_document(d, Strategy.HEADING, size=90)
    assert ("Guide", "Install") in {tuple(c.heading_path) for c in small}
    for c in chunks:
        if c.heading_path:
            assert c.text.lstrip().startswith("#")


def test_heading_oversized_section_is_split_but_keeps_its_path() -> None:
    big = "# Top\n\n" + ("sentence about one thing. " * 60)
    d = doc(text=big)
    chunks = chunk_document(d, Strategy.HEADING, size=300)
    covers_everything(d, chunks)
    assert len(chunks) > 1
    assert all(c.heading_path == ["Top"] for c in chunks)
    assert all(c.n_chars <= 300 for c in chunks)


def test_preamble_before_first_heading_is_kept() -> None:
    d = doc(text="no heading yet, just prose.\n\n# Later\n\nBody.\n")
    chunks = chunk_document(d, Strategy.HEADING, size=1000)
    covers_everything(d, chunks)
    assert chunks[0].start == 0 and chunks[0].heading_path in ([], ["Later"])


class TopicEmbedder:
    """Fake embedder: identical vectors inside a topic, orthogonal across topics."""

    def __init__(self, topics: dict[str, int]) -> None:
        self.topics = topics

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            axis = next((axis for word, axis in self.topics.items() if word in t), 0)
            vec = [0.0, 0.0, 0.0]
            vec[axis] = 1.0
            out.append(vec)
        return out


def test_semantic_splits_on_topic_change_and_respects_size_cap() -> None:
    text = (
        "Cats purr softly. Cats nap all day. Cats chase string. "
        "Rockets burn fuel. Rockets reach orbit quickly.\n"
    )
    d = doc(text=text)
    embedder = TopicEmbedder({"Rocket": 1})
    chunks = chunk_document(d, Strategy.SEMANTIC, size=10_000, embedder=embedder)
    assert len(chunks) == 2
    assert "Cats" in chunks[0].text and "Rockets" in chunks[1].text
    covers_everything(d, chunks)
    # the cap is soft by at most one sentence: a topic bigger than `size` is split early
    capped = chunk_document(d, Strategy.SEMANTIC, size=40, embedder=embedder)
    assert len(capped) >= 3 and all(c.n_chars <= 40 + 40 for c in capped)


def test_semantic_requires_an_embedder() -> None:
    with pytest.raises(ValueError, match="embedder"):
        chunk_document(doc(), Strategy.SEMANTIC)


def test_pages_are_resolved_from_offsets() -> None:
    d = doc(text="page one text\npage two text\n", pages=[0, 14])
    chunks = chunk_document(d, Strategy.FIXED, size=14, overlap=0)
    assert chunks[0].page == 1 and chunks[-1].page == 2


def test_cosine() -> None:
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine([0.0], [0.0]) == 0.0
