"""Alert-style dialogs built on Adw.AlertDialog (libadwaita 1.5+)."""

from __future__ import annotations

from typing import Callable

from gi.repository import Adw, Gtk

from ..core.gitrunner import GitError


def confirm(parent: Gtk.Widget, heading: str, body: str, action: str,
            on_yes: Callable[[], None], destructive: bool = True) -> None:
    dlg = Adw.AlertDialog(heading=heading, body=body)
    dlg.add_response("cancel", "Cancel")
    dlg.add_response("yes", action)
    dlg.set_response_appearance("yes", Adw.ResponseAppearance.DESTRUCTIVE if destructive
                                else Adw.ResponseAppearance.SUGGESTED)
    dlg.set_default_response("cancel" if destructive else "yes")
    dlg.set_close_response("cancel")

    def on_response(_d, response):
        if response == "yes":
            on_yes()

    dlg.connect("response", on_response)
    dlg.present(parent)


def choose(parent: Gtk.Widget, heading: str, body: str,
           options: list[tuple[str, str]], on_choice: Callable[[str], None],
           suggested: str | None = None) -> None:
    """options: [(response_id, label)]; 'cancel' is added automatically."""
    dlg = Adw.AlertDialog(heading=heading, body=body)
    dlg.add_response("cancel", "Cancel")
    for rid, text in options:
        dlg.add_response(rid, text)
    if suggested:
        dlg.set_response_appearance(suggested, Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response(suggested)
    dlg.set_close_response("cancel")
    dlg.connect("response", lambda _d, r: r != "cancel" and on_choice(r))
    dlg.present(parent)


def prompt(parent: Gtk.Widget, heading: str, body: str, action: str,
           on_ok: Callable[[str], None], initial: str = "", placeholder: str = "",
           validate: Callable[[str], bool] | None = None) -> None:
    dlg = Adw.AlertDialog(heading=heading, body=body)
    entry = Gtk.Entry(text=initial, placeholder_text=placeholder, activates_default=True)
    dlg.set_extra_child(entry)
    dlg.add_response("cancel", "Cancel")
    dlg.add_response("ok", action)
    dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
    dlg.set_default_response("ok")
    dlg.set_close_response("cancel")

    def update(*_a):
        text = entry.get_text().strip()
        ok = bool(text) and (validate(text) if validate else True)
        dlg.set_response_enabled("ok", ok)

    entry.connect("changed", update)
    update()

    def on_response(_d, response):
        if response == "ok":
            on_ok(entry.get_text().strip())

    dlg.connect("response", on_response)
    dlg.present(parent)
    entry.grab_focus()


def show_error(parent: Gtk.Widget, heading: str, exc: BaseException | str) -> None:
    if isinstance(exc, GitError):
        body = exc.hint or "Git reported an error."
        detail = exc.stderr or str(exc)
    else:
        body = str(exc)
        detail = ""
    dlg = Adw.AlertDialog(heading=heading, body=body)
    if detail and detail != body:
        view = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                            top_margin=8, bottom_margin=8, left_margin=8, right_margin=8)
        view.get_buffer().set_text(detail)
        scroller = Gtk.ScrolledWindow(min_content_height=80, max_content_height=240,
                                      propagate_natural_height=True)
        scroller.set_child(view)
        scroller.add_css_class("card")
        dlg.set_extra_child(scroller)
    dlg.add_response("close", "Close")
    dlg.set_default_response("close")
    dlg.present(parent)
