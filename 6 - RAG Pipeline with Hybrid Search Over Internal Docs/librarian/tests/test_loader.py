"""Loaders on real files: every format lands as headed plaintext with faithful metadata."""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from librarian.ingest.loader import load_dir, load_path, supported
from librarian.types import Format

HTML = """<html><head><title>Config Guide</title><style>p{color:red}</style></head>
<body><nav>skip me</nav>
<h1>Config</h1><p>Set the key.</p>
<h2>Advanced</h2><ul><li>First <b>option</b></li><li>Second option</li></ul>
<script>alert('skip')</script></body></html>"""


def test_markdown_and_text(tmp_path: Path) -> None:
    md = tmp_path / "a.md"
    md.write_text("# Title\n\nBody line.\n\n\n\n\nCompressed.\n", encoding="utf-8")
    d = load_path(md)
    assert d.format is Format.MARKDOWN and d.title == "Title"
    assert "\n\n\n" not in d.text and "Compressed." in d.text
    assert d.doc_id and d.page_offsets == []

    txt = tmp_path / "b.txt"
    txt.write_text("plain contents\n", encoding="utf-8")
    assert load_path(txt).title == "plain contents"


def test_html_keeps_headings_drops_junk(tmp_path: Path) -> None:
    f = tmp_path / "c.html"
    f.write_text(HTML, encoding="utf-8")
    d = load_path(f)
    assert d.title == "Config Guide"
    assert "# Config" in d.text and "## Advanced" in d.text
    assert "First option" in d.text and "Second option" in d.text
    assert "skip" not in d.text and "color:red" not in d.text


def test_pdf_pages_and_offsets(tmp_path: Path) -> None:
    f = tmp_path / "d.pdf"
    pdf = fitz.open()
    for line in ("alpha page", "beta page"):
        page = pdf.new_page()
        page.insert_text((72, 72), line)
    pdf.save(f)
    d = load_path(f)
    assert d.format is Format.PDF and len(d.page_offsets) == 2
    assert d.page_offsets[0] == 0
    assert "alpha page" in d.text[: d.page_offsets[1]]
    assert "beta page" in d.text[d.page_offsets[1] :]


def test_load_dir_is_sorted_and_filtered(tmp_path: Path) -> None:
    (tmp_path / "z.md").write_text("# Z\n", encoding="utf-8")
    (tmp_path / "a.txt").write_text("A\n", encoding="utf-8")
    (tmp_path / "skip.png").write_bytes(b"\x89PNG")
    docs = load_dir(tmp_path)
    assert [Path(d.source).name for d in docs] == ["a.txt", "z.md"]
    assert not supported(tmp_path / "skip.png")


def test_unsupported_suffix_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        load_path(tmp_path / "x.docx")
