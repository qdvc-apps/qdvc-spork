"""Builds the real window around a throwaway repository and clicks through it.

Needs GTK 4, libadwaita 1.5+ and a display; run headless with
`xvfb-run -a python3 -m unittest tests.test_ui_smoke`. Skipped otherwise.
"""

import os
import tempfile
import time
import unittest

from helpers import TempRepo

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, GLib, Gtk
    HAVE_GTK = Gtk.init_check() if hasattr(Gtk, "init_check") else True
    HAVE_GTK = HAVE_GTK and (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 5)
except (ImportError, ValueError):
    HAVE_GTK = False


def pump(seconds: float = 0.5) -> None:
    ctx = GLib.MainContext.default()
    end = time.time() + seconds
    while time.time() < end:
        while ctx.pending():
            ctx.iteration(False)
        time.sleep(0.01)


@unittest.skipUnless(HAVE_GTK, "GTK 4 / libadwaita 1.5 with a display not available")
class WindowSmokeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="spork-ui-")
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.tmp, "config")
        os.environ["XDG_DATA_HOME"] = os.path.join(self.tmp, "data")
        os.environ["XDG_CACHE_HOME"] = os.path.join(self.tmp, "cache")
        self.repo = TempRepo()
        self.repo.commit("README.md", "# Hello\n", "Initial commit")
        self.repo.commit("src/app.py", "print('hi')\n", "Add app")
        self.repo.write("README.md", "# Hello, world\n")

    def test_open_repository_and_switch_tabs(self):
        from sporkapp.ui.app import SporkApplication
        from sporkapp.ui.state import AppState
        from sporkapp.ui.window import SporkWindow

        Adw.init()
        app = SporkApplication()
        app.register(None)
        app.state = AppState()
        win = SporkWindow(app, app.state)
        win.present()
        win.open_path(self.repo.path)
        pump(1.5)
        view = win.current_view()
        self.assertIsNotNone(view)
        self.assertEqual(view.selected, "WORKING")
        for tab in ("changes", "browse", "commit"):
            view.detail.set_tab(tab)
            pump(0.5)
        view.list_pane.selection.set_selected(1)
        pump(0.8)
        self.assertNotEqual(view.selected, "WORKING")
        win.sidebar.select("__dashboard__")
        pump(0.5)
        win.close()


if __name__ == "__main__":
    unittest.main()
