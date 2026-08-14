#!/usr/bin/env python3
# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "pygobject>=3.56.3",
#     "pygobject-stubs>=2.17.0",
# ]
# ///
"""
markdown_renderer.py — Markdown Editor & Renderer Prototype Component for PDF Atlas.

Features:
  - Disables WebKit compositing mode (WEBKIT_DISABLE_COMPOSITING_MODE=1) at launch.
  - Centered "Source", "Rendered", and "Side by Side" tabs in the Adw.HeaderBar.
  - Open File (Ctrl+O) and Save File (Ctrl+S) buttons in top-left HeaderBar.
  - Monospace code editor in the "Source" tab.
  - WebKit.WebView preview in the "Rendered" tab with Marked.js & KaTeX math rendering.
  - Live preview updates at 32ms with GTK dark/light theme support.
  - CLI argparse with optional file positional arg and --lorem-ipsum showcase flag.

Usage:
  uv run scripts/markdown_renderer.py
  uv run scripts/markdown_renderer.py --lorem-ipsum
  uv run scripts/markdown_renderer.py note.md
"""

import argparse
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
- **Libadwaita Header Tabs**: Easily switch between *Source*, *Rendered*, and *Side by Side* views.

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
- [x] Open / Save file dialogs
"""

LOREM_IPSUM_SHOWCASE = """# Lorem Ipsum & Markdown Showcase

> *"Neque porro quisquam est qui dolorem ipsum quia dolor sit amet, consectetur, adipisci velit..."*

Welcome to the **Lorem Ipsum Showcase** demonstrating live Markdown formatting and LaTeX mathematical equations in PDF Atlas.

## 1. Mathematical Physics & Calculus
Inline equation: $f(x) = \\int_{-\\infty}^{x} \\frac{1}{\\sqrt{2\\pi}\\sigma} e^{-\\frac{(t-\\mu)^2}{2\\sigma^2}} dt$

Display equation (Schrödinger Equation):
$$i\\hbar\\frac{\\partial}{\\partial t}\\Psi(\\mathbf{r},t) = \\left[ -\\frac{\\hbar^2}{2m}\\nabla^2 + V(\\mathbf{r},t) \\right]\\Psi(\\mathbf{r},t)$$

Maxwell's Equations in Differential Form:
$$\\nabla \\cdot \\mathbf{E} = \\frac{\\rho}{\\varepsilon_0}, \\quad \\nabla \\cdot \\mathbf{B} = 0$$

## 2. Formatting & Lists
- **Bold Typography**, *Italicized Emphasis*, `Monospace Code Identifiers`
- [x] High-performance WebKit GTK4 renderer
- [x] Live KaTeX LaTeX equation parsing
- [x] Split side-by-side synchronized editing
- [x] Monitor-aware window scaling

## 3. Code Implementation
```python
def fibonacci(n: int) -> list[int]:
    \"\"\"Generate Fibonacci sequence up to n terms.\"\"\"
    a, b = 0, 1
    result: list[int] = []
    for _ in range(n):
        result.append(a)
        a, b = b, a + b
    return result

# Print first 10 terms
print(fibonacci(10))
```

## 4. Benchmark Performance Matrix

