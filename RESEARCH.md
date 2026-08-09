# PDF Atlas Research Log & Technical Knowledge Base

This document records durable technical findings, architectural decisions, mathematical coordinate systems, and rejected approaches discovered during the development of **PDF Atlas**.

---

## 1. Durable Technical Findings

### 1.1. Bottom-Up PDF Coordinate Inversion
- **Finding:** PyMuPDF `link.get("to")` target points (`Point(x, y)`) use PDF native **bottom-up coordinates** where $0.0$ is at the bottom of the page and `page_rect.height` is at the top.
- **Top-Down Conversion:**
  $$\text{target\_y\_in\_page} = \max(0.0, \text{page\_rect.height} - \text{to\_point.y})$$
- **Application:** Used in `_on_link_clicked()` and `_show_link_portal_preview()` in [`pdfatlas/ui/window.py`](pdfatlas/ui/window.py) to position internal jump targets and center portal preview cards accurately.

### 1.2. 1:1 Pixel-Perfect MuPDF Rasterization & Alpha Blending
- **Finding 1 (Alpha Edge Fringing):** Rendering PDF pages or portal snippets with `alpha=True` returns straight (un-premultiplied) alpha channels. When painted in Cairo (`cairo.FORMAT_ARGB32`) or blended in OpenGL (`glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)`), straight alpha causes dark, fuzzy character edge fringing.
- **Solution 1:** Render document pages and portal snippets with `alpha=False` onto solid white `cairo.FORMAT_RGB24` surfaces. MuPDF's subpixel font engine computes crisp antialiasing directly against the white paper background.
- **Finding 2 (Resampling Blurry Text):** Scaling rendered surfaces by arbitrary fractional scale factors (e.g. $0.963$) in Cairo or OpenGL forces font stems across non-integer pixel boundaries, destroying text sharpness.
- **Solution 2:** Render portal snippets to the **exact physical hardware pixel dimensions** ($\text{portal\_w} \times \text{scale\_factor}$, $\text{portal\_h} \times \text{scale\_factor}$) using:
  $$\text{matrix\_x} = \frac{\text{render\_w}}{\text{page\_rect.width}}, \quad \text{matrix\_y} = \frac{\text{render\_h}}{\text{actual\_crop\_h}}$$
  Setting `surface.set_device_scale(scale_factor, scale_factor)` allows Cairo and GTK 4 to paint the surface 1:1 onto HiDPI screens with zero resampling.

### 1.3. Page Gap Isolation during Zoom Midpoint Anchoring
- **Finding:** Inter-page gaps (`page_gap = 12px`) are constant visual spacing constants that do **not** scale with the zoom factor.
- **The Drift Bug:** Multiplying the total viewport midpoint $\text{center\_y}$ by $\frac{\text{new\_zoom}}{\text{old\_zoom}}$ scales the accumulated page gaps (e.g. 66 gaps $\times 12\text{px} = 792\text{px}$ of unscaled gaps for Page 66), adding an artificial $158.4\text{px}$ vertical drift on Page 66 when zooming.
- **Solution:** Isolate unscaled page gaps from scaled content height:
  $$\text{fixed\_gaps} = (k + 1) \times \text{page\_gap}$$
  $$\text{content\_y} = \text{center\_y} - \text{fixed\_gaps}$$
  $$\text{new\_center\_y} = \text{fixed\_gaps} + \text{content\_y} \times \left(\frac{\text{new\_zoom}}{\text{old\_zoom}}\right)$$
  This anchors the vertical center of the viewport dead still at every page index from Page 1 to Page 353.

### 1.4. Kinetic/Inertial Scroll Deceleration Cancellation
- **Finding:** GTK 4's `Gtk.ScrolledWindow` runs kinetic/inertial scroll decay animations in the background frame clock during touchpad fling gestures. Updating `vadjustment.set_value()` during zoom is overwritten by active animation ticks on subsequent frames.
- **Solution:** Toggle `self.scrolled_window.set_kinetic_scrolling(False)` then `set_kinetic_scrolling(True)` immediately before reading adjustments in `set_zoom_level()`, halting active decay animations instantly.

### 1.5. Two-Tier Cache: Exact Match vs Best Match (`get` vs `get_best`)

- **Finding:** After a zoom change, `_update_visibility()` must decide whether to queue new render jobs. Using a best-match fallback during this decision causes starvation — no new jobs are ever queued because `get_best` always returns some surface.
- **Design Rule — Two separate lookup paths:**
  1. **Exact-match (`PageCache.get`):** Only returns a surface if `(page_index, round(scale, 2), crop_key)` matches exactly. Used exclusively by `_update_visibility()` to gate render job queuing.
  2. **Best-match (`PageCache.get_best`):** Scans all cache entries for the same page+crop and returns the one with the closest scale. Used exclusively by draw functions (`_draw_func`, `_on_render`) to show the best available surface when no exact match exists.
