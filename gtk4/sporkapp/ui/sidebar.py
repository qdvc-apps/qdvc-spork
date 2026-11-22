"""Left pane: Dashboard entry and the bookmarked repositories."""

from __future__ import annotations

from typing import Callable

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from ..core.models import Level, RepoSummary
from .util import label

DASHBOARD = "__dashboard__"


def badge_text(s: RepoSummary | None) -> tuple[str, str]:
    """(text, tooltip) for the little badge beside a repository."""
    if s is None:
        return "", ""
    if s.level == Level.MISSING:
        return "!", "; ".join(s.reasons)
    parts = []
    if s.total_ahead:
        parts.append(f"↑{s.total_ahead}")
    if s.total_behind:
        parts.append(f"↓{s.total_behind}")
    if s.dirty_count:
        parts.append(f"●{s.dirty_count}")
    if s.level == Level.UNKNOWN and not parts:
        parts.append("?")
    tip = "\n".join(s.reasons) if s.reasons else (
        "Some remotes couldn't be checked" if s.level == Level.UNKNOWN else "Clean and in sync")
    return " ".join(parts), tip


class RepoRow(Gtk.ListBoxRow):
    def __init__(self, path: str, name: str):
        super().__init__()
        self.path = path
        box = Gtk.Box(spacing=10, margin_start=4, margin_end=4, margin_top=6, margin_bottom=6)
        self.dot = Gtk.Box(valign=Gtk.Align.CENTER)
        self.dot.add_css_class("status-dot")
        box.append(self.dot)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.name = label(name, ellipsize=True)
        self.branch = label("", "caption", "dim-label", ellipsize=True)
        texts.append(self.name)
        texts.append(self.branch)
        box.append(texts)
        self.badge = label("", "sidebar-badge", "numeric", xalign=0.5)
        self.badge.set_valign(Gtk.Align.CENTER)
        self.badge.set_visible(False)
        box.append(self.badge)
        self.set_child(box)
        self.set_tooltip_text(path)

    def update(self, s: RepoSummary | None) -> None:
        for lvl in Level:
            self.dot.remove_css_class(lvl.value)
        self.badge.remove_css_class("attention")
        if s is None:
            return
        self.dot.add_css_class(s.level.value)
        if s.level == Level.MISSING:
            self.branch.set_text("Not found")
        elif s.branch:
            self.branch.set_text(s.branch + (f" · {s.operation}" if s.operation else ""))
        elif s.detached:
            self.branch.set_text("Detached HEAD")
        text, tip = badge_text(s)
        self.badge.set_text(text)
        self.badge.set_visible(bool(text))
        if s.level in (Level.ATTENTION, Level.MISSING):
            self.badge.add_css_class("attention")
        self.set_tooltip_text(f"{self.path}\n{tip}" if tip else self.path)


