"""The middle pane: remotes strip, search bar and the commit list with its graph."""

from __future__ import annotations

from typing import Callable

from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango

from ..core.graph import GraphBuilder, GraphRow
from ..core.models import Commit, RefKind, RemoteBranchState, WorkingStatus
from ..core.parsers import parse_refs
from ..core.repo import SearchField
from .graph_widget import GraphCell
from .util import is_dark, label, plural, relative_time

PAGE = 300

SEARCH_FIELDS = [
    (SearchField.ANY, "Message or Author"),
    (SearchField.MESSAGE, "Message"),
    (SearchField.AUTHOR, "Author"),
    (SearchField.HASH, "Hash"),
    (SearchField.PATH, "Path"),
]


class CommitItem(GObject.Object):
    __gtype_name__ = "SporkCommitItem"

    def __init__(self, commit: Commit | None, graph: GraphRow | None,
                 working: WorkingStatus | None = None):
        super().__init__()
        self.commit = commit
        self.graph = graph
        self.working = working

    @property
    def is_working(self) -> bool:
        return self.working is not None


class CommitRow(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.add_css_class("commit-row")
        self.graph = GraphCell()
        self.append(self.graph)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True,
                       valign=Gtk.Align.CENTER, margin_top=5, margin_bottom=5, margin_end=8)
        top = Gtk.Box(spacing=4)
        self.refs = Gtk.Box(spacing=3)
        self.subject = label("", "subject", ellipsize=True)
        self.subject.set_hexpand(True)
        top.append(self.refs)
        top.append(self.subject)
        self.meta = label("", "caption", "dim-label", ellipsize=True)
        text.append(top)
        text.append(self.meta)
        self.append(text)

    def bind(self, item: CommitItem, max_width: int, show_graph: bool) -> None:
        dark = is_dark()
        self.graph.set_visible(show_graph)
        child = self.refs.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.refs.remove(child)
            child = nxt
        if item.is_working:
            self.add_css_class("working-row")
            self.graph.set_row(None, 1, True, dark)
            w = item.working
            n = len(w.entries)
            self.subject.set_text("Uncommitted Changes")
            parts = [plural(n, "file")]
            if w.staged:
                parts.append(f"{len(w.staged)} staged")
            if w.conflicted:
                parts.append(f"{len(w.conflicted)} conflicted")
            self.meta.set_text(" · ".join(parts))
            return
        self.remove_css_class("working-row")
        c = item.commit
        self.graph.set_row(item.graph, max_width, False, dark)
        self.subject.set_text(c.subject or "(no message)")
        self.subject.set_tooltip_text(c.subject)
        for ref in parse_refs(c.refs)[:4]:
            pill = Gtk.Label(label=ref.name)
            pill.add_css_class("ref-pill")
            pill.add_css_class(ref.kind.value)
            pill.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            pill.set_max_width_chars(22)
            pill.set_tooltip_text({RefKind.HEAD: "Current branch", RefKind.BRANCH: "Branch",
                                   RefKind.REMOTE: "Remote branch", RefKind.TAG: "Tag",
                                   RefKind.DETACHED: "Detached HEAD"}[ref.kind] + f": {ref.name}")
            self.refs.append(pill)
        self.meta.set_text(f"{c.author_name} · {relative_time(c.author_time)} · {c.short_sha}")


