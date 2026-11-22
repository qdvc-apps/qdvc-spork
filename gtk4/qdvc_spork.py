#!/usr/bin/env python3
"""QDVC Spork for GNOME — entry point.

    python3 qdvc_spork.py [path]        open Spork (and the repository at path)
    python3 qdvc_spork.py --askpass P   password dialog used by git/ssh
"""

import os
import sys

# Absolute imports from the folder this file lives in, wherever it's run from
# (running a package's script from inside its folder breaks relative imports).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib  # noqa: E402

from sporkapp import APP_ID  # noqa: E402

# Must happen before any window exists so the desktop matches windows to the
# launcher (WM_CLASS on X11; the app ID / .desktop name on Wayland).
GLib.set_prgname(APP_ID)


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--askpass":
        from sporkapp.ui.askpass import run
        return run(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "--version":
        from sporkapp import VERSION
        print(f"QDVC Spork {VERSION}")
        return 0
    from sporkapp.ui.app import main as app_main
    argv = [sys.argv[0]] + [os.path.abspath(a) for a in sys.argv[1:] if not a.startswith("-")]
    return app_main(argv)


if __name__ == "__main__":
    sys.exit(main())