- **Do not put a best-match fallback inside `get()`.** Prior versions had a zoom-blind fallback in `get` that returned any uncropped surface regardless of scale — this made `_update_visibility` think every page was already cached and suppressed all new render jobs after zoom changes.

### 1.6. `fitz.Rect` vs `tuple` Key Mismatch in LRU Cache

- **Finding:** `fitz.Rect` objects do **not** compare equal to plain tuples `(x0, y0, x1, y1)` with the same numeric values. When used as `OrderedDict` keys, the lookup silently fails.
- **The bug:** `RenderWorker._run()` stored cache entries with tuple crop keys `(crop_rect.x0, crop_rect.y0, crop_rect.x1, crop_rect.y1)`, but `_draw_func`, `_on_render`, and `_update_visibility` all passed raw `fitz.Rect` objects to `cache.get()` and `cache.get_best()`. No cache hit was ever possible when crop was active.
- **Solution:** Normalize the crop key at the `RenderCache` boundary by converting `fitz.Rect` to `(x0, y0, x1, y1)` tuples before passing to `PageCache`:
  ```python
  @staticmethod
  def _normalize_crop_key(crop_rect):
      if crop_rect is None or isinstance(crop_rect, tuple):
          return crop_rect
      return (crop_rect.x0, crop_rect.y0, crop_rect.x1, crop_rect.y1)
  ```

### 1.7. Suppressing Render Jobs During Pinch-to-Zoom

- **Finding:** During a pinch gesture, zoom changes continuously (multiple times per frame). Queuing render jobs for each intermediate zoom floods the worker queue and causes visual lag as stale jobs complete long after the pinch ends.
- **Solution:** Add `is_pinching`, `pinch_center_x`, `pinch_center_y` state to `PDFCanvas`. While `is_pinching=True`:
  - `_update_visibility()` skips all render job queuing (both visible and prefetch).
  - `GLCanvas._on_render` draws the best-match cached surface scaled to the current pinch zoom (the anchor center stays fixed via the shader uniforms).
  - On pinch end (`_on_pinch_end`): set `is_pinching=False`, call `set_zoom(final_zoom)` which triggers `_update_visibility` with the guard removed, queuing exact-match render jobs at the final zoom. Each completion redraws the page via `_on_render_complete` + `get_best` for progressively sharper display.
- **Stale-job guard removal:** `_on_render_complete` previously checked `zoom_key == current_zoom` and skipped redraw for stale zoom completions. This is now removed — every completion triggers a redraw, and `get_best` picks the best available match from the cache regardless of which zoom produced it.

### 1.8. Scroll-Aware Low-Resolution Progressive Rendering
- **Finding:** On large raster PDFs (e.g., dense handwritten notes), a full-resolution page render at high zoom can take 60–100 ms. While scrolling, `_update_visibility` queues full-res jobs for each newly visible page, and the GPU shows a blank placeholder until the job completes — visible flicker during every scroll.
- **Solution:** Detect "slow" documents empirically and rasterize scroll previews at low resolution:
  1. `_on_render_complete` times each full-res render (via `time.perf_counter()` stored in `_render_started` keyed by the job key). Completions slower than `SLOW_RENDER_MS` (16 ms) push onto a `deque(maxlen=4)`; `_slow_renders = any(...)` flips on after a single slow render and off after 4 fast ones.
  2. On scroll start (`_on_scroll`), `_scroll_use_low_res` snapshots `_slow_renders` so the decision is stable for the whole scroll session.
  3. While scrolling a slow doc, `_effective_render_zoom()` returns `SCROLL_RENDER_ZOOM` (0.25) instead of the full texture zoom, so queued jobs rasterize 1/16 the pixels and upload near-instantly; the GL pass upscales them with `get_best` while the page is moving.
  4. A debounced `SCROLL_SETTLE_MS` (150 ms) timeout (`_on_scroll_settled`) sets `is_scrolling=False` and re-runs `_update_visibility`, which now queues full-res jobs; each completion redraws for progressively sharper display.
- **Cache-key hygiene:** the render-zoom must be part of the `in_flight` job key `(page_index, zoom_key, render_zoom, scale_factor, crop_key)` so a low-res preview job and the later full-res job for the same page don't collide. Low-res entries stay in the `RenderCache` (LRU-evicted) and only feed `get_best` when the full-res entry is absent.

