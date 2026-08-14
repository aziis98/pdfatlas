"""Editable markdown notes anchored to PDF coordinates.

Note icons are GTK buttons overlaid on the page containers; hovering an icon
shows a rendered preview popover (shared WebKit webview), clicking opens a
standalone editor window with Source/Rendered tabs and autosave.
"""

import json

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")
from gi.repository import Adw, Gdk, GLib, Gtk, WebKit

from ..core.layout import layout_scale, pdf_point_to_page_margin
from ..core.resources import get_assets_dir

#: Debounce before showing the hover preview after entering an icon.
PREVIEW_SHOW_MS = 120
#: Debounce before hiding the hover preview after leaving an icon/popover.
PREVIEW_HIDE_MS = 200
#: Debounce between keystroke and webview re-render in the editor.
EDITOR_RENDER_MS = 32
#: Debounce between keystroke and DB write in the editor.
EDITOR_SAVE_MS = 300

HTML_PREVIEW_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Markdown Preview</title>

  <!-- markdown-it for Markdown parsing -->
  <script src="markdown-it.min.js"></script>
  <!-- markdown-it-texmath: tokenizes $/$$/\\(…\\)/\\[…\\]/begin{} math BEFORE the
       escape rule, so LaTeX escapes like \\{ \\} \\, reach KaTeX untouched. -->
  <script src="texmath.js"></script>

  <!-- KaTeX for TeX Math rendering -->
  <link rel="stylesheet" href="katex.min.css">
  <script src="katex.min.js"></script>

  <style>
    :root {
      color-scheme: light dark;
      --text-color: #24292e;
      --code-bg: #f6f8fa;
      --border-color: #e1e4e8;
      --link-color: #0366d6;
    }

    @media (prefers-color-scheme: dark) {
      :root {
        --text-color: #d4d4d4;
        --code-bg: #2d2d2d;
        --border-color: #3c3c3c;
        --link-color: #58a6ff;
      }
    }

    /* No background-color here: the webview is transparent so the popover /
       editor window's theme background shows through. */
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      font-size: 15px;
      line-height: 1.6;
      color: var(--text-color);
      padding: 0 16px;
      margin: 0;
      word-wrap: break-word;
    }

    /* Compact variant for the hover preview popover: no body padding,
       smaller type, tighter heading/block margins. */
    body.compact {
      font-size: 13px;
      line-height: 1.45;
      padding: 0 8px;
    }
    body.compact h1, body.compact h2, body.compact h3,
    body.compact h4, body.compact h5, body.compact h6 {
      margin-top: 10px;
      margin-bottom: 6px;
      border-bottom: none;
      padding-bottom: 0;
    }
    body.compact p {
      margin: 6px 0;
    }
    body.compact ul, body.compact ol {
      margin: 6px 0;
      padding-left: 16px;
    }
    body.compact pre {
      padding: 8px;
    }
    body.compact blockquote {
      padding: 0 0.5em;
    }
    body.compact code {
      padding: 0.1em 0.3em;
    }

    h1, h2, h3, h4, h5, h6 {
      margin-top: 24px;
      margin-bottom: 16px;
      font-weight: 600;
      line-height: 1.25;
      border-bottom: 1px solid var(--border-color);
      padding-bottom: 0.3em;
    }

    h1 { font-size: 2em; }
    h2 { font-size: 1.5em; }
    h3 { font-size: 1.25em; }

    a {
      color: var(--link-color);
      text-decoration: none;
    }
    a:hover {
      text-decoration: underline;
    }

    code {
      font-family: SFMono-Regular, Consolas, "Liberation Mono", Menlo, monospace;
      font-size: 85%;
      background-color: var(--code-bg);
      padding: 0.2em 0.4em;
      border-radius: 6px;
    }

    pre {
      background-color: var(--code-bg);
      padding: 16px;
      overflow: auto;
      font-size: 85%;
      line-height: 1.45;
      border-radius: 6px;
    }

    pre code {
      background-color: transparent;
      padding: 0;
    }

    blockquote {
      margin: 0;
      padding: 0 1em;
      color: #6a737d;
      border-left: 0.25em solid var(--border-color);
    }

    table {
      border-collapse: collapse;
      width: 100%;
      margin-top: 16px;
      margin-bottom: 16px;
    }

    table th, table td {
      padding: 6px 13px;
      border: 1px solid var(--border-color);
    }

    table tr:nth-child(2n) {
      background-color: var(--code-bg);
    }

    .katex-display {
      overflow-x: auto;
      overflow-y: hidden;
      padding: 8px 0;
    }

    /* markdown-it-texmath output wrappers (css/texmath.css) */
    .katex { font-size: 1em !important; }
    eq { display: inline-block; }
    eqn { display: block; }
    section.eqno {
      display: flex;
      flex-direction: row;
      align-content: space-between;
      align-items: center;
    }
    section.eqno > eqn {
      width: 100%;
      margin-left: 3em;
    }
    section.eqno > span {
      width: 3em;
      text-align: right;
    }
  </style>
