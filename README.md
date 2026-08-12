# PDF Atlas

> [!NOTE]
> This project was built with a _reasonable amount of AI assistance_. As a result, parts of the codebase might be a bit sloppy, but this doesn't mean that I don't care and I will try my best to manage and maintain it. Issues and _smallish_ PRs are welcome.

<img src="assets/logo.png" width="96" style="vertical-align: middle; margin-right: 10px;" alt="logo" align="right" />

A PDF reader built with Python, GTK4, Libadwaita, PyMuPDF, and OpenGL. Features continuous page scrolling, page margin auto-cropping, a page thumbnail grid, and SQLite FTS5 text search with inline snippet previews.

<p align="center">
  <img src="assets/screenshots/attention_hero.png" alt="PDF Atlas Reader View" width="100%" />
</p>

## Key Features

<img src="assets/screenshots/attention_portal_search.png" alt="FTS Search Portals" width="45%" align="right" style="margin-left: 20px; margin-bottom: 20px;" />

### Full-Text Search ("Portals")

Entering text in the headerbar switches the application from Document View to Search View:

- Excerpt results are presented as cropped image strips ("portals") displaying exact visual context.
- Search term matches are highlighted across search portals and the continuous canvas.
- Excerpt pinning allows bookmarking key context snippets.
- Clicking a search portal card returns to Reader Mode and scrolls directly to the match.

<br clear="all" />

<img src="assets/screenshots/attention_no_gaps.png" alt="Continuous Reader & Gapless Mode" width="45%" align="left" style="margin-right: 20px; margin-bottom: 20px;" />

### Continuous Reading & Gap-less View

Vertical page layout using PyMuPDF rendered to textures and drawn on a hardware-accelerated OpenGL (`PyOpenGL`) canvas:

- Uses background worker threads for page rendering to keep scrolling responsive.
- Gap-less mode connects pages vertically without page margins.
- Mouse-centered canvas zooming.

<br clear="all" />

<img src="assets/screenshots/category_theory_minimap_view.png" alt="Grid Minimap Navigator" width="45%" align="right" style="margin-left: 20px; margin-bottom: 20px;" />

### Page Thumbnail Grid / Minimap

Pressing `M` opens a page thumbnail grid overlay:

- Displays all document page thumbnails in a wrapping multi-column grid.
- Highlights the current viewport position and page crop boundaries.
- Allows quick navigation across document pages.

<br clear="all" />

### Auto-Crop Margins & Index Caching

- **Auto-Crop Margins (`C`):** Trims page whitespace margins in the background to maximize readable text area on smaller screens.
- **Search Index Cache:** Text search indexes are cached in `~/.cache/pdfatlas/` keyed by file SHA-256 for faster opening on subsequent runs.

<br clear="all" />

<img src="assets/screenshots/attention_text_selection.png" alt="Text Selection Toolbar" width="45%" align="left" style="margin-right: 20px; margin-bottom: 20px;" />

### Text Selection & Highlighting

Select text in any document to open the context toolbar:

- **Text Highlighting (`H`):** Create persistent color highlights saved globally per document SHA-256 hash.
- **PDF Plain Text Copy (`Ctrl+Shift+C`):** Copy selected plain text from the PDF.
- **LaTeX Source Copy (`Ctrl+C`):** For arXiv papers, map selections back to original LaTeX source code.

<br clear="all" />

<img src="assets/screenshots/attention_notes_annotations.png" alt="Annotations, Highlights & Markdown Notes" width="45%" align="right" style="margin-left: 20px; margin-bottom: 20px;" />

### Annotations & Highlights

- **Overview Popover:** Lists all document highlights grouped by page.
- **Quick Jump:** Click any item to jump directly to it in the document.
- **Coming Soon:** *Text notes & comments on annotations.*

<br clear="all" />


## Architecture

```
pdfatlas/
├── pdfatlas/                # Main application package
│   ├── __init__.py          # Package initialization
│   ├── main.py              # Application entry point (Adw.Application & CLI parser)
│   ├── core/                # Core non-UI logic and indexing engines
│   │   ├── __init__.py      # Package init
│   │   ├── cache.py         # LRU RenderCache & MiniMapCache
│   │   ├── crop.py          # Background margin cropping analyzer logic
│   │   ├── document.py      # PyMuPDF fitz.Document thread-safe wrapper
│   │   ├── index.py         # SQLite FTS5 text indexing and search logic
│   │   ├── renderer.py      # Multi-threaded background render worker pool
│   │   └── settings.py      # App settings model & state management
│   └── ui/                  # GTK4 / Libadwaita UI components
│       ├── __init__.py      # Package init
│       ├── canvas.py        # Continuous scroll layout canvas & page containers
│       ├── gl_canvas.py     # OpenGL hardware-accelerated background render canvas
│       ├── minimap.py       # Minimap thumbnail drawing & modal navigator window
│       ├── portal.py        # FTS search result card list item (ResultRow)
│       ├── settings.py      # Settings popover & configuration dialog
│       ├── theme.py         # Window CSS provider loading
│       └── window.py        # MainWindow (Adw.HeaderBar, Gtk.Stack navigation)
├── assets/
│   ├── sample-files/        # Sample PDF documents
│   ├── screenshots/         # Documentation screenshots
│   └── window.css           # Main window GTK CSS theme
├── prototypes/              # Prototype scripts & launcher shortcuts
├── scripts/                 # Maintenance and benchmark scripts
├── pyproject.toml           # Packaging and dependency declarations
├── README.md                # Project documentation
└── uv.lock                  # Lockfile
```

