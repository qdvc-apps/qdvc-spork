"""The right-hand pane: Commit / Changes / Browse tabs and the remote actions."""

from __future__ import annotations

from gi.repository import Adw, Gio, Gtk

from .browse import BrowsePage
from .changes import ChangesPage
from .commit_info import CommitInfoPage, ComposerPage


class DetailPane(Adw.Bin):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.toolbar = Adw.ToolbarView()
        self.header = Adw.HeaderBar(show_start_title_buttons=False)
        self.stack = Adw.ViewStack()
        self.switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        self.header.set_title_widget(self.switcher)

        self.spinner = Gtk.Spinner(tooltip_text="Working…")
        self.header.pack_end(self.spinner)

        self.push_button = Adw.SplitButton(icon_name="go-up-symbolic", tooltip_text="Push (Ctrl+P)",
                                           dropdown_tooltip="Push to…")
        self.push_button.set_action_name("repo.push")
        self.pull_button = Adw.SplitButton(icon_name="go-down-symbolic",
                                           tooltip_text="Pull (Ctrl+Shift+P)",
                                           dropdown_tooltip="Pull from…")
        self.pull_button.set_action_name("repo.pull")
        self.fetch_button = Adw.SplitButton(icon_name="emblem-synchronizing-symbolic",
                                            tooltip_text="Fetch All Remotes (Ctrl+Shift+T)",
                                            dropdown_tooltip="Fetch from…")
        self.fetch_button.set_action_name("repo.fetch-all")
        self.header.pack_start(self.fetch_button)
        self.header.pack_start(self.pull_button)
        self.header.pack_start(self.push_button)
        self.toolbar.add_top_bar(self.header)

        self.commit_page = CommitInfoPage(on_open_parent=ctx.open_commit)
        self.composer = ComposerPage(on_commit=ctx.do_commit,
                                     get_last_message=ctx.get_last_message)
        self.commit_stack = Gtk.Stack()
        self.commit_stack.add_named(self.commit_page, "commit")
        self.commit_stack.add_named(self.composer, "composer")
        self.empty = Adw.StatusPage(icon_name="emblem-documents-symbolic",
                                    title="No Commit Selected")
        self.commit_stack.add_named(self.empty, "empty")
        self.changes = ChangesPage(ctx)
        self.browse = BrowsePage(ctx)
        p1 = self.stack.add_titled(self.commit_stack, "commit", "Commit")
        p1.set_icon_name("emblem-documents-symbolic")
        p2 = self.stack.add_titled(self.changes, "changes", "Changes")
        p2.set_icon_name("document-edit-symbolic")
        p3 = self.stack.add_titled(self.browse, "browse", "Browse")
        p3.set_icon_name("folder-symbolic")
        self.toolbar.set_content(self.stack)
        self.set_child(self.toolbar)
        self.stack.connect("notify::visible-child-name", lambda *_: ctx.on_tab_changed())

    def set_busy(self, busy: bool) -> None:
        self.spinner.set_spinning(busy)
        self.spinner.set_visible(busy)

    def set_remote_menus(self, fetch: Gio.MenuModel, pull: Gio.MenuModel, push: Gio.MenuModel) -> None:
        self.fetch_button.set_menu_model(fetch)
        self.pull_button.set_menu_model(pull)
        self.push_button.set_menu_model(push)

    @property
    def tab(self) -> str:
        return self.stack.get_visible_child_name() or "commit"

    def set_tab(self, name: str) -> None:
        self.stack.set_visible_child_name(name)

    def show_empty(self, title: str = "No Commit Selected", desc: str = "") -> None:
        self.empty.set_title(title)
        self.empty.set_description(desc)
        self.commit_stack.set_visible_child_name("empty")
