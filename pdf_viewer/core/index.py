"""
SQLite/FTS5 text indexing and query logic for PDF documents.
Caches indices in XDG cache directories indexed by PDF file hash.
"""

import hashlib
import os
import sqlite3
from typing import Any, Dict, List, cast

import fitz  # PyMuPDF


def compute_pdf_hash(pdf_path: str) -> str:
    """Compute the SHA-256 hash of the PDF file content."""
    sha256 = hashlib.sha256()
    with open(pdf_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def get_cache_dir() -> str:
    """Retrieve the application's XDG cache directory."""
    cache_base = os.environ.get("XDG_CACHE_HOME")
    if not cache_base:
        cache_base = os.path.expanduser("~/.cache")
    app_cache_dir = os.path.join(cache_base, "pdf-reader-portals")
    os.makedirs(app_cache_dir, exist_ok=True)
    return app_cache_dir


def get_db_path(pdf_path: str) -> str:
    """Generate the SQLite database file path based on the PDF file's SHA-256 hash."""
    pdf_hash = compute_pdf_hash(pdf_path)
    return os.path.join(get_cache_dir(), f"{pdf_hash}_v2.db")


def build_schema(conn: sqlite3.Connection):
    """Initialize the blocks and blocks_fts virtual tables."""
    conn.executescript("""
    PRAGMA journal_mode = WAL;

    DROP TABLE IF EXISTS blocks;
    CREATE TABLE blocks (
        id          INTEGER PRIMARY KEY,
        page        INTEGER NOT NULL,
        block_no    INTEGER NOT NULL,
        x0          REAL NOT NULL,
        y0          REAL NOT NULL,
        x1          REAL NOT NULL,
        y1          REAL NOT NULL,
        width       REAL NOT NULL,
        height      REAL NOT NULL,
        text        TEXT NOT NULL
    );
    CREATE INDEX idx_blocks_page ON blocks(page);

    DROP TABLE IF EXISTS blocks_fts;
    CREATE VIRTUAL TABLE blocks_fts USING fts5(
        text,
        content='blocks',
        content_rowid='id',
        tokenize='trigram'
    );

    DROP TRIGGER IF EXISTS blocks_ai;
    CREATE TRIGGER blocks_ai AFTER INSERT ON blocks BEGIN
        INSERT INTO blocks_fts(rowid, text) VALUES (new.id, new.text);
    END;
    DROP TRIGGER IF EXISTS blocks_ad;
    CREATE TRIGGER blocks_ad AFTER DELETE ON blocks BEGIN
        INSERT INTO blocks_fts(blocks_fts, rowid, text) VALUES ('delete', old.id, old.text);
    END;
    DROP TRIGGER IF EXISTS blocks_au;
    CREATE TRIGGER blocks_au AFTER UPDATE ON blocks BEGIN
        INSERT INTO blocks_fts(blocks_fts, rowid, text) VALUES ('delete', old.id, old.text);
        INSERT INTO blocks_fts(rowid, text) VALUES (new.id, new.text);
    END;
    """)
    conn.commit()


def extract_text_to_db(pdf_path: str, conn: sqlite3.Connection) -> int:
    """Extract all text blocks from the PDF and insert them into the database."""
    doc = fitz.open(pdf_path)
    rows = []
    for page_index in range(len(doc)):
        page = doc[page_index]
        page_dict = cast(Dict[str, Any], page.get_text("dict"))
        blocks: List[Dict[str, Any]] = page_dict.get("blocks", [])
        for block in blocks:
            if block.get("type") != 0:
                continue
            bbox = block.get("bbox", (0.0, 0.0, 0.0, 0.0))
            x0, y0, x1, y1 = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
            lines: List[Dict[str, Any]] = block.get("lines", [])
            text = "\n".join(
                "".join(str(span.get("text", "")) for span in line.get("spans", [])) for line in lines
            ).strip()
            if not text:
                continue
            rows.append(
                (
                    page_index + 1,
                    int(block.get("number", 0)),
                    x0,
                    y0,
                    x1,
                    y1,
                    x1 - x0,
                    y1 - y0,
                    text,
                )
            )
    doc.close()
    conn.executemany(
        """INSERT INTO blocks (page, block_no, x0, y0, x1, y1, width, height, text)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    return len(rows)


def get_db_for_pdf(pdf_path: str) -> sqlite3.Connection:
    """
    Get (or build) the SQLite search database for the given PDF.
    If the cached database file does not exist, it gets built and populated in the XDG cache folder.
    """
    db_path = get_db_path(pdf_path)
    exists = os.path.exists(db_path)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    if not exists:
        try:
            build_schema(conn)
            extract_text_to_db(pdf_path, conn)
        except Exception as e:
            # Clean up partial/broken database file on failure
            conn.close()
            if os.path.exists(db_path):
                os.remove(db_path)
            raise e
    return conn


def search(conn: sqlite3.Connection, query: str, limit: int = 25) -> list[dict]:
    """Perform fuzzy FTS5 search on the document blocks. Returns ranked results."""
    if not query.strip():
        return []

    # Sanitize: wrap query terms in quotes to form an implicit AND of quoted tokens
    terms = query.strip().split()
    safe_query = " ".join(f'"{t}"' for t in terms)

    rows = conn.execute(
        """
        SELECT b.id, b.page, b.x0, b.y0, b.x1, b.y1, b.text
        FROM blocks_fts f
        JOIN blocks b ON b.id = f.rowid
        WHERE blocks_fts MATCH ?
        ORDER BY rank
        LIMIT ?
        """,
        (safe_query, limit),
    ).fetchall()

    return [
        {"id": r[0], "page": r[1], "x0": r[2], "y0": r[3], "x1": r[4], "y1": r[5], "text": r[6]} for r in rows
    ]
