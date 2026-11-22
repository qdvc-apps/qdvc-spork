"""The Browse tab: the repository's files as of the selected commit."""

from __future__ import annotations

import os

from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk, Pango

from ..core.models import BlameLine, Commit, TreeEntry
from ..core.parsers import is_probably_binary
from ..core.repo import MAX_BLOB_BYTES
from .util import is_dark, label, relative_time

try:
    import gi
    gi.require_version("GtkSource", "5")
    from gi.repository import GtkSource
except (ImportError, ValueError):
    GtkSource = None

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".svg", ".tif", ".tiff"}


class TreeNode(GObject.Object):
    __gtype_name__ = "SporkTreeNode"

    def __init__(self, name: str, path: str, entry: TreeEntry | None):
        super().__init__()
        self.name = name
        self.path = path
        self.entry = entry
        self.children: list["TreeNode"] = []

    @property
    def is_dir(self) -> bool:
        return self.entry is None


def build_tree(entries: list[TreeEntry]) -> list[TreeNode]:
    root = TreeNode("", "", None)
    dirs: dict[str, TreeNode] = {"": root}
    for e in entries:
        parts = e.path.split("/")
        parent = root
        for i, part in enumerate(parts[:-1]):
            key = "/".join(parts[:i + 1])
            node = dirs.get(key)
            if node is None:
                node = TreeNode(part, key, None)
                dirs[key] = node
                parent.children.append(node)
            parent = node
        parent.children.append(TreeNode(parts[-1], e.path, e))

    def sort(node: TreeNode):
        node.children.sort(key=lambda n: (not n.is_dir, n.name.lower()))
        for c in node.children:
            if c.is_dir:
                sort(c)
    sort(root)
    return root.children


def _store(nodes: list[TreeNode]) -> Gio.ListStore:
    st = Gio.ListStore(item_type=TreeNode)
    st.splice(0, 0, nodes)
    return st


