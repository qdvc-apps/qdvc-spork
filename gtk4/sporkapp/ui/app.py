"""Adw.Application: single instance, `spork <path>` forwarding, app-level actions."""

from __future__ import annotations

import os

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .. import APP_ID, APP_NAME, VERSION
from .preferences import PreferencesDialog
from .state import AppState
from .util import css_provider_for
from .window import SporkWindow

HERE = os.path.dirname(os.path.abspath(__file__))


class SporkApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.state: AppState | None = None
        self.window: SporkWindow | None = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        GLib.set_application_name(APP_NAME)
        Gtk.Window.set_default_icon_name(APP_ID)
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, css_provider_for(os.path.join(HERE, "style.css")),
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.state = AppState()

        def add(name, cb):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda *_: cb())
            self.add_action(act)

        add("quit", self.quit)
        add("preferences", self.show_preferences)
        add("about", self.show_about)

        accels = {
            "app.quit": ["<Control>q"],
            "app.preferences": ["<Control>comma"],
            "win.add-repo": ["<Control>o"],
            "win.show-dashboard": ["<Control>0"],
            "win.fetch-everything": ["<Control><Alt>t"],
            "win.repo-fetch-all": ["<Control><Shift>t"],
            "win.repo-pull": ["<Control><Shift>p"],
            "win.repo-push": ["<Control>p"],
            "win.repo-refresh": ["<Control>r", "F5"],
            "win.repo-search": ["<Control>f"],
            "win.repo-new-branch": ["<Control><Shift>n"],
            "win.repo-tab::commit": ["<Alt>1"],
            "win.repo-tab::changes": ["<Alt>2"],
            "win.repo-tab::browse": ["<Alt>3"],
            "window.close": ["<Control>w"],
        }
        for action, keys in accels.items():
            self.set_accels_for_action(action, keys)
        for i in range(9):
            self.set_accels_for_action(f"win.select-repo({i})", [f"<Control>{i + 1}"])

    def do_activate(self) -> None:
        if self.window is None:
            self.window = SporkWindow(self, self.state)
        self.window.present()

    def do_open(self, files, n_files, hint) -> None:
        self.do_activate()
        for f in files:
            path = f.get_path()
            if path:
                self.window.open_path(path)

    def show_preferences(self, page: str | None = None) -> None:
        if self.window is None:
            return
        PreferencesDialog(self.state, page).present(self.window)

    def show_about(self) -> None:
        about = Adw.AboutDialog(
            application_name=APP_NAME, application_icon=APP_ID, version=VERSION,
            developer_name="QDVC Apps",
            website="https://github.com/qdvc-apps/qdvc-spork",
            issue_url="https://github.com/qdvc-apps/qdvc-spork/issues",
            comments="A native Git client for bookmarked repositories, multiple remotes "
                     "and local servers with self-signed certificates.")
        about.add_credit_section("Built with", [
            "GTK https://gtk.org",
            "libadwaita https://gnome.pages.gitlab.gnome.org/libadwaita/",
            "Git https://git-scm.com",
        ])
        about.present(self.window)


def main(argv: list[str]) -> int:
    app = SporkApplication()
    return app.run(argv)
