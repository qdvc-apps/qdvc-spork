"""Draws one row of the commit graph with cairo."""

from __future__ import annotations

import math

from gi.repository import Gtk

from ..core.graph import GraphRow

LANE_W = 14
NODE_R = 4.0

# Light-mode / dark-mode lane colours (GNOME palette).
PALETTE_LIGHT = [(0.21, 0.52, 0.89), (0.15, 0.64, 0.41), (0.90, 0.38, 0.0),
                 (0.57, 0.25, 0.67), (0.75, 0.11, 0.16), (0.39, 0.27, 0.17),
                 (0.10, 0.55, 0.60), (0.80, 0.55, 0.0)]
PALETTE_DARK = [(0.38, 0.63, 0.92), (0.34, 0.82, 0.55), (1.0, 0.56, 0.25),
                (0.75, 0.47, 0.83), (0.96, 0.38, 0.32), (0.71, 0.54, 0.38),
                (0.30, 0.78, 0.80), (0.98, 0.82, 0.30)]


def colour(index: int, dark: bool) -> tuple[float, float, float]:
    pal = PALETTE_DARK if dark else PALETTE_LIGHT
    return pal[index % len(pal)]


class GraphCell(Gtk.DrawingArea):
    def __init__(self) -> None:
        super().__init__()
        self.row: GraphRow | None = None
        self.max_width = 1
        self.working = False
        self.dark = False
        self.set_vexpand(True)
        self.set_draw_func(self._draw)

    def set_row(self, row: GraphRow | None, max_width: int, working: bool, dark: bool) -> None:
        self.row = row
        self.working = working
        self.dark = dark
        self.max_width = max(1, min(max_width, 12))
        self.set_content_width(LANE_W * self.max_width + 6)
        self.queue_draw()

    def _x(self, lane: int) -> float:
        return 3 + LANE_W * lane + LANE_W / 2

    def _draw(self, _area, cr, width: int, height: int) -> None:
        mid = height / 2
        cr.set_line_width(2.0)
        if self.working:
            r, g, b = colour(0, self.dark)
            cr.set_source_rgba(r, g, b, 0.9)
            cr.set_dash([3.0, 3.0])
            cr.move_to(self._x(0), mid)
            cr.line_to(self._x(0), height)
            cr.stroke()
            cr.set_dash([])
            cr.arc(self._x(0), mid, NODE_R, 0, 2 * math.pi)
            if self.dark:
                cr.set_source_rgb(0.15, 0.15, 0.15)
            else:
                cr.set_source_rgb(1, 1, 1)
            cr.fill_preserve()
            cr.set_source_rgb(r, g, b)
            cr.stroke()
            return
        row = self.row
        if row is None:
            return
        for a, b, c in row.top:
            if a >= self.max_width and b >= self.max_width:
                continue
            self._segment(cr, a, 0, b, mid, c)
        for a, b, c in row.bottom:
            if a >= self.max_width and b >= self.max_width:
                continue
            self._segment(cr, a, mid, b, height, c)
        x = self._x(min(row.node, self.max_width - 1))
        r, g, b = colour(row.color, self.dark)
        cr.arc(x, mid, NODE_R, 0, 2 * math.pi)
        cr.set_source_rgb(r, g, b)
        cr.fill()

    def _segment(self, cr, a: int, y0: float, b: int, y1: float, c: int) -> None:
        r, g, bl = colour(c, self.dark)
        cr.set_source_rgb(r, g, bl)
        x0 = self._x(min(a, self.max_width - 1))
        x1 = self._x(min(b, self.max_width - 1))
        cr.move_to(x0, y0)
        if x0 == x1:
            cr.line_to(x1, y1)
        else:
            ym = (y0 + y1) / 2
            cr.curve_to(x0, ym, x1, ym, x1, y1)
        cr.stroke()
