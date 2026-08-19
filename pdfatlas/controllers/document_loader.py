from __future__ import annotations

import os
from pathlib import Path
import threading
from typing import TYPE_CHECKING, Any
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib

from ..core import crop as crop_mod
from ..core import document as doc_mod
from ..core.arxiv_mapper import ArxivDiffMapper, arxiv_id_from_path
from ..core.index import get_db_for_pdf, load_doc_state
from ..core.pdf_source import PdfSource
from ..core.state import CliState

if TYPE_CHECKING:
    from ..ui.window import MainWindow


class DocumentLoader:
    """
    Handles document opening pipeline, background arXiv downloading,
    asynchronous FTS5 text indexing, arXiv LaTeX diff mapping,
    deferred CLI state restoration, and state persistence.
    """

    def __init__(self, win: MainWindow):
        self.win = win
        self._state_save_timer_id: int | None = None

    def open_document(self, source: PdfSource, new_tab: bool = True):
        raw_path = os.path.expanduser(source.uri)
        try:
            filepath = os.path.abspath(raw_path) if os.path.exists(raw_path) else raw_path
        except OSError:
            filepath = raw_path

        aid = arxiv_id_from_path(filepath)

        # If a local file exists, open it directly without requiring internet.
        # If the file does not exist locally (or is an arXiv source whose local path is gone),
        # check the local arXiv cache first, and then attempt to download from arXiv if needed.
        if aid and not os.path.exists(filepath):
            from ..core.arxiv_mapper import ARXIV_CACHE_ROOT, download_arxiv_source
            from ..ui.document_view import PdfDocumentView
            from ..ui.welcome import WelcomeView

            cached_pdf = ARXIV_CACHE_ROOT / aid / "paper.pdf"
            if cached_pdf.exists():
                filepath = str(cached_pdf)
                source = PdfSource(
                    source_type="arxiv",
                    uri=filepath,
                    display_name=source.display_name or f"arXiv:{aid}",
                )
            else:
                # Open or allocate the tab to host the in-tab loading view
                selected = self.win.tab_view.get_selected_page()
                current_child = selected.get_child() if selected else None
                is_empty_tab = isinstance(current_child, WelcomeView)

                display_title = source.display_name or f"arXiv:{aid}"

                if is_empty_tab and selected is not None:
                    pos = self.win.tab_view.get_page_position(selected)
                    doc_view = self.win._create_doc_view()
                    page = self.win.tab_view.insert(doc_view, pos)
                    self.win.tab_view.close_page(selected)
                elif self.win.tab_view.get_n_pages() == 0 or not new_tab:
                    if self.win.tab_view.get_n_pages() == 0:
                        doc_view = self.win._create_doc_view()
                        page = self.win.tab_view.append(doc_view)
                    else:
                        page = selected if selected else self.win.tab_view.get_nth_page(0)
                        child = page.get_child()
                        if isinstance(child, PdfDocumentView):
                            doc_view = child
                        else:
                            pos = self.win.tab_view.get_page_position(page)
                            doc_view = self.win._create_doc_view()
                            page = self.win.tab_view.insert(doc_view, pos)
                            self.win.tab_view.close_page(selected if selected else page)
                else:
                    doc_view = self.win._create_doc_view()
                    page = self.win.tab_view.append(doc_view)

                page.props.title = display_title
                self.win.tab_view.set_selected_page(page)
                self.win.stack.set_visible_child_name("document-view")

                doc_view.show_loading(
                    title=f"Downloading {display_title}",
                    subtitle="Connecting to arXiv...",
                )

                def _download_worker():
                    def _on_progress(fraction: float, message: str):
                        def _update():
                            doc_view.set_loading_progress(fraction, message)
                            return False

                        GLib.idle_add(_update)

                    try:
                        download_arxiv_source(
                            aid,
                            download_pdf=True,
                            download_source=False,
                            progress_callback=_on_progress,
                        )
                        new_source = PdfSource(
                            source_type="arxiv",
                            uri=str(cached_pdf),
                            display_name=source.display_name or f"arXiv:{aid}",
                        )

                        def _on_success():
                            doc_model = doc_mod.DocumentModel(str(cached_pdf))
                            if self.win.render_worker:
                                self.win.render_worker.set_document(str(cached_pdf))
                            meta_title = (doc_model.doc.metadata or {}).get("title")
                            if meta_title and isinstance(meta_title, str):
                                cleaned_meta = meta_title.strip()
                                if cleaned_meta and cleaned_meta.lower() not in (
                                    "paper.pdf",
                                    "untitled",
                                    "none",
                                ):
                                    new_source.display_name = cleaned_meta

                            page.props.title = new_source.display_name
                            doc_view.set_document(doc_model, new_source, self.win.render_worker)
                            self.win.recent_files.add(new_source)
                            self.win._rebuild_open_menu()

                            if self.win.tab_view.get_selected_page() == page:
                                self.win._on_selected_tab_changed(self.win.tab_view, None)
                            return False

                        GLib.idle_add(_on_success)
                    except Exception as e:
                        err_msg = str(e)

                        def _on_fail():
                            if self.win.tab_view.get_page_position(page) >= 0:
                                self.win.tab_view.close_page(page)
                            self.win._show_error_dialog(
                                f"Failed to download arXiv paper '{source.uri}':\n{err_msg}"
                            )
                            return False

                        GLib.idle_add(_on_fail)

                threading.Thread(target=_download_worker, daemon=True).start()
                return

        if not os.path.exists(filepath):
            self.win._show_error_dialog(f"File not found: {filepath}")
            return

        aid = arxiv_id_from_path(filepath)
        existing = self.win.recent_files.get_by_uri(filepath)
        if not existing and aid:
            existing = self.win.recent_files.get_by_arxiv_id(aid)

        if existing and existing.display_name and existing.display_name != "paper.pdf":
            source = PdfSource(
                source_type=existing.source_type,
                uri=filepath,
                display_name=existing.display_name,
            )

        try:
            # Save state for previous document
            if self.win.db_service and self.win.doc_model and self.win.current_source:
                self.win._save_current_doc_state()

            self.win.doc_model = doc_mod.DocumentModel(filepath)
            self.win.crop_analyzer = crop_mod.CropAnalyzer(self.win.doc_model)
            if self.win.render_worker:
                self.win.render_worker.set_document(filepath)

            # Try extracting PDF metadata title for local files if display name is just basename/generic
            if source.display_name in (os.path.basename(filepath), "paper.pdf") or source.display_name.startswith("arXiv:"):
                meta_title = (self.win.doc_model.doc.metadata or {}).get("title")
                if meta_title and isinstance(meta_title, str):
                    cleaned_meta = meta_title.strip()
                    if cleaned_meta and cleaned_meta.lower() not in ("paper.pdf", "untitled", "none"):
                        source.display_name = cleaned_meta

            self.win.current_source = source
            self.win.recent_files.add(source)
            self.win._rebuild_open_menu()

            self.win._active_progress_tasks.clear()
            if self.win.progress_card_box:
                self.win.progress_card_box.set_visible(False)
            if self.win.progress_bar:
                self.win.progress_bar.set_visible(False)

            # Create or reuse TabPage with PdfDocumentView
            from ..ui.document_view import PdfDocumentView
            from ..ui.welcome import WelcomeView

            selected = self.win.tab_view.get_selected_page()
            current_child = selected.get_child() if selected else None
            is_empty_tab = isinstance(current_child, WelcomeView)

            if is_empty_tab and selected is not None:
                pos = self.win.tab_view.get_page_position(selected)
                doc_view = self.win._create_doc_view()
                doc_view.set_document(self.win.doc_model, source, self.win.render_worker)
                page = self.win.tab_view.insert(doc_view, pos)
                self.win.tab_view.close_page(selected)
            elif self.win.tab_view.get_n_pages() == 0 or not new_tab:
                if self.win.tab_view.get_n_pages() == 0:
                    doc_view = self.win._create_doc_view()
                    doc_view.set_document(self.win.doc_model, source, self.win.render_worker)
                    page = self.win.tab_view.append(doc_view)
                else:
                    page = selected if selected else self.win.tab_view.get_nth_page(0)
                    child = page.get_child()
                    if isinstance(child, PdfDocumentView):
                        doc_view = child
                        doc_view.set_document(self.win.doc_model, source, self.win.render_worker)
                    else:
                        pos = self.win.tab_view.get_page_position(page)
                        doc_view = self.win._create_doc_view()
                        doc_view.set_document(self.win.doc_model, source, self.win.render_worker)
                        page = self.win.tab_view.insert(doc_view, pos)
                        self.win.tab_view.close_page(selected if selected else page)
            else:
                doc_view = self.win._create_doc_view()
                doc_view.set_document(self.win.doc_model, source, self.win.render_worker)
                page = self.win.tab_view.append(doc_view)

            page.props.title = source.display_name
            self.win.tab_view.set_selected_page(page)
            self.win.stack.set_visible_child_name("document-view")

            self.win.canvas = doc_view.canvas
            self.win.vadjustment = doc_view.vadjustment
            self.win.hadjustment = doc_view.hadjustment
            self.win.zoom = doc_view.zoom
            self.win.notes_layer = doc_view.notes_layer
            self.win.notes = doc_view.notes
            self.win.highlights = doc_view.highlights

            self.win.set_title(f"PDF Viewer — {source.display_name}")
            self.win.filename_label.set_label(source.display_name)
            if self.win.doc_model:
                self.win.page_total_label.set_label(f"of {self.win.doc_model.page_count}")
            self.win.page_input.set_text("1")
            self.win.page_input.set_sensitive(True)

            if source.is_arxiv and aid and source.display_name in ("paper.pdf", f"arXiv:{aid}"):
                def _bg_fetch(paper_aid: str, paper_uri: str, target_page: Any):
                    from ..ui.arxiv_dialog import _fetch_arxiv_title

                    title = _fetch_arxiv_title(paper_aid)
                    if title:
                        def _update():
                            target_page.props.title = title
                            if self.win.current_source and self.win.current_source.uri == paper_uri:
                                self.win.current_source.display_name = title
                                self.win.current_source.source_type = "arxiv"
                                self.win.recent_files.add(self.win.current_source)
                                self.win._rebuild_open_menu()
                                self.win.set_title(f"PDF Viewer — {title}")
                                self.win.filename_label.set_label(title)

                        GLib.idle_add(_update)

                threading.Thread(target=_bg_fetch, args=(aid, filepath, page), daemon=True).start()

            self.win.arxiv_mapper = None
            if source.is_arxiv:
                aid = arxiv_id_from_path(filepath)
                if aid:
                    self.win._show_progress("arxiv_diff", "Analyzing arXiv TeX sources...", 0.0)
                    arxiv_thread = threading.Thread(
                        target=self._arxiv_diff_worker, args=(aid, filepath), daemon=True
                    )
                    arxiv_thread.start()

            # Start crop analysis
            self.win._start_crop_analysis()

            # Trigger background indexing
            self.win.entry.set_text("")
            self.win.entry.set_placeholder_text("Indexing text index...")
            self.win.entry.set_sensitive(False)
            self.win.stack.set_visible_child_name("document-view")

            self.win._show_progress("indexing", "Indexing document text for search...", 0.0)
            self.win.db_service.open_db(filepath, self._on_indexing_complete)

            # Restore state if passed programmatically
            if self.win.initial_state:
                self.apply_initial_cli_state(self.win.initial_state)

            if self.win.follow_link is not None:
                follow_idx: int = self.win.follow_link
                GLib.timeout_add(400, lambda: self.win._follow_link_by_index(follow_idx))

        except Exception as e:
            import traceback

            traceback.print_exc()
            self.win._show_error_dialog(f"Failed to open PDF document:\n{e}")

    def apply_initial_cli_state(self, json_state: str):
        try:
            state = CliState.from_json(json_state)

            if state.zoom is not None:
                self.win.set_zoom_level(state.zoom)
            if state.crop is not None:
                self.win.settings.enabled = state.crop
            if state.page_gaps is not None:
                self.win.settings.page_gaps = state.page_gaps
            if state.color_scheme is not None:
                self.win.settings.color_scheme = state.color_scheme
            elif state.night_mode is not None:
                self.win.settings.color_scheme = "dark" if state.night_mode else "light"
            elif state.dark_mode is not None:
                self.win.settings.color_scheme = "dark" if state.dark_mode else "light"

            if state.night_mode_invert is not None:
                self.win.settings.night_mode_invert = state.night_mode_invert
            if state.night_mode_hue_rotate is not None:
                self.win.settings.night_mode_hue_rotate = state.night_mode_hue_rotate

            self.win._on_crop_settings_updated()

            # Defer scroll_y, fit_width, and search query application until layout realizes
            def apply_deferred_state():
                if state.fit_width:
                    self.win.zoom_fit_width()
                if state.scroll_y is not None and self.win.vadjustment:
                    self.win.vadjustment.set_value(state.scroll_y)
                if state.query:
                    if self.win.index_conn:
                        self.win.entry.set_text(state.query)
                        self.win.run_search(state.query)
                    else:
                        self.win._deferred_state_query = state.query
                if state.minimap:
                    GLib.timeout_add(500, self.win.toggle_minimap)
                if state.hover_link is not None:
                    hover_idx = state.hover_link
                    GLib.timeout_add(400, lambda: self.win._simulate_link_hover(hover_idx))
                if state.scroll_benchmark is not None:
                    bench_info = state.scroll_benchmark
                    GLib.timeout_add(300, lambda: self.win._run_scroll_benchmark(bench_info))
                if state.selection is not None and self.win.canvas:
                    sel_info = state.selection
                    page_idx = sel_info.page
                    if self.win.canvas.text_selection:
                        pi = self.win.canvas.text_selection.get_page_index(page_idx)
                        start_idx = sel_info.start_idx
                        end_idx = sel_info.end_idx

                        if start_idx is None or end_idx is None:
                            if pi and pi.chars and sel_info.start:
                                s_text = sel_info.start
                                e_text = sel_info.end or s_text
                                for idx, c in enumerate(pi.chars):
                                    if start_idx is None and s_text in c.char:
                                        start_idx = idx
                                    if e_text in c.char:
                                        end_idx = idx

                        if start_idx is not None and end_idx is not None:
                            self.win.canvas.text_selection.start_selection(page_idx, start_idx)
                            self.win.canvas.text_selection.update_focus(page_idx, end_idx)
                            self.win.canvas.text_selection.end_selection()
                            self.win.canvas.queue_draw_overlays("selection-update")
                            self.win._update_selection_toolbar(True)
                return False

            GLib.idle_add(apply_deferred_state)

            if state.highlights is not None:
                sample_hls = state.highlights
                for idx, h in enumerate(sample_hls):
                    if "id" not in h:
                        h["id"] = idx + 1
                    if "rects" not in h:
                        h["rects"] = []
                self.win.highlights = sample_hls
                if self.win.canvas:
                    self.win.canvas.set_highlights(sample_hls)
                self.win._update_annotations_button()

            if state.notes is not None:
                sample_notes = state.notes
                for idx, n in enumerate(sample_notes):
                    if "id" not in n:
                        n["id"] = idx + 1
                    if "markdown" not in n:
                        n["markdown"] = ""
                self.win.notes = sample_notes
                if hasattr(self.win, "notes_layer"):
                    self.win.notes_layer.set_notes(sample_notes)
                self.win._update_annotations_button()

            if state.annotations_popover:
                def open_popover():
                    if self.win.annotations_btn and self.win.annotations_btn.get_visible():
                        if os.environ.get("PDFATLAS_HIDE_CURSOR") == "1" and self.win.annotations_popover:
                            blank = Gdk.Cursor.new_from_name("none", None)
                            self.win.annotations_popover.set_cursor(blank)
                        if self.win.annotations_popover:
                            self.win.annotations_popover.popup()
                    return False

                GLib.timeout_add(400, open_popover)

            if state.open_note_preview is not None:
                def open_note():
                    nid = state.open_note_preview
                    note = next(
                        (n for n in self.win.notes if n.get("id") == nid),
                        self.win.notes[0] if self.win.notes else None,
                    )
                    if note and hasattr(self.win, "notes_layer"):
                        self.win.notes_layer.prepare()
                        self.win.notes_layer._on_preview_show(note)
                        rect = self.win.notes_layer._preview_anchor_rect(note)
                        if rect:
                            exact_x = rect.x + 12
                            exact_y = 46 + rect.y + 12
                            print(f"[PDFAtlas] NOTE_ICON_EXACT_COORDS: {exact_x},{exact_y}", flush=True)
                    return False

                GLib.timeout_add(600, open_note)

            # If page is specified, navigate to it after layout
            if state.page is not None:
                target_page = state.page - 1
                GLib.idle_add(lambda: self.win.jump_to_page(target_page))
        except Exception as e:
            print(f"Failed to restore initial CLI state: {e}")

    def _index_worker(self, filepath: str):
        try:
            conn = get_db_for_pdf(filepath)
            GLib.idle_add(self._on_indexing_complete, conn)
        except Exception as e:
            GLib.idle_add(self.win._show_error_dialog, f"Search indexing failed:\n{e}")

    def _arxiv_diff_worker(self, arxiv_id: str, filepath: str):
        try:
            from ..core.index import get_db_for_pdf, load_arxiv_diff_from_db, save_arxiv_diff_to_db

            conn = get_db_for_pdf(filepath)
            cached_data = load_arxiv_diff_from_db(conn)
            if cached_data is not None:
                mapper = ArxivDiffMapper()
                mapper.from_dict(cached_data)
                GLib.idle_add(self._on_arxiv_diff_complete, mapper)
                return

            def progress_cb(f: float) -> None:
                GLib.idle_add(self._on_arxiv_diff_progress, f)

            mapper = ArxivDiffMapper()
            mapper.process(
                arxiv_id,
                Path(filepath),
                progress_callback=progress_cb,
            )
            save_arxiv_diff_to_db(conn, mapper.to_dict())
            GLib.idle_add(self._on_arxiv_diff_complete, mapper)
        except Exception as e:
            print(f"[DocumentLoader] Arxiv diff calculation failed: {e}", flush=True)
            GLib.idle_add(self._on_arxiv_diff_complete, None)

    def _on_arxiv_diff_progress(self, fraction: float):
        self.win._show_progress("arxiv_diff", "Analyzing arXiv TeX sources...", fraction)

    def _on_arxiv_diff_complete(self, mapper: ArxivDiffMapper | None):
        self.win.arxiv_mapper = mapper
        self.win._hide_progress("arxiv_diff")
        if self.win.selection_toolbar and self.win.selection_toolbar.get_visible():
            self.win._update_selection_toolbar(True)

    def _on_indexing_complete(self, conn: Any):
        self.win._hide_progress("indexing")
        self.win.index_conn = conn
        self.win.entry.set_sensitive(True)
        self.win.entry.set_placeholder_text("Search document...")
        if not self.win.initial_state:
            self.win.db_service.load_highlights(self.win.annotations_controller.on_highlights_loaded)
            self.win.db_service.load_notes(self.win.annotations_controller.on_notes_loaded)

        if self.win._deferred_state_query:
            query = self.win._deferred_state_query
            self.win._deferred_state_query = None
            self.win.entry.set_text(query)
            self.win.run_search(query)

        # Restore saved zoom & scroll_x/scroll_y state from .db if no CLI state was specified
        if not self.win.initial_state and conn is not None:
            saved_state = load_doc_state(conn)
            if "zoom" in saved_state:
                self.win.set_zoom_level(saved_state["zoom"])
            if "scroll_x" in saved_state or "scroll_y" in saved_state:
                scroll_x = saved_state.get("scroll_x", 0.0)
                scroll_y = saved_state.get("scroll_y", 0.0)

                def apply_saved_scroll():
                    if "scroll_x" in saved_state and self.win.hadjustment:
                        self.win.hadjustment.set_value(scroll_x)
                    if "scroll_y" in saved_state and self.win.vadjustment:
                        self.win.vadjustment.set_value(scroll_y)

                GLib.idle_add(apply_saved_scroll)

    def schedule_state_save(self):
        if self._state_save_timer_id is not None:
            GLib.source_remove(self._state_save_timer_id)

        def _on_save_timer():
            self._state_save_timer_id = None
            self.save_current_doc_state()
            return False

        self._state_save_timer_id = GLib.timeout_add(1000, _on_save_timer)

    def save_current_doc_state(self):
        if self.win.db_service:
            zoom = self.win.zoom
            scroll_y = self.win.vadjustment.get_value() if self.win.vadjustment else 0.0
            scroll_x = self.win.hadjustment.get_value() if self.win.hadjustment else 0.0
            self.win.db_service.save_state(zoom, scroll_y, scroll_x)
