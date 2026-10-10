"""
SQLite/FTS5 text indexing and query logic for PDF documents.
Caches indices in XDG cache directories indexed by PDF file hash.
"""

import hashlib
import os
import sqlite3
from typing import Any, Dict, List, cast

import pymupdf as fitz


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
    app_cache_dir = os.path.join(cache_base, "pdfatlas")
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
    CREATE INDEX idx_blocks_page_y ON blocks(page, y0);

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
    conn.executemany(
        """INSERT INTO blocks (page, block_no, x0, y0, x1, y1, width, height, text)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()

    # Import any embedded annotations (e.g. highlight notes, sticky notes)
    try:
        from pdfatlas.core.pdf_notes_import import extract_embedded_notes_from_doc
        embedded_notes = extract_embedded_notes_from_doc(doc)
        if embedded_notes:
            ensure_notes_table(conn)
            for n in embedded_notes:
                conn.execute(
                    "INSERT INTO notes (page, x, y, markdown) VALUES (?, ?, ?, ?)",
                    (n["page"], n["x"], n["y"], n["markdown"]),
                )
            conn.commit()
    except Exception as e:
        print(f"[Index] Error importing embedded notes: {e}", flush=True)

    doc.close()
    return len(rows)


def sync_embedded_notes(pdf_path: str, conn: sqlite3.Connection) -> None:
    """
    Sync any embedded notes from the PDF into the SQLite database if they aren't already present.
    Ensures existing databases created prior to annotation import also get them.
    Checks both coordinates and text content to avoid creating duplicate entries.
    """
    try:
        ensure_notes_table(conn)
        existing = conn.execute("SELECT page, x, y, markdown FROM notes").fetchall()
        existing_coords = {(row[0], round(row[1], 1), round(row[2], 1)) for row in existing}
        existing_texts = {(row[3] or "").strip() for row in existing if (row[3] or "").strip()}

        from pdfatlas.core.pdf_notes_import import extract_embedded_notes_from_file
        embedded_notes = extract_embedded_notes_from_file(pdf_path)
        inserted = False
        for n in embedded_notes:
            coord_key = (n["page"], round(n["x"], 1), round(n["y"], 1))
            text_norm = (n["markdown"] or "").strip()
            if coord_key not in existing_coords and (not text_norm or text_norm not in existing_texts):
                conn.execute(
                    "INSERT INTO notes (page, x, y, markdown) VALUES (?, ?, ?, ?)",
                    (n["page"], n["x"], n["y"], n["markdown"]),
                )
                existing_coords.add(coord_key)
                if text_norm:
                    existing_texts.add(text_norm)
                inserted = True
        if inserted:
            conn.commit()
    except Exception as e:
        print(f"[Index] Error syncing embedded notes: {e}", flush=True)


def recalc_embedded_notes(pdf_path: str, conn: sqlite3.Connection) -> dict[str, int]:
    """
    Reload/recalculate embedded notes positions from the PDF file.
    Matches existing notes in the database by text content:
    - Updates (page, x, y) for matched notes so their coordinates are corrected.
    - Inserts any embedded notes from the PDF that were missing.
    - Keeps any user-created / new notes intact (notes whose text does not match any embedded note).
    Returns stats dict: {"updated": int, "inserted": int, "kept": int}.
    """
    ensure_notes_table(conn)
    existing_rows = conn.execute("SELECT id, page, x, y, markdown FROM notes").fetchall()

    from pdfatlas.core.pdf_notes_import import extract_embedded_notes_from_file
    fresh_embedded = extract_embedded_notes_from_file(pdf_path)

    # Index existing notes by normalized text
    existing_by_text: dict[str, list[tuple[int, int, float, float, str]]] = {}
    for row in existing_rows:
        norm = (row[4] or "").strip()
        existing_by_text.setdefault(norm, []).append(row)

    matched_ids: set[int] = set()
    updated_count = 0
    inserted_count = 0

    for fresh in fresh_embedded:
        fresh_norm = (fresh["markdown"] or "").strip()
        candidates = existing_by_text.get(fresh_norm, [])
        matched = None
        for c in candidates:
            if c[0] not in matched_ids:
                matched = c
                break

        if matched is not None:
            nid = matched[0]
            matched_ids.add(nid)
            conn.execute(
                "UPDATE notes SET page = ?, x = ?, y = ? WHERE id = ?",
                (fresh["page"], fresh["x"], fresh["y"], nid),
            )
            updated_count += 1
        else:
            conn.execute(
                "INSERT INTO notes (page, x, y, markdown) VALUES (?, ?, ?, ?)",
                (fresh["page"], fresh["x"], fresh["y"], fresh["markdown"]),
            )
            inserted_count += 1

    conn.commit()
    kept_count = len(existing_rows) - len(matched_ids)
    return {"updated": updated_count, "inserted": inserted_count, "kept": kept_count}


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
    else:
        sync_embedded_notes(pdf_path, conn)
    return conn


def search(conn: sqlite3.Connection, query: str, limit: int = 25) -> list[dict]:
    """Perform fuzzy FTS5 search on the document blocks. Returns results ordered by page and vertical y position."""
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
        ORDER BY b.page, b.y0, b.x0
        LIMIT ?
        """,
        (safe_query, limit),
    ).fetchall()

    return [
        {"id": r[0], "page": r[1], "x0": r[2], "y0": r[3], "x1": r[4], "y1": r[5], "text": r[6]} for r in rows
    ]


