# Developer & Agent Guidelines for PDF Atlas

This document outlines project conventions, development workflows, and automated checks for AI agents and human contributors working on **PDF Atlas**.

---

## 1. Environment & Package Management

- **Tooling:** Always use `uv` for dependency management, package execution, and environment synchronization.

- **Run Commands:**
    - `uv run main.py [file.pdf]` — Run the main application.
    - `uv run pyright` — Run static type checking.
    - `uv run ruff check .` — Run code linting.

- **Imports Policy:** Dependencies listed in `pyproject.toml` (such as `numpy`, `PyOpenGL`, `PyMuPDF`, `PyGObject`) are guaranteed to be installed. **Do not use `try...except ImportError` fallback patterns** for standard project dependencies. Import them directly at top-level.

---

## 2. Automated Quality Checks

Whenever making code edits, automatically run the following check commands to ensure code quality:

```bash
uv run pyright
uv run ruff check .
```

Ensure both commands report **0 errors**.

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
│   ├── core/                # Core non-UI logic
│   │   ├── cache.py         # RenderCache & MiniMapCache
│   │   ├── crop.py          # Background margin cropping analyzer
│   │   ├── document.py      # PyMuPDF fitz.Document thread-safe wrapper
│   │   ├── index.py         # SQLite FTS5 text indexing and search logic
│   │   ├── renderer.py      # Asynchronous background render worker pool
│   │   └── settings.py      # App settings model & state persistence
│   └── ui/                  # GTK4 / Libadwaita UI components
│       ├── canvas.py        # Continuous scroll layout canvas & page containers
│       ├── gl_canvas.py     # OpenGL hardware-accelerated background canvas
│       ├── minimap.py       # Multi-column grid thumbnail navigator modal
│       ├── portal.py        # FTS search result card list item (ResultRow)
│       ├── settings.py      # Settings configuration popover
│       ├── theme.py         # Window CSS provider loading
│       └── window.py        # MainWindow (Adw.HeaderBar, Gtk.Stack navigation)
├── assets/
│   ├── sample-files/        # Sample PDF documents
│   ├── screenshots/         # Documentation screenshots
│   └── window.css           # Main window GTK CSS theme
├── scripts/                 # Maintenance and benchmark scripts
└── pyproject.toml           # Package configuration & tool settings
```

---

## 5. Research Log & Technical Knowledge Base

- **Rule:** Maintain and update [`RESEARCH.md`](RESEARCH.md) periodically whenever discovering durable technical findings, architectural tradeoffs, coordinate system math, or rejected approaches.
- Always document **why** an approach failed and **how** the durable solution works to prevent regression loops.
