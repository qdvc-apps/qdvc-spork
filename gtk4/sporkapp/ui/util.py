"""Small UI helpers: threads, time formatting, clipboard, launching apps."""

from __future__ import annotations

import datetime as dt
import os
import shlex
import shutil
import subprocess
import threading
import traceback
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

IN_FLATPAK = bool(os.environ.get("FLATPAK_ID"))


def run_async(work: Callable[[], object], done: Callable[[object], None] | None = None,
              error: Callable[[BaseException], None] | None = None) -> None:
    """Run `work` on a thread; deliver the result on the main loop.

    The idle callbacks return False explicitly: GLib re-runs an idle callback
    for as long as it returns a truthy value (see Cursed Knowledge).
    """
    def deliver_ok(value):
        if done is not None:
            done(value)
        return False

    def deliver_err(exc):
        if error is not None:
            error(exc)
        else:
            traceback.print_exception(exc)
        return False

    def runner():
        try:
            value = work()
        except BaseException as exc:  # noqa: BLE001
            GLib.idle_add(deliver_err, exc)
            return
        GLib.idle_add(deliver_ok, value)

    threading.Thread(target=runner, daemon=True).start()


def idle_once(fn: Callable[[], None]) -> None:
    def cb():
        fn()
        return False
    GLib.idle_add(cb)


def relative_time(epoch: int | None) -> str:
    if not epoch:
        return ""
    now = dt.datetime.now(dt.timezone.utc)
    then = dt.datetime.fromtimestamp(epoch, dt.timezone.utc)
    secs = int((now - then).total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        m = secs // 60
        return f"{m} minute{'s' if m != 1 else ''} ago"
    if secs < 86400:
        h = secs // 3600
        return f"{h} hour{'s' if h != 1 else ''} ago"
    days = secs // 86400
    if days < 7:
        return f"{days} day{'s' if days != 1 else ''} ago"
    local = then.astimezone()
    if local.year == dt.datetime.now().year:
        return local.strftime("%d %b").lstrip("0")
    return local.strftime("%d %b %Y").lstrip("0")


def full_time(epoch: int) -> str:
    return dt.datetime.fromtimestamp(epoch).astimezone().strftime("%A %d %B %Y, %H:%M %Z")


def copy_text(text: str) -> None:
    display = Gdk.Display.get_default()
    if display is not None:
        display.get_clipboard().set(text)


def open_folder(window: Gtk.Window, path: str) -> None:
    Gtk.FileLauncher.new(Gio.File.new_for_path(path)).launch(window, None, None, None)


def reveal_file(window: Gtk.Window, path: str) -> None:
    Gtk.FileLauncher.new(Gio.File.new_for_path(path)).open_containing_folder(
        window, None, None, None)


def open_file(window: Gtk.Window, path: str, editor: str = "", ask: bool = False) -> None:
    if editor and not IN_FLATPAK:
        try:
            subprocess.Popen(shlex.split(editor) + [path], start_new_session=True)
            return
        except OSError:
            pass
    launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(path))
    launcher.set_always_ask(ask)
    launcher.launch(window, None, None, None)


TERMINALS = [
    ("xdg-terminal-exec", []), ("ptyxis", ["--new-window", "--working-directory={dir}"]),
    ("kgx", ["--working-directory={dir}"]), ("gnome-terminal", ["--working-directory={dir}"]),
    ("konsole", ["--workdir", "{dir}"]), ("xfce4-terminal", ["--working-directory={dir}"]),
    ("mate-terminal", ["--working-directory={dir}"]), ("tilix", ["--working-directory={dir}"]),
    ("alacritty", ["--working-directory", "{dir}"]), ("kitty", ["--directory", "{dir}"]),
    ("foot", ["--working-directory={dir}"]), ("xterm", []),
]


def terminal_available(custom: str = "") -> bool:
    if IN_FLATPAK:
        return False
    if custom:
        return True
    return any(shutil.which(t) for t, _ in TERMINALS)


def open_terminal(path: str, custom: str = "") -> bool:
    if IN_FLATPAK:
        return False
    if custom:
        cmd = [a.replace("{dir}", path) for a in shlex.split(custom)]
        subprocess.Popen(cmd, cwd=path, start_new_session=True)
        return True
    for name, args in TERMINALS:
        exe = shutil.which(name)
        if exe:
            subprocess.Popen([exe] + [a.replace("{dir}", path) for a in args],
                             cwd=path, start_new_session=True)
            return True
    return False


def css_provider_for(path: str) -> Gtk.CssProvider:
    provider = Gtk.CssProvider()
    provider.load_from_path(path)
    return provider


def is_dark() -> bool:
    return Adw.StyleManager.get_default().get_dark()


def plural(n: int, word: str, plural_word: str | None = None) -> str:
    return f"{n} {word if n == 1 else (plural_word or word + 's')}"


def clear_box(box: Gtk.Widget) -> None:
    child = box.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt


def label(text: str = "", *classes: str, xalign: float = 0.0, wrap: bool = False,
          ellipsize: bool = False, selectable: bool = False) -> Gtk.Label:
    lbl = Gtk.Label(label=text, xalign=xalign)
    for c in classes:
        lbl.add_css_class(c)
    if wrap:
        lbl.set_wrap(True)
        lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if ellipsize:
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
    if selectable:
        lbl.set_selectable(True)
    return lbl