def ensure_state_table(conn: sqlite3.Connection):
    """Ensure the doc_state key-value table exists in the database."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS doc_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    conn.commit()


def save_doc_state(
    conn: sqlite3.Connection,
    zoom: float,
    scroll_y: float,
    scroll_x: float,
    crop_enabled: bool | None = None,
    page_gaps: bool | None = None,
) -> None:
    """Save document view state (zoom, scroll_x, scroll_y, crop_enabled, page_gaps) to the database."""
    try:
        ensure_state_table(conn)
        conn.execute(
            "INSERT INTO doc_state (key, value) VALUES ('zoom', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(zoom),),
        )
        conn.execute(
            "INSERT INTO doc_state (key, value) VALUES ('scroll_y', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(scroll_y),),
        )
        conn.execute(
            "INSERT INTO doc_state (key, value) VALUES ('scroll_x', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(scroll_x),),
        )
        if crop_enabled is not None:
            conn.execute(
                "INSERT INTO doc_state (key, value) VALUES ('crop_enabled', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("1" if crop_enabled else "0",),
            )
        if page_gaps is not None:
            conn.execute(
                "INSERT INTO doc_state (key, value) VALUES ('page_gaps', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("1" if page_gaps else "0",),
            )
        conn.commit()
    except Exception as e:
        print(f"[Index] Error saving document state: {e}", flush=True)


def load_doc_state(conn: sqlite3.Connection) -> dict[str, float | bool]:
    """Load document view state from the database."""
    try:
        ensure_state_table(conn)
        rows = conn.execute("SELECT key, value FROM doc_state").fetchall()
        result: dict[str, float | bool] = {}
        for k, v in rows:
            key = str(k)
            if key in ("crop_enabled", "page_gaps"):
                result[key] = str(v).strip().lower() in ("1", "true")
            else:
                try:
                    result[key] = float(v)
                except ValueError:
                    pass
        return result
    except Exception as e:
        print(f"[Index] Error loading document state: {e}", flush=True)
        return {}


def ensure_highlights_table(conn: sqlite3.Connection):
    """Ensure the highlights table exists in the database."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS highlights (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            page        INTEGER NOT NULL,
            char_start  INTEGER NOT NULL,
            char_end    INTEGER NOT NULL,
            color       TEXT NOT NULL,
            rects_json  TEXT NOT NULL,
            text        TEXT NOT NULL,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_highlights_page ON highlights(page);")
    conn.commit()


