"""Core data shapes shared by every layer (Phases.md, Phase 1)."""

from __future__ import annotations

import hashlib
from enum import StrEnum

from pydantic import BaseModel, Field


class Format(StrEnum):
    MARKDOWN = "markdown"
    TEXT = "text"
    HTML = "html"
    PDF = "pdf"


class Strategy(StrEnum):
    FIXED = "fixed"
    HEADING = "heading"
    SEMANTIC = "semantic"


class Document(BaseModel):
    """A loaded source document, normalised to markdown-flavoured plaintext.

    ``text`` keeps headings as ``#`` lines so the heading chunker works on every format.
    ``page_offsets[i]`` is the character offset where page ``i+1`` starts (PDF only, else empty);
    chunkers map a chunk's offset back to a page number with it.
    """

    doc_id: str
    source: str
    format: Format
    title: str = ""
    text: str
    page_offsets: list[int] = Field(default_factory=list)

    @property
    def n_chars(self) -> int:
        return len(self.text)

    @staticmethod
    def make_id(source: str, text: str) -> str:
        return hashlib.sha256(f"{source}\x00{text}".encode()).hexdigest()[:16]


class Chunk(BaseModel):
    """One retrievable unit. Offsets index into the parent document's ``text``."""

    chunk_id: str
    doc_id: str
    source: str
    strategy: Strategy
    index: int
    text: str
    start: int
    end: int
    heading_path: list[str] = Field(default_factory=list)
    page: int | None = None

    @property
    def n_chars(self) -> int:
        return len(self.text)


class SearchHit(BaseModel):
    """A retrieved chunk with its score at every stage — the dashboard and evals show all of
    them, so a hit can always explain *why* it ranked where it did."""

    chunk_id: str
    doc_id: str
    source: str
    text: str
    heading_path: list[str] = Field(default_factory=list)
    page: int | None = None
    dense_rank: int | None = None
    dense_score: float | None = None
    sparse_rank: int | None = None
    sparse_score: float | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None

    @property
    def score(self) -> float:
        """The best available final score: rerank > fused > single-index."""
        for s in (self.rerank_score, self.rrf_score, self.dense_score, self.sparse_score):
            if s is not None:
                return s
        return 0.0


class Citation(BaseModel):
    """One [n] reference in the answer, with its verification verdict.

    ``supported`` is True/False from the judge, or None when the judge could not run —
    unverified is shown as unverified, never upgraded (Rules.md §2).
    """

    n: int
    sentence: str
    chunk_id: str = ""
    source: str = ""
    heading_path: list[str] = Field(default_factory=list)
    page: int | None = None
    supported: bool | None = None
    note: str = ""


class Confidence(BaseModel):
    retrieval: float = 0.0
    citation_coverage: float = 0.0
    completeness: float = 0.0
    composite: float = 0.0


class Answer(BaseModel):
    """What `ask()` returns: the answer or a structured refusal, never a bare guess."""

    question: str
    text: str
    refusal: bool
    citations: list[Citation] = Field(default_factory=list)
    confidence: Confidence = Field(default_factory=Confidence)
    hits: list[SearchHit] = Field(default_factory=list)
    generator: str = ""
    judge: str = ""
    elapsed_s: float = 0.0
