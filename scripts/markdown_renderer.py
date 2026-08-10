#!/usr/bin/env python3
"""
markdown_renderer.py — Markdown Editor & Renderer Prototype Component for PDF Atlas.

Features:
  - Disables WebKit compositing mode (WEBKIT_DISABLE_COMPOSITING_MODE=1) at launch.
  - Centered "Source" and "Rendered" tabs in the Adw.HeaderBar.
  - Monospace code editor in the "Source" tab.
  - WebKit.WebView preview in the "Rendered" tab with Marked.js & KaTeX math rendering.
  - Live preview updates with GTK dark/light theme support.

Usage:
  uv run scripts/markdown_renderer.py
  uv run scripts/markdown_renderer.py [sample.md]
"""

import os
import sys

# Must be set before initializing GTK and WebKit
os.environ["WEBKIT_DISABLE_COMPOSITING_MODE"] = "1"

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")
from gi.repository import Adw, Gio, GLib, GObject, Gtk, WebKit

DEFAULT_MARKDOWN_SAMPLE = """# Markdown Note & Preview

Welcome to the **PDF Atlas** Markdown note component prototype.

## Features
- **Monospace Editor**: Write raw Markdown source with word wrapping.
- **Live Preview**: Render Markdown and KaTeX math equations in real-time.
- **Libadwaita Header Tabs**: Easily switch between *Source* and *Rendered* views.

## Math Equations
Inline math: $E = mc^2$ or $\\nabla \\cdot \\mathbf{B} = 0$.

Display math:
$$\\int_{-\\infty}^{\\infty} e^{-x^2} dx = \\sqrt{\\pi}$$

## Code Example
```python
def calculate_area(radius: float) -> float:
    import math
    return math.pi * radius ** 2
```

## Checklist
- [x] WebKit GTK4 integration
- [x] Centered Adw.HeaderBar switcher
- [x] Dark / Light theme support
"""

HTML_PREVIEW_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Markdown Preview</title>

  <!-- Marked.js for Markdown parsing -->
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>

  <!-- KaTeX for LaTeX Math rendering -->
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.css">
  <script src="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/contrib/auto-render.min.js"></script>

  <style>
    :root {
      color-scheme: light dark;
      --bg-color: #ffffff;
      --text-color: #24292f;
      --code-bg: #f6f8fa;
      --border-color: #d0d7de;
      --link-color: #0969da;
      --blockquote-color: #57606a;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg-color: #1e1e1e;
        --text-color: #e6edf3;
        --code-bg: #2d333b;
        --border-color: #444c56;
        --link-color: #2f81f7;
        --blockquote-color: #768390;
      }
    }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      margin: 0;
      padding: 24px 28px;
      background-color: var(--bg-color);
      color: var(--text-color);
      line-height: 1.6;
      font-size: 15px;
      word-wrap: break-word;
    }
    h1, h2, h3, h4, h5, h6 {
      margin-top: 20px;
      margin-bottom: 12px;
      font-weight: 600;
      line-height: 1.25;
    }
    h1 { font-size: 1.8em; border-bottom: 1px solid var(--border-color); padding-bottom: 0.3em; }
    h2 { font-size: 1.4em; border-bottom: 1px solid var(--border-color); padding-bottom: 0.3em; }
    h3 { font-size: 1.2em; }
    p { margin-top: 0; margin-bottom: 16px; }
    code {
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
      font-size: 85%;
      background-color: var(--code-bg);
      padding: 0.2em 0.4em;
      border-radius: 6px;
    }
    pre {
      background-color: var(--code-bg);
      padding: 16px;
      overflow: auto;
      border-radius: 8px;
      line-height: 1.45;
    }
    pre code {
      background-color: transparent;
      padding: 0;
      font-size: 13px;
    }
    blockquote {
      margin: 0 0 16px 0;
      padding: 0 1em;
      color: var(--blockquote-color);
      border-left: 0.25em solid var(--border-color);
    }
    ul, ol {
      padding-left: 2em;
      margin-top: 0;
      margin-bottom: 16px;
    }
    table {
      border-collapse: collapse;
      width: 100%;
      margin-bottom: 16px;
    }
    table th, table td {
      padding: 6px 13px;
      border: 1px solid var(--border-color);
    }
    table tr:nth-child(2n) {
      background-color: var(--code-bg);
    }
    a {
      color: var(--link-color);
      text-decoration: none;
    }
    a:hover {
      text-decoration: underline;
    }
    hr {
      height: 0.25em;
      padding: 0;
      margin: 24px 0;
      background-color: var(--border-color);
      border: 0;
    }
    .katex-display {
      margin: 1em 0;
      overflow-x: auto;
      overflow-y: hidden;
    }
  </style>
