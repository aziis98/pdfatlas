from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Graphene, Gsk, Gtk


class MasonryLayoutManager(Gtk.LayoutManager):
    """
    Custom GTK4 LayoutManager arranging children into a multi-column masonry (waterfall) grid.
    Packs each child item into the column with the current minimum accumulated height.
    """

    def __init__(
        self,
        column_spacing: int = 14,
        row_spacing: int = 14,
        min_column_width: int = 470,
        max_columns: int | None = None,
    ):
        super().__init__()
        self.column_spacing = int(column_spacing)
        self.row_spacing = int(row_spacing)
        self.min_column_width = int(min_column_width)
        self.max_columns = int(max_columns) if max_columns is not None else None

    def do_get_request_mode(self, widget: Gtk.Widget) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(
        self, widget: Gtk.Widget, orientation: Gtk.Orientation, for_size: int
    ) -> tuple[int, int, int, int]:
        if orientation == Gtk.Orientation.HORIZONTAL:
            min_w = self.min_column_width
            nat_w = self.min_column_width * 2 + self.column_spacing
            return (min_w, nat_w, -1, -1)

        avail_w = for_size if for_size > 0 else 800
        cols = max(
            1,
            int((avail_w + self.column_spacing) / (self.min_column_width + self.column_spacing)),
        )
        if self.max_columns is not None:
            cols = min(cols, self.max_columns)

        col_w = max(1, int((avail_w - (cols - 1) * self.column_spacing) / cols))
        col_heights = [0] * cols

        child = widget.get_first_child()
        while child is not None:
            if child.should_layout():
                _, nat_h, _, _ = child.measure(Gtk.Orientation.VERTICAL, col_w)
                shortest_col = col_heights.index(min(col_heights))
                col_heights[shortest_col] += nat_h + self.row_spacing
            child = child.get_next_sibling()

        total_h = max(col_heights) if col_heights else 0
        if total_h > 0:
            total_h -= self.row_spacing
        return (total_h, total_h, -1, -1)

    def do_allocate(
        self, widget: Gtk.Widget, width: int, height: int, baseline: int
    ) -> None:
        cols = max(
            1,
            int((width + self.column_spacing) / (self.min_column_width + self.column_spacing)),
        )
        if self.max_columns is not None:
            cols = min(cols, self.max_columns)

        col_w = max(1, int((width - (cols - 1) * self.column_spacing) / cols))
        col_heights = [0] * cols
        start_x = max(0, (width - (cols * col_w + (cols - 1) * self.column_spacing)) // 2)

        child = widget.get_first_child()
        while child is not None:
            if child.should_layout():
                _, nat_h, _, _ = child.measure(Gtk.Orientation.VERTICAL, col_w)
                col_idx = col_heights.index(min(col_heights))
                x = start_x + col_idx * (col_w + self.column_spacing)
                y = col_heights[col_idx]

                pt = Graphene.Point()
                pt.init(float(x), float(y))
                transform = Gsk.Transform().translate(pt)
                child.allocate(col_w, nat_h, -1, transform)

                col_heights[col_idx] += nat_h + self.row_spacing
            child = child.get_next_sibling()


class MasonryBox(Gtk.Box):
    """Container widget backed by MasonryLayoutManager for waterfall item tiling."""

    def __init__(
        self,
        column_spacing: int = 14,
        row_spacing: int = 14,
        min_column_width: int = 470,
        max_columns: int | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.layout_mgr = MasonryLayoutManager(
            column_spacing=column_spacing,
            row_spacing=row_spacing,
            min_column_width=min_column_width,
            max_columns=max_columns,
        )
        self.set_layout_manager(self.layout_mgr)