### 1.9. Content-Box Centered Horizontal Coordinate Model
- **Finding:** `Gtk.ScrolledWindow` positions its child in a *content-box* coordinate space whose origin is the top-left of the child widget. When every page fits the viewport, the GTK box stretches to the viewport width (`box_w = viewport_w`) and pages are centered inside it. When a page is wider than the viewport, the box expands to the widest page's width (`box_w = max_dw`) and that page is left-aligned at content `x = 0`. The OpenGL layer and every hit-test / screen↔PDF transform must use the same `box_w`:
  $$\text{box\_w} = \max(\text{viewport\_w}, \max_i\, dw_i), \qquad \text{page\_x0} = \frac{\text{box\_w} - dw}{2.0}$$
  `box_w` collapses to `viewport_w` whenever pages fit, so the formula is backward compatible.
- **Solution:** 
  1. `content_width(layout, viewport_w)` in [`pdfatlas/core/layout.py`](pdfatlas/core/layout.py) computes `box_w`; `page_rect_at`, `screen_to_pdf`, `pdf_rect_to_screen`, and `link_screen_rect` derive `page_x0` from it.
  2. `GLCanvas._on_render` in [`pdfatlas/ui/gl_canvas.py`](pdfatlas/ui/gl_canvas.py) renders each page quad at `x_offset = (box_w - dw) / 2` and passes `x_min = hadjustment.get_value()` into `u_offset`, keeping GPU quads/overlays 1:1 with GTK scrolling.
  3. `PDFCanvas._hit_test_link` in [`pdfatlas/ui/canvas.py`](pdfatlas/ui/canvas.py) uses the same `box_w`-based `page_x0`; `get_link_screen_rect` forwards `scroll_x` to `link_screen_rect`.
- **Horizontal zoom anchoring:** `NavigationController.set_zoom_level` in [`pdfatlas/controllers/navigation.py`](pdfatlas/controllers/navigation.py) mirrors the vertical anchor math horizontally. It captures `box_w_old` before `canvas.set_zoom()`, then recomputes the anchor against the box center:
  $$\text{new\_center\_x} = \frac{\text{box\_w\_new}}{2} + \left(\text{center\_x} - \frac{\text{box\_w\_old}}{2}\right) \times \frac{\text{new\_zoom}}{\text{old\_zoom}}$$
  After `canvas.set_zoom()` it sets `hadjustment.set_upper(box_w_new)` (mirroring how `update_layout` sets the vertical upper synchronously) and clamps the value to $[0, \text{box\_w\_new} - \text{viewport\_w}]$, collapsing to `0` when everything fits. This keeps the point under the cursor fixed during Ctrl+scroll / pinch zoom and keeps the page centered when zooming via buttons or at document load.
- **Viewport-relative `center_x` convention:** Callers (`_on_pinch_scale_changed`, `_on_canvas_scroll` in [`pdfatlas/ui/window.py`](pdfatlas/ui/window.py)) now pass `center_x` in content space as `pointer_x + hadjustment.get_value()`, matching the existing `center_y` convention.

### 1.10. arXiv TeX Diff & Word-Level Sourcemapping
- **Finding:** arXiv tarballs contain raw LaTeX source code divided across multiple `.tex` files with custom macro imports (`\input`, `\include`, `\subfile`). arXiv identifiers follow two main formats: modern IDs (`YYMM.NNNN(N)`) and pre-2007 legacy IDs (`category/YYMMNNN` or `category.subcategory/YYMMNNN`, e.g., `hep-ph/9504271`, `math.DG/0101001`).
- **URL & Path Resolution:** Legacy arXiv IDs include forward slashes (`/`). Parsing regexes (`ARXIV_ID_RE`) and cache directory path resolution (`arxiv_id_from_path`) must explicitly match both `(category)(.subcategory)?/YYMMNNN` and `YYMM.NNNN`. Legacy endpoints (`/pdf/hep-ph/9504271.pdf`, `/e-print/hep-ph/9504271`, and arXiv API query `?id_list=hep-ph/9504271`) mirror modern endpoint behaviors seamlessly.
- **Inlining & Tokenization:** `ArxivDiffMapper` in [`pdfatlas/core/arxiv_mapper.py`](pdfatlas/core/arxiv_mapper.py) recursively inlines TeX files, strips TeX comments (`%...`), tokenizes non-whitespace words, and extracts PyMuPDF native `words` (`page.get_text("words")`).
- **Diff Alignment:** Running `difflib.SequenceMatcher` between PDF words and TeX words generates a 1-to-1 mapping $(i_{\text{pdf}} \leftrightarrow i_{\text{tex}})$. Selecting text or pressing `Ctrl+C` maps the selected word range directly to raw LaTeX snippets.