## Requirements

- Python 3.11+
- GTK 4 & Libadwaita
- Cairo & PyGObject development headers

### macOS Setup

On a fresh macOS system, install Xcode Command Line Tools and Homebrew system dependencies before installing `pdfatlas`:

1. **Install Xcode Command Line Tools** (from Terminal):
   ```bash
   xcode-select --install
   ```
   A software update dialog will appear. Click **Install**, agree to the terms, and wait for the installation to finish. Verify the installation:
   ```bash
   xcode-select -p
   # Expected output: /Library/Developer/CommandLineTools
   ```

2. **Install Homebrew & System Dependencies** (including `cmake` & `pkg-config`):
   If Homebrew is not installed, install it via:
   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```
   Then install the required build tools and GTK4 libraries via `brew`:
   ```bash
   brew install cmake pkg-config gtk4 libadwaita pygobject3 cairo
   ```

### Linux Setup

On Ubuntu / Debian:
```bash
sudo apt install libgirepository1.0-dev gir1.2-adw-1 libcairo2-dev cmake pkg-config
```

On Arch Linux (AUR):
```bash
# Using paru
paru -S pdfatlas-git

# Or using yay
yay -S pdfatlas-git
```




---

## Getting Started

### Installation on macOS via Homebrew

On macOS, you can install `pdfatlas` and all its required C libraries (`cmake`, `gtk4`, `libadwaita`, `cairo`, `pygobject3`) in a single command using Homebrew:

1. Ensure Xcode Command Line Tools are installed:
   ```bash
   xcode-select --install
   ```

2. Install directly via the Homebrew Formula:
   ```bash
   brew install https://raw.githubusercontent.com/aziis98/pdfatlas/main/scripts/Formula/pdfatlas.rb
   ```

---

### Installation via `uv` (Linux & macOS)

Once system prerequisites are installed, you can install `pdfatlas` using `uv`:

```bash
uv tool install git+https://github.com/aziis98/pdfatlas.git
```

Or install from a local clone of the repository:

```bash
uv tool install .
```


Once installed, launch the application from anywhere using:

```bash
# Open a local PDF file
pdfatlas path/to/document.pdf

# Open an arXiv paper directly by ID or URL
pdfatlas 1706.03762
pdfatlas https://arxiv.org/abs/2305.12345
```

### Updating / Upgrading

To update `pdfatlas` to the latest commit from GitHub:

```bash
uv tool install git+https://github.com/aziis98/pdfatlas.git --reinstall
```


### Local Development

To install dependencies and run locally:

```bash
# Install dependencies
uv sync

# Launch with hardware-accelerated OpenGL renderer (default)
uv run main.py 1706.03762

# Launch with arXiv sourcemap debug overlay enabled
uv run main.py 1706.03762 --debug
```


## Keyboard Shortcuts

| Shortcut                | Action                                     |
| ----------------------- | ------------------------------------------ |
| `Ctrl+O`                | Open PDF Document                          |
| `Ctrl+F`                | Focus Search Bar                           |
| `+` / `-` / `=`         | Zoom In / Out                              |
| `Ctrl+scroll`           | Zoom centered on cursor                    |
| `Ctrl+0`                | Reset Zoom to 100%                         |
| `W`                     | Fit Page Width                             |
| `F`                     | Fit Entire Page in Viewport                |
| `M`                     | Toggle Pages Minimap Navigator             |
| `C`                     | Toggle Auto-crop margins                   |
| `Page Up` / `Page Down` | Scroll by viewport height                  |
| `Left` / `Right` or `h` / `l` | Scroll Back / Forward by viewport height   |
| `Up` / `Down` or `k` / `j` | Fine step scroll Up / Down                 |
| `Ctrl+C`                | Copy Source TeX (if available, else PDF text) |
| `Ctrl+Shift+C`          | Copy raw PDF text                          |
| `Escape`                | Clear selection/search or close Minimap modal |
| `Ctrl+Q` or `q`         | Quit                                       |

## Contributing

Issues and _smallish_ PRs are welcome. For larger features, prefer creating an issue tagged with **enhancement** to suggest new feature ideas rather than opening a large PR directly.

## License

GNU Affero General Public License v3.0 (AGPL-3.0).

