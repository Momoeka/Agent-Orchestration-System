"""Multi-format document loading (Phases.md, Phase 1).

Everything becomes a `Document` whose text is markdown-flavoured plaintext: headings survive as
``#`` lines (so the heading chunker treats every format alike), and PDF page boundaries are kept
as character offsets for page-numbered citations.
"""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf
from bs4 import BeautifulSoup, Tag

from librarian.types import Document, Format

_SUFFIXES: dict[str, Format] = {
    ".md": Format.MARKDOWN,
    ".markdown": Format.MARKDOWN,
    ".txt": Format.TEXT,
    ".html": Format.HTML,
    ".htm": Format.HTML,
    ".pdf": Format.PDF,
}

_BLANKS = re.compile(r"\n{3,}")
_HTML_DROP = ("script", "style", "nav", "header", "footer", "aside")
_HTML_KEEP = ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre", "td", "th", "blockquote")


def supported(path: Path) -> bool:
    return path.suffix.lower() in _SUFFIXES


def load_path(path: Path) -> Document:
    fmt = _SUFFIXES.get(path.suffix.lower())
    if fmt is None:
        raise ValueError(f"Unsupported file type: {path.name} (know: {sorted(_SUFFIXES)})")
    if fmt is Format.PDF:
        return _load_pdf(path)
    if fmt is Format.HTML:
        return _load_html(path)
    return _load_text(path, fmt)


def load_dir(root: Path) -> list[Document]:
    """Every supported file under ``root``, sorted for deterministic ingestion order."""
    return [load_path(p) for p in sorted(root.rglob("*")) if p.is_file() and supported(p)]


def _normalise(text: str) -> str:
    return _BLANKS.sub("\n\n", text.replace("\r\n", "\n")).strip() + "\n"


def _title_from_text(text: str, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.lstrip("#").strip()
        if stripped:
            return stripped
    return fallback


def _load_text(path: Path, fmt: Format) -> Document:
    text = _normalise(path.read_text(encoding="utf-8", errors="replace"))
    return Document(
        doc_id=Document.make_id(str(path), text),
        source=str(path),
        format=fmt,
        title=_title_from_text(text, path.stem),
        text=text,
    )


def _load_html(path: Path) -> Document:
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "html.parser")
    for junk in soup.find_all(_HTML_DROP):
        junk.decompose()
    lines: list[str] = []
    for el in soup.find_all(_HTML_KEEP):
        if not isinstance(el, Tag):
            continue
        if any(isinstance(parent, Tag) and parent.name in _HTML_KEEP for parent in el.parents):
            continue  # e.g. a <p> inside a <li>: the outer element already carries the text
        content = el.get_text(" ", strip=True)
        if not content:
            continue
        if el.name.startswith("h") and el.name[1:].isdigit():
            lines.append(f"{'#' * int(el.name[1:])} {content}")
        else:
            lines.append(content)
        lines.append("")
    text = _normalise("\n".join(lines))
    title = soup.title.get_text(strip=True) if soup.title else ""
    return Document(
        doc_id=Document.make_id(str(path), text),
        source=str(path),
        format=Format.HTML,
        title=title or _title_from_text(text, path.stem),
        text=text,
    )


def _load_pdf(path: Path) -> Document:
    pages: list[str] = []
    with pymupdf.open(path) as pdf:
        for page in pdf:
            pages.append(_normalise(str(page.get_text())))
    offsets: list[int] = []
    at = 0
    for body in pages:
        offsets.append(at)
        at += len(body) + 1  # the "\n" used to join pages below
    text = "\n".join(pages)
    return Document(
        doc_id=Document.make_id(str(path), text),
        source=str(path),
        format=Format.PDF,
        title=_title_from_text(text, path.stem),
        text=text,
        page_offsets=offsets,
    )