def save_highlight_to_db(
    conn: sqlite3.Connection,
    page: int,
    char_start: int,
    char_end: int,
    color: str,
    rects: list[tuple[float, float, float, float]],
    text: str,
) -> int:
    """Save a new highlight to the database and return its row ID."""
    import json
    ensure_highlights_table(conn)
    rects_json = json.dumps(rects)
    cursor = conn.execute(
        """INSERT INTO highlights (page, char_start, char_end, color, rects_json, text)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (page, char_start, char_end, color, rects_json, text),
    )
    conn.commit()
    return cursor.lastrowid or 0


def delete_highlight_from_db(conn: sqlite3.Connection, highlight_id: int):
    """Delete a highlight by ID from the database."""
    ensure_highlights_table(conn)
    conn.execute("DELETE FROM highlights WHERE id = ?", (highlight_id,))
    conn.commit()


def load_highlights_from_db(conn: sqlite3.Connection) -> list[dict]:
    """Load all highlights for the document from the database."""
    import json
    try:
        ensure_highlights_table(conn)
        rows = conn.execute(
            "SELECT id, page, char_start, char_end, color, rects_json, text FROM highlights ORDER BY page, char_start"
        ).fetchall()
        results = []
        for row in rows:
            try:
                raw_rects = json.loads(row[5])
                rects = [(float(r[0]), float(r[1]), float(r[2]), float(r[3])) for r in raw_rects]
            except Exception:
                rects = []
            results.append({
                "id": row[0],
                "page": row[1],
                "char_start": row[2],
                "char_end": row[3],
                "color": row[4],
                "rects": rects,
                "text": row[6],
            })
        return results
    except Exception as e:
        print(f"[Index] Error loading highlights: {e}", flush=True)
        return []


def ensure_notes_table(conn: sqlite3.Connection) -> None:
    """Ensure the notes table exists in the database."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            page        INTEGER NOT NULL,
            x           REAL NOT NULL,
            y           REAL NOT NULL,
            markdown    TEXT NOT NULL,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_page ON notes(page);")
    conn.commit()


def save_note_to_db(conn: sqlite3.Connection, page: int, x: float, y: float, markdown: str) -> int:
    """Save a new note to the database and return its row ID."""
    ensure_notes_table(conn)
    cursor = conn.execute(
        "INSERT INTO notes (page, x, y, markdown) VALUES (?, ?, ?, ?)",
        (page, x, y, markdown),
    )
    conn.commit()
    return cursor.lastrowid or 0


def update_note_to_db(conn: sqlite3.Connection, note_id: int, markdown: str) -> None:
    """Update the markdown content of a note."""
    ensure_notes_table(conn)
    conn.execute("UPDATE notes SET markdown = ? WHERE id = ?", (markdown, note_id))
    conn.commit()


def delete_note_from_db(conn: sqlite3.Connection, note_id: int) -> None:
    """Delete a note by ID from the database."""
    ensure_notes_table(conn)
    conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    conn.commit()


def load_notes_from_db(conn: sqlite3.Connection) -> list[dict]:
    """Load all notes for the document from the database."""
    try:
        ensure_notes_table(conn)
        rows = conn.execute(
            "SELECT id, page, x, y, markdown FROM notes ORDER BY page, id"
        ).fetchall()
        return [
            {"id": row[0], "page": row[1], "x": float(row[2]), "y": float(row[3]), "markdown": row[4]}
            for row in rows
        ]
    except Exception as e:
        print(f"[Index] Error loading notes: {e}", flush=True)
        return []


def ensure_custom_links_table(conn: sqlite3.Connection) -> None:
    """Ensure the custom_links table exists in the database."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS custom_links (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            page        INTEGER NOT NULL,
            char_start  INTEGER NOT NULL,
            char_end    INTEGER NOT NULL,
            rects_json  TEXT NOT NULL,
            text        TEXT NOT NULL,
            uri         TEXT,
            file_path   TEXT,
            title       TEXT,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_custom_links_page ON custom_links(page);")
    conn.commit()


def save_custom_link_to_db(
    conn: sqlite3.Connection,
    page: int,
    char_start: int,
    char_end: int,
    rects: list[tuple[float, float, float, float]],
    text: str,
    uri: str | None = None,
    file_path: str | None = None,
    title: str | None = None,
) -> int:
    """Save a new custom user link to the database and return its row ID."""
    import json
    ensure_custom_links_table(conn)
    rects_json = json.dumps(rects)
    cursor = conn.execute(
        """INSERT INTO custom_links (page, char_start, char_end, rects_json, text, uri, file_path, title)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (page, char_start, char_end, rects_json, text, uri, file_path, title),
    )
    conn.commit()
    return cursor.lastrowid or 0