class Sidebar(Adw.Bin):
    def __init__(self, state, on_select: Callable[[str], None],
                 on_add: Callable[[], None], on_drop: Callable[[str], None]):
        super().__init__()
        self.state = state
        self.on_select = on_select
        self.rows: dict[str, RepoRow] = {}
        self._rebuilding = False

        self.toolbar = Adw.ToolbarView()
        self.header = Adw.HeaderBar(show_end_title_buttons=False)
        self.header.set_title_widget(Adw.WindowTitle(title="Spork"))
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Add Repository (Ctrl+O)")
        add.connect("clicked", lambda *_: on_add())
        self.header.pack_start(add)
        menu = Gio.Menu()
        s1 = Gio.Menu()
        s1.append("Fetch All Repositories", "win.fetch-everything")
        menu.append_section(None, s1)
        s2 = Gio.Menu()
        s2.append("Preferences", "app.preferences")
        s2.append("About QDVC Spork", "app.about")
        menu.append_section(None, s2)
        primary = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                 tooltip_text="Main Menu", primary=True)
        self.header.pack_end(primary)
        self.toolbar.add_top_bar(self.header)

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.add_css_class("navigation-sidebar")
        self.list.connect("row-selected", self._on_selected)
        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.connect("pressed", self._on_right_click)
        self.list.add_controller(click)

        self.dash_row = Gtk.ListBoxRow()
        dbox = Gtk.Box(spacing=10, margin_start=4, margin_top=8, margin_bottom=8)
        dbox.append(Gtk.Image(icon_name="view-grid-symbolic"))
        dbox.append(label("Dashboard"))
        self.dash_row.set_child(dbox)
        self.dash_row.path = DASHBOARD

        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroller.set_child(self.list)
        self.empty = Adw.StatusPage(icon_name="folder-symbolic", title="No Repositories",
                                    description="Add a repository, drop a folder here, or run "
                                                "<tt>spork .</tt> in a terminal.")
        self.empty.add_css_class("compact")
        add_btn = Gtk.Button(label="Add Repository…", halign=Gtk.Align.CENTER)
        add_btn.add_css_class("pill")
        add_btn.add_css_class("suggested-action")
        add_btn.connect("clicked", lambda *_: on_add())
        self.empty.set_child(add_btn)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(scroller)
        box.append(self.empty)
        self.toolbar.set_content(box)
        self.toolbar.add_css_class("sidebar-pane")
        self.set_child(self.toolbar)

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", lambda _t, value, _x, _y: self._on_drop(value, on_drop))
        self.add_controller(drop)
        self.rebuild()

    def rebuild(self) -> None:
        current = self.selected_path()
        self._rebuilding = True
        self.list.remove_all()
        self.rows.clear()
        self.list.append(self.dash_row)
        bookmarks = self.state.settings.bookmarks
        if bookmarks:
            header = label("Repositories", "caption-heading", "dim-label")
            header.set_margin_start(8)
            header.set_margin_top(12)
            header.set_margin_bottom(4)
            hrow = Gtk.ListBoxRow(selectable=False, activatable=False, child=header)
            self.list.append(hrow)
        for b in bookmarks:
            row = RepoRow(b.path, b.display_name)
            row.update(self.state.summaries.get(b.path))
            self.rows[b.path] = row
            self.list.append(row)
        self.empty.set_visible(not bookmarks)
        self._rebuilding = False
        self.select(current or DASHBOARD, notify=False)

    def update_summaries(self) -> None:
        for path, row in self.rows.items():
            row.update(self.state.summaries.get(path))

    def select(self, path: str, notify: bool = True) -> None:
        row = self.dash_row if path == DASHBOARD else self.rows.get(path)
        if row is None:
            row = self.dash_row
        if not notify:
            self._rebuilding = True
        self.list.select_row(row)
        self._rebuilding = False

    def selected_path(self) -> str | None:
        row = self.list.get_selected_row()
        return getattr(row, "path", None) if row is not None else None

    def _on_selected(self, _lb, row):
        if self._rebuilding or row is None:
            return
        self.on_select(getattr(row, "path", DASHBOARD))

    def _on_drop(self, value, on_drop) -> bool:
        files = value.get_files() if hasattr(value, "get_files") else []
        for f in files:
            p = f.get_path()
            if p:
                on_drop(p)
        return True

    def _on_right_click(self, _g, _n, x, y):
        row = self.list.get_row_at_y(int(y))
        if not isinstance(row, RepoRow):
            return
        v = GLib.Variant.new_string(row.path)
        menu = Gio.Menu()
        s1 = Gio.Menu()
        for text, act in (("Fetch", "win.fetch-repo"), ("Open in Files", "win.open-repo-files"),
                          ("Copy Path", "win.copy-repo-path")):
            item = Gio.MenuItem.new(text, None)
            item.set_action_and_target_value(act, v)
            s1.append_item(item)
        menu.append_section(None, s1)
        s2 = Gio.Menu()
        for text, act in (("Move Up", "win.move-repo-up"), ("Move Down", "win.move-repo-down"),
                          ("Remove from Spork", "win.remove-repo")):
            item = Gio.MenuItem.new(text, None)
            item.set_action_and_target_value(act, v)
            s2.append_item(item)
        menu.append_section(None, s2)
        pop = Gtk.PopoverMenu.new_from_model(menu)
        pop.set_parent(self.list)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        pop.set_pointing_to(rect)
        pop.set_has_arrow(False)
        pop.connect("closed", lambda p: GLib.idle_add(lambda: (p.unparent(), False)[1]))
        pop.popup()

