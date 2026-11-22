"""Main window: sidebar | (dashboard or repository view)."""

from __future__ import annotations

import os

from gi.repository import Adw, Gio, GLib, Gtk

from ..core.repo import find_toplevel
from . import dialogs
from .dashboard import Dashboard
from .repo_view import RepoView
from .sidebar import DASHBOARD, Sidebar
from .util import IN_FLATPAK, copy_text, open_folder


class SporkWindow(Adw.ApplicationWindow):
    def __init__(self, app, state):
        super().__init__(application=app, title="QDVC Spork")
        self.state = state
        self.set_default_size(1280, 800)
        self.set_size_request(900, 520)
        self.views: dict[str, RepoView] = {}

        self.toasts = Adw.ToastOverlay()
        self.outer = Gtk.Paned(position=250, shrink_start_child=False, shrink_end_child=False,
                               resize_start_child=False)
        self.sidebar = Sidebar(state, on_select=self._on_sidebar_select,
                               on_add=self.choose_repository, on_drop=self.open_path)
        self.sidebar.set_size_request(200, -1)
        self.dashboard = Dashboard(state, on_open=lambda p: self.sidebar.select(p))
        self.main_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                                    transition_duration=120)
        self.main_stack.add_named(self.dashboard, DASHBOARD)
        self.outer.set_start_child(self.sidebar)
        self.outer.set_end_child(self.main_stack)
        self.toasts.set_child(self.outer)
        self.set_content(self.toasts)

        self._install_actions()
        state.connect(self._on_state)
        self.connect("notify::is-active", self._on_active)
        self.dashboard.update()
        state.refresh_all_summaries()

    # -- helpers ------------------------------------------------------------------

    def toast(self, text: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=text, timeout=3))

    def current_view(self) -> RepoView | None:
        name = self.main_stack.get_visible_child_name()
        return self.views.get(name) if name and name != DASHBOARD else None

    def _on_state(self, topic: str) -> None:
        if topic == "bookmarks":
            self.sidebar.rebuild()
            for path in list(self.views):
                if not any(b.path == path for b in self.state.settings.bookmarks):
                    view = self.views.pop(path)
                    self.main_stack.remove(view)
            self.dashboard.update()
        elif topic == "summaries":
            self.sidebar.update_summaries()
            self.dashboard.update()
        elif topic == "fetch":
            self.dashboard.set_fetching(self.state.fetching)
            self.dashboard.update()
        elif topic == "servers":
            self.dashboard.update()

    def _on_active(self, *_a):
        """Coming back to the window: pick up changes made in a terminal or editor."""
        if self.is_active():
            view = self.current_view()
            if view is not None:
                view.refresh()

    # -- navigation -----------------------------------------------------------------

    def _on_sidebar_select(self, path: str) -> None:
        if path == DASHBOARD:
            self.main_stack.set_visible_child_name(DASHBOARD)
            self.set_title("QDVC Spork")
            return
        view = self.views.get(path)
        if view is None:
            if not os.path.isdir(path):
                self.main_stack.set_visible_child_name(DASHBOARD)
                dialogs.choose(self, "Repository Not Found",
                               f"{path} no longer exists. Remove it from Spork?",
                               [("remove", "Remove")],
                               lambda _c: self.state.remove_bookmark(path), suggested="remove")
                return
            view = RepoView(self, self.state, path)
            self.views[path] = view
            self.main_stack.add_named(view, path)
        else:
            view.refresh()
        self.main_stack.set_visible_child_name(path)
        self.set_title(f"{os.path.basename(path)} — QDVC Spork")

    def open_path(self, path: str) -> None:
        """Open (and bookmark) the repository containing `path`."""
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(path):
            note = (" The Flatpak can only see folders inside your home folder."
                    if IN_FLATPAK and not path.startswith(os.path.expanduser("~")) else "")
            dialogs.show_error(self, "Folder Not Found", f"{path} doesn't exist or isn't "
                               f"visible to Spork.{note}")
            return
        top = find_toplevel(self.state.runner, path)
        if top is None:
            dialogs.show_error(self, "Not a Git Repository",
                               f"{path} isn't inside a Git repository.")
            return
        top = self.state.add_bookmark(top)
        self.sidebar.select(top)

    def choose_repository(self) -> None:
        dlg = Gtk.FileDialog(title="Add Repository", modal=True)

        def done(d, result):
            try:
                folder = d.select_folder_finish(result)
            except GLib.Error:
                return
            if folder is not None and folder.get_path():
                self.open_path(folder.get_path())

        dlg.select_folder(self, None, done)

    # -- actions ------------------------------------------------------------------

    def _install_actions(self) -> None:
        def add(name, cb, ptype=None):
            act = Gio.SimpleAction.new(name, GLib.VariantType.new(ptype) if ptype else None)
            act.connect("activate", lambda a, p: cb(p.unpack() if p is not None else None))
            self.add_action(act)

        add("add-repo", lambda _p: self.choose_repository())
        add("show-dashboard", lambda _p: self.sidebar.select(DASHBOARD))
        add("select-repo", self._select_index, "i")
        add("fetch-everything", lambda _p: self._fetch_everything())
        add("fetch-repo", lambda p: self.state.fetch([p]), "s")
        add("open-repo-files", lambda p: open_folder(self, p), "s")
        add("copy-repo-path", lambda p: (copy_text(p), self.toast("Path copied")), "s")
        add("move-repo-up", lambda p: self._move(p, -1), "s")
        add("move-repo-down", lambda p: self._move(p, +1), "s")
        add("remove-repo", self._remove, "s")
        # Forward common shortcuts to whichever repository is showing.
        for name in ("fetch-all", "pull", "push", "refresh", "search", "new-branch"):
            add(f"repo-{name}", lambda _p, n=name: self._forward(n))
        add("repo-tab", lambda t: self._forward("tab", t), "s")

    def _forward(self, action: str, param: str | None = None) -> None:
        view = self.current_view()
        if view is None:
            if action == "fetch-all":
                self._fetch_everything()
            return
        view.actions.activate_action(action, GLib.Variant.new_string(param) if param else None)

    def _fetch_everything(self) -> None:
        def done(report):
            n_err = len(report.errors)
            off = sum(1 for s in report.servers.values() if s.online is False)
            parts = [f"Fetched {len(report.fetched)} remotes"]
            if off:
                parts.append(f"{off} server{'s' if off != 1 else ''} offline")
            if n_err:
                parts.append(f"{n_err} failed")
            self.toast(", ".join(parts))
            view = self.current_view()
            if view is not None:
                view.refresh(full=True)
        self.state.fetch(None, done)

    def _select_index(self, i: int) -> None:
        bms = self.state.settings.bookmarks
        if 0 <= i < len(bms):
            self.sidebar.select(bms[i].path)

    def _move(self, path: str, delta: int) -> None:
        bms = self.state.settings.bookmarks
        idx = next((i for i, b in enumerate(bms) if b.path == path), None)
        if idx is None:
            return
        self.state.settings.move_bookmark(path, idx + delta)
        self.state.save("bookmarks")

    def _remove(self, path: str) -> None:
        name = os.path.basename(path)
        dialogs.confirm(self, f"Remove {name} from Spork?",
                        "Only the bookmark is removed. The folder and its files stay on disk.",
                        "Remove", lambda: self.state.remove_bookmark(path))