class CommitListPane(Adw.Bin):
    """Emits callbacks rather than GObject signals to keep things simple."""

    def __init__(self, on_select: Callable[[CommitItem | None], None],
                 on_query_changed: Callable[[], None],
                 on_load_more: Callable[[], None],
                 remote_menu: Callable[[str], Gio.MenuModel]):
        super().__init__()
        self.on_select = on_select
        self.on_query_changed = on_query_changed
        self.on_load_more = on_load_more
        self.remote_menu = remote_menu
        self.graph_builder = GraphBuilder()
        self.max_width = 1
        self.exhausted = False
        self.loading = False
        self._suppress_select = False

        self.toolbar = Adw.ToolbarView()
        self.header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        self.title = Adw.WindowTitle(title="", subtitle="")
        self.header.set_title_widget(self.title)

        self.branch_button = Gtk.MenuButton(always_show_arrow=True,
                                            tooltip_text="Switch Branch")
        self.branch_button.set_label("")
        self.header.pack_start(self.branch_button)

        self.search_toggle = Gtk.ToggleButton(icon_name="system-search-symbolic",
                                              tooltip_text="Search Commits (Ctrl+F)")
        self.header.pack_end(self.search_toggle)
        self.view_menu = Gtk.MenuButton(icon_name="view-more-symbolic", tooltip_text="Repository")
        self.header.pack_end(self.view_menu)
        self.toolbar.add_top_bar(self.header)

        # search
        self.search_bar = Gtk.SearchBar()
        sbox = Gtk.Box(spacing=6)
        self.search_entry = Gtk.SearchEntry(placeholder_text="Search commits", hexpand=True)
        self.field_dropdown = Gtk.DropDown.new_from_strings([l for _, l in SEARCH_FIELDS])
        self.field_dropdown.set_tooltip_text("Search in")
        sbox.append(self.search_entry)
        sbox.append(self.field_dropdown)
        self.search_bar.set_child(sbox)
        self.search_bar.connect_entry(self.search_entry)
        self.search_toggle.bind_property("active", self.search_bar, "search-mode-enabled",
                                         GObject.BindingFlags.BIDIRECTIONAL)
        self.search_entry.connect("search-changed", lambda *_: self.on_query_changed())
        self.field_dropdown.connect("notify::selected", lambda *_: self.on_query_changed())
        self.search_bar.connect("notify::search-mode-enabled", self._on_search_mode)
        self.toolbar.add_top_bar(self.search_bar)

        # remotes strip
        self.remotes_box = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                                       max_children_per_line=8, column_spacing=4,
                                       row_spacing=4, margin_start=8, margin_end=8,
                                       margin_top=4, margin_bottom=6, homogeneous=False)
        self.remotes_revealer = Gtk.Revealer(child=self.remotes_box, reveal_child=False)
        self.toolbar.add_top_bar(self.remotes_revealer)

        # list
        self.store = Gio.ListStore(item_type=CommitItem)
        self.selection = Gtk.SingleSelection(model=self.store, autoselect=False,
                                             can_unselect=True)
        self.selection.connect("selection-changed", self._on_selection)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", lambda _f, li: li.set_child(CommitRow()))
        factory.connect("bind", self._bind)
        self.list_view = Gtk.ListView(model=self.selection, factory=factory)
        self.list_view.add_css_class("commit-list")
        self.scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.scroller.set_child(self.list_view)
        self.scroller.connect("edge-reached", self._on_edge)

        self.empty = Adw.StatusPage(icon_name="system-search-symbolic", title="No Commits",
                                    description="")
        self.empty.add_css_class("compact")
        self.content = Gtk.Stack()
        self.content.add_named(self.scroller, "list")
        self.content.add_named(self.empty, "empty")
        self.toolbar.set_content(self.content)
        self.set_child(self.toolbar)

    # -- query ------------------------------------------------------------------

    @property
    def search_text(self) -> str:
        if not self.search_bar.get_search_mode():
            return ""
        return self.search_entry.get_text().strip()

    @property
    def search_field(self) -> str:
        return SEARCH_FIELDS[self.field_dropdown.get_selected()][0]

    def _on_search_mode(self, *_a):
        if not self.search_bar.get_search_mode() and self.search_entry.get_text():
            self.search_entry.set_text("")
        self.on_query_changed()

    # -- data -------------------------------------------------------------------

    def set_commits(self, commits: list[Commit], working: WorkingStatus | None,
                    keep_sha: str | None = None, keep_working: bool = False) -> None:
        self._suppress_select = True
        self.graph_builder = GraphBuilder()
        rows = self.graph_builder.add(commits)
        self.max_width = max([r.width for r in rows], default=1)
        items = []
        if working is not None and not working.is_clean and not self.search_text:
            items.append(CommitItem(None, None, working))
        items += [CommitItem(c, g) for c, g in zip(commits, rows)]
        self.store.splice(0, self.store.get_n_items(), items)
        self.exhausted = len(commits) < PAGE
        self.content.set_visible_child_name("list" if items else "empty")
        if not items:
            self.empty.set_description("Nothing matches your search." if self.search_text
                                       else "This branch has no commits yet.")
        # restore selection
        target = None
        for i, it in enumerate(items):
            if keep_working and it.is_working:
                target = i
                break
            if keep_sha and it.commit and it.commit.sha == keep_sha:
                target = i
                break
        self._suppress_select = False
        if target is None and items:
            target = 0
        if target is not None:
            self.selection.set_selected(target)
            self._on_selection()
        else:
            self.selection.set_selected(Gtk.INVALID_LIST_POSITION)
            self.on_select(None)

    def append_commits(self, commits: list[Commit]) -> None:
        rows = self.graph_builder.add(commits)
        self.max_width = max([self.max_width] + [r.width for r in rows])
        self.store.splice(self.store.get_n_items(), 0, [CommitItem(c, g) for c, g in zip(commits, rows)])
        self.exhausted = len(commits) < PAGE

    def commit_count(self) -> int:
        n = self.store.get_n_items()
        if n and self.store.get_item(0).is_working:
            n -= 1
        return n

    def update_working(self, working: WorkingStatus) -> None:
        """Insert, update or remove the Uncommitted Changes row in place."""
        has = self.store.get_n_items() > 0 and self.store.get_item(0).is_working
        show = not working.is_clean and not self.search_text
        selected = self.selection.get_selected()
        self._suppress_select = True
        if has and show:
            self.store.splice(0, 1, [CommitItem(None, None, working)])
            if selected == 0:
                self.selection.set_selected(0)
        elif has and not show:
            self.store.remove(0)
        elif show and not has:
            self.store.insert(0, CommitItem(None, None, working))
        self._suppress_select = False

    def select_sha(self, sha: str) -> bool:
        for i in range(self.store.get_n_items()):
            it = self.store.get_item(i)
            if it.commit and it.commit.sha == sha:
                self.selection.set_selected(i)
                self.list_view.scroll_to(i, Gtk.ListScrollFlags.FOCUS, None)
                return True
        return False

    def selected_item(self) -> CommitItem | None:
        return self.selection.get_selected_item()

    def _bind(self, _f, list_item):
        row: CommitRow = list_item.get_child()
        row.bind(list_item.get_item(), self.max_width, not self.search_text)

    def _on_selection(self, *_a):
        if self._suppress_select:
            return
        self.on_select(self.selection.get_selected_item())

    def _on_edge(self, _sw, pos):
        if pos == Gtk.PositionType.BOTTOM and not self.exhausted and not self.loading:
            self.on_load_more()

    # -- remotes strip ------------------------------------------------------------

    def set_remotes(self, states: list[RemoteBranchState], branch: str | None,
                    offline: list[str], errors: dict[str, str]) -> None:
        child = self.remotes_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.remotes_box.remove(child)
            child = nxt
        for st in states:
            btn = Gtk.MenuButton()
            btn.add_css_class("remote-chip")
            btn.add_css_class("flat")
            box = Gtk.Box(spacing=4)
            icon_name = "network-server-symbolic"
            tip = st.remote
            if st.remote in offline:
                icon_name = "network-offline-symbolic"
                btn.add_css_class("offline")
                tip = f"{st.remote}: server offline (showing last known state)"
            elif st.remote in errors:
                icon_name = "dialog-warning-symbolic"
                tip = f"{st.remote}: last fetch failed\n{errors[st.remote][:300]}"
            box.append(Gtk.Image(icon_name=icon_name, pixel_size=12))
            box.append(label(st.remote, "caption-heading"))
            if not st.exists:
                txt = f"no {branch}" if branch else "—"
                box.append(label(txt, "caption", "dim-label"))
                tip += f"\nThis remote has no branch named {branch}." if branch else ""
            else:
                if st.ahead == 0 and st.behind == 0:
                    box.append(label("✓", "caption", "level-ok"))
                    tip += "\nIn sync"
                if st.ahead:
                    box.append(label(f"↑{st.ahead}", "caption", "numeric", "level-attention"))
                    tip += f"\n{plural(st.ahead, 'commit')} to push"
                if st.behind:
                    box.append(label(f"↓{st.behind}", "caption", "numeric", "level-attention"))
                    tip += f"\n{plural(st.behind, 'commit')} to pull"
            btn.set_child(box)
            btn.set_tooltip_text(tip)
            btn.set_menu_model(self.remote_menu(st.remote))
            self.remotes_box.append(btn)
        self.remotes_revealer.set_reveal_child(bool(states))

    def set_branch_label(self, text: str) -> None:
        self.branch_button.set_label(text)

    def toggle_search(self) -> None:
        self.search_toggle.set_active(not self.search_toggle.get_active())
        if self.search_toggle.get_active():
            GLib.idle_add(lambda: (self.search_entry.grab_focus(), False)[1])
