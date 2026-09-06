"""Three hand-written chunking strategies (Phases.md, Phase 1).

All three return `Chunk`s whose ``start``/``end`` index the parent document's text — the
invariants (nothing lost, sizes bounded, offsets faithful) are tested, not assumed. Which
strategy wins is Phase 4's bake-off, not an opinion.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from math import sqrt
from typing import Protocol

from librarian.types import Chunk, Document, Strategy

DEFAULT_SIZE = 1200  # characters, ~300 tokens
DEFAULT_OVERLAP = 200
_HEADING = re.compile(r"^(#{1,6})\s+(\S.*)$", re.MULTILINE)
_SENTENCE = re.compile(r"[^.!?\n]+[.!?\n]*", re.MULTILINE)


class Embedder(Protocol):
    """What semantic chunking needs; Phase 2's Ollama client satisfies it, tests fake it."""

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = sqrt(sum(x * x for x in a)) * sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def chunk_document(
    doc: Document,
    strategy: Strategy,
    *,
    size: int = DEFAULT_SIZE,
    overlap: int = DEFAULT_OVERLAP,
    embedder: Embedder | None = None,
) -> list[Chunk]:
    if strategy is Strategy.FIXED:
        spans = _fixed_spans(doc.text, size=size, overlap=overlap)
        paths: list[list[str]] = [[] for _ in spans]
    elif strategy is Strategy.HEADING:
        spans, paths = _heading_spans(doc.text, size=size)
    else:
        if embedder is None:
            raise ValueError("semantic chunking needs an embedder")
        spans = _semantic_spans(doc.text, embedder, size=size)
        paths = [[] for _ in spans]
    chunks: list[Chunk] = []
    for i, ((start, end), path) in enumerate(zip(spans, paths, strict=True)):
        body = doc.text[start:end]
        if not body.strip():
            continue
        chunks.append(
            Chunk(
                chunk_id=f"{doc.doc_id}:{strategy}:{i}",
                doc_id=doc.doc_id,
                source=doc.source,
                strategy=strategy,
                index=i,
                text=body,
                start=start,
                end=end,
                heading_path=path,
                page=_page_of(doc, start),
            )
        )
    return chunks


def _page_of(doc: Document, offset: int) -> int | None:
    if not doc.page_offsets:
        return None
    return bisect_right(doc.page_offsets, offset)  # offsets are page starts; pages are 1-based


def _break_near(text: str, target: int, floor: int) -> int:
    """The last whitespace in (floor, target], or ``target`` when the run has none."""
    window = text[floor:target]
    for i in range(len(window) - 1, -1, -1):
        if window[i].isspace():
            return floor + i + 1
    return target


def _fixed_spans(text: str, *, size: int, overlap: int) -> list[tuple[int, int]]:
    if size <= 0 or not 0 <= overlap < size:
        raise ValueError(f"need size > 0 and 0 <= overlap < size, got {size=}, {overlap=}")
    spans: list[tuple[int, int]] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            end = _break_near(text, end, start + size // 2)
        spans.append((start, end))
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
        while start < end and not text[start - 1].isspace():
            start += 1  # overlap begins at a word boundary, never mid-word
    return spans


def _heading_spans(text: str, *, size: int) -> tuple[list[tuple[int, int]], list[list[str]]]:
    """Split on markdown headings, then greedily pack sections up to ``size``.

    A single section longer than ``size`` is fixed-split inside, keeping its heading path.
    The chunk's path is the heading stack where it *starts* — deeper headings packed into the
    same chunk stay visible in the chunk text itself.
    """
    marks = list(_HEADING.finditer(text))
    sections: list[tuple[int, int, list[str]]] = []
    stack: list[tuple[int, str]] = []  # (level, title)
    if not marks or marks[0].start() > 0:
        sections.append((0, marks[0].start() if marks else len(text), []))
    for i, m in enumerate(marks):
        level = len(m.group(1))
        stack = [(lv, t) for lv, t in stack if lv < level] + [(level, m.group(2).strip())]
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        sections.append((m.start(), end, [t for _, t in stack]))

    spans: list[tuple[int, int]] = []
    paths: list[list[str]] = []
    open_start: int | None = None
    open_path: list[str] = []

    def close(upto: int) -> None:
        nonlocal open_start
        if open_start is not None and upto > open_start:
            spans.append((open_start, upto))
            paths.append(open_path)
        open_start = None

    for start, end, path in sections:
        if end - start > size:  # oversized section: flush, then fixed-split it on its own
            close(start)
            for s, e in _fixed_spans(text[start:end], size=size, overlap=0):
                spans.append((start + s, start + e))
                paths.append(path)
            continue
        if open_start is None:
            open_start, open_path = start, path
        elif end - open_start > size:
            close(start)
            open_start, open_path = start, path
    close(sections[-1][1] if sections else len(text))
    return spans, paths


def _semantic_spans(text: str, embedder: Embedder, *, size: int, sim_floor: float = 0.55) -> list[
    tuple[int, int]
]:
    """Start a new chunk where neighbouring sentences stop resembling each other.

    Adjacent-sentence cosine below ``sim_floor`` marks a topic boundary; ``size`` still caps a
    chunk so one long topic cannot swallow the document.
    """
    sents = [
        (m.start(), m.end()) for m in _SENTENCE.finditer(text) if m.group().strip()
    ]
    if not sents:
        return []
    vectors = embedder.embed([text[s:e] for s, e in sents])
    spans: list[tuple[int, int]] = []
    start = sents[0][0]
    for i in range(1, len(sents)):
        boundary = cosine(vectors[i - 1], vectors[i]) < sim_floor
        too_big = sents[i][1] - start > size
        if boundary or too_big:
            spans.append((start, sents[i][0]))
            start = sents[i][0]
    spans.append((start, sents[-1][1]))
    return spans