def delete_custom_link_from_db(conn: sqlite3.Connection, link_id: int) -> None:
    """Delete a custom link by ID from the database."""
    ensure_custom_links_table(conn)
    conn.execute("DELETE FROM custom_links WHERE id = ?", (link_id,))
    conn.commit()


def load_custom_links_from_db(conn: sqlite3.Connection) -> list[dict]:
    """Load all custom user links for the document from the database."""
    import json
    try:
        ensure_custom_links_table(conn)
        rows = conn.execute(
            """SELECT id, page, char_start, char_end, rects_json, text, uri, file_path, title
               FROM custom_links ORDER BY page, char_start"""
        ).fetchall()
        results = []
        for row in rows:
            try:
                raw_rects = json.loads(row[4])
                rects = [(float(r[0]), float(r[1]), float(r[2]), float(r[3])) for r in raw_rects]
            except Exception:
                rects = []
            results.append({
                "id": row[0],
                "page": row[1],
                "char_start": row[2],
                "char_end": row[3],
                "rects": rects,
                "text": row[5],
                "uri": row[6],
                "file_path": row[7],
                "title": row[8],
            })
        return results
    except Exception as e:
        print(f"[Index] Error loading custom links: {e}", flush=True)
        return []


def ensure_crop_table(conn: sqlite3.Connection):
    """Ensure the crop_bboxes table exists in the database."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS crop_bboxes (
            page        INTEGER PRIMARY KEY,
            x0          REAL,
            y0          REAL,
            x1          REAL,
            y1          REAL
        );
    """)
    conn.commit()


def save_crop_bboxes_to_db(conn: sqlite3.Connection, bboxes: list[fitz.Rect | None]):
    """Save raw crop bounding boxes for each page."""
    try:
        ensure_crop_table(conn)
        rows = []
        for i, rect in enumerate(bboxes):
            if rect is not None:
                rows.append((i, float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)))
            else:
                rows.append((i, None, None, None, None))
        conn.executemany(
            """INSERT INTO crop_bboxes (page, x0, y0, x1, y1)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(page) DO UPDATE SET
                   x0=excluded.x0, y0=excluded.y0, x1=excluded.x1, y1=excluded.y1""",
            rows,
        )
        conn.commit()
    except Exception as e:
        print(f"[Index] Error saving crop bboxes: {e}", flush=True)


def load_crop_bboxes_from_db(conn: sqlite3.Connection, page_count: int) -> list[fitz.Rect | None] | None:
    """Load cached crop bounding boxes if all pages have been analyzed."""
    try:
        ensure_crop_table(conn)
        rows = conn.execute("SELECT page, x0, y0, x1, y1 FROM crop_bboxes ORDER BY page").fetchall()
        if len(rows) != page_count:
            return None
        bboxes: list[fitz.Rect | None] = []
        for r in rows:
            if r[1] is not None and r[2] is not None and r[3] is not None and r[4] is not None:
                bboxes.append(fitz.Rect(float(r[1]), float(r[2]), float(r[3]), float(r[4])))
            else:
                bboxes.append(None)
        return bboxes
    except Exception as e:
        print(f"[Index] Error loading crop bboxes: {e}", flush=True)
        return None


