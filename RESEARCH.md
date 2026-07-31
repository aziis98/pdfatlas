# PDF Atlas Research Log & Technical Knowledge Base

This document records durable technical findings, architectural decisions, mathematical coordinate systems, and rejected approaches discovered during the development of **PDF Atlas**.

---

## 1. Durable Technical Findings

### 1.1. Bottom-Up PDF Coordinate Inversion
- **Finding:** PyMuPDF `link.get("to")` target points (`Point(x, y)`) use PDF native **bottom-up coordinates** where $0.0$ is at the bottom of the page and `page_rect.height` is at the top.
- **Top-Down Conversion:**
  $$\text{target\_y\_in\_page} = \max(0.0, \text{page\_rect.height} - \text{to\_point.y})$$
- **Application:** Used in `_on_link_clicked()` and `_show_link_portal_preview()` in [`pdf_viewer/ui/window.py`](pdf_viewer/ui/window.py) to position internal jump targets and center portal preview cards accurately.

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
  - The Cairo `_draw_func` and GL `_on_render` draw the best-match cached surface scaled to the current pinch zoom (the anchor center stays fixed via `cr.translate/scale` for Cairo, and the shader uniforms for GL).
  - On pinch end (`_on_pinch_end`): set `is_pinching=False`, call `set_zoom(final_zoom)` which triggers `_update_visibility` with the guard removed, queuing exact-match render jobs at the final zoom. Each completion redraws the page via `_on_render_complete` + `get_best` for progressively sharper display.
- **Stale-job guard removal:** `_on_render_complete` previously checked `zoom_key == current_zoom` and skipped redraw for stale zoom completions. This is now removed — every completion triggers a redraw, and `get_best` picks the best available match from the cache regardless of which zoom produced it.

### 1.9. Viewport vs Content Width Offset Invariant at High Zoom (>200%)
- **Finding:** When zooming past $200\%$, the page layout width `dw` (e.g. $1600\text{px}$) exceeds the window width `viewport_w` (e.g. $800\text{px}$). `canvas.get_width()` returns $1600\text{px}$ (the expanded GTK container allocation width), forcing $(1600 - 1600) / 2.0 = 0.0$. However, OpenGL and Cairo center pages relative to `viewport_w = 800px`, positioning the page's left edge at a **negative offset** $\text{page\_x0} = \frac{800 - 1600}{2.0} = -400\text{px}$.
- **Solution:** 
  1. `_screen_to_pdf_point`, `_hit_test_link`, and `get_link_screen_rect` in [`pdf_viewer/ui/canvas.py`](pdf_viewer/ui/canvas.py) use $\text{viewport\_w} = \text{self.hadjustment.get\_page\_size()}$ (the visible window width) without `max(0.0, ...)` clamping.
  2. `GLCanvas.on_draw()` in [`pdf_viewer/ui/gl_canvas.py`](pdf_viewer/ui/gl_canvas.py) passes `x_min = canvas.hadjustment.get_value()` into `u_offset` (`glUniform2f(u_offset, x_min, y_min)`), ensuring horizontal scrolling shifts all OpenGL page quads and text overlays in 1:1 sync with GTK scrolling.

### 1.10. arXiv TeX Diff & Word-Level Sourcemapping
- **Finding:** arXiv tarballs contain raw LaTeX source code divided across multiple `.tex` files with custom macro imports (`\input`, `\include`, `\subfile`). arXiv identifiers follow two main formats: modern IDs (`YYMM.NNNN(N)`) and pre-2007 legacy IDs (`category/YYMMNNN` or `category.subcategory/YYMMNNN`, e.g., `hep-ph/9504271`, `math.DG/0101001`).
- **URL & Path Resolution:** Legacy arXiv IDs include forward slashes (`/`). Parsing regexes (`ARXIV_ID_RE`) and cache directory path resolution (`arxiv_id_from_path`) must explicitly match both `(category)(.subcategory)?/YYMMNNN` and `YYMM.NNNN`. Legacy endpoints (`/pdf/hep-ph/9504271.pdf`, `/e-print/hep-ph/9504271`, and arXiv API query `?id_list=hep-ph/9504271`) mirror modern endpoint behaviors seamlessly.
- **Inlining & Tokenization:** `ArxivDiffMapper` in [`pdf_viewer/core/arxiv_mapper.py`](pdf_viewer/core/arxiv_mapper.py) recursively inlines TeX files, strips TeX comments (`%...`), tokenizes non-whitespace words, and extracts PyMuPDF native `words` (`page.get_text("words")`).
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
- **Application:** Adopted `Icon=com-aziis98-pdfatlas` (hyphenated) across `.desktop` entries, installer scripts (`PKGBUILD`), and Linux installation services (`pdf_viewer/core/installation.py`) as a pragmatic fix.

---

## 2. Rejected Approaches

| Approach | Why It Failed / Was Rejected | Replacement |
| :--- | :--- | :--- |
| **GTK `PageContainer` Child Widgets on `Gtk.GLArea`** | GTK's layout engine allocation pass diverged from OpenGL coordinate space under zoom and container margins. | Render all overlays directly in OpenGL shaders (`GLCanvas._on_render`) with math-based hit testing. |
| **`alpha=True` (`cairo.FORMAT_ARGB32`) for Page Surfaces** | Straight-alpha pixels from PyMuPDF produced dark, fuzzy antialiasing edges when blended in Cairo/OpenGL. | Render pages and portals with `alpha=False` on solid `cairo.FORMAT_RGB24` surfaces. |
| **`max_physical_zoom` Clamping in `RenderWorker`** | Clamped document page resolution to $192\text{ DPI}$ ($2.66\times$), causing OpenGL to scale up textures at high zoom levels, making document pages blurrier than link portals. | Removed `max_physical_zoom` capping for document pages (`physical_zoom = zoom * scale_factor`). |
| **Aspect-Ratio Cover Scaling (`max(w_scale, h_scale)`) for Portals** | Scaled portal snippet surfaces to fill container aspect ratio, cropping off left and right margins/text. | Set portal card size to $(\text{page\_dw} - 2 \times \text{page\_gap})$ and paint surfaces 1:1 without edge cropping. |
| **`GLib.timeout_add(150, ...)` Debounce on Link Hover** | Mouse motion ticks re-evaluated link hover states and continuously cancelled/reset the timer, preventing portal cards from popping up. | Fire `_on_link_hovered()` once on link entry with link `xref` / `from` equality checks. |
| **Best-match fallback inside `PageCache.get()`** | Caused `_update_visibility()` to always find a cached surface (even at a different zoom), preventing render jobs from being queued after zoom changes. Starvation of new renders. | `get()` returns exact matches only; `get_best()` (separate method) handles closest-zoom fallback for drawing. |
| **Clearing `RenderCache` on every zoom change** | Destroyed all previously rendered surfaces at nearby zoom levels, preventing `get_best()` from showing anything during pinch or incremental zoom. | Keep the cache populated across zoom changes; let LRU eviction handle memory. |
| **Sorting `rawdict` chars for Text Selection** | PDF fonts lack explicit space characters and rawdict sorting interleaved characters across adjacent columns in two-column papers. | Use PyMuPDF's native `page.get_text("words")` engine which preserves reading order and spaces natively. |
| **Clamping `page_x0` with `max(0.0, ...)`** | When zoomed in >200%, page width exceeds window width. Clamping `page_x0` to 0.0 shifted hit-testing horizontally by ~168 PDF points. | Use unclamped `page_x0 = (viewport_w - dw) / 2.0` with `viewport_w = hadjustment.get_page_size()`. |

