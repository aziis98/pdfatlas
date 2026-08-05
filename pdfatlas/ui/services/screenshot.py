import os
from PIL import Image, ImageDraw, ImageFilter

import gi
gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Graphene, Gtk


class ScreenshotService:
    """
    Programmatic window screenshot capture, GTK snapshot rendering,
    PIL image compositing, and GNOME drop-shadow generation.
    """

    @staticmethod
    def capture_window(
        window: Adw.ApplicationWindow,
        screenshot_path: str,
        minimap_dialog: Gtk.Widget | None = None,
    ) -> bool:
        print(f"[ScreenshotService] Taking screenshot to: {screenshot_path}", flush=True)
        window.queue_allocate()
        renderer = window.get_renderer()
        if not renderer:
            print("[ScreenshotService] Window has no active renderer yet", flush=True)
            return False

        popover_widget = getattr(window, "annotations_popover", None)
        is_popover = popover_widget is not None and popover_widget.get_visible()
        is_minimap = minimap_dialog is not None and minimap_dialog.get_visible()

        if is_minimap and minimap_dialog and screenshot_path:
            base_widget = window.get_content()
            if not base_widget:
                return False
            bw = base_widget.get_width()
            bh = base_widget.get_height()
            b_rect = Graphene.Rect.alloc()
            b_rect.init(0.0, 0.0, float(bw), float(bh))
            b_snap = Gtk.Snapshot.new()
            bg_color = Gdk.RGBA()
            bg_color.parse("#ffffff")
            b_snap.append_color(bg_color, b_rect)
            Gtk.WidgetPaintable.new(base_widget).snapshot(b_snap, float(bw), float(bh))
            b_texture = renderer.render_texture(b_snap.to_node(), b_rect)

            modal_widget = minimap_dialog
            mw = modal_widget.get_width()
            mh = modal_widget.get_height()
            m_rect = Graphene.Rect.alloc()
            m_rect.init(0.0, 0.0, float(mw), float(mh))
            m_snap = Gtk.Snapshot.new()
            m_snap.append_color(bg_color, m_rect)
            Gtk.WidgetPaintable.new(modal_widget).snapshot(m_snap, float(mw), float(mh))
            m_texture = renderer.render_texture(m_snap.to_node(), m_rect)

            base_path = screenshot_path + ".base.png"
            modal_path = screenshot_path + ".modal.png"
            if b_texture and m_texture:
                b_texture.save_to_png(base_path)
                m_texture.save_to_png(modal_path)
                ScreenshotService.composite_minimap(base_path, modal_path, screenshot_path)
        elif is_popover and popover_widget and screenshot_path:
            base_widget = window.get_content()
            if not base_widget:
                return False
            bw = base_widget.get_width()
            bh = base_widget.get_height()
            b_rect = Graphene.Rect.alloc()
            b_rect.init(0.0, 0.0, float(bw), float(bh))
            b_snap = Gtk.Snapshot.new()
            bg_color = Gdk.RGBA()
            bg_color.parse("#ffffff")
            b_snap.append_color(bg_color, b_rect)
            Gtk.WidgetPaintable.new(base_widget).snapshot(b_snap, float(bw), float(bh))
            b_texture = renderer.render_texture(b_snap.to_node(), b_rect)

            target_w = popover_widget.get_child() or popover_widget
            pw = target_w.get_width()
            ph = target_w.get_height()
            p_rect = Graphene.Rect.alloc()
            p_rect.init(0.0, 0.0, float(pw), float(ph))
            p_snap = Gtk.Snapshot.new()
            p_snap.append_color(bg_color, p_rect)
            Gtk.WidgetPaintable.new(target_w).snapshot(p_snap, float(pw), float(ph))
            p_texture = renderer.render_texture(p_snap.to_node(), p_rect)

            base_path = screenshot_path + ".base.png"
            pop_path = screenshot_path + ".popover.png"
            if b_texture and p_texture:
                b_texture.save_to_png(base_path)
                p_texture.save_to_png(pop_path)
                pop_x = max(10, bw - pw - 210)
                pop_y = 48
                btn_widget = getattr(window, "annotations_btn", None)
                if btn_widget and base_widget:
                    success, pt = btn_widget.compute_point(base_widget, Graphene.Point.zero())
                    if success:
                        btn_x = float(pt.x)
                        btn_w = float(btn_widget.get_width())
                        pop_x = int(btn_x + (btn_w / 2.0) - (float(pw) / 2.0))
                        pop_y = int(float(pt.y) + float(btn_widget.get_height()) + 6.0)
                ScreenshotService.composite_popover(base_path, pop_path, screenshot_path, pop_x, pop_y)
        else:
            content_widget = window.get_content()
            if not content_widget or not screenshot_path:
                print("[ScreenshotService] Window has no content widget to snapshot", flush=True)
                return False

            w = content_widget.get_width()
            h = content_widget.get_height()
            rect = Graphene.Rect.alloc()
            rect.init(0.0, 0.0, float(w), float(h))
            snapshot = Gtk.Snapshot.new()
            bg_color = Gdk.RGBA()
            bg_color.parse("#ffffff")
            snapshot.append_color(bg_color, rect)
            Gtk.WidgetPaintable.new(content_widget).snapshot(snapshot, float(w), float(h))

            texture = renderer.render_texture(snapshot.to_node(), rect)
            if texture and screenshot_path:
                texture.save_to_png(screenshot_path)
                print("[ScreenshotService] Programmatic screenshot saved successfully.", flush=True)
                ScreenshotService.apply_gnome_shadow(screenshot_path)
            else:
                print("[ScreenshotService] Failed to render snapshot node to texture.", flush=True)
        return True

    @staticmethod
    def composite_minimap(base_path: str, modal_path: str, out_path: str) -> None:
        base = Image.open(base_path).convert("RGBA")
        modal = Image.open(modal_path).convert("RGBA")

        bw, bh = base.size
        mw, mh = modal.size

        dim = Image.new("RGBA", (bw, bh), (0, 0, 0, 45))
        base_dimmed = Image.alpha_composite(base, dim)

        modal_radius = 12
        modal_mask = Image.new("L", (mw, mh), 0)
        draw_m = ImageDraw.Draw(modal_mask)
        draw_m.rounded_rectangle((0, 0, mw - 1, mh - 1), radius=modal_radius, fill=255)

        rounded_modal = Image.new("RGBA", (mw, mh), (0, 0, 0, 0))
        rounded_modal.paste(modal, (0, 0), mask=modal_mask)

        draw_b = ImageDraw.Draw(rounded_modal)
        draw_b.rounded_rectangle(
            (0, 0, mw - 1, mh - 1), radius=modal_radius, outline=(180, 180, 180, 120), width=1
        )

        shadow_blur = 16
        shadow_opacity = 0.25
        offset_y = 6

        shadow_mask = Image.new("L", (bw, bh), 0)
        s_draw = ImageDraw.Draw(shadow_mask)

        x0 = (bw - mw) // 2
        y0 = (bh - mh) // 2

        shadow_box = (x0, y0 + offset_y, x0 + mw - 1, y0 + offset_y + mh - 1)
        s_draw.rounded_rectangle(shadow_box, radius=modal_radius, fill=int(255 * shadow_opacity))
        shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(shadow_blur))

        dark_fill = Image.new("RGBA", (bw, bh), (10, 10, 16, 255))
        base_dimmed.paste(dark_fill, (0, 0), mask=shadow_mask)
        base_dimmed.paste(rounded_modal, (x0, y0), mask=rounded_modal)

        base_dimmed.save(out_path, format="PNG")

        if os.path.exists(base_path):
            os.remove(base_path)
        if os.path.exists(modal_path):
            os.remove(modal_path)

        ScreenshotService.apply_gnome_shadow(out_path)
        print(f"[ScreenshotService] Composited minimap window to {out_path}", flush=True)

    @staticmethod
    def composite_popover(base_path: str, popover_path: str, out_path: str, x: int, y: int) -> None:
        base = Image.open(base_path).convert("RGBA")
        pop = Image.open(popover_path).convert("RGBA")

        bw, bh = base.size
        pw, ph = pop.size

        padding = 12
        pop_w = pw + padding * 2
        pop_h = ph + padding * 2

        # Create padded popover card
        pop_card = Image.new("RGBA", (pop_w, pop_h), (255, 255, 255, 255))
        pop_card.paste(pop, (padding, padding), mask=pop)

        pop_radius = 12
        pop_mask = Image.new("L", (pop_w, pop_h), 0)
        draw_p = ImageDraw.Draw(pop_mask)
        draw_p.rounded_rectangle((0, 0, pop_w - 1, pop_h - 1), radius=pop_radius, fill=255)

        rounded_pop = Image.new("RGBA", (pop_w, pop_h), (0, 0, 0, 0))
        rounded_pop.paste(pop_card, (0, 0), mask=pop_mask)

        draw_b = ImageDraw.Draw(rounded_pop)
        draw_b.rounded_rectangle(
            (0, 0, pop_w - 1, pop_h - 1), radius=pop_radius, outline=(190, 190, 190, 160), width=1
        )

        shadow_blur = 14
        shadow_opacity = 0.22
        offset_y = 4

        shadow_mask = Image.new("L", (bw, bh), 0)
        s_draw = ImageDraw.Draw(shadow_mask)

        shadow_box = (x, y + offset_y, x + pop_w - 1, y + offset_y + pop_h - 1)
        s_draw.rounded_rectangle(shadow_box, radius=pop_radius, fill=int(255 * shadow_opacity))
        shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(shadow_blur))

        dark_fill = Image.new("RGBA", (bw, bh), (10, 10, 16, 255))
        base.paste(dark_fill, (0, 0), mask=shadow_mask)
        base.paste(rounded_pop, (x, y), mask=rounded_pop)

        base.save(out_path, format="PNG")

        if os.path.exists(base_path):
            os.remove(base_path)
        if os.path.exists(popover_path):
            os.remove(popover_path)

        ScreenshotService.apply_gnome_shadow(out_path)
        print(f"[ScreenshotService] Composited popover overlay window to {out_path}", flush=True)

    @staticmethod
    def apply_gnome_shadow(file_path: str) -> None:
        img = Image.open(file_path).convert("RGBA")
        w, h = img.size

        corner_radius = 12
        shadow_margin = 60
        shadow_blur = 18
        shadow_offset_y = 6
        shadow_opacity = 0.20
        border_color = (180, 180, 180, 100)

        mask = Image.new("L", (w, h), 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=corner_radius, fill=255)

        rounded_img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        rounded_img.paste(img, (0, 0), mask=mask)

        draw_border = ImageDraw.Draw(rounded_img)
        draw_border.rounded_rectangle(
            (0, 0, w - 1, h - 1), radius=corner_radius, outline=border_color, width=1
        )

        canvas_w = w + shadow_margin * 2
        canvas_h = h + shadow_margin * 2 + shadow_offset_y

        shadow_mask = Image.new("L", (canvas_w, canvas_h), 0)
        shadow_draw = ImageDraw.Draw(shadow_mask)
        shadow_box = (
            shadow_margin,
            shadow_margin + shadow_offset_y,
            shadow_margin + w - 1,
            shadow_margin + shadow_offset_y + h - 1,
        )
        shadow_draw.rounded_rectangle(shadow_box, radius=corner_radius, fill=int(255 * shadow_opacity))
        shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(shadow_blur))

        canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        dark_fill = Image.new("RGBA", (canvas_w, canvas_h), (12, 16, 24, 255))
        canvas.paste(dark_fill, (0, 0), mask=shadow_mask)
        canvas.paste(rounded_img, (shadow_margin, shadow_margin), mask=rounded_img)

        canvas.save(file_path, format="PNG")
        print(f"[ScreenshotService] Applied GNOME drop-shadow to {file_path}", flush=True)