| Component | Library | Update Latency | Feature Support |
| :--- | :--- | :--- | :--- |
| **Parser** | Marked.js | < 5 ms | GFM, Tables, Tasks |
| **Math Engine** | KaTeX | < 15 ms | TeX Math, Symbols |
| **Split Container** | Gtk.Paned | 60 FPS | Double-pane 50/50 |
"""

HTML_PREVIEW_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Markdown Preview</title>

  <!-- markdown-it for Markdown parsing -->
  <script src="https://cdn.jsdelivr.net/npm/markdown-it@14.3.0/dist/markdown-it.min.js"></script>
  <!-- markdown-it-texmath: tokenizes $/$$/\\(…\\)/\\[…\\]/begin{} math BEFORE the
       escape rule, so LaTeX escapes like \\{ \\} \\, reach KaTeX untouched. -->
  <script src="https://cdn.jsdelivr.net/npm/markdown-it-texmath@1.0.0/texmath.js"></script>

  <!-- KaTeX for TeX Math rendering -->
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.css">
  <script src="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.js"></script>

  <style>
    :root {
      color-scheme: light dark;
      --bg-color: #ffffff;
      --text-color: #24292e;
      --code-bg: #f6f8fa;
      --border-color: #e1e4e8;
      --link-color: #0366d6;
    }

    @media (prefers-color-scheme: dark) {
      :root {
        --bg-color: #1e1e1e;
        --text-color: #d4d4d4;
        --code-bg: #2d2d2d;
        --border-color: #3c3c3c;
        --link-color: #58a6ff;
      }
    }

    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      font-size: 15px;
      line-height: 1.6;
      background-color: var(--bg-color);
      color: var(--text-color);
      padding: 24px 32px;
      margin: 0;
      word-wrap: break-word;
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


class MarkdownRendererComponent(Gtk.Box):
    """Reusable GTK4 / Libadwaita Markdown component with centered tabs & file I/O."""

    __gtype_name__ = "MarkdownRendererComponent"

    def __init__(self, initial_text: str = "", initial_filepath: str | None = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._update_timer_id: int = 0
        self._web_single_loaded: bool = False
        self._web_split_loaded: bool = False
        self._is_doubled: bool = False
        self._saved_single_width: int = 0
        self._current_filepath: str | None = initial_filepath

        # HeaderBar with centered ViewSwitcher
        self.header_bar = Adw.HeaderBar()

        # Top-Left Open and Save File Buttons
        self.open_btn = Gtk.Button(icon_name="document-open-symbolic")
        self.open_btn.set_tooltip_text("Open File (Ctrl+O)")
        self.open_btn.connect("clicked", self._on_open_clicked)
        self.header_bar.pack_start(self.open_btn)

        self.save_btn = Gtk.Button(icon_name="document-save-symbolic")
        self.save_btn.set_tooltip_text("Save File (Ctrl+S)")
        self.save_btn.connect("clicked", self._on_save_clicked)
        self.header_bar.pack_start(self.save_btn)

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

        # Setup Ctrl+O and Ctrl+S Keyboard Shortcuts
        shortcut_controller = Gtk.ShortcutController()
        shortcut_controller.add_shortcut(
            Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string("<Control>o"),
                Gtk.CallbackAction.new(self._shortcut_open_cb),
            )
        )
        shortcut_controller.add_shortcut(
            Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string("<Control>s"),
                Gtk.CallbackAction.new(self._shortcut_save_cb),
            )
        )
        self.add_controller(shortcut_controller)

    def _shortcut_open_cb(self, _widget: Gtk.Widget, _args: GLib.Variant | None) -> bool:
        self._on_open_clicked(self.open_btn)
        return True

    def _shortcut_save_cb(self, _widget: Gtk.Widget, _args: GLib.Variant | None) -> bool:
        self._on_save_clicked(self.save_btn)
        return True

    def _on_open_clicked(self, _btn: Gtk.Button) -> None:
        """Open a file dialog to choose and load a Markdown file."""
        dialog = Gtk.FileDialog(title="Open Markdown File")
        filters = Gio.ListStore.new(Gtk.FileFilter)

        md_filter = Gtk.FileFilter()
        md_filter.set_name("Markdown Files (*.md, *.markdown)")
        md_filter.add_pattern("*.md")
        md_filter.add_pattern("*.markdown")
        filters.append(md_filter)

        all_filter = Gtk.FileFilter()
        all_filter.set_name("All Files (*)")
        all_filter.add_pattern("*")
        filters.append(all_filter)

        dialog.set_filters(filters)
        root_win = self.get_root()
        win = root_win if isinstance(root_win, Gtk.Window) else None
        dialog.open(win, None, self._on_open_dialog_finish)

    def _on_open_dialog_finish(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        """Process chosen file from open dialog."""
        try:
            gfile = dialog.open_finish(result)
            if gfile:
                path = gfile.get_path()
                if path:
                    with open(path, "r", encoding="utf-8") as f:
                        content = f.read()
                    self._current_filepath = path
                    self.buffer.set_text(content)
                    print(f"Loaded note from {path}")
        except Exception as err:
            print(f"Open dialog cancelled or failed: {err}")

    def _on_save_clicked(self, _btn: Gtk.Button) -> None:
        """Save file directly or launch save dialog if untitled."""
        if self._current_filepath:
            self.save_to_path(self._current_filepath)
        else:
            self._save_as_dialog()

    def _save_as_dialog(self) -> None:
        """Launch Save As file dialog."""
        dialog = Gtk.FileDialog(title="Save Markdown File")
        dialog.set_initial_name("note.md")
        root_win = self.get_root()
        win = root_win if isinstance(root_win, Gtk.Window) else None
        dialog.save(win, None, self._on_save_dialog_finish)

    def _on_save_dialog_finish(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        """Process chosen path from save dialog."""
        try:
            gfile = dialog.save_finish(result)
            if gfile:
                path = gfile.get_path()
                if path:
                    self.save_to_path(path)
        except Exception as err:
            print(f"Save dialog cancelled or failed: {err}")

    def save_to_path(self, path: str) -> None:
        """Write current text buffer to specified file path."""
        text = self.get_text()
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        self._current_filepath = path
        print(f"Saved note to {path}")

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

    def __init__(
        self,
        app: Adw.Application,
        filepath: str | None = None,
        initial_text: str | None = None,
    ):
        super().__init__(application=app, title="Markdown Note Editor")
        self.set_default_size(700, 500)

        content_text = initial_text
        if content_text is None and filepath and os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content_text = f.read()
            except Exception as err:
                print(f"Error reading file {filepath}: {err}", file=sys.stderr)

        self.component = MarkdownRendererComponent(
            initial_text=content_text or "",
            initial_filepath=filepath,
        )
        self.set_content(self.component)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Markdown Editor & Live Renderer Prototype Component for PDF Atlas."
    )
    parser.add_argument(
        "file",
        nargs="?",
        default=None,
        help="Optional path to a Markdown file to open.",
    )
    parser.add_argument(
        "--lorem-ipsum",
        action="store_true",
        help="Populate editor with rich Lorem Ipsum showcase text (math, tables, lists).",
    )
    args = parser.parse_args(sys.argv[1:])

    app = Adw.Application(
        application_id="org.pdfatlas.MarkdownRendererPrototype",
        flags=Gio.ApplicationFlags.HANDLES_OPEN,
    )

    def on_activate(application: Adw.Application) -> None:
        initial_text = None
        filepath = None
        if args.lorem_ipsum:
            initial_text = LOREM_IPSUM_SHOWCASE
        elif args.file and os.path.exists(args.file):
            filepath = args.file

        win = MarkdownRendererWindow(application, filepath=filepath, initial_text=initial_text)
        win.present()

    app.connect("activate", on_activate)
    app.run([])


if __name__ == "__main__":
    main()