### 1.11. Pre-Baking GTK Card Decorations onto `cairo.ImageSurface` in Background Threads
- **Finding:** Rendering 30 search result or portal card widgets inside GTK layouts (`Gtk.FlowBox` / `Gtk.Overlay`) causes GTK frame repaints to re-evaluate vector clipping paths (`cr.clip_preserve()`), Cairo rounded rectangle arc math (`cr.arc` $\times 4$), background fills, and 1px border strokes on every scroll frame.
- **Solution:** Move card decoration rendering into background worker threads (`render_strip_surface` & `RenderWorker`):
  1. Render the PDF snippet onto an ARGB32 surface (`cairo.FORMAT_ARGB32`).
  2. Execute `apply_card_decorations(surface, scale_factor)` in the background worker thread using `cairo.OPERATOR_DEST_IN` with an 8px rounded rect path to clip outer transparent corners, followed by `cairo.OPERATOR_OVER` to stroke the 1px border.
  3. In `LinkPortalPreviewCard._draw_func`, GTK frame repaints perform a **pure 1:1 hardware memory blit** (`cr.set_source_surface(self.surface, 0, 0); cr.paint()`) with ZERO Cairo arc math or clipping calculations during scrolling.

### 1.12. Dedicated Single-Thread SQLite `DatabaseService` & `rawdict` Caching
- **Finding 1 (SQLite Threading Restrictions):** Python's `sqlite3` module enforces `check_same_thread=True` by default. Executing FTS5 queries directly on the main GTK UI thread blocks user typing, while passing `sqlite3.Connection` instances across worker threads raises `sqlite3.ProgrammingError`.
- **Solution 1:** Wrap all SQLite operations in a `DatabaseService` backed by a dedicated single-threaded worker (`ThreadPoolExecutor(max_workers=1, thread_name_prefix="db-thread")`). Database creation, FTS5 searching, and `save_doc_state` execute exclusively on `db-thread`, returning search results to the GTK main thread asynchronously via `GLib.idle_add()`.
- **Finding 2 (Redundant Character Parsing):** `get_query_match_rects()` calls `page.get_text("rawdict")` for every search result card, parsing full page character dictionaries 30+ times.
- **Solution 2:** Implement a thread-safe LRU cache `_rawdict_cache` indexed by `(pdf_path, page_no)` so `rawdict` is parsed once per page and reused across all result card renders and search highlight passes.

### 1.13. Freedesktop GTK Desktop Entry Icon Names: Dots vs Hyphens
- **Status / Ongoing Finding:**
  - **Empirical Observation:** Setting `Icon=com-aziis98-pdfatlas` (with hyphens) resolves the missing icon issue in GNOME Shell, whereas `Icon=com.aziis98.pdfatlas` (with dots) consistently fails to render the app icon even when matching filenames exist on disk. The exact root cause for why dots fail in GNOME Shell remains unclear.