</head>
<body>
  <div id="content"></div>
  <script>
    function updateContent(markdownText) {
      const target = document.getElementById("content");
      if (typeof marked !== "undefined" && typeof marked.parse === "function") {
        target.innerHTML = marked.parse(markdownText);
      } else {
        target.innerText = markdownText;
      }
      if (typeof renderMathInElement === "function") {
        renderMathInElement(target, {
          delimiters: [
            {left: "$$", right: "$$", display: true},
            {left: "$", right: "$", display: false},
            {left: "\\(", right: "\\)", display: false},
            {left: "\\[", right: "\\]", display: true}
          ],
          throwOnError: false
        });
      }
    }
  </script>
</body>
</html>
"""


class MarkdownRendererComponent(Gtk.Box):
    """Reusable GTK4 / Libadwaita Markdown component with centered tabs."""

    __gtype_name__ = "MarkdownRendererComponent"

    def __init__(self, initial_text: str = ""):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._update_timer_id: int = 0
        self._web_single_loaded: bool = False
        self._web_split_loaded: bool = False
        self._is_doubled: bool = False
        self._saved_single_width: int = 0

        # HeaderBar with centered ViewSwitcher
        self.header_bar = Adw.HeaderBar()

        self.view_switcher = Adw.ViewSwitcher()
        self.view_switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
        self.header_bar.set_title_widget(self.view_switcher)

        # ViewStack for Source, Rendered, and Side by Side pages
        self.view_stack = Adw.ViewStack()
        self.view_switcher.set_stack(self.view_stack)

        # --- Tab 1: Source (Monospace Editor) ---
        self.text_view = Gtk.TextView()
        self.text_view.set_monospace(True)
        self.text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.text_view.set_left_margin(16)
        self.text_view.set_right_margin(16)
        self.text_view.set_top_margin(16)
        self.text_view.set_bottom_margin(16)

        self.buffer = self.text_view.get_buffer()
        self.buffer.set_text(initial_text or DEFAULT_MARKDOWN_SAMPLE)
        self.buffer.connect("changed", self._on_buffer_changed)

        self.source_scroll = Gtk.ScrolledWindow()
        self.source_scroll.set_child(self.text_view)

        self.view_stack.add_titled_with_icon(
            self.source_scroll,
            name="source",
            title="Source",
            icon_name="document-edit-symbolic",
        )

        # --- Tab 2: Rendered (Single WebView) ---
        self.web_view = WebKit.WebView()
        self.web_view.connect("load-changed", self._on_single_load_changed)
        self.web_view.load_html(HTML_PREVIEW_TEMPLATE, "http://localhost")

        self.view_stack.add_titled_with_icon(
            self.web_view,
            name="rendered",
            title="Rendered",
            icon_name="view-reveal-symbolic",
        )

        # --- Tab 3: Side by Side (Split View) ---
        self.split_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.split_paned.set_wide_handle(True)

        self.split_text_view = Gtk.TextView(buffer=self.buffer)
        self.split_text_view.set_monospace(True)
        self.split_text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.split_text_view.set_left_margin(16)
        self.split_text_view.set_right_margin(16)
        self.split_text_view.set_top_margin(16)
        self.split_text_view.set_bottom_margin(16)

        self.split_source_scroll = Gtk.ScrolledWindow()
        self.split_source_scroll.set_child(self.split_text_view)
        self.split_source_scroll.set_hexpand(True)
        self.split_source_scroll.set_vexpand(True)

        self.web_view_split = WebKit.WebView()
        self.web_view_split.connect("load-changed", self._on_split_load_changed)
        self.web_view_split.load_html(HTML_PREVIEW_TEMPLATE, "http://localhost")
        self.web_view_split.set_hexpand(True)
        self.web_view_split.set_vexpand(True)

        self.split_paned.set_start_child(self.split_source_scroll)
        self.split_paned.set_end_child(self.web_view_split)
        self.split_paned.set_resize_start_child(True)
        self.split_paned.set_shrink_start_child(True)
        self.split_paned.set_resize_end_child(True)
        self.split_paned.set_shrink_end_child(True)

        self.view_stack.add_titled_with_icon(
            self.split_paned,
            name="split",
            title="Side by Side",
            icon_name="view-dual-symbolic",
        )

        # Connect stack visible child changes
        self.view_stack.connect("notify::visible-child", self._on_visible_child_changed)

        # Pack layout
        self.append(self.header_bar)
        self.append(self.view_stack)

        # Expand view stack inside box
        self.view_stack.set_vexpand(True)
        self.view_stack.set_hexpand(True)

    def _on_buffer_changed(self, _buffer: Gtk.TextBuffer) -> None:
        """Debounce text buffer updates at 32ms for live rendering."""
        if self._update_timer_id > 0:
            GLib.source_remove(self._update_timer_id)
        self._update_timer_id = GLib.timeout_add(32, self._trigger_render_update)

    def _on_single_load_changed(self, _web_view: WebKit.WebView, load_event: WebKit.LoadEvent) -> None:
        """Trigger render update once Single WebKit page finishes loading."""
        if load_event == WebKit.LoadEvent.FINISHED:
            self._web_single_loaded = True
            self._trigger_render_update()

    def _on_split_load_changed(self, _web_view: WebKit.WebView, load_event: WebKit.LoadEvent) -> None:
        """Trigger render update once Split WebKit page finishes loading."""
        if load_event == WebKit.LoadEvent.FINISHED:
            self._web_split_loaded = True
            self._trigger_render_update()

    def _resize_window(self, win: Gtk.Window, target_w: int, target_h: int) -> None:
        """Resize GTK4 window using set_size_request."""
        win.set_size_request(target_w, target_h)

    def _on_visible_child_changed(self, _stack: Adw.ViewStack, _param: GObject.ParamSpec) -> None:
        """Handle tab switching and window doubling/halving logic."""
        visible_name = self.view_stack.get_visible_child_name()
        if visible_name in ("rendered", "split"):
            self._trigger_render_update()

        root_win = self.get_root()
        if not isinstance(root_win, Gtk.Window):
            return

        if visible_name == "split":
            if not root_win.is_maximized() and not self._is_doubled:
                cur_w = root_win.get_width()
                cur_h = root_win.get_height()
                target_w = cur_w * 2

                # Check if target doubled width fits on the current monitor
                display = root_win.get_display()
                surface = root_win.get_surface()
                mon_w = 99999
                if display and surface:
                    mon = display.get_monitor_at_surface(surface)
                    if mon:
                        mon_w = mon.get_geometry().width

                if target_w <= mon_w:
                    self._saved_single_width = cur_w
                    self._resize_window(root_win, target_w, cur_h)
                    self._is_doubled = True

            def _force_half_split() -> bool:
                total_w = self.split_paned.get_width()
                if total_w > 10:
                    self.split_paned.set_position(total_w // 2)
                    return False
                return True

            GLib.timeout_add(50, _force_half_split)
        else:
            if not root_win.is_maximized() and self._is_doubled:
                cur_h = root_win.get_height()
                restore_w = self._saved_single_width if self._saved_single_width > 0 else max(400, root_win.get_width() // 2)
                self._resize_window(root_win, restore_w, cur_h)
                self._is_doubled = False

    def _trigger_render_update(self) -> bool:
        """Extract text from buffer and run JS updateContent in active WebView(s)."""
        self._update_timer_id = 0
        text = self.get_text()
        escaped_text = (
            text.replace("\\", "\\\\")
            .replace("`", "\\`")
            .replace("$", "\\$")
        )
        js_code = f"if (typeof updateContent === 'function') {{ updateContent(`{escaped_text}`); }}"

        visible_name = self.view_stack.get_visible_child_name()
        if visible_name == "rendered" and self._web_single_loaded:
            self.web_view.evaluate_javascript(js_code, -1, None, None, None, None, None)
        elif visible_name == "split" and self._web_split_loaded:
            self.web_view_split.evaluate_javascript(js_code, -1, None, None, None, None, None)
        elif visible_name == "source":
            if self._web_single_loaded:
                self.web_view.evaluate_javascript(js_code, -1, None, None, None, None, None)
            if self._web_split_loaded:
                self.web_view_split.evaluate_javascript(js_code, -1, None, None, None, None, None)
        return False

    def get_text(self) -> str:
        """Return the current Markdown source text."""
        start = self.buffer.get_start_iter()
        end = self.buffer.get_end_iter()
        return self.buffer.get_text(start, end, True)

    def set_text(self, text: str) -> None:
        """Set the Markdown source text."""
        self.buffer.set_text(text)


class MarkdownRendererWindow(Adw.ApplicationWindow):
    """Standalone window housing the MarkdownRendererComponent."""

    def __init__(self, app: Adw.Application, filepath: str | None = None):
        super().__init__(application=app, title="Markdown Note Editor")
        self.set_default_size(700, 500)

        initial_text = DEFAULT_MARKDOWN_SAMPLE
        if filepath and os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    initial_text = f.read()
            except Exception as err:
                print(f"Error reading file {filepath}: {err}", file=sys.stderr)

        self.component = MarkdownRendererComponent(initial_text=initial_text)
        self.set_content(self.component)


def main() -> None:
    app = Adw.Application(
        application_id="org.pdfatlas.MarkdownRendererPrototype",
        flags=Gio.ApplicationFlags.FLAGS_NONE,
    )

    def on_activate(application: Adw.Application) -> None:
        filepath = sys.argv[1] if len(sys.argv) > 1 else None
        win = MarkdownRendererWindow(application, filepath=filepath)
        win.present()

    app.connect("activate", on_activate)
    app.run(sys.argv)


if __name__ == "__main__":
    main()