class BrowsePage(Adw.Bin):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx   # RepoView: load_tree, load_blob, load_blame, load_history, open/select
        self.rev: str | None = None
        self.current: TreeNode | None = None

        self.paned = Gtk.Paned(position=280, shrink_start_child=False, shrink_end_child=False,
                               resize_start_child=False)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup)
        factory.connect("bind", self._bind)
        self.tree_model = None
        self.selection = Gtk.SingleSelection(autoselect=False, can_unselect=True)
        self.selection.connect("selection-changed", self._on_select)
        self.list = Gtk.ListView(model=self.selection, factory=factory)
        self.list.add_css_class("navigation-sidebar")
        self.list.connect("activate", self._on_activate)
        left = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        left.set_child(self.list)
        left.set_size_request(200, -1)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bar = Gtk.Box(spacing=6, margin_start=10, margin_end=6, margin_top=4, margin_bottom=4)
        self.path_label = label("", "caption", "dim-label", ellipsize=True)
        self.path_label.set_ellipsize(Pango.EllipsizeMode.START)
        self.path_label.set_hexpand(True)
        bar.append(self.path_label)
        modes = Gtk.Box()
        modes.add_css_class("linked")
        self.mode_contents = Gtk.ToggleButton(label="Contents", active=True)
        self.mode_blame = Gtk.ToggleButton(label="Blame", group=self.mode_contents)
        self.mode_history = Gtk.ToggleButton(label="History", group=self.mode_contents)
        for b in (self.mode_contents, self.mode_blame, self.mode_history):
            b.connect("toggled", self._on_mode)
            modes.append(b)
        bar.append(modes)
        self.open_button = Gtk.MenuButton(icon_name="document-open-symbolic",
                                          tooltip_text="Open")
        self.open_button.add_css_class("flat")
        bar.append(self.open_button)
        right.append(bar)
        right.append(Gtk.Separator())

        self.stack = Gtk.Stack(vexpand=True)
        # contents
        if GtkSource is not None:
            self.text = GtkSource.View(editable=False, monospace=True, show_line_numbers=True,
                                       cursor_visible=False, highlight_current_line=False)
            self.text_buffer = GtkSource.Buffer()
            self.text.set_buffer(self.text_buffer)
        else:
            self.text = Gtk.TextView(editable=False, monospace=True, cursor_visible=False)
            self.text_buffer = self.text.get_buffer()
        self.text.add_css_class("file-view")
        self.text.set_left_margin(6)
        self.text.set_top_margin(6)
        sw = Gtk.ScrolledWindow()
        sw.set_child(self.text)
        self.stack.add_named(sw, "text")
        # image
        self.picture = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.SCALE_DOWN)
        pic_sw = Gtk.ScrolledWindow()
        pic_sw.set_child(self.picture)
        self.stack.add_named(pic_sw, "image")
        # blame
        self.blame_view = Gtk.TextView(editable=False, monospace=True, cursor_visible=False,
                                       left_margin=6, top_margin=6)
        self.blame_view.add_css_class("file-view")
        bsw = Gtk.ScrolledWindow()
        bsw.set_child(self.blame_view)
        self.stack.add_named(bsw, "blame")
        # history
        self.history = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.history.add_css_class("boxed-list")
        self.history.connect("row-activated", self._on_history_row)
        hclamp = Adw.Clamp(maximum_size=800, child=self.history, margin_top=12,
                           margin_bottom=12, margin_start=12, margin_end=12)
        hsw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        hsw.set_child(hclamp)
        self.stack.add_named(hsw, "history")
        # status
        self.status = Adw.StatusPage(icon_name="folder-open-symbolic", title="Select a File")
        self.status.add_css_class("compact")
        self.stack.add_named(self.status, "status")
        right.append(self.stack)

        self.paned.set_start_child(left)
        self.paned.set_end_child(right)
        self.set_child(self.paned)
        self._set_status("Select a File", "Choose a file on the left to see its contents.")
        Adw.StyleManager.get_default().connect("notify::dark", lambda *_: self._apply_scheme())
        self._apply_scheme()

    # -- loading ----------------------------------------------------------------

    def show_revision(self, rev: str, entries: list[TreeEntry], reveal: str | None = None) -> None:
        keep = reveal or (self.current.path if self.current and not self.current.is_dir else None)
        self.rev = rev
        roots = build_tree(entries)
        self.tree_model = Gtk.TreeListModel.new(_store(roots), False, False, self._children)
        self.selection.set_model(self.tree_model)
        self.selection.set_selected(Gtk.INVALID_LIST_POSITION)
        self.current = None
        if keep and any(e.path == keep for e in entries):
            self.reveal(keep)
        else:
            self._set_status("Select a File", "Choose a file on the left to see its contents.")
            self.path_label.set_text("")

    def reveal(self, path: str) -> None:
        """Expand folders down to `path` and select it."""
        if self.tree_model is None:
            return
        parts = path.split("/")
        pos = 0
        for depth in range(len(parts)):
            want = "/".join(parts[:depth + 1])
            found = None
            n = self.tree_model.get_n_items()
            for i in range(pos, n):
                row = self.tree_model.get_row(i)
                node = row.get_item()
                if node.path == want:
                    found = i
                    break
            if found is None:
                return
            row = self.tree_model.get_row(found)
            if depth < len(parts) - 1:
                row.set_expanded(True)
                pos = found + 1
            else:
                self.selection.set_selected(found)
                self.list.scroll_to(found, Gtk.ListScrollFlags.FOCUS, None)

    def _children(self, node: TreeNode):
        if node.is_dir:
            return _store(node.children)
        return None

    def _setup(self, _f, li):
        expander = Gtk.TreeExpander()
        box = Gtk.Box(spacing=6)
        icon = Gtk.Image()
        name = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        box.append(icon)
        box.append(name)
        expander.set_child(box)
        li.set_child(expander)

    def _bind(self, _f, li):
        row = li.get_item()
        expander: Gtk.TreeExpander = li.get_child()
        expander.set_list_row(row)
        node: TreeNode = row.get_item()
        box = expander.get_child()
        icon = box.get_first_child()
        name = icon.get_next_sibling()
        if node.is_dir:
            icon.set_from_icon_name("folder-symbolic")
        elif node.entry.kind == "commit":
            icon.set_from_icon_name("folder-remote-symbolic")
        else:
            icon.set_from_icon_name("text-x-generic-symbolic")
        name.set_text(node.name)

    def _on_activate(self, _lv, position):
        row = self.tree_model.get_row(position) if self.tree_model else None
        if row is not None and row.is_expandable():
            row.set_expanded(not row.get_expanded())

    def _on_select(self, *_a):
        row = self.selection.get_selected_item()
        if row is None:
            return
        node: TreeNode = row.get_item()
        if node.is_dir:
            return
        self.current = node
        self.path_label.set_text(node.path)
        self.open_button.set_menu_model(self.ctx.browse_menu(node.path))
        self._load_current()

    def _on_mode(self, button):
        if button.get_active() and self.current is not None:
            self._load_current()

    def _load_current(self) -> None:
        node = self.current
        if node is None or self.rev is None:
            return
        if node.entry.kind == "commit":
            self._set_status("Submodule", f"Points at commit {node.entry.sha[:12]}.",
                             icon="folder-remote-symbolic")
            return
        if self.mode_history.get_active():
            self.ctx.load_history(node.path, self._show_history)
        elif self.mode_blame.get_active():
            if node.entry.size and node.entry.size > MAX_BLOB_BYTES:
                self._set_status("File Too Large", "Blame is not available for very large files.")
                return
            self.ctx.load_blame(node.path, self._show_blame)
        else:
            if node.entry.size and node.entry.size > MAX_BLOB_BYTES:
                self._set_status("File Too Large",
                                 f"{node.entry.size / 1048576:.1f} MB — too big to show here.",
                                 icon="dialog-warning-symbolic")
                return
            self.ctx.load_blob(node.path, lambda data, p=node.path: self._show_blob(p, data))

    # -- display ----------------------------------------------------------------

    def _set_status(self, title: str, desc: str = "", icon: str = "text-x-generic-symbolic") -> None:
        self.status.set_title(title)
        self.status.set_description(desc)
        self.status.set_icon_name(icon)
        self.stack.set_visible_child_name("status")

    def _show_blob(self, path: str, data: bytes) -> None:
        if self.current is None or self.current.path != path:
            return
        ext = os.path.splitext(path)[1].lower()
        if ext in IMAGE_EXT:
            try:
                loader = GdkPixbuf.PixbufLoader()
                loader.write(data)
                loader.close()
                pix = loader.get_pixbuf()
                if pix is not None:
                    self.picture.set_paintable(Gdk.Texture.new_for_pixbuf(pix))
                    self.stack.set_visible_child_name("image")
                    return
            except GLib.Error:
                pass
        if is_probably_binary(data):
            self._set_status("Binary File", f"{len(data):,} bytes", icon="application-x-executable-symbolic")
            return
        text = data.decode("utf-8", errors="replace")
        if GtkSource is not None:
            lm = GtkSource.LanguageManager.get_default()
            lang = lm.guess_language(path, None)
            self.text_buffer.set_language(lang)
            self.text_buffer.set_highlight_syntax(lang is not None)
        self.text_buffer.set_text(text)
        self.stack.set_visible_child_name("text")

    def _show_blame(self, lines: list[BlameLine]) -> None:
        buf = Gtk.TextBuffer()
        gutter = buf.create_tag("gutter", foreground="#888888")
        alt_bg = "rgba(127,127,127,0.10)"
        rgba = Gdk.RGBA()
        rgba.parse(alt_bg)
        alt = buf.create_tag("alt", paragraph_background_rgba=rgba)
        it = buf.get_end_iter()
        prev = None
        shade = False
        width = len(str(len(lines)))
        for ln in lines:
            if ln.sha != prev:
                shade = not shade
                when = relative_time(ln.author_time)
                who = (ln.author[:14]).ljust(14)
                g = f"{ln.sha[:8]} {who} {when[:12]:<12} "
                prev = ln.sha
            else:
                g = " " * 37
            start = buf.create_mark(None, it, True)
            buf.insert_with_tags(it, g, gutter)
            buf.insert_with_tags(it, f"{ln.line_no:>{width}} ", gutter)
            buf.insert(it, ln.text + "\n")
            if shade:
                buf.apply_tag(alt, buf.get_iter_at_mark(start), it)
            buf.delete_mark(start)
        self.blame_view.set_buffer(buf)
        self.stack.set_visible_child_name("blame")

    def _show_history(self, commits: list[Commit]) -> None:
        self.history.remove_all()
        for c in commits:
            row = Adw.ActionRow(title=c.subject or "(no message)", use_markup=False,
                                subtitle=f"{c.author_name} · {relative_time(c.author_time)} · {c.short_sha}",
                                activatable=True)
            row.sha = c.sha
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            self.history.append(row)
        if not commits:
            self._set_status("No History", "No commits touch this file.")
            return
        self.stack.set_visible_child_name("history")

    def _on_history_row(self, _lb, row):
        sha = getattr(row, "sha", None)
        if sha:
            self.ctx.open_commit(sha)

    def _apply_scheme(self) -> None:
        if GtkSource is None:
            return
        sm = GtkSource.StyleSchemeManager.get_default()
        scheme = sm.get_scheme("Adwaita-dark" if is_dark() else "Adwaita")
        if scheme is not None:
            self.text_buffer.set_style_scheme(scheme)