def ensure_arxiv_diff_table(conn: sqlite3.Connection):
    """Ensure the arxiv_diff_cache table exists in the database."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS arxiv_diff_cache (
            key         TEXT PRIMARY KEY,
            data        TEXT NOT NULL
        );
    """)
    conn.commit()


def save_arxiv_diff_to_db(conn: sqlite3.Connection, mapper_data: dict[str, Any]):
    """Save serialized arXiv diff data to the SQLite database."""
    import json
    try:
        ensure_arxiv_diff_table(conn)
        serialized = json.dumps(mapper_data)
        conn.execute(
            "INSERT INTO arxiv_diff_cache (key, data) VALUES ('diff_map', ?) ON CONFLICT(key) DO UPDATE SET data=excluded.data",
            (serialized,),
        )
        conn.commit()
    except Exception as e:
        print(f"[Index] Error saving arxiv diff data: {e}", flush=True)


def load_arxiv_diff_from_db(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Load serialized arXiv diff data from the SQLite database."""
    import json
    try:
        ensure_arxiv_diff_table(conn)
        row = conn.execute("SELECT data FROM arxiv_diff_cache WHERE key='diff_map'").fetchone()
        if not row:
            return None
        return json.loads(row[0])
    except Exception as e:
        print(f"[Index] Error loading arxiv diff data: {e}", flush=True)
        return None


from concurrent.futures import ThreadPoolExecutor
from typing import Callable
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib


class DatabaseService:
    """
    Dedicated single-thread SQLite database worker.
    Ensures all SQLite operations (FTS5 search, indexing, state persistence)
    occur exclusively on a single dedicated background thread.
    """

    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="db-thread")
        self._conn: sqlite3.Connection | None = None
        self._db_path: str | None = None

    def open_db(self, pdf_path: str, on_complete: Callable[[sqlite3.Connection | None], None]):
        """Open or build the SQLite DB on the dedicated DB thread."""
        self._executor.submit(self._bg_open_db, pdf_path, on_complete)

    def _bg_open_db(self, pdf_path: str, on_complete: Callable[[sqlite3.Connection | None], None]):
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

        try:
            conn = get_db_for_pdf(pdf_path)
            self._conn = conn
            self._db_path = pdf_path
            GLib.idle_add(on_complete, conn)
        except Exception as e:
            print(f"[DatabaseService] Failed to open/index DB: {e}", flush=True)
            GLib.idle_add(on_complete, None)

    def search(
        self,
        query: str,
        limit: int,
        search_id: int,
        on_results: Callable[[list[dict], int], None],
    ):
        """Submit an FTS5 search query to run on the dedicated DB thread."""
        self._executor.submit(self._bg_search, query, limit, search_id, on_results)

    def _bg_search(
        self,
        query: str,
        limit: int,
        search_id: int,
        on_results: Callable[[list[dict], int], None],
    ):
        if not self._conn:
            GLib.idle_add(on_results, [], search_id)
            return

        try:
            results = search(self._conn, query, limit=limit)
        except Exception as e:
            print(f"[DatabaseService] Search query error: {e}", flush=True)
            results = []

        GLib.idle_add(on_results, results, search_id)

    def save_state(
        self,
        zoom: float,
        scroll_y: float,
        scroll_x: float,
        crop_enabled: bool | None = None,
        page_gaps: bool | None = None,
    ) -> None:
        """Save document view state (zoom, scroll_x, scroll_y, crop_enabled, page_gaps) on the DB thread."""
        self._executor.submit(self._bg_save_state, zoom, scroll_y, scroll_x, crop_enabled, page_gaps)

    def _bg_save_state(
        self,
        zoom: float,
        scroll_y: float,
        scroll_x: float,
        crop_enabled: bool | None = None,
        page_gaps: bool | None = None,
    ) -> None:
        if self._conn:
            save_doc_state(self._conn, zoom, scroll_y, scroll_x, crop_enabled, page_gaps)

    def save_highlight(
        self,
        page: int,
        char_start: int,
        char_end: int,
        color: str,
        rects: list[tuple[float, float, float, float]],
        text: str,
        on_complete: Callable[[int], None] | None = None,
    ):
        self._executor.submit(self._bg_save_highlight, page, char_start, char_end, color, rects, text, on_complete)

    def _bg_save_highlight(
        self,
        page: int,
        char_start: int,
        char_end: int,
        color: str,
        rects: list[tuple[float, float, float, float]],
        text: str,
        on_complete: Callable[[int], None] | None = None,
    ):
        if self._conn:
            hid = save_highlight_to_db(self._conn, page, char_start, char_end, color, rects, text)
            if on_complete:
                GLib.idle_add(on_complete, hid)

    def delete_highlight(self, highlight_id: int, on_complete: Callable[[], None] | None = None):
        self._executor.submit(self._bg_delete_highlight, highlight_id, on_complete)

    def _bg_delete_highlight(self, highlight_id: int, on_complete: Callable[[], None] | None = None):
        if self._conn:
            delete_highlight_from_db(self._conn, highlight_id)
            if on_complete:
                GLib.idle_add(on_complete)

    def load_highlights(self, on_complete: Callable[[list[dict]], None]):
        self._executor.submit(self._bg_load_highlights, on_complete)

    def _bg_load_highlights(self, on_complete: Callable[[list[dict]], None]):
        if self._conn:
            hl = load_highlights_from_db(self._conn)
            GLib.idle_add(on_complete, hl)
        else:
            GLib.idle_add(on_complete, [])

    def save_note(
        self,
        page: int,
        x: float,
        y: float,
        markdown: str,
        on_complete: Callable[[int], None] | None = None,
    ):
        self._executor.submit(self._bg_save_note, page, x, y, markdown, on_complete)

    def _bg_save_note(
        self,
        page: int,
        x: float,
        y: float,
        markdown: str,
        on_complete: Callable[[int], None] | None = None,
    ):
        if self._conn:
            nid = save_note_to_db(self._conn, page, x, y, markdown)
            if on_complete:
                GLib.idle_add(on_complete, nid)

    def update_note(self, note_id: int, markdown: str):
        """Update a note's markdown content (fire-and-forget)."""
        self._executor.submit(self._bg_update_note, note_id, markdown)

    def _bg_update_note(self, note_id: int, markdown: str):
        if self._conn:
            update_note_to_db(self._conn, note_id, markdown)

    def delete_note(self, note_id: int):
        """Delete a note by ID (fire-and-forget)."""
        self._executor.submit(self._bg_delete_note, note_id)

    def _bg_delete_note(self, note_id: int):
        if self._conn:
            delete_note_from_db(self._conn, note_id)

    def load_notes(self, on_complete: Callable[[list[dict]], None]):
        self._executor.submit(self._bg_load_notes, on_complete)

    def _bg_load_notes(self, on_complete: Callable[[list[dict]], None]):
        if self._conn:
            notes = load_notes_from_db(self._conn)
            GLib.idle_add(on_complete, notes)

    def reload_embedded_notes(
        self,
        pdf_path: str,
        on_complete: Callable[[dict[str, int], list[dict]], None] | None = None,
    ):
        """Recalculate embedded notes positions from the PDF file on the dedicated DB thread."""
        self._executor.submit(self._bg_reload_embedded_notes, pdf_path, on_complete)

    def _bg_reload_embedded_notes(
        self,
        pdf_path: str,
        on_complete: Callable[[dict[str, int], list[dict]], None] | None = None,
    ):
        if not self._conn:
            if on_complete:
                GLib.idle_add(on_complete, {"updated": 0, "inserted": 0, "kept": 0}, [])
            return

        try:
            stats = recalc_embedded_notes(pdf_path, self._conn)
            notes = load_notes_from_db(self._conn)
            if on_complete:
                GLib.idle_add(on_complete, stats, notes)
        except Exception as e:
            print(f"[DatabaseService] Failed to reload embedded notes: {e}", flush=True)
            if on_complete:
                GLib.idle_add(on_complete, {"updated": 0, "inserted": 0, "kept": 0}, [])

    def load_crop_bboxes(self, page_count: int, on_complete: Callable[[list[fitz.Rect | None] | None], None]):
        self._executor.submit(self._bg_load_crop_bboxes, page_count, on_complete)

    def _bg_load_crop_bboxes(self, page_count: int, on_complete: Callable[[list[fitz.Rect | None] | None], None]):
        if self._conn:
            bboxes = load_crop_bboxes_from_db(self._conn, page_count)
            GLib.idle_add(on_complete, bboxes)
        else:
            GLib.idle_add(on_complete, None)

    def save_crop_bboxes(self, bboxes: list[fitz.Rect | None]):
        self._executor.submit(self._bg_save_crop_bboxes, bboxes)

    def _bg_save_crop_bboxes(self, bboxes: list[fitz.Rect | None]):
        if self._conn:
            save_crop_bboxes_to_db(self._conn, bboxes)

    def load_arxiv_diff(self, on_complete: Callable[[dict[str, Any] | None], None]):
        self._executor.submit(self._bg_load_arxiv_diff, on_complete)

    def _bg_load_arxiv_diff(self, on_complete: Callable[[dict[str, Any] | None], None]):
        if self._conn:
            data = load_arxiv_diff_from_db(self._conn)
            GLib.idle_add(on_complete, data)
        else:
            GLib.idle_add(on_complete, None)

    def save_arxiv_diff(self, mapper_data: dict[str, Any]):
        self._executor.submit(self._bg_save_arxiv_diff, mapper_data)

    def _bg_save_arxiv_diff(self, mapper_data: dict[str, Any]):
        if self._conn:
            save_arxiv_diff_to_db(self._conn, mapper_data)

    def save_custom_link(
        self,
        page: int,
        char_start: int,
        char_end: int,
        rects: list[tuple[float, float, float, float]],
        text: str,
        uri: str | None = None,
        file_path: str | None = None,
        title: str | None = None,
        on_complete: Callable[[int], None] | None = None,
    ):
        self._executor.submit(
            self._bg_save_custom_link,
            page, char_start, char_end, rects, text, uri, file_path, title, on_complete
        )

    def _bg_save_custom_link(
        self,
        page: int,
        char_start: int,
        char_end: int,
        rects: list[tuple[float, float, float, float]],
        text: str,
        uri: str | None = None,
        file_path: str | None = None,
        title: str | None = None,
        on_complete: Callable[[int], None] | None = None,
    ):
        if self._conn:
            lid = save_custom_link_to_db(
                self._conn, page, char_start, char_end, rects, text, uri, file_path, title
            )
            if on_complete:
                GLib.idle_add(on_complete, lid)

    def delete_custom_link(self, link_id: int):
        self._executor.submit(self._bg_delete_custom_link, link_id)

    def _bg_delete_custom_link(self, link_id: int):
        if self._conn:
            delete_custom_link_from_db(self._conn, link_id)

    def load_custom_links(self, on_complete: Callable[[list[dict]], None]):
        self._executor.submit(self._bg_load_custom_links, on_complete)

    def _bg_load_custom_links(self, on_complete: Callable[[list[dict]], None]):
        if self._conn:
            links = load_custom_links_from_db(self._conn)
            GLib.idle_add(on_complete, links)

    def close(self):
        """Close database connection on the DB thread and shutdown executor."""
        def _bg_close():
            if self._conn:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
        self._executor.submit(_bg_close)
        self._executor.shutdown(wait=False)


