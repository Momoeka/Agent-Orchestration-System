"""ChunkStore: the single source of truth both indexes are built from (Rules.md §2).

SQLite, file-based, zero infrastructure. The dense index adds vectors as chunks are ingested;
the sparse index is rebuilt from this store on load — they cannot drift because neither is fed
from anywhere else.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from librarian.types import Chunk, Document, Format, Strategy

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    format TEXT NOT NULL,
    title TEXT NOT NULL,
    n_chars INTEGER NOT NULL,
    ingested_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL REFERENCES documents(doc_id),
    source TEXT NOT NULL,
    strategy TEXT NOT NULL,
    idx INTEGER NOT NULL,
    text TEXT NOT NULL,
    start INTEGER NOT NULL,
    end INTEGER NOT NULL,
    heading_path TEXT NOT NULL,
    page INTEGER
);
CREATE INDEX IF NOT EXISTS chunks_by_doc ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS chunks_by_strategy ON chunks(strategy);
"""


class ChunkStore:
    def __init__(self, path: Path | str = ":memory:") -> None:
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)

    def upsert_document(self, doc: Document) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO documents VALUES (?, ?, ?, ?, ?, ?)",
                (
                    doc.doc_id,
                    doc.source,
                    doc.format.value,
                    doc.title,
                    doc.n_chars,
                    datetime.now(UTC).isoformat(timespec="seconds"),
                ),
            )

    def upsert_chunks(self, chunks: list[Chunk]) -> None:
        rows = [
            (
                c.chunk_id,
                c.doc_id,
                c.source,
                c.strategy.value,
                c.index,
                c.text,
                c.start,
                c.end,
                json.dumps(c.heading_path),
                c.page,
            )
            for c in chunks
        ]
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT OR REPLACE INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
            )

    def get_chunk(self, chunk_id: str) -> Chunk | None:
        row = self._conn.execute("SELECT * FROM chunks WHERE chunk_id = ?", (chunk_id,)).fetchone()
        return _to_chunk(row) if row else None

    def all_chunks(self, strategy: Strategy | None = None) -> list[Chunk]:
        if strategy is None:
            rows = self._conn.execute("SELECT * FROM chunks ORDER BY chunk_id").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM chunks WHERE strategy = ? ORDER BY chunk_id", (strategy.value,)
            ).fetchall()
        return [_to_chunk(r) for r in rows]

    def documents(self) -> list[dict[str, object]]:
        rows = self._conn.execute(
            "SELECT d.*, COUNT(c.chunk_id) AS chunks FROM documents d "
            "LEFT JOIN chunks c ON c.doc_id = d.doc_id GROUP BY d.doc_id ORDER BY d.source"
        ).fetchall()
        return [dict(r) for r in rows]

    def counts(self) -> dict[str, int]:
        docs = self._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        chunks = self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        return {"documents": int(docs), "chunks": int(chunks)}

    def close(self) -> None:
        self._conn.close()


def _to_chunk(row: sqlite3.Row) -> Chunk:
    return Chunk(
        chunk_id=row["chunk_id"],
        doc_id=row["doc_id"],
        source=row["source"],
        strategy=Strategy(row["strategy"]),
        index=row["idx"],
        text=row["text"],
        start=row["start"],
        end=row["end"],
        heading_path=json.loads(row["heading_path"]),
        page=row["page"],
    )


def document_stub(doc_id: str, source: str, fmt: str, title: str, text: str) -> Document:
    """Helper for tests: a Document without touching the filesystem."""
    return Document(doc_id=doc_id, source=source, format=Format(fmt), title=title, text=text)