</head>
<body>
  <div id="content"></div>

  <script>
    let _md = null;

    function getMarkdownIt() {
      if (_md === null &&
          typeof markdownit !== 'undefined' &&
          typeof texmath !== 'undefined' &&
          typeof katex !== 'undefined') {
        _md = markdownit({ html: true }).use(texmath, {
          engine: katex,
          delimiters: ['dollars', 'brackets', 'beg_end'],
          katexOptions: { throwOnError: false }
        });
      }
      return _md;
    }

    function updateContent(markdownText) {
      const container = document.getElementById("content");
      if (!container) return;

      const md = getMarkdownIt();
      if (md !== null) {
        container.innerHTML = md.render(markdownText);
      } else {
        container.textContent = markdownText;
      }
    }
  </script>
</body>
</html>
"""

# Compact variant for the hover preview popover (body class="compact" — the
# CSS rules live in the template above next to body { … }). The editor's
# Rendered tab keeps the full-padding template.
HTML_PREVIEW_TEMPLATE_COMPACT = HTML_PREVIEW_TEMPLATE.replace(
    "<body>", '<body class="compact">', 1
)


def _assets_base_uri() -> str:
    """Base URI (file://…) for the vendored markdown assets, trailing slash."""
    return (get_assets_dir() / "markdown").as_uri() + "/"


class NotesLayer:
    """Owns note icons over the canvas, the shared hover preview, and editors."""

    def __init__(self, win):
        self.win = win
        self._icons: dict[int, Gtk.Button] = {}
        self._icon_menus: dict[int, Gtk.Popover] = {}
        self._editors: dict[int, "NoteEditorWindow"] = {}
        self._preview_webview: WebKit.WebView | None = None
        self._preview_popover: Gtk.Popover | None = None
        self._preview_loaded = False
        self._pending_preview_md: str | None = None
        self._hover_show_id: int | None = None
        self._hover_hide_id: int | None = None
        self._in_preview = False

    # --- Lifecycle -------------------------------------------------------

    def prepare(self):
        """Idempotently create and load the shared preview webview."""
        if self._preview_webview is None:
            self._preview_webview = WebKit.WebView.new()
            self._preview_webview.connect("load-changed", self._on_preview_load_changed)
            self._preview_webview.load_html(
                HTML_PREVIEW_TEMPLATE_COMPACT, _assets_base_uri()
            )
        if self._preview_loaded and self._pending_preview_md is not None:
            md = self._pending_preview_md
            self._pending_preview_md = None
            self._render_preview(md)

    def set_notes(self, notes: list[dict]):
        self._remove_all_icons()
        for note in notes:
            self._ensure_icon(note)

    def clear(self):
        self._remove_all_icons()
        for ed in list(self._editors.values()):
            ed.close()
        self._editors.clear()
        self.hide_preview()

    def close(self):
        self.clear()
        if self._preview_popover is not None:
            self._preview_popover.popdown()
            self._preview_popover.unparent()
            self._preview_popover = None
        if self._preview_webview is not None:
            if self._preview_webview.get_parent() is not None:
                self._preview_webview.unparent()
            self._preview_webview = None
        self._preview_loaded = False

    # --- Creation / deletion ---------------------------------------------

    def create_note(self, page: int, x: float, y: float):
        # WARNING: the on_complete callback runs inside GLib's idle loop, so it
        # MUST return GLib.SOURCE_REMOVE. A lambda that returns GLib.idle_add's
        # result (a non-zero source id) reschedules itself forever — hundreds of
        # _on_note_saved calls/sec → endless editor pop-ups + CPU pegging.
        def on_complete(nid: int) -> bool:
            GLib.idle_add(self._on_note_saved, nid, page, x, y)
            return GLib.SOURCE_REMOVE

        self.win.db_service.save_note(page, x, y, "", on_complete=on_complete)

    def _on_note_saved(self, nid: int, page: int, x: float, y: float):
        note = {"id": nid, "page": page, "x": x, "y": y, "markdown": ""}
        self.win.notes.append(note)
        self._ensure_icon(note)
        self.win._update_annotations_button()
        self.open_editor(note)

    def delete_note(self, note: dict):
        nid = note["id"]
        self.win.db_service.delete_note(nid)
        self.win.notes = [n for n in self.win.notes if n.get("id") != nid]
        self._remove_icon(nid)
        ed = self._editors.pop(nid, None)
        if ed is not None:
            ed.close()
        self.win._update_annotations_button()
        self._preview_popdown()

    def save_content(self, note_id: int, markdown: str):
        note = next((n for n in self.win.notes if n.get("id") == note_id), None)
        if note is None:
            return  # deleted while an editor was still open
        note["markdown"] = markdown
        self.win.db_service.update_note(note_id, markdown)

    # --- Icons ------------------------------------------------------------

    def _ensure_icon(self, note: dict):
        nid = note["id"]
        if nid in self._icons:
            return
        if not (0 <= note["page"] < len(self.win.canvas.containers)):
            return
        btn = Gtk.Button(icon_name="mail-attachment-symbolic")
        btn.add_css_class("note-icon")
        btn.set_halign(Gtk.Align.START)
        btn.set_valign(Gtk.Align.START)
        btn.set_tooltip_text("Note")
        motion = Gtk.EventControllerMotion.new()
        motion.connect("enter", lambda ctrl, x, y: self._schedule_preview_show(note))
        motion.connect("leave", lambda ctrl: self._schedule_preview_hide())
        btn.add_controller(motion)
        btn.connect("clicked", lambda b: self._on_icon_clicked(note))
        # Right-click: the note's own context menu (Delete note) instead of the
        # page-level "Add note here" popover (the canvas gesture defers to
        # NotesLayer.icon_at()).
        rc = Gtk.GestureClick.new()
        rc.set_button(3)
        rc.connect("pressed", self._on_icon_context_press, note)
        btn.add_controller(rc)
        self.win.canvas.containers[note["page"]].add_overlay(btn)
        self._icons[nid] = btn
        self._position_icon(note, btn)

    def _position_icon(self, note: dict, btn: Gtk.Button):
        layout = self.win.canvas.page_layout
        if not (0 <= note["page"] < len(layout)):
            return
        _y_offset, dw, dh, crop_rect = layout[note["page"]]
        scale = layout_scale(self.win.canvas.zoom, self.win.canvas.dpi_scale_factor)
        mx, my = pdf_point_to_page_margin(
            scale, note["x"], note["y"], crop_rect, dw, dh
        )
        btn.set_margin_start(int(mx))
        btn.set_margin_top(int(my))

    def _remove_icon(self, nid: int):
        menu = self._icon_menus.pop(nid, None)
        if menu is not None:
            menu.popdown()
            menu.unparent()
        btn = self._icons.pop(nid, None)
        if btn is not None:
            btn.unparent()

    def _remove_all_icons(self):
        for nid in list(self._icons):
            self._remove_icon(nid)

    def on_layout_changed(self):
        self.hide_preview()
        container_count = len(self.win.canvas.containers)
        for note in self.win.notes:
            btn = self._icons.get(note.get("id"))
            if btn is None:
                continue
            if note["page"] < container_count:
                self._position_icon(note, btn)

    def _on_icon_clicked(self, note: dict):
        self.hide_preview()
        self.open_editor(note)

    def _on_icon_context_press(self, gesture, n_press, x, y, note: dict):
        """Classic right-click context menu on a note icon: Delete note."""
        nid = note["id"]
        popover = self._icon_menus.get(nid)
        if popover is None:
            btn = self._icons.get(nid)
            if btn is None:
                return
            popover = Gtk.Popover()
            popover.set_parent(btn)
            delete_btn = Gtk.Button(label="Delete note")
            delete_btn.connect("clicked", lambda b: self._on_icon_menu_delete(nid))
            popover.set_child(delete_btn)
            self._icon_menus[nid] = popover
        # Gdk.Rectangle() Boxed ctor args are silently ignored — set fields.
        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        popover.set_pointing_to(rect)
        popover.popup()

    def _on_icon_menu_delete(self, nid: int):
        popover = self._icon_menus.get(nid)
        if popover is not None:
            popover.popdown()
        note = next((n for n in self.win.notes if n.get("id") == nid), None)
        if note is not None:
            self.delete_note(note)

    def icon_at(self, x: float, y: float) -> bool:
        """True if (x, y) — viewport coordinates, as delivered by the canvas
        right-click gesture — is over a note icon. The canvas uses this to
        defer its "Add note here" popover to the icon's own context menu."""
        for nid, btn in self._icons.items():
            if not btn.get_mapped():
                continue
            note = next((n for n in self.win.notes if n.get("id") == nid), None)
            if note is None:
                continue
            rect = self._preview_anchor_rect(note)
            if rect is None:
                continue
            cx = rect.x + rect.width / 2.0
            cy = rect.y + rect.height / 2.0
            if abs(x - cx) <= 20.0 and abs(y - cy) <= 20.0:
                return True
        return False

    def open_editor(self, note: dict):
        nid = note["id"]
        if nid in self._editors:
            self._editors[nid].present()
            return
        ed = NoteEditorWindow(self.win, note)
        self._editors[nid] = ed
        ed.present()

    # --- Hover preview -----------------------------------------------------

    def _on_preview_load_changed(self, web_view, load_event):
        if load_event == WebKit.LoadEvent.FINISHED:
            self._preview_loaded = True
            if self._pending_preview_md is not None:
                self._render_preview(self._pending_preview_md)
                self._pending_preview_md = None

    def _render_preview(self, markdown: str):
        if self._preview_webview is None:
            return
        js = "updateContent(" + json.dumps(markdown) + ");"
        self._preview_webview.evaluate_javascript(js, -1, None, None, None, None, None)

    def _schedule_preview_show(self, note: dict):
        if self._hover_show_id is not None:
            GLib.source_remove(self._hover_show_id)
        if self._hover_hide_id is not None:
            GLib.source_remove(self._hover_hide_id)
            self._hover_hide_id = None
        self._hover_show_id = GLib.timeout_add(
            PREVIEW_SHOW_MS, self._on_preview_show, note
        )

    def _schedule_preview_hide(self):
        if self._hover_show_id is not None:
            GLib.source_remove(self._hover_show_id)
            self._hover_show_id = None
        if self._hover_hide_id is not None:
            GLib.source_remove(self._hover_hide_id)
        self._hover_hide_id = GLib.timeout_add(PREVIEW_HIDE_MS, self._on_preview_hide)

    def _on_preview_show(self, note: dict):
        self._hover_show_id = None
        if self._in_preview:
            return GLib.SOURCE_REMOVE
        if self._preview_webview is None:
            self.prepare()
        if not self._preview_loaded:
            self._pending_preview_md = note["markdown"]
            return GLib.SOURCE_REMOVE
        webview = self._preview_webview
        if webview is None:
            return GLib.SOURCE_REMOVE
        self._render_preview(note["markdown"])
        icon = self._icons.get(note["id"])
        if icon is None:
            return GLib.SOURCE_REMOVE
        if self._preview_popover is None:
            self._preview_popover = Gtk.Popover()
            # GTK 4.22: set_modal/set_relative_to are gone; modality is set_autohide
            # and widget anchoring must be expressed as a pointing rect in the
            # popover parent's (canvas) coordinates.
            self._preview_popover.set_autohide(False)  # type: ignore[attr-defined]
            self._preview_popover.set_has_arrow(True)
            self._preview_popover.add_css_class("note-preview-popover")
            self._preview_popover.set_parent(self.win.canvas)
            w = max(220, min(self.win.canvas.get_width() // 3, 300))
            child = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            child.set_size_request(w, 180)
            webview.set_hexpand(True)
            webview.set_vexpand(True)
            child.append(webview)
            preview_motion = Gtk.EventControllerMotion.new()
            preview_motion.connect("enter", self._on_preview_enter)
            preview_motion.connect("leave", self._on_preview_leave)
            child.add_controller(preview_motion)
            self._preview_popover.set_child(child)
        # Anchor the preview to the icon's bounds in canvas coordinates. GTK's
        # compute_bounds() returns garbage across the scrolled viewport (the
        # scroll offset lands in the transform twice), so derive the rect from
        # the note's PDF point with the same layout math used for hit-testing
        # (inverse of _screen_to_pdf_point). The canvas and the gesture share
        # their origin, so the result is in popover-parent space directly.
        rect = self._preview_anchor_rect(note)
        if rect is None:
            return GLib.SOURCE_REMOVE
        self._preview_popover.set_pointing_to(rect)
        self._preview_popover.popup()
        if getattr(self.win, "debug_note_rect", False):
            # _preview_anchor_rect is viewport-relative (scroll already subtracted);
            # the GL overlay draws in content coordinates, so add scroll back.
            canvas = self.win.canvas
            sx = canvas.hadjustment.get_value() if canvas.hadjustment else 0.0
            sy = canvas.vadjustment.get_value() if canvas.vadjustment else 0.0
            canvas.debug_note_rect = (
                rect.x + sx,
                rect.y + sy,
                float(rect.width),
                float(rect.height),
            )
            canvas.queue_draw_overlays("debug-note-rect")
        return GLib.SOURCE_REMOVE

    def _preview_anchor_rect(self, note: dict) -> Gdk.Rectangle | None:
        """Icon anchor rectangle in canvas coordinates for the preview popover."""
        canvas = self.win.canvas
        layout = canvas.page_layout
        if not (0 <= note["page"] < len(layout)):
            return None
        _y_offset, dw, dh, crop_rect = layout[note["page"]]
        scale = layout_scale(canvas.zoom, canvas.dpi_scale_factor)
        scroll_x = canvas.hadjustment.get_value() if canvas.hadjustment else 0.0
        scroll_y = canvas.vadjustment.get_value() if canvas.vadjustment else 0.0
        viewport_w = (
            canvas.hadjustment.get_page_size()
            if canvas.hadjustment and canvas.hadjustment.get_page_size() > 0
            else float(canvas.get_width())
        )
        box_w = max(viewport_w, max((d for _, d, _, _ in layout), default=0.0))
        page_x0 = (box_w - dw) / 2.0
        crop_off_x = crop_rect.x0 if crop_rect is not None else 0.0
        crop_off_y = crop_rect.y0 if crop_rect is not None else 0.0
        # Icon centered on the note point; top-left uses a -13 offset with a
        # 34x34 size.
        rect = Gdk.Rectangle()
        rect.x = round(page_x0 + scale * (note["x"] - crop_off_x) - scroll_x - 13.0)
        rect.y = round(_y_offset + scale * (note["y"] - crop_off_y) - scroll_y - 13.0)
        rect.width = 34
        rect.height = 34
        return rect

    def _on_preview_hide(self):
        self._hover_hide_id = None
        if self._in_preview:
            return GLib.SOURCE_REMOVE
        self._preview_popdown()
        return GLib.SOURCE_REMOVE

    def _on_preview_enter(self, controller, x, y):
        self._in_preview = True
        if self._hover_hide_id is not None:
            GLib.source_remove(self._hover_hide_id)
            self._hover_hide_id = None

    def _on_preview_leave(self, controller):
        self._in_preview = False
        self._schedule_preview_hide()

    def hide_preview(self):
        if self._hover_show_id is not None:
            GLib.source_remove(self._hover_show_id)
            self._hover_show_id = None
        if self._hover_hide_id is not None:
            GLib.source_remove(self._hover_hide_id)
            self._hover_hide_id = None
        self._in_preview = False
        self._preview_popdown()
        self._pending_preview_md = None

    def _preview_popdown(self):
        if self._preview_popover is not None:
            self._preview_popover.popdown()


class NoteEditorWindow(Adw.Window):
    """Standalone note editor: Source/Rendered tabs, autosave, red Delete button."""

    def __init__(self, win, note: dict):
        super().__init__(title=f"Note {note['id']} on page {note['page'] + 1}")
        self.win = win
        self.note_id = note["id"]
        self.set_default_size(560, 420)

        self._render_id: int | None = None
        self._save_id: int | None = None

        self._webview = WebKit.WebView.new()
        self._web_loaded = False
        self._webview.connect("load-changed", self._on_webview_load_changed)
        self._webview.load_html(HTML_PREVIEW_TEMPLATE, _assets_base_uri())

        self._buffer = Gtk.TextBuffer.new(None)
        self._buffer.set_text(note["markdown"])
        self._buffer.connect("changed", self._on_buffer_changed)

        self._view_stack = Adw.ViewStack()
        source_page = self._view_stack.add_titled(
            self._build_source_page(), "source", "Source"
        )
        source_page.set_icon_name("document-edit-symbolic")
        rendered_page = self._view_stack.add_titled(
            self._build_rendered_page(), "rendered", "Rendered"
        )
        rendered_page.set_icon_name("view-reveal-symbolic")

        switcher = Adw.ViewSwitcher()
        switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
        switcher.set_stack(self._view_stack)

        header = Adw.HeaderBar()
        header.set_title_widget(switcher)
        delete_btn = Gtk.Button(label="Delete", icon_name="user-trash-symbolic")
        delete_btn.add_css_class("destructive-action")
        delete_btn.connect("clicked", lambda b: win.notes_layer.delete_note(note))
        header.pack_end(delete_btn)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(header)
        content.append(self._view_stack)
        self.set_content(content)

        self._view_stack.connect(
            "notify::visible-child", self._on_visible_child_changed
        )
        self.connect("close-request", self._on_close_request)

    def _build_source_page(self) -> Gtk.Widget:
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        self._text_view = Gtk.TextView.new_with_buffer(self._buffer)
        self._text_view.set_monospace(True)
        self._text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._text_view.set_left_margin(16)
        self._text_view.set_right_margin(16)
        self._text_view.set_top_margin(16)
        self._text_view.set_bottom_margin(16)
        scrolled.set_child(self._text_view)
        return scrolled

    def _build_rendered_page(self) -> Gtk.Widget:
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        self._webview.set_hexpand(True)
        self._webview.set_vexpand(True)
        scrolled.set_child(self._webview)
        return scrolled

    def _current_text(self) -> str:
        start = self._buffer.get_start_iter()
        end = self._buffer.get_end_iter()
        return self._buffer.get_text(start, end, False)

    def _on_buffer_changed(self, _buffer):
        if self._render_id is not None:
            GLib.source_remove(self._render_id)
        if self._save_id is not None:
            GLib.source_remove(self._save_id)
        self._render_id = GLib.timeout_add(EDITOR_RENDER_MS, self._push_render)
        self._save_id = GLib.timeout_add(EDITOR_SAVE_MS, self._on_save_timer)

    def _push_render(self):
        self._render_id = None
        if self._web_loaded:
            js = "updateContent(" + json.dumps(self._current_text()) + ");"
            self._webview.evaluate_javascript(js, -1, None, None, None, None, None)
        return GLib.SOURCE_REMOVE

    def _on_save_timer(self):
        self._save_id = None
        self.win.notes_layer.save_content(self.note_id, self._current_text())
        return GLib.SOURCE_REMOVE

    def _on_webview_load_changed(self, web_view, load_event):
        if load_event == WebKit.LoadEvent.FINISHED:
            self._web_loaded = True
            if self._view_stack.get_visible_child_name() == "rendered":
                self._push_render()

    def _on_visible_child_changed(self, stack, pspec):
        if stack.get_visible_child_name() == "rendered" and self._web_loaded:
            self._push_render()

    def _on_close_request(self, window):
        # Flush any pending buffer content before the window goes away.
        self.win.notes_layer.save_content(self.note_id, self._current_text())
        self.win.notes_layer._editors.pop(self.note_id, None)
        if self._save_id is not None:
            GLib.source_remove(self._save_id)
            self._save_id = None
        if self._render_id is not None:
            GLib.source_remove(self._render_id)
            self._render_id = None
        return False  # allow close
