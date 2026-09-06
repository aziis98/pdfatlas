# PDF Atlas Architecture & Component Guide

This document provides an up-to-date outline of every component in **PDF Atlas**, their architectural responsibilities, and visual ASCII layout diagrams for all GTK UI components.

---

## Table of Contents

1. [High-Level Architecture & Layering](#1-high-level-architecture--layering)
2. [GTK & Libadwaita UI Components](#2-gtk--libadwaita-ui-components)
   - [MainWindow (`pdfatlas.ui.window`)](#mainwindow-pdfatlasuiwindow)
   - [PdfDocumentView (`pdfatlas.ui.document_view`)](#pdfdocumentview-pdfatlasuidocument_view)
   - [PDFCanvas & PageContainer (`pdfatlas.ui.canvas`)](#pdfcanvas--pagecontainer-pdfatlasuicanvas)
   - [GLCanvas & OpenGL Pipeline (`pdfatlas.ui.gl_canvas`, `gl_renderer`)](#glcanvas--opengl-pipeline-pdfatlasuigl_canvas-gl_renderer)
   - [WelcomeView (`pdfatlas.ui.welcome`)](#welcomeview-pdfatlasuiwelcome)
   - [MinimapDialog (`pdfatlas.ui.minimap`)](#minimapdialog-pdfatlasuiminimap)
   - [SettingsWindow (`pdfatlas.ui.settings`)](#settingswindow-pdfatlasuisettings)
   - [ArxivDialog (`pdfatlas.ui.arxiv_dialog`)](#arxivdialog-pdfatlasuiarxiv_dialog)
   - [Search Portal & Previews (`pdfatlas.ui.portal`, `link_preview`)](#search-portal--previews-pdfatlasuiportal-link_preview)
3. [Feature Controllers (`pdfatlas.controllers.*`)](#3-feature-controllers-pdfatlascontrollers)
4. [Core Engine & Models (`pdfatlas.core.*`)](#4-core-engine--models-pdfatlascore)
5. [Build & Packaging Pipeline (`hatch_build.py`, `pdfatlas._version`)](#5-build--packaging-pipeline-hatch_buildpy-pdfatlas_version)

---

## 1. High-Level Architecture & Layering

PDF Atlas uses a **hybrid rendering architecture** separating interaction from rasterization:
- **OpenGL Acceleration (`GLCanvas` + `QuadRenderer`)**: High-performance background quad renderer executing textured page draws, smooth zoom scaling, and night-mode shader color inversion directly on the GPU.
- **GTK4 / Libadwaita Interaction Overlay (`PDFCanvas` + `PageContainer`)**: Transparent native widgets positioned over the GL canvas to handle text selection, link clicks, drop shadows, and annotation note overlays.
- **Async Thread Workers (`RenderWorker` + `TextureUploader`)**: Background thread pool rasterizing MuPDF pages to raw RGB buffers asynchronously, uploading to OpenGL textures on the GTK frame clock.

---

## 2. GTK & Libadwaita UI Components

### MainWindow (`pdfatlas.ui.window`)
Primary application window. Manages the top header bar, tabs, window-level `Gio.SimpleAction` commands, and view transitions.

```
+-------------------------------------------------------------------------------+
| Adw.HeaderBar                                                                 |
| [Open SplitBtn] [Filename]           [SearchEntry]           [Page#] [MenuBtn]|
+-------------------------------------------------------------------------------+
| Adw.TabBar (Tab strip)                                                        |
+-------------------------------------------------------------------------------+
| Adw.ToastOverlay                                                              |
| +---------------------------------------------------------------------------+ |
| | Gtk.Stack                                                                 | |
|   ├─ "welcome-view": WelcomeView (Recent files list & drag-drop prompt)   | |
|   ├─ "search-view": SearchResultsView (FTS5 search results list)          | |
|   └─ "document-view": Adw.TabView (Hosts active PdfDocumentView tabs)     | |
| +---------------------------------------------------------------------------+ |
+-------------------------------------------------------------------------------+
```

- **Direct View Activation**: When created with an `initial_source`, `MainWindow` directly activates `"document-view"`, bypassing the welcome screen and avoiding startup flashing. `Gtk.Stack` transition is initialized to `NONE` during window construction and enabled (`CROSSFADE`) only after window realization (`_on_window_realized`).

---

### PdfDocumentView (`pdfatlas.ui.document_view`)
Tab content wrapper. Manages the transition between paper download/loading states and the document canvas.

```
+-------------------------------------------------------------------------------+
| Gtk.Stack                                                                     |
|   ├─ "loading": Gtk.Box                                                       |
|   │     ├─ Adw.Spinner                                                        |
|   │     ├─ Loading Title Label                                                |
|   │     ├─ Loading Subtitle Label                                             |
|   │     └─ Gtk.ProgressBar (Download / Processing progress)                   |
|   └─ "canvas": PDFCanvas (Continuous document scroll view)                    |
+-------------------------------------------------------------------------------+
```

---

### PDFCanvas & PageContainer (`pdfatlas.ui.canvas`)
Continuous scroll canvas. Houses the OpenGL background canvas and the transparent GTK layout containing per-page interactive containers.

```
+-------------------------------------------------------------------------------+
| PDFCanvas (Gtk.Overlay)                                                       |
|   ├─ Background Layer: GLCanvas (Gtk.GLArea - GPU quad rendering)             |
|   └─ Foreground Layer: Gtk.ScrolledWindow (Kinetic scrolling, zoom pan)       |
|         └─ Gtk.Viewport                                                       |
|               └─ Gtk.Fixed (_layout)                                          |
|                     ├─ PageContainer 0 (Gtk.Overlay: drop shadow, notes)      |
|                     ├─ PageContainer 1 (Gtk.Overlay: drop shadow, notes)      |
|                     └─ PageContainer N (Gtk.Overlay: drop shadow, notes)      |
+-------------------------------------------------------------------------------+
```

- **`PageContainer`**: Outer `Gtk.Overlay` for each page. Holds CSS `.page-shadow` and `.page-container`, note popover pins, and link click hitboxes.
- **Gap-less Handling**: When `page_gaps = False`, `.page-shadow` and border-radius are suppressed to maintain a continuous, seamless vertical ribbon.
- **Progressive Low-Res Pre-seeding**: In `set_document()`, `seed_initial_previews()` extracts an ultra-fast 0.25× low-resolution thumbnail into `RenderCache` so Frame 1 renders page content immediately on screen without a blank placeholder.

---

### GLCanvas & OpenGL Pipeline (`pdfatlas.ui.gl_canvas`, `gl_renderer`)
Hardware-accelerated viewport renderer.

- **`GLCanvas` (`Gtk.GLArea`)**: Configures OpenGL 3.3 core profile. Re-renders visible page quads from `RenderCache` on every scroll or zoom event.
- **`QuadRenderer` (`pdfatlas.ui.gl_renderer`)**: Compiles `assets/shaders/quad.vert` and `assets/shaders/quad.frag`. Discards pixels outside rounded corners (`u_radius`), applies night-mode matrix inversion, and blends page textures with search highlights.
- **`CompositingLayer`**: Offscreen FBOs for multi-pass text selection and search highlight rendering.

---

### WelcomeView (`pdfatlas.ui.welcome`)
Initial empty state screen. Displays recent files, quick tips, and drag-and-drop targets.

```
+-------------------------------------------------------------------------------+
| Adw.StatusPage [Icon: "document-open-symbolic", Title: "PDF Atlas"]           |
|   ├─ Action Button: "Open Document" (Ctrl+O)                                  |
|   ├─ Quick Action: "Browse arXiv Papers" (Ctrl+Shift+O)                       |
|   └─ Gtk.ListBox: Recent Documents (Unlimited history list)                   |
+-------------------------------------------------------------------------------+
```

---

### MinimapDialog (`pdfatlas.ui.minimap`)
Modal grid overview for jump navigation across all document pages.

```
+-------------------------------------------------------------------------------+
| Adw.HeaderBar [Title: "Overview (N Pages)"]                                   |
+-------------------------------------------------------------------------------+
| Gtk.ScrolledWindow                                                            |
|   └─ Gtk.DrawingArea (Multi-column grid, fit-to-height thumbnails, current page)|
+-------------------------------------------------------------------------------+
```

---

### SettingsWindow (`pdfatlas.ui.settings`)
Preferences modal configured with Libadwaita rows.

```
+-------------------------------------------------------------------------------+
| Adw.PreferencesDialog                                                         |
|   └─ Adw.PreferencesPage                                                      |
|         ├─ Adw.PreferencesGroup ("Appearance")                                |
|         │     ├─ Theme Mode: System / Light / Dark (Adw.ComboRow)             |
|         │     ├─ Page shadows: Enable / Disable (Adw.SwitchRow)               |
|         │     ├─ Invert Amount: 0 - 100% (Adw.SpinRow)                        |
|         │     └─ Hue Rotate: 180° Preservation (Adw.SwitchRow)                |
|         └─ Adw.PreferencesGroup ("Cropping")                                  |
|               ├─ Enable auto-crop: Whitespace trimming (Adw.SwitchRow)        |
|               └─ Page gaps: 12px / 0px gapless (Adw.SwitchRow)                |
+-------------------------------------------------------------------------------+
```

---

### ArxivDialog (`pdfatlas.ui.arxiv_dialog`)
Search and paper download modal.

```
+-------------------------------------------------------------------------------+
| Adw.HeaderBar [Search Entry: "Query or arXiv ID..."] [Download Button]        |
+-------------------------------------------------------------------------------+
| Gtk.ScrolledWindow                                                            |
|   └─ Gtk.ListBox (Paper Title, Authors, Abstract snippet, Version tags)       |
+-------------------------------------------------------------------------------+
```

---

### Search Portal & Previews (`pdfatlas.ui.portal`, `link_preview`)
Overlay cards for search matches and link destinations.

- **`ResultRow` (`pdfatlas.ui.portal`)**: Search result card embedding a 1:1 physical pixel Cairo rasterized snippet of the search match context.
- **`LinkPreviewManager` (`pdfatlas.ui.link_preview`)**: Hover popover rendering destination snippets when lingering over citations or internal cross-references.

---

## 3. Feature Controllers (`pdfatlas.controllers.*`)

Controllers decouple business logic, state mutations, and external integrations from GTK view widgets:

- **`PrintController` (`printing.py`)**: Native printing integration using `Gtk.PrintDialog.print_file()` to hand PDF files directly to the OS print spooler/portal.
- **`DocumentLoader` (`document_loader.py`)**: Asynchronous document fetching (local path, URL, or arXiv ID), disk caching in `~/.cache/pdfatlas`, and progress notifications.
- **`SearchController` (`search.py`)**: Manages debounced text search, SQLite FTS queries, search portal card navigation, and match cycling (Enter / Shift+Enter).
- **`NavigationController` (`navigation.py`)**: Scroll position synchronization, history stack (back/forward page jumps), and page input validation.
- **`TabController` (`tabs.py`)**: Multi-tab lifecycle, tab reordering, and cross-window tab dragging.
- **`AnnotationsController` (`annotations.py`)**: Text highlights, color palette swatches, and note popover CRUD.
- **`ClipboardController` (`clipboard.py`)**: Text selection copying, citation formatting, and LaTeX source extraction.

---

## 4. Core Engine & Models (`pdfatlas.core.*`)

- **`DocumentModel` (`document.py`)**: Thread-safe PyMuPDF (`fitz.Document`) wrapper with mutex protection for multithreaded rasterization.
- **`RenderWorker` & `RenderCache` (`renderer.py`, `cache.py`)**: Background render worker pool and LRU cache storing raw RGB pixel buffers.
- **`CropAnalyzer` (`crop.py`)**: Background bounding-box analyzer calculating optimal page whitespace crops.
- **`ArxivDiffMapper` (`arxiv_mapper.py`)**: Downloads arXiv LaTeX source bundles, parses TeX ASTs, and computes word-level sourcemaps between compiled PDF words and original TeX source lines.
- **`SearchIndex` (`index.py`)**: In-memory / on-disk SQLite FTS5 index for fast full-text querying across hundreds of pages.
- **`PdfSource` (`pdf_source.py`)**: Uniform data model for local files, remote URLs, and arXiv identifiers, plus `RecentFilesManager` (unlimited history persistence).
- **`CropSettings` (`settings.py`)**: Dataclass model managing appearance, night-mode, crop, and shadow configurations serialized to `~/.config/pdfatlas/settings.json`.
- **`TextSelection` (`text_selection.py`)**: Character-accurate spatial text selection state across multiple continuous pages.

---

## 5. Build & Packaging Pipeline (`hatch_build.py`, `pdfatlas._version`)

- **Hatchling Build Hook (`hatch_build.py`)**: `CustomBuildHook` derives from `BuildHookInterface` and runs during package build (`uv tool install . --force` or wheel packaging). It queries `git rev-parse --short HEAD` in the repo root and generates `pdfatlas/_build_commit.py` containing `COMMIT = "<short-hash>"`.
- **Dynamic Version Resolver (`pdfatlas._version`)**: `get_version()` resolves the active version by checking:
  1. Live git repository short commit (when working directly in a git clone).
  2. Build-time `pdfatlas._build_commit.py` (when installed as a tool/wheel via `uv tool install`).
  3. Package metadata `importlib.metadata.version("pdfatlas")`.
  4. Static fallback `0.1.0`.
- **CLI Flag**: Configured via `argparse.ArgumentParser` in `main.py` with `--version` and `-v`.
