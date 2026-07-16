# PDF Reader with Portal Search & Minimap

A high-performance, modern PDF viewer built with Python, GTK4, Libadwaita, and PyMuPDF. It merges continuous scrolling, auto-crop margins, and a grid-based minimap navigator with an integrated full-text search (FTS5) engine that presents results as cropped "portals" of the matched sections.

Search indexes are cached locally in the user's XDG cache directory and mapped by the PDF's cryptographic hash to ensure instant subsequent lookups.

## Key Features

- **Continuous Scroll & Asynchronous Rendering:** Smooth vertical page layout. Pages render in a background worker pool to prevent UI freezes.
- **Auto-Crop Margins:** Automatically crops page white-space margins to maximize font sizes on smaller screens.
- **Grid Minimap Navigator:** A multi-column wrapping thumbnail strip that tracks the viewport, overlays crop bounds, and offers quick grid navigation.
- **FTS5 Search Portals:** An integrated search entry in the headerbar. Entering text switches the application from Document View to Search View:
  - Results are displayed as tightly cropped image strips ("portals") showing the exact context of the matched text block.
  - Matches inside portals and the main canvas are highlighted using Cairo overlays.
  - Results can be "Pinned" to save important excerpts across queries.
  - Clicking any result card instantly switches back to Reader Mode and scrolls to center the matched block.
- **Cryptographic Cache:** Text block indexes are cached as SQLite DBs in `~/.cache/pdf-reader-portals/<sha256>.db`, preventing duplicate indexing runs.

---

## Planned Architecture

```
pdf-reader-portals/
├── pdf_viewer/              # Main application package
│   ├── __init__.py          # Package initialization
│   ├── main.py              # Application entry point (Adw.Application)
│   ├── core/                # Core non-UI logic and indexing engines
│   │   ├── __init__.py      # Package init
│   │   ├── document.py      # fitz.Document wrapper & thread-safe access
│   │   ├── renderer.py      # Background rendering worker threads
│   │   ├── cache.py         # LRU RenderCache & MiniMapCache
│   │   ├── crop.py          # Margin cropping analyzer logic
│   │   └── index.py         # SQLite/FTS5 text indexing and query logic
│   └── ui/                  # GTK4 / Libadwaita UI components
│       ├── __init__.py      # Package init
│       ├── window.py        # MainWindow (Gtk.Stack, HeaderBar integration)
│       ├── canvas.py        # PDFCanvas (continuous scroll rendering + search outline)
│       ├── minimap.py       # Minimap drawing area & Modal dialog window
│       ├── settings.py      # Settings Popover / dialog & state
│       └── portal.py        # Search result portal list item (ResultRow)
├── pyproject.toml           # Packaging and dependency declarations
├── README.md                # Project documentation
└── uv.lock                  # Lockfile
```

---

## Requirements

- Python 3.11+
- GTK 4 and GObject Introspection libraries (`libgirepository1.0-dev` or equivalent)
- Cairo development libraries (`libcairo2-dev`)
- PIL/Pillow and PyMuPDF dependencies

---

## Getting Started

### Installation as a System-Wide Tool (Recommended)

You can install `gtk-pdfviewer` directly from GitHub into an isolated global environment using `uv`:

```bash
uv tool install git+https://github.com/aziis98/gtk-pdfviewer.git
```

Alternatively, to install it system-wide from a local clone of the repository:

```bash
uv tool install .
```

This registers the `gtk-pdfviewer` command globally in your `PATH`. Once installed, you can launch the application from anywhere using:

```bash
gtk-pdfviewer [path/to/document.pdf]
```

### Local Development

To install dependencies and run the application locally:

```bash
# Sync dependencies
uv sync

# Run the app with standard Cairo renderer (default)
uv run python main.py [path/to/document.pdf]

# Run the app with hardware-accelerated OpenGL renderer
uv run python main.py --backend opengl [path/to/document.pdf]
```

## Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+O` | Open PDF Document |
| `+` / `-` | Zoom In / Out |
| `Ctrl+scroll` | Zoom centered on cursor |
| `Ctrl+0` | Reset Zoom to 100% |
| `M` | Toggle Pages Minimap Navigator |
| `C` | Toggle Auto-crop margins |
| `Page Up` / `Page Down` | Scroll by one page/viewport height |
| `Escape` | Clear/close search or close active dialogs |
| `Ctrl+Q` or `q` | Quit |
