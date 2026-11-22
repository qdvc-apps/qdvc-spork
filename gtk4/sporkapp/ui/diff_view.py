"""Unified diff viewer with line numbers and per-hunk actions."""

from __future__ import annotations

from typing import Callable

from gi.repository import Adw, Gdk, Gtk, Pango

from ..core.models import FileDiff
from .util import is_dark

MAX_LINES = 20000

# (label, callback(file_diff, hunk_index)) pairs shown on each hunk header
HunkAction = tuple[str, Callable[[FileDiff, int], None], bool]


def _rgba(spec: str) -> Gdk.RGBA:
    c = Gdk.RGBA()
    c.parse(spec)
    return c


class DiffView(Adw.Bin):
    def __init__(self) -> None:
        super().__init__()
        self.stack = Gtk.Stack()
        self.view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                                 top_margin=6, bottom_margin=24, left_margin=0, right_margin=12)
        self.view.add_css_class("diff-view")
        self.view.set_wrap_mode(Gtk.WrapMode.NONE)
        self.scroller = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self.scroller.set_child(self.view)
        self.status = Adw.StatusPage(icon_name="text-x-generic-symbolic", title="No File Selected")
        self.status.add_css_class("compact")
        self.big_button = Gtk.Button(label="Show Diff Anyway", halign=Gtk.Align.CENTER)
        self.big_button.add_css_class("pill")
        self.big_button.connect("clicked", lambda *_: self._render(force=True))
        self.stack.add_named(self.scroller, "diff")
        self.stack.add_named(self.status, "status")
        self.set_child(self.stack)
        self._diffs: list[FileDiff] = []
        self._actions: list[HunkAction] = []
        self._tags_dark: bool | None = None
        Adw.StyleManager.get_default().connect("notify::dark", lambda *_: self._render())
        self.show_message("No File Selected", "Select a file to see its changes.")

    # -- public ---------------------------------------------------------------

    def set_wrap(self, wrap: bool) -> None:
        self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR if wrap else Gtk.WrapMode.NONE)

    def show_message(self, title: str, description: str = "",
                     icon: str = "text-x-generic-symbolic", big: bool = False) -> None:
        self.status.set_title(title)
        self.status.set_description(description)
        self.status.set_icon_name(icon)
        self.status.set_child(self.big_button if big else None)
        self.stack.set_visible_child_name("status")

    def show_diffs(self, diffs: list[FileDiff], actions: list[HunkAction] | None = None) -> None:
        self._diffs = diffs
        self._actions = actions or []
        self._render()

    # -- rendering ----------------------------------------------------------------

    def _ensure_tags(self, buf: Gtk.TextBuffer) -> None:
        dark = is_dark()
        if self._tags_dark == dark:
            return
        table = buf.get_tag_table()
        for name in ("add", "del", "hunk", "gutter", "meta", "add-gutter", "del-gutter"):
            t = table.lookup(name)
            if t is not None:
                table.remove(t)
        if dark:
            add_bg, del_bg, hunk_bg = "rgba(46,194,126,0.16)", "rgba(237,51,59,0.18)", "rgba(53,132,228,0.18)"
        else:
            add_bg, del_bg, hunk_bg = "rgba(46,194,126,0.14)", "rgba(237,51,59,0.12)", "rgba(53,132,228,0.10)"
        buf.create_tag("add", paragraph_background_rgba=_rgba(add_bg))
        buf.create_tag("del", paragraph_background_rgba=_rgba(del_bg))
        buf.create_tag("hunk", paragraph_background_rgba=_rgba(hunk_bg),
                       foreground_rgba=_rgba("rgba(127,127,127,1)"), pixels_above_lines=6,
                       pixels_below_lines=2)
        buf.create_tag("gutter", foreground_rgba=_rgba("rgba(127,127,127,0.85)"))
        buf.create_tag("meta", foreground_rgba=_rgba("rgba(127,127,127,1)"), style=Pango.Style.ITALIC)
        self._tags_dark = dark

    def _render(self, force: bool = False) -> None:
        diffs = self._diffs
        if not diffs:
            if self.stack.get_visible_child_name() == "diff":
                self.show_message("No Changes", "This file has no textual changes.")
            return
        total = sum(len(h.lines) for d in diffs for h in d.hunks)
        if total > MAX_LINES and not force:
            self.show_message("Large Diff", f"This diff has {total:,} lines.",
                              icon="dialog-warning-symbolic", big=True)
            return
        buf = Gtk.TextBuffer()
        self._ensure_tags_on_new(buf)
        # Attach the buffer first: child anchors must belong to the view's buffer.
        self.view.set_buffer(buf)
        it = buf.get_end_iter()
        width = 1
        for d in diffs:
            for h in d.hunks:
                width = max(width, len(str(h.old_start + h.old_count)),
                            len(str(h.new_start + h.new_count)))
        pad = width
        for d in diffs:
            if d.binary:
                buf.insert_with_tags_by_name(it, "Binary file — contents not shown\n", "meta")
                continue
            if not d.hunks:
                desc = "Mode change only" if any(l.startswith("old mode") for l in d.header_lines) \
                    else "No textual changes"
                buf.insert_with_tags_by_name(it, desc + "\n", "meta")
                continue
            for hi, h in enumerate(d.hunks):
                start_mark = buf.create_mark(None, it, True)
                if self._actions:
                    anchor = buf.create_child_anchor(it)
                    box = Gtk.Box(spacing=4, margin_start=6, margin_end=6)
                    for text, cb, destructive in self._actions:
                        b = Gtk.Button(label=text)
                        b.add_css_class("hunk-button")
                        b.add_css_class("destructive-action" if destructive else "flat")
                        b.connect("clicked", lambda _b, cb=cb, d=d, hi=hi: cb(d, hi))
                        box.append(b)
                    self.view.add_child_at_anchor(box, anchor)
                    it = buf.get_end_iter()
                buf.insert(it, " " + h.header + "\n")
                buf.apply_tag_by_name("hunk", buf.get_iter_at_mark(start_mark), buf.get_end_iter())
                it = buf.get_end_iter()
                for ln in h.lines:
                    if ln.kind == "\\":
                        buf.insert_with_tags_by_name(it, " " * (2 * pad + 4) + "↵ " + ln.text.strip() + "\n", "meta")
                        continue
                    old = str(ln.old_no) if ln.old_no is not None else ""
                    new = str(ln.new_no) if ln.new_no is not None else ""
                    gutter = f" {old:>{pad}} {new:>{pad}} "
                    line_start = buf.create_mark(None, it, True)
                    buf.insert_with_tags_by_name(it, gutter, "gutter")
                    sign = {"+": "+", "-": "−", " ": " "}[ln.kind]
                    buf.insert(it, f"{sign} {ln.text}\n")
                    if ln.kind in "+-":
                        buf.apply_tag_by_name("add" if ln.kind == "+" else "del",
                                              buf.get_iter_at_mark(line_start), it)
                    buf.delete_mark(line_start)
                buf.delete_mark(start_mark)
        self.stack.set_visible_child_name("diff")
        vadj = self.scroller.get_vadjustment()
        vadj.set_value(0)

    def _ensure_tags_on_new(self, buf: Gtk.TextBuffer) -> None:
        self._tags_dark = None
        self._ensure_tags(buf)