- **Technical Specification & Source Code References:**
  - **Specification:** According to the [Freedesktop Icon Naming Specification](https://specifications.freedesktop.org/icon-naming-spec/icon-naming-spec-latest.html), periods (`.`) are explicitly listed as valid characters alongside hyphens (`-`), underscores (`_`), lowercase letters, and numbers.
  - **Extension Stripping in GLib ([`gio/gdesktopappinfo.c#L2048-L2055`](https://gitlab.gnome.org/GNOME/glib/-/blob/main/gio/gdesktopappinfo.c#L2048-L2055)):** GLib parses `.desktop` files and strips extensions (`.png`, `.svg`, `.xpm`) via `strrchr` before creating a `GThemedIcon`.
  - **Hyphen Fallbacks in GLib ([`gio/gthemedicon.c#L294-L313`](https://gitlab.gnome.org/GNOME/glib/-/blob/main/gio/gthemedicon.c#L294-L313)):** GLib's `GThemedIcon` uses hyphens (`'-'`) to build fallback resolution hierarchies (`com-aziis98-pdfatlas` $\rightarrow$ `com-aziis98` $\rightarrow$ `com`), while dots (`'.'`) do not generate fallback sub-names.
  - **Icon Theme Suffix Resolution in GTK ([`gtk/gtkicontheme.c#L2775-L2787`](https://gitlab.gnome.org/GNOME/gtk/-/blob/main/gtk/gtkicontheme.c#L2775-L2787)):** GTK's `GtkIconTheme` matches filenames against specific extension suffixes (`.symbolic.png`, `.png`, `.svg`, `.xpm`).
- **Application:** Adopted `Icon=com-aziis98-pdfatlas` (hyphenated) across `.desktop` entries, installer scripts (`PKGBUILD`), and Linux installation services (`pdfatlas/core/installation.py`) as a pragmatic fix.

### 1.14. Modular Layout, GTK GUI Builder & OpenGL Quad Renderer Decoupling
- **Finding:** Concentrating GTK widget construction, Cairo utility drawing, OpenGL shader lifecycle, and multi-page coordinate conversion within monolithic UI components (`window.py`, `canvas.py`, `gl_canvas.py`) led to code repetition and reduced testability.
- **Solution:**
  1. Extracted pure geometric coordinate conversions (`screen_to_pdf`, `pdf_rect_to_screen`, `page_at_point`, `anchor_before`, `anchor_after`, `layout_scale`) into [`pdfatlas/core/layout.py`](pdfatlas/core/layout.py) and added comprehensive unit test suites in `tests/test_layout.py`.
  2. Extracted OpenGL shader loading, program linking, and quad drawing into [`pdfatlas/ui/gl_renderer.py`](pdfatlas/ui/gl_renderer.py) with vertex/fragment shaders in `assets/shaders/`.
  3. Created declarative widget builders (`box`, `button`, `label`, `search_entry`, `scrolled_window`, `spacer`) in [`pdfatlas/ui/gui.py`](pdfatlas/ui/gui.py) and Cairo helpers in [`pdfatlas/ui/cairo_utils.py`](pdfatlas/ui/cairo_utils.py) to streamline GTK container assembly.

### 1.15. Multi-Window Tab Detach: Single-Process `Adw.TabView` Model
- **Finding:** `Adw.TabView` (libadwaita ≥ 1.0) natively implements all browser-style tab drag-and-drop plumbing within a single process — no custom `Gtk.DragSource`/`Gtk.DropTarget` code required:
  1. **Reordering** tabs inside a window.
  2. **Cross-window transfer:** dragging a tab onto another window's `Adw.TabBar` moves the page between `Adw.TabView`s via `adw_tab_view_transfer_page(page, other_view, position)` (the `Adw.TabPage` object is reused).
  3. **Detach-to-new-window:** dropping a tab on the desktop emits the `Adw.TabView::create-window` signal. The handler only needs to create a new window, present it, and **return its `Adw.TabView`**; the page is transferred into it automatically. It also handles re-inserting the page if the drop is cancelled.
- **Window lifecycle:** connect `page-detached` and close the window when `view.get_n_pages() == 0` (guard against `dispose` to avoid closing during teardown). Tab close buttons use the `close-page` signal whose default handler closes non-pinned pages.
- **Headerbar integration:** `Adw.TabBar` can be set as the `Adw.HeaderBar` `title_widget` (GNOME Web/Text-Editor pattern), placing the tabs inside the headerbar itself; `autohide=False` keeps the tab bar visible with a single tab.
- **Multi-process rejected:** a multi-window tab model spread across processes requires cross-process DnD (X11 selection / Wayland data device) plus a transport (D-Bus) to move document state between processes, for zero user-visible benefit. Single-process with one `Adw.TabView` per window is the idiomatic, robust choice.
- **Application:** reference prototype in [`scripts/multiwindow_tabs_prototype.py`](scripts/multiwindow_tabs_prototype.py) (`uv run scripts/multiwindow_tabs_prototype.py`).

### 1.16. Child-Process Rasterization (`spawn` + `multiprocessing.Queue`)
- **Finding:** PyMuPDF re-acquires the GIL in bursts during image decode and scaling. Even with `page.get_pixmap()` running in a background thread, those GIL bursts stall the main GTK thread, causing visible scroll/zoom hitches on scanned PDFs (benchmarked up to 72 ms main-thread stalls, 8 hitches ≥ 5 ms on a 200-page scan-heavy document).
- **Solution:** Move every PyMuPDF rasterization call into a dedicated **child process** (`pdfatlas/core/render_child.py`, spawned with `multiprocessing.get_context("spawn")`). The child owns the `fitz.Document` and performs `render`/`portal`/`crop`/`open` ops; the parent (`RenderWorker`) runs a priority queue + dispatcher thread (forwards jobs) + pump thread (rebuilds `cairo.ImageSurface` from raw RGB bytes into the existing caches). Raw pixels travel back over a `multiprocessing.Queue` (pickled `bytes`), so the parent never touches `fitz` at all.
- **Child independence:** `render_child` deliberately imports only `fitz` + stdlib — `pdfatlas/__init__.py` and `pdfatlas/core/__init__.py` are GI-free, so spawning the child never pulls GTK/cairo into the subprocess. The cross-process protocol is a set of `TypedDict`s (`ChildRequest`/`ChildResult`) documented on the module.
- **Backend switch:** the rasterization backend is selectable via `--render-mode {mp,mt}` (`create_render_worker` in `pdfatlas/core/renderer.py`). `mp` is the default child-process backend above; `mt` (`pdfatlas/core/renderer_mt.py`) is the original single-daemon-thread backend calling PyMuPDF against the shared `DocumentModel`, kept for benchmarking. Both expose the same public API (`queue_render_job`/`queue_crop_job`/`queue_portal_job`/`clear_canvas_render_jobs`/`set_document`/`shutdown`) and both deliver page renders as raw-RGB `PageTexture`; only minimap/portal rebuild cairo surfaces. The `mt` backend adds a generation/`_active_filepath` staleness guard (mirroring `mp`'s `_is_stale`) so stale in-flight results are dropped instead of drawn.
- **Lifecycle & failure rules (learned the hard way):**
  1. The parent keeps a `_pending` table (`kind`, `gen`, `filepath`, callbacks, `dispatched_at`) keyed by seq. A job is only "owned" by the parent once dispatched (`in_flight`); abandon paths must fire the same visible contract the success path does: render → `redraw_callback` (canvas re-requests), crop → finalize page as blank + progress/completion, portal → drop.
  2. Child death detection happens in the pump loop on `Queue.Empty`/EOF; on death, **abandon every pending entry** (else canvas `in_flight` wedges a page into a permanent grey placeholder and the crop scan spinner hangs forever) then respawn (bounded to 3 respawns). During respawn the dispatcher gates on `_accepting` so it abandons rather than queues into a dead pipe.
  3. Delivery exceptions must not swallow the redraw callback — wrap delivery in `try/except` and `_abandon` on failure.
- **Benchmark:** `scripts/benchmark_render.py` (A/B against the threaded worker). Child-process max main-thread stall 2.36 ms, 0 hitches ≥ 5 ms.

### 1.17. Cairo-Free GPU Texture Path (Raw RGB Upload, No Channel Swap)
- **Finding:** cairo `ImageSurface` stores pixels in native-endian BGRx memory for `FORMAT_RGB24`/`FORMAT_ARGB32` on little-endian machines. The GL canvas previously had to (1) swap PyMuPDF RGB samples → BGRA numpy buffer to feed cairo, then (2) upload that BGRx memory as `GL_RGBA` and swap channels back in the fragment shader (`vec4(tex.b, tex.g, tex.r, tex.a)`). A wasteful **RGB → BGR → RGB** roundtrip that existed only because cairo was the intermediate pixel carrier for textures.
- **Solution:** Drop cairo from the main canvas path entirely. PyMuPDF pixmaps are tightly packed (`stride == width × channels`, verified for arbitrary dims), so `page.get_pixmap(alpha=False).samples` bytes upload directly:
  $$glTexImage2D(\text{GL\_TEXTURE\_2D}, 0, \text{GL\_RGB8}, w, h, 0, \text{GL\_RGB}, \text{GL\_UNSIGNED\_BYTE}, \text{samples})$$
  with **zero copy and zero conversion**. The fragment shader becomes a passthrough (`FragColor = vec4(tex.rgb, 1.0)`). `RenderCache` now stores a `PageTexture` (`width`/`height`/`channels`/`samples` in [`pdfatlas/core/texture.py`](pdfatlas/core/texture.py)) instead of a cairo surface; the pump thread skips both the numpy swap and `set_device_scale` (device scale is only meaningful for cairo consumers; GL uses physical pixel dims directly).
- **Residual cairo:** minimap thumbnails and portal / link-preview cards still use cairo `ImageSurface` (BGRA) because those widgets need cairo vector drawing (viewport strips, borders, "Ld N" text, baked rounded-corner decorations via `apply_card_decorations`). Their RGB→BGR conversion happens on ~90 px and ~450×140 px surfaces — negligible cost.
- **`GL_UNPACK_ALIGNMENT` gotcha:** 3-channel uploads break the default 4-byte row alignment. The old cairo path uploaded 4 B/px so every row was 4-aligned and the driver never complained. A `GL_RGB` row is `width × 3` bytes; with the default `GL_UNPACK_ALIGNMENT = 4` the GPU mis-reads every row after the first (offset by the missing padding), producing diagonal texture skew on pages whose `width × 3 % 4 ≠ 0`. Set `glPixelStorei(GL_UNPACK_ALIGNMENT, 1)` immediately before `glTexImage2D` whenever uploading tightly-packed RGB.

### 1.19. Off-Main-Thread GL Texture Upload (Worker `Gdk.GLContext` + Fence Sync)
- **Finding:** `GLCanvas._on_render` uploaded page textures with a synchronous `glTexImage2D` on the main thread. At 2.5× texture zoom a full A4 page is ~9.4 MB; the pixel copy (plus `glDeleteTextures` eviction of scrolled-away pages in the same pass) stalled the UI thread for tens of milliseconds per visible page during flips. Rasterization is already off-thread (`render_child.py`), so the remaining flip choppiness came entirely from main-thread GL work.
- **Solution:** [`pdfatlas/ui/texture_uploader.py`](pdfatlas/ui/texture_uploader.py) uploads and deletes textures on a dedicated daemon thread that owns its own GL context:
  1. `Gdk.Display.create_gl_context()` (GDK ≥ 4.6) creates a worker context; `gdk_gl_context_is_shared(worker, area.get_context())` confirms it shares textures with the GLArea's context. GDK 4.4+ guarantees two contexts on the same display with the same settings are compatible.
  2. The worker runs `glTexImage2D` + parameter setup, inserts a `glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE)` and waits with `glClientWaitSync(..., GL_TIMEOUT_IGNORED)` **inside the worker context**. Waiting there (not on the main thread) guarantees the copy completed before the texture is published, so the main thread can safely sample it from its own context — no cross-context `GLsync` needed.
  3. Evicted textures are queued to the worker (`_deletes`) and drained there, so `glDeleteTextures` never runs on the main thread. Pending/in-flight textures evicted before landing are deleted right after upload instead of being published.
  4. On publish the worker schedules `GLib.idle_add(notify)` → the canvas `queue_draw()`s; the render pass draws a white-card placeholder until then. Fallback (headless/old GDK/non-shared context): synchronous main-thread uploads, identical behavior to the old path.
- **Constants gotcha:** PyOpenGL (3.1.10) exposes the fence condition as `GL_SYNC_GPU_COMMANDS_COMPLETE` — `GL_SYNC_GPU_COMMANDS_NONE` does **not** exist in this build and raises `AttributeError` at import time.
- **Benchmark:** `scripts/benchmark_flip.py` (run under `xvfb-run` + llvmpipe). Synchronous per-page upload stalls the main thread up to ~2.5 ms per page; the `TextureUploader` worker path measures **0.00 ms** max main-thread gap for the same burst.

### 1.18. Capping Page-Texture Render Zoom at 250% (`texture_zoom`)
- **Finding:** Rasterizing full pages at extreme zoom levels (UI allows up to 5000%) burns enormous texture memory and rasterization time for pixels the eye can no longer resolve once the page quad is GPU-upscaled.
- **Solution:** Cap the **zoom setting** used for page-texture rendering at `MAX_TEXTURE_ZOOM = 2.5` (250%):
  $$\text{render\_zoom} = \text{layout\_scale}(\min(\text{zoom}, 2.5), \text{dpi\_scale\_factor}), \qquad \text{texture\_res} = \text{render\_zoom} \times \text{scale\_factor}$$
  via `texture_zoom()` in [`pdfatlas/core/layout.py`](pdfatlas/core/layout.py). Applied consistently at the two scale consumers (`PDFCanvas.render_zoom()` feeds `GLCanvas._on_render` cache lookups and the default `_request_render` job zoom, overridden by `_effective_render_zoom()` during low-res scrolls).
  Beyond 250% zoom the quad is drawn at the full logical `dw × dh` size while the texture stays at the 2.5× resolution — `GL_LINEAR` upscales it ("rescaling technique"). Overlay math (`scale = zoom × dpi_scale_factor`) is deliberately **not** capped so selection/highlight/link rects stay pixel-correct at true zoom.
- **Configurable in Settings:** the cap is a `CropSettings.max_texture_zoom` field (default `2.5`), editable from the Settings dialog as a text field whose placeholder is **"Infinity"** — clearing the field sets it to `None` (no cap, textures render at true zoom). `min_zoom` / `max_zoom` settings (defaults `0.25` / `50.0`) replace the hardcoded clamp in `NavigationController.set_zoom_level`; the window re-clamps the current zoom when the limits change.
- **Why logical-zoom cap (not physical-pixel cap):** capping physical pixels per PDF point (the earlier rejected `max_physical_zoom` clamp at 2.66×) made HiDPI (2×) displays start rescaling at 150% zoom, blurring text through the 150–250% range. Capping the zoom *setting* keeps textures 1:1 sharp up to 250% zoom on every display while still bounding memory at the 250%-zoom-equivalent size.
- **Cache consistency:** the stored cache scale (`render_zoom × scale_factor`), the gating check, and the draw lookup all use the capped value, so zooming past 250% reuses the same cached texture instead of re-rendering; `get_best` proximity picks it during pinch too.



---

## 2. Rejected Approaches

| Approach | Why It Failed / Was Rejected | Replacement |
| :--- | :--- | :--- |
| **GTK `PageContainer` Child Widgets on `Gtk.GLArea`** | GTK's layout engine allocation pass diverged from OpenGL coordinate space under zoom and container margins. | Render all overlays directly in OpenGL shaders (`GLCanvas._on_render`) with math-based hit testing. |
| **`alpha=True` (`cairo.FORMAT_ARGB32`) for Page Surfaces** | Straight-alpha pixels from PyMuPDF produced dark, fuzzy antialiasing edges when blended in Cairo/OpenGL. | Render pages and portals with `alpha=False` on solid `cairo.FORMAT_RGB24` surfaces. |
| **`max_physical_zoom` Clamping in `RenderWorker`** | Clamped document page resolution to $192\text{ DPI}$ ($2.66\times$), causing OpenGL to scale up textures at high zoom levels, making document pages blurrier than link portals. | Cap the **logical zoom setting** at 250% (`texture_zoom` in `layout.py`) instead of physical pixel density — textures stay 1:1 sharp to 250% on any display, GPU rescales only beyond. |
| **Aspect-Ratio Cover Scaling (`max(w_scale, h_scale)`) for Portals** | Scaled portal snippet surfaces to fill container aspect ratio, cropping off left and right margins/text. | Set portal card size to $(\text{page\_dw} - 2 \times \text{page\_gap})$ and paint surfaces 1:1 without edge cropping. |
| **`GLib.timeout_add(150, ...)` Debounce on Link Hover** | Mouse motion ticks re-evaluated link hover states and continuously cancelled/reset the timer, preventing portal cards from popping up. | Fire `_on_link_hovered()` once on link entry with link `xref` / `from` equality checks. |
| **Best-match fallback inside `PageCache.get()`** | Caused `_update_visibility()` to always find a cached surface (even at a different zoom), preventing render jobs from being queued after zoom changes. Starvation of new renders. | `get()` returns exact matches only; `get_best()` (separate method) handles closest-zoom fallback for drawing. |
| **Clearing `RenderCache` on every zoom change** | Destroyed all previously rendered surfaces at nearby zoom levels, preventing `get_best()` from showing anything during pinch or incremental zoom. | Keep the cache populated across zoom changes; let LRU eviction handle memory. |
| **Sorting `rawdict` chars for Text Selection** | PDF fonts lack explicit space characters and rawdict sorting interleaved characters across adjacent columns in two-column papers. | Use PyMuPDF's native `page.get_text("words")` engine which preserves reading order and spaces natively. |
| **Clamping `page_x0` with `max(0.0, ...)`** | When zoomed in >200%, page width exceeds window width. Clamping `page_x0` to 0.0 shifted hit-testing horizontally by ~168 PDF points. | Use unclamped `page_x0 = (viewport_w - dw) / 2.0` with `viewport_w = hadjustment.get_page_size()`. |
| **Viewport-centered horizontal offsets (`page_x0 = (viewport_w - dw) / 2`)** | Worked only while `scroll_x` never moved. Once the page overflowed the viewport the GTK content box is `box_w = max_dw` wide (page at content `x = 0`), so the viewport-centered formula misaligned GPU quads, hit-testing, links, and selection overlays from the GTK page box, and horizontal scrolling was asymmetric (scroll pinned at 0, page "kept being centered"). | Content-box-centered `page_x0 = (box_w - dw) / 2` via `content_width(layout, viewport_w)`, with `hadjustment` centered / cursor-anchored in `set_zoom_level`. |
| **Multi-process multi-window tab detach** | Requires cross-process DnD (X11 selection / Wayland data device) and a D-Bus transport to migrate document state between processes; libadwaita offers no support; fragile and no user-visible benefit. | Single-process, one `Adw.TabView` per window — reordering, cross-window transfer, and desktop-detach (`::create-window`) are all built in. |
| **Background-thread rasterization (PyMuPDF in a `ThreadPoolExecutor`)** | PyMuPDF re-acquires the GIL in bursts during decode/scaling, so long scans still stutter the main GTK thread (72 ms stalls, 8 hitches ≥ 5 ms in benchmarks). Moving the GIL-offloading work to a thread cannot avoid this. | Dedicated `spawn` child process owning `fitz.Document` (`render_child.py`); parent pump thread only rebuilds cairo surfaces from raw pixels. |
| **cairo `ImageSurface` as the intermediate for GL page textures** | `FORMAT_RGB24`/`FORMAT_ARGB32` cairo memory is BGRx on little-endian, forcing an RGB→BGR numpy swap in the pump thread and a BGR→RGB swap back in the fragment shader. Two full passes over every page texture for zero visual benefit. | Store raw tightly-packed PyMuPDF RGB `samples` as a `PageTexture` and upload `GL_RGB` directly; keep cairo only for the small minimap/portal-card surfaces that still need vector drawing. |
| **Synchronous `glTexImage2D` in the render pass (main thread)** | Even with rasterization off-thread, uploading each ~9.4 MB page texture and deleting scrolled-away textures inside `_on_render` stalled the UI thread for tens of ms per flip. | `TextureUploader` worker thread with a shared `Gdk.GLContext`: uploads + `glDeleteTextures` run off the main thread, fence-synced before publish. |

