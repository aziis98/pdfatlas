from typing import TYPE_CHECKING
import gi

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk

if TYPE_CHECKING:
    from ..ui.window import MainWindow


class ClipboardController:
    """
    Controller managing system clipboard operations for plain PDF text and source LaTeX snippets.
    """

    def __init__(self, main_window: "MainWindow"):
        self.win = main_window

    def copy_pdf_text(self) -> None:
        """Copy selected PDF plain text to the system clipboard [Ctrl+Shift+C]."""
        sel = self.win.canvas.text_selection
        if sel is None or not sel.has_selection():
            return
        text = sel.get_selected_text()
        if not text:
            return
        display = Gdk.Display.get_default()
        if display is not None:
            clipboard = display.get_clipboard()
            clipboard.set(text)

    def copy_tex(self) -> None:
        """Copy selected text as LaTeX source TeX if available, otherwise plain PDF text [Ctrl+C]."""
        sel = self.win.canvas.text_selection
        if sel is None or not sel.has_selection():
            return

        text = ""
        if self.win.arxiv_mapper and self.win.arxiv_mapper.is_ready:
            if sel.anchor_page is not None and sel.focus_page is not None:
                p_start = min(sel.anchor_page, sel.focus_page)
                p_end = max(sel.anchor_page, sel.focus_page)

                latex_parts = []
                for pi in range(p_start, p_end + 1):
                    rng = sel._selection_range(pi)
                    if rng:
                        s_char, e_char = rng
                        rects = sel.get_selection_rects(pi)
                        tex_snippet = self.win.arxiv_mapper.get_latex_for_pdf_range(pi, s_char, e_char, char_rects=rects)
                        if tex_snippet:
                            latex_parts.append(tex_snippet)

                if latex_parts:
                    text = "\n".join(latex_parts)

        if not text:
            text = sel.get_selected_text()

        if not text:
            return

        display = Gdk.Display.get_default()
        if display is not None:
            clipboard = display.get_clipboard()
            clipboard.set(text)
