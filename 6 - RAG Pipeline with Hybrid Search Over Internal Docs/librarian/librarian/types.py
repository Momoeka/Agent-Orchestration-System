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
