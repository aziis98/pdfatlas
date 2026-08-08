# Developer & Agent Guidelines for PDF Atlas

This document outlines project conventions, development workflows, and automated checks for AI agents and human contributors working on **PDF Atlas**.

---

## 1. Environment & Package Management

- **Tooling:** Always use `uv` for dependency management, package execution, and environment synchronization.

- **Run Commands:**
    - `uv run main.py [file.pdf]` — Run the main application.
    - `uv run pyright` — Run static type checking.
    - `uv run ruff check .` — Run code linting.
    - `uv run pytest` — Run unit test suite.

- **Imports Policy:** Dependencies listed in `pyproject.toml` (such as `numpy`, `PyOpenGL`, `PyMuPDF`, `PyGObject`) are guaranteed to be installed. **Do not use `try...except ImportError` fallback patterns** for standard project dependencies. Import them directly at top-level.

---

## 2. Automated Quality Checks

Whenever making code edits, automatically run the following check commands to ensure code quality:

```bash
uv run pyright
uv run ruff check .
uv run pytest
```

Ensure all check commands report **0 errors** and all tests pass.

---

## 3. Screenshot Policy

- The standalone script [`scripts/generate_screenshots.py`](scripts/generate_screenshots.py) programmatically re-generates all README screenshots with GNOME window drop-shadows.

- **Rule:** **Only run `scripts/generate_screenshots.py` if explicitly asked by the user.** Do not automatically re-generate screenshots after routine bug fixes or refactorings.

---

## 4. Codebase Architecture

```
pdfatlas/
├── pdfatlas/                # Main application package
│   ├── main.py              # Adw.Application entry point & CLI parser
│   ├── controllers/         # Feature controllers
│   │   ├── clipboard.py     # Text and source TeX copy logic
│   │   ├── navigation.py    # Scroll and page navigation logic
│   │   └── search.py        # Search execution state and query logic
│   ├── core/                # Core non-UI logic
│   │   ├── arxiv_mapper.py  # arXiv TeX source diff mapping & sourcemaps
│   │   ├── cache.py         # RenderCache & MiniMapCache
│   │   ├── crop.py          # Background margin cropping analyzer
│   │   ├── document.py      # PyMuPDF fitz.Document thread-safe wrapper
│   │   ├── index.py         # SQLite FTS5 text indexing and search logic
│   │   ├── installation.py # Linux desktop launcher installation
│   │   ├── layout.py        # Viewport geometry and layout coordinate transforms
│   │   ├── pdf_source.py    # Local file, URL, and arXiv PDF source model
│   │   ├── renderer.py      # Async background render worker (raw RGB → PageTexture / cairo)
│   │   ├── settings.py      # App settings model & state persistence
│   │   ├── text_selection.py# Text selection state tracker
│   │   └── texture.py       # PageTexture: raw RGB page pixels for GL upload
│   └── ui/                  # GTK4 / Libadwaita UI components
│       ├── arxiv_dialog.py  # arXiv search and paper fetch modal
│       ├── cairo_utils.py   # Cairo surface painting and shape utilities
│       ├── canvas.py        # Continuous scroll layout canvas & overlay container
│       ├── components/      # Modular UI floating controls & toolbars
│       ├── gl_canvas.py     # OpenGL hardware-accelerated rendering widget
│       ├── gl_renderer.py   # OpenGL shader compilation & quad pipeline
│       ├── gui.py           # Declarative GTK widget builder functions
│       ├── link_preview.py  # Link hover preview manager
│       ├── minimap.py       # Multi-column grid thumbnail navigator modal
│       ├── portal.py        # FTS search result card list item (ResultRow)
│       ├── portal_preview.py# Link portal card preview overlay
│       ├── services/        # UI helper services (screenshotting, icon themes)
│       ├── settings.py      # Settings configuration popover
│       ├── shortcuts.py     # Keyboard shortcut reference modal
│       ├── theme.py         # Window CSS provider loading
│       └── window.py        # MainWindow (Adw.HeaderBar, Gtk.Stack navigation)
├── assets/
│   ├── sample-files/        # Sample PDF documents
│   ├── screenshots/         # Documentation screenshots
│   ├── shaders/             # OpenGL vertex and fragment shaders
│   └── window.css           # Main window GTK CSS theme
├── scripts/                 # Maintenance and benchmark scripts
├── tests/                   # Pytest unit test suites
└── pyproject.toml           # Package configuration & tool settings
```

---

## 5. Research Log & Technical Knowledge Base

- **Rule:** Maintain and update [`RESEARCH.md`](RESEARCH.md) periodically whenever discovering durable technical findings, architectural tradeoffs, coordinate system math, or rejected approaches.
- Always document **why** an approach failed and **how** the durable solution works to prevent regression loops.
