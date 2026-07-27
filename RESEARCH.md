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

### 1.5. Pure Math-Based OpenGL Canvas Hit Testing
- **Finding:** In OpenGL backend mode, GTK 4 `PageContainer` widget allocations can drift or fail to realize when child widgets are unmounted.
- **Solution:** Render interactive PDF link stroke outlines, link hover fills, search match highlights, and debug borders directly inside `GLCanvas._on_render()` using OpenGL shader quads. Perform hit-testing (`_hit_test_link`, `_hit_test_page`) directly from `page_layout` and `vadjustment`:
  $$x_0 = \frac{\text{viewport\_w} - \text{dw}}{2.0}, \quad y_0 = \text{y\_offset} - \text{scroll\_y}$$

---

## 2. Rejected Approaches

| Approach | Why It Failed / Was Rejected | Replacement |
| :--- | :--- | :--- |
| **GTK `PageContainer` Child Widgets on `Gtk.GLArea`** | GTK's layout engine allocation pass diverged from OpenGL coordinate space under zoom and container margins. | Render all overlays directly in OpenGL shaders (`GLCanvas._on_render`) with math-based hit testing. |
| **`alpha=True` (`cairo.FORMAT_ARGB32`) for Page Surfaces** | Straight-alpha pixels from PyMuPDF produced dark, fuzzy antialiasing edges when blended in Cairo/OpenGL. | Render pages and portals with `alpha=False` on solid `cairo.FORMAT_RGB24` surfaces. |
| **`max_physical_zoom` Clamping in `RenderWorker`** | Clamped document page resolution to $192\text{ DPI}$ ($2.66\times$), causing OpenGL to scale up textures at high zoom levels, making document pages blurrier than link portals. | Removed `max_physical_zoom` capping for document pages (`physical_zoom = zoom * scale_factor`). |
| **Aspect-Ratio Cover Scaling (`max(w_scale, h_scale)`) for Portals** | Scaled portal snippet surfaces to fill container aspect ratio, cropping off left and right margins/text. | Set portal card size to $(\text{page\_dw} - 2 \times \text{page\_gap})$ and paint surfaces 1:1 without edge cropping. |
| **`GLib.timeout_add(150, ...)` Debounce on Link Hover** | Mouse motion ticks re-evaluated link hover states and continuously cancelled/reset the timer, preventing portal cards from popping up. | Fire `_on_link_hovered()` once on link entry with link `xref` / `from` equality checks. |
