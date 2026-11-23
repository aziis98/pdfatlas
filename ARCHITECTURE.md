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
   - [LinkSearchDialog (`pdfatlas.ui.components.link_search_dialog`)](#linksearchdialog-pdfatlasuicomponentslink_search_dialog)
   - [UrlInputDialog (`pdfatlas.ui.components.url_dialog`)](#urlinputdialog-pdfatlasuiurl_dialog)
   - [SearchResultsView & Previews (`pdfatlas.ui.components.search_results_view`, `pdfatlas.ui.portal`, `link_preview`, `masonry_layout`)](#search-results-view--previews-pdfatlasuicomponentssearch_results_view-pdfatlasuiportal-link_preview-masonry_layout)
3. [Feature Controllers (`pdfatlas.controllers.*`)](#3-feature-controllers-pdfatlascontrollers)
4. [Core Engine & Models (`pdfatlas.core.*`)](#4-core-engine--models-pdfatlascore)
5. [Build & Packaging Pipeline (`hatch_build.py`, `pdfatlas._version`)](#5-build--packaging-pipeline-hatch_buildpy-pdfatlas_version)

---

## 1. High-Level Architecture & Layering

PDF Atlas uses a **hybrid rendering architecture** separating interaction from rasterization:
- **OpenGL Acceleration (`GLCanvas` + `QuadRenderer`)**: High-performance background quad renderer executing textured page draws, smooth zoom scaling, and night-mode shader color inversion directly on the GPU.
- **GTK4 / Libadwaita Interaction Overlay (`PDFCanvas` + `PageContainer`)**: Transparent native widgets positioned over the GL canvas to handle text selection, link clicks, drop shadows, and annotation note overlays.
- **Async Thread Workers (`RenderWorker` + `TextureUploader`)**: Background thread pool rasterizing MuPDF pages to raw RGB buffers asynchronously, uploading to OpenGL textures on the GTK frame clock.
- **Process-Scoped Worker & Window Teardown**: `PDFViewerApplication.get_render_worker()` lazily creates **one** `RenderWorker` for the whole process and owns it (`do_shutdown()`); every `MainWindow` resolves it through the app factory (or an explicit `render_worker=`), falling back to a private worker only for hosts without a factory (tests/embedders). Each window's `close-request` handler (`_on_close_request`) saves reading state, cancels GLib timers and the search debounce, closes every `PdfDocumentView` (releasing PyMuPDF handles and queued render jobs), shuts down its search `ThreadPoolExecutor`, closes its `DatabaseService` SQLite writer thread, and shuts down the worker only when the window owns it. `open_document_when_mapped()` shows the window with an "Opening Paper…" state first and performs the open from an idle callback, so the window is painted before PDF parsing/layout. `main.py` imports `ui.window` lazily, so a secondary launch that merely forwards a URI never imports the GTK widget stack, PyMuPDF or NumPy.

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
|   ├─ "loading-view": Opening/Downloading spinner + progress bar           | |
|   └─ "document-view": Adw.TabView (Hosts active PdfDocumentView tabs)     | |
| +---------------------------------------------------------------------------+ |
+-------------------------------------------------------------------------------+
```

- **Direct View Activation**: When created with an `initial_source`, `MainWindow` directly activates `"document-view"`, bypassing the welcome screen and avoiding startup flashing. `Gtk.Stack` transition is initialized to `NONE` during window construction and enabled (`CROSSFADE`) only after window realization (`_on_window_realized`).
- **Deferred Open (`open_document_when_mapped`)**: Invoked by `main.py` after `present()`; it switches the stack to the loading view, stores the request in `_pending_open`, and opens the document from a `GLib.idle_add` callback so the compositor can paint the window first. `_open_document_deferred` is also callable directly for deterministic tests; `_on_close_request` drops any pending open. Windows created for tab detachment (`TabView::create-window`) show `"document-view"` directly because the dragged page is attached immediately.
- **Tab Context Menu**: `TabController` installs a persistent `Gio.Menu` on the `Adw.TabView`; libadwaita opens it on tab right-click and emits `::setup-menu(page)` so the menu is rebuilt in place for that tab. The same *Move to Window…* submenu is embedded dynamically in the header-bar options menu (`MenuBtn`, refreshed on `notify::active`), so it remains reachable when the tab bar is hidden because only one tab is open. Both menus omit the *Move to Window…* section entirely if no other windows are present. Each window carries a stable `window_uid` and resolves its folder dirname dynamically from its open document tabs:

```
Right-click tab
   ├─ New Tab / New Window
   ├─ Move to Window… ▸ ┌────────────────────────────────────────────────┐
   │                    │ ⊞ Window 2 · papers                            │
   │                    │     diffusion.pdf, survey.pdf, +2 more         │
   │                    ├────────────────────────────────────────────────┤
   │                    │ ✓ Window 1 · pdfatlas                          │
   │                    │     attention.pdf, notes.pdf  (this window)    │
   │                    └────────────────────────────────────────────────┘
   └─ Close Tab / Close Other Tabs
```

- **Cross-Window Move**: The submenu action targets the destination `window_uid`; `TabController.move_page_to_window()` calls `Adw.TabView.transfer_page()`, reusing the live tab/document view. If the source window loses its last tab, the existing `page-detached` path closes it. `page-attached` calls `PdfDocumentView.set_render_worker()` / `PDFCanvas.set_render_worker()` so the transferred tab drops jobs queued on the source window's worker and renders through the destination window's worker.

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

- **Per-Document Settings Isolation**: Owns an isolated, cloned copy of `CropSettings` (`self.settings = copy.copy(settings)`). Toggling auto-crop (`enabled`) or gap-less mode (`page_gaps`) mutates only this tab's instance and persists directly to the document's SQLite cache database (`doc_state`), leaving other open tabs unaffected.

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
- **Worker Swap (`set_render_worker`)**: Clears `in_flight` / `_render_started`, cancels the old worker's jobs for the current file, and re-runs `_update_visibility()` so a tab moved between windows continues rendering without re-layout (used by `TabController.page-attached`).
- **Dynamic Viewport Width Resizing**: Tracks whether the document page width matches the viewport width (e.g. after pressing `w` or via `--fit-width`). When the window and horizontal adjustment viewport resize (`notify::page-size`), `_on_viewport_width_changed()` detects if the page was fitted and automatically recalculates zoom to continuously resize the page together with the window.

#### Canvas Context Menu (`popover.menu.canvas-context-menu`)
Official Libadwaita desktop context menu anchored to right-click coordinates (`_build_context_menu`):

```
+-----------------------------------------------+
| popover.menu.canvas-context-menu              |
| +-------------------------------------------+ |
| | [Globe]  Open Link in Browser             | |  <-- When right-clicking over a link
| | [Copy]   Copy Link Address                | |
| | [Trash]  Remove Link                      | |  <-- If custom user link (destructive)
| |-------------------------------------------| |
| | [Copy]   Copy                      Ctrl+C | |  <-- When text is selected
| | [Copy]   Copy TeX Source     Ctrl+Shift+C | |  <-- If arXiv source ready
| | [Brush]  Highlight                      H | |
| | [Link]   Add Link…                        | |
| | [Trash]  Remove Link                      | |  <-- If selection covers custom link
| |-------------------------------------------| |
| | [Note]   Add Note Here                    | |  <-- Always present on page area
| |-------------------------------------------| |
| | [Clear]  Deselect                     Esc | |  <-- When text is selected
| +-------------------------------------------+ |
+-----------------------------------------------+
```

- **Native Libadwaita Metrics**: Inherits native `popover.menu` styling (15px rounded container, theme background, soft double shadow, `set_has_arrow(False)`). Menu items use official 32px row height with 9px rounded corners on hover (`color-mix(in srgb, currentColor 10%, transparent)`), 6px box padding, 12px icon-to-label spacing, and 6px separator vertical margins.
- **Note Icon Context Menu (`NotesLayer` in `pdfatlas.ui.notes`)**:
```
+-----------------------------------------------+
| popover.menu.canvas-context-menu              |
| +-------------------------------------------+ |
| | [Edit]   Open Note                        | |
| | [Trash]  Delete Note                      | |  <-- Destructive styling
| +-------------------------------------------+ |
+-----------------------------------------------+
```

---

### GLCanvas & OpenGL Pipeline (`pdfatlas.ui.gl_canvas`, `gl_renderer`)
Hardware-accelerated viewport renderer.

- **`GLCanvas` (`Gtk.GLArea`)**: Configures OpenGL 3.3 core profile. Re-renders visible page quads from `RenderCache` on every scroll or zoom event, and renders search occurrence highlights across visible pages as orange stroke outlines.
- **`QuadRenderer` (`pdfatlas.ui.gl_renderer`)**: Compiles `assets/shaders/quad.vert` and `assets/shaders/quad.frag`. Discards pixels outside rounded corners (`u_radius`), applies night-mode matrix inversion, blends page textures, and renders batch quads (`fill_rects`) as well as hollow border outlines (`stroke_rect`, `stroke_rects`).
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
|   ├─ Adw.PreferencesPage ("General")                                          |
|   │     ├─ Adw.PreferencesGroup ("Appearance")                                |
|   │     │     ├─ Theme Mode: System / Light / Dark (Adw.ComboRow)             |
|   │     │     ├─ Page shadows: Enable / Disable (Adw.SwitchRow)               |
|   │     │     ├─ Invert Amount: 0 - 100% (Adw.SpinRow)                        |
|   │     │     └─ Hue Rotate: 180° Preservation (Adw.SwitchRow)                |
|   │     └─ Adw.PreferencesGroup ("Cropping")                                  |
|   │           ├─ Enable auto-crop: Whitespace trimming (Adw.SwitchRow)        |
|   │           └─ Page gaps: 12px / 0px gapless (Adw.SwitchRow)                |
|   ├─ Adw.PreferencesPage ("Crop")                                             |
|   ├─ Adw.PreferencesPage ("Search")                                           |
|   ├─ Adw.PreferencesPage ("Zoom")                                             |
|   └─ Adw.PreferencesPage ("Debug")                                            |
|         └─ Adw.PreferencesGroup ("PDF Annotations")                           |
|               └─ Reload & Recalculate Notes: Re-extract & update (ActionRow)  |
+-------------------------------------------------------------------------------+
```

---

### ArxivDialog (`pdfatlas.ui.arxiv_dialog`)
arXiv URL/ID input and recent papers modal.

```
+-------------------------------------------------------------------------------+
| Adw.HeaderBar [Title: "Open from arXiv"]                                      |
+-------------------------------------------------------------------------------+
| Gtk.Box                                                                       |
|   ├─ Gtk.Entry ("arXiv URL or ID (e.g. 2305.12345)")                          |
|   ├─ Gtk.Label (Status message if invalid)                                    |
|   ├─ Gtk.Button ("Fetch & Open")                                              |
|   ├─ Gtk.Separator                                                            |
|   └─ Gtk.ListBox (Recent arXiv papers)                                        |
+-------------------------------------------------------------------------------+
```

- **Instant Local Open**: Checks local arXiv cache and recent store. If `paper.pdf` exists locally, immediately opens the document in 0ms with zero network requests.
- **Delegated In-Tab Download**: If uncached, closes the modal immediately and hands off to `DocumentLoader.open_document` to render an in-tab progress bar for the PDF, deferring LaTeX source download and diffing to non-blocking background workers.

---

### LinkSearchDialog (`pdfatlas.ui.components.link_search_dialog`)
Academic article search and paper link attachment modal using OpenAlex REST API.

```
+-------------------------------------------------------------------------------+
| Adw.HeaderBar [Title: "Link Academic Article"]            [Cancel] [Link Paper]|
+-------------------------------------------------------------------------------+
| Gtk.Box                                                                       |
|   ├─ Gtk.SearchEntry ("Search paper title, DOI, or author...")                |
|   ├─ Gtk.Box: [Gtk.Spinner] [Gtk.Label: "Searching OpenAlex / Found N results"]|
|   └─ Gtk.ScrolledWindow                                                       |
|         └─ Gtk.ListBox                                                        |
|               ├─ Row 0: Title, Authors, Year, DOI, [OA PDF / Closed badge]     |
|               ├─ Row 1: Title, Authors, Year, DOI, [OA PDF / Closed badge]     |
|               └─ Row N: Title, Authors, Year, DOI, [OA PDF / Closed badge]     |
+-------------------------------------------------------------------------------+
```

- **Dual-Target Model**: Resolves papers to `(Link?, Pdf?)`. When clicked, PDF Atlas prioritizes opening the open-access PDF directly in a new tab if available, falling back to opening the landing page/DOI in the default browser.
- **Search Pipeline**: 350ms debounced background worker querying OpenAlex REST API (`https://api.openalex.org/works?search={query}&per_page=6`), extracting title, authors, publication year, DOI, open-access status, and PDF URLs.

---

### UrlInputDialog (`pdfatlas.ui.components.url_dialog`)
Modal dialog for entering a web URL or DOI to attach to selected text.

```
+-------------------------------------------------------------------------------+
| Adw.HeaderBar [Title: "Add Web Link"]                     [Cancel] [Add Link] |
+-------------------------------------------------------------------------------+
| Gtk.Box                                                                       |
|   ├─ URL or DOI: Gtk.Entry ("https://... or 10.xxxx/...")                     |
|   └─ Link Title: Gtk.Entry ("Display label for link")                         |
+-------------------------------------------------------------------------------+
```

- **Normalization**: Auto-prefixes raw DOIs (`10.xxxx/...` → `https://doi.org/10.xxxx/...`) and schemes (`domain.com` → `https://domain.com`).
- **Selection Pre-fill**: Pre-fills the URL field if the selected document text resembles a web link or DOI.

---

### SearchResultsView & Previews (`pdfatlas.ui.components.search_results_view`, `pdfatlas.ui.portal`, `link_preview`, `masonry_layout`)
Search results container view and overlay cards for search matches and link destinations.

#### SearchResultsView (`pdfatlas.ui.components.search_results_view`)
Full-screen search results view activated when searching across document text. Switched via an internal `Gtk.Stack`:

```
+-------------------------------------------------------------------------------+
| SearchResultsView (Gtk.Box: .search-results-view)                             |
| +---------------------------------------------------------------------------+ |
| | Gtk.Stack                                                                 | |
|   ├─ "results": Gtk.ScrolledWindow                                          | |
|   │     └─ Gtk.Viewport                                                     | |
|   │           └─ Gtk.Box (.search-results-container)                        | |
|   │                 ├─ Pinned Cards (ResultRow items with pin badge)        | |
|   │                 ├─ Gtk.Separator (visible if pinned cards exist)        | |
|   │                 └─ Live Matches (ResultRow cards or Adw.StatusPage)     | |
|   └─ "empty": Adw.StatusPage                                                | |
|         ├─ Icon: "system-search-symbolic"                                   | |
|         ├─ Title: "No Results Found"                                        | |
|         └─ Description: "No matches found for “<query>”. Try different..."  | |
| +---------------------------------------------------------------------------+ |
+-------------------------------------------------------------------------------+
```

- **Empty State Display**: When a search query yields no matches and no cards are pinned, `SearchResultsView` switches `Gtk.Stack` to `"empty"`, rendering an `Adw.StatusPage` with `system-search-symbolic`, "No Results Found", and the search query. If pinned cards exist but no live matches are found, the view stays on `"results"` to keep pinned items accessible while showing the empty status page notice below the pinned list separator.
- **Masonry Layout (`MasonryLayoutManager`, `MasonryBox`)**: Enabled by default for `"grid"` search layout, `SearchResultsView` uses a responsive full-width `MasonryBox` (`halign=FILL`, `min_column_width=450`, `max_columns=3`) paired with `_FlexiblePortalLayout` on `LinkPortalPreviewCard` so cards scale proportionally to column width while varying height (`compute_portal_card_height`) and auto-trimming outer page white margins (`search_autotrim=True` by default, using page-level bounds with a `0.60 * page_w` sparse-page floor):
  - **Deduplicating Vertical Clustering**: Nearby matches on the same page (vertical gap $< 30\%$ page height, total span $\le 50\%$) are merged via `merge_search_results` into a single card covering their combined vertical span $[y_{\min}, y_{\max}]$. A floating pill badge indicates `Page X • N matches` (or `Page X` if single), counting exact visual occurrences of the query term within the card viewport.
  - **Dynamic Content-Adaptive Heights**: Instead of rigid fixed-height cards, `ResultRow` computes dynamic snippet card heights via `compute_portal_card_height(y0, y1, target_w=450, page_w, page_h)` strictly capped at 50% of page height (clamped between 90px and $\le 0.5 \times \text{page\_height}$ based on matched text block height + context padding). Single-line snippets produce compact ~95px cards while multi-line paragraphs and merged clusters produce tall ~220–290px cards without exceeding the 50% page height ceiling.
  - **Waterfall Masonry Packing**: Backed by `MasonryLayoutManager` implementing `HEIGHT_FOR_WIDTH` request mode and greedy `argmin(col_heights)` waterfall packing (`column_spacing=14`, `row_spacing=14`, `min_column_width=470`).
  - **Shadow & Margin Clearance**: With `min_column_width=470` (exceeding card 450px width + 12px margin) and dual-layer box-shadow (`0px 2px 8px rgba(0, 0, 0, 0.09), 0px 1px 2px rgba(0, 0, 0, 0.06)`), cards never collide or bleed shadows across adjacent items or container boundaries.

```
+-------------------------------------------------------------------------------+
| MasonryBox (Gtk.Box backed by MasonryLayoutManager)                           |
| Column 0 (x=0)               Column 1 (x=col_w + gap)        Column K-1       |
| +-------------------------+  +-------------------------+     ...              |
| | ResultRow (h=140px)     |  | ResultRow (h=220px)     |                      |
| +-------------------------+  |                         |                      |
| | ResultRow (h=180px)     |  +-------------------------+                      |
| |                         |  | ResultRow (h=120px)     |                      |
| +-------------------------+  +-------------------------+                      |
+-------------------------------------------------------------------------------+
```

- **`ResultRow` (`pdfatlas.ui.portal`)**: Search result card embedding a 1:1 physical pixel Cairo rasterized snippet of the search match context.
- **`LinkPreviewManager` (`pdfatlas.ui.link_preview`)**: Hover popover rendering destination snippets when lingering over citations or internal cross-references.

---

## 3. Feature Controllers (`pdfatlas.controllers.*`)

Controllers decouple business logic, state mutations, and external integrations from GTK view widgets:

- **`PrintController` (`printing.py`)**: Native printing integration using `Gtk.PrintDialog.print_file()` to hand PDF files directly to the OS print spooler/portal.
- **`DocumentLoader` (`document_loader.py`)**: Asynchronous document opening pipeline for local files, remote web PDF URLs (`source_type="url"`), and arXiv papers (`source_type="arxiv"`). Checks local URL disk cache (`~/.cache/pdfatlas/urls/<hash>/<name>.pdf`), displays in-tab loading screen with progress bar during downloads, validates `%PDF-` magic header bytes, and falls back to opening the fallback DOI / landing page in the default web browser if direct PDF downloading fails. For arXiv-named local files it schedules the GIL-heavy TeX-source diff mapper on a 1200 ms timer (instead of immediately on open) and drops late `idle_add` callbacks once the window is closing (`_is_closing`).
- **`SearchController` (`search.py`)**: Manages the search bar state machine (`SearchState`: `IDLE`, `EDITING`, `SEARCH_VIEW`, `RESULT_ACTIVE`), debounced text search, `Gtk.SearchEntry` clear-icon / `stop-search` / focus-descendant tracking, SQLite FTS queries, search portal card navigation, extracting precise word occurrence bounding boxes across all document pages via `page.search_for(query, flags=fitz.TEXT_DEHYPHENATE)`, dispatching orange outline stroke highlights to `PDFCanvas.set_search_highlights()`, and cleanly unfocusing to `PDFCanvas` when exiting search or clicking Clear (`×`).
- **`NavigationController` (`navigation.py`)**: Document navigation, viewport scroll synchronization, zoom anchoring, continuous step scrolling (80 px), and page-height-aware navigation (`Left`/`Right` arrow keys and vim `h`/`l` keys scrolling by `1 page height + gap` using `canvas.page_layout` and `canvas.page_gap`).
- **`TabController` (`tabs.py`)**: Multi-tab lifecycle, tab reordering, cross-window tab dragging, the per-tab right-click context menu (New Tab / New Window, conditional *Move to Window…* submenu listing every other window's document directory name and open tab titles when 2+ windows exist, Close Tab / Close Other Tabs), and tab state synchronization (restores viewport scroll position and reactively synchronizes `win.crop-mode` and `win.gapless-mode` GTK action states with the activated document's isolated `CropSettings`).
- **`AnnotationsController` (`annotations.py`)**: Text highlights, color palette swatches, note popover CRUD, and custom user link creation (`Adw.SplitButton` remembering most recent action among `Link Paper`, `Link File`, and `Link URL`; compact popover menu; unlinking; annotations list integration). Owns the unified selection toolbar attached to `MainWindow.content_overlay` with action order: `[Highlight, Remove, Copy, Copy Source TeX, Link, Unlink]`.
- **`ClipboardController` (`clipboard.py`)**: Text selection copying, citation formatting, and LaTeX source extraction.

---

## 4. Core Engine & Models (`pdfatlas.core.*`)

- **`DocumentModel` (`document.py`)**: Thread-safe PyMuPDF (`fitz.Document`) wrapper with mutex protection for multithreaded rasterization. Pre-caches page rectangles and native PDF links on `__init__` in milliseconds, while plain-text arXiv citation links are detected lazily on-demand in a dedicated background worker thread (`_arxiv_link_worker`) to eliminate startup stalls. Maintains per-document custom user links (`_custom_links`, `_custom_links_by_page`) and merges them into `get_page_links()` via `coalesce_rects_by_line()` for transparent GL quad rendering and click hit-testing as clean single bounding boxes per line.
- **`RenderWorker` & `RenderCache` (`renderer.py`, `cache.py`)**: Process-wide background render pool (lazily created by `PDFViewerApplication.get_render_worker()`, two child processes plus an optional 256 MB zero-copy SHM ring) and LRU pixel cache. `shutdown()` is idempotent, unregisters its `atexit` hook so a stopped worker can be garbage collected, drains/terminates children and closes the SHM segment. Priority queue: visible pages (0) → ±1 (1) → ±2/portals (2) → minimap (3) → crop scans (4).
- **`CropAnalyzer` (`crop.py`)**: Background bounding-box analyzer calculating optimal page whitespace crops.
- **`ArxivDiffMapper` (`arxiv_mapper.py`)**: Downloads arXiv LaTeX source bundles, parses TeX ASTs, and computes word-level sourcemaps between compiled PDF words and original TeX source lines.
- **`SearchIndex` & `DatabaseService` (`index.py`)**: In-memory / on-disk SQLite FTS5 index for fast full-text querying across hundreds of pages. Sorts search results by document reading order (`b.page, b.y0, b.x0`) instead of FTS relevance rank so results flow top-to-bottom page-by-page. Manages the `doc_state` SQLite table for persistent per-document reading state (`zoom`, `scroll_x`, `scroll_y`, `crop_enabled`, `page_gaps`), and the `custom_links` table for storing user-attached links. All SQL runs on a single dedicated `db-thread` executor; `close()` queues the connection close and then shuts the executor down without blocking the UI (called from window teardown, after the final state save).
- **`PdfSource` (`pdf_source.py`)**: Uniform data model for local files, remote web PDF URLs (`source_type="url"`), and arXiv identifiers (`source_type="arxiv"`), supporting `fallback_uri` for publisher DOI resolution and `RecentFilesManager` (unlimited history persistence).
- **`CropSettings` (`settings.py`)**: Dataclass model managing appearance, night-mode, crop, shadow, and last-opened file chooser directory configurations serialized to `~/.config/pdfatlas/settings.json`. Directional padding values (`min_padding_left`, `min_padding_right`, `min_padding_top`, `min_padding_bottom`) accept `float | "original"` to allow disabling cropping along specific edges while preserving full page margins. Serves as the global defaults template for newly opened documents, while per-document reading state overrides `enabled` and `page_gaps` per tab.
- **`TextSelection` (`text_selection.py`)**: Character-accurate spatial text selection state across multiple continuous pages. Provides `get_selection_line_rects()` to coalesce per-character bounding boxes into continuous, word/line-level rectangles.
- **`HTMLToMarkdown` (`html_to_markdown.py`)**: Lightweight, dependency-free HTML/XHTML to Markdown parser converting rich annotation text (Acrobat `/RC` XML fragments, inline styles, formatting, and links) into clean Markdown.
- **`PdfNotesImporter` (`pdf_notes_import.py`)**: Embedded PDF annotation scanner extracting comments from highlights and sticky notes, converting markup to Markdown, and positioning imported notes at 0-indexed page coordinates (centered on icon for sticky notes, and placed 12 pt to the right of the whole highlight bounding box mid-right border point). Provides `recalc_embedded_notes` in `index.py` to recalibrate note anchors by matching markdown content while preserving user-created notes.
- **`SearchCluster`, `cluster_search_results` & `merge_search_results` (`search_cluster.py`)**: Per-page vertical linear clustering and deduplication algorithm for search results. Groups matches linearly by reading order with a 30% page height split gap and 50% maximum vertical span, providing cluster headers, region detection (`Top`, `Middle`, `Bottom`), and percentage span metrics. In masonry mode (`merge_search_results`), consecutive nearby matches on the same page are deduplicated into a single unified card covering their combined vertical span $[y_{\min}, y_{\max}]$, showing a pill badge (`Page X • N matches`) and highlighting all occurrences within the snippet.


---

## 5. Build & Packaging Pipeline (`hatch_build.py`, `pdfatlas._version`)

- **Hatchling Build Hook (`hatch_build.py`)**: `CustomBuildHook` derives from `BuildHookInterface` and runs during package build (`uv tool install . --force` or wheel packaging). In `initialize()`, it queries `git rev-parse --short HEAD` in the repo root, creates `pdfatlas/_build_commit.py` containing `COMMIT = "<short-hash>"`, and registers it in `build_data["artifacts"]` so Hatchling packages it into the wheel despite being `.gitignored`. In `finalize()` and `clean()`, it cleans up the generated file from the source checkout.
- **Dynamic Version Resolver (`pdfatlas._version`)**: `get_version()` resolves the active version by checking:
  1. Live git repository short commit via `git rev-parse --short HEAD` (when working directly in a git clone).
  2. Build-time `pdfatlas._build_commit.py` dynamically imported via `importlib` (when installed as an isolated tool/wheel via `uv tool install`).
  3. Package metadata `importlib.metadata.version("pdfatlas")`.
  4. Static fallback `0.1.0`.
- **CLI Flag**: Configured via `argparse.ArgumentParser` in `main.py` with `--version` and `-v`.
