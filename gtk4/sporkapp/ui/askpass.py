"""`--askpass` mode: a small dialog git and ssh call to ask for a secret.

git runs `$GIT_ASKPASS "<prompt>"` and ssh runs `$SSH_ASKPASS "<prompt>"`;
whatever we print on stdout is the answer, and a non-zero exit means "cancel".
"""

from __future__ import annotations

import sys

from gi.repository import Adw, Gio, Gtk

from .util import label


def classify(prompt: str) -> str:
    p = prompt.lower()
    if "(yes/no" in p or "yes/no/[fingerprint]" in p:
        return "confirm"
    if "username" in p:
        return "text"
    return "secret"


class AskpassApp(Adw.Application):
    def __init__(self, prompt: str):
        super().__init__(application_id=None, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.prompt = prompt.strip() or "Password:"
        self.answer: str | None = None
        self.kind = classify(self.prompt)

    def do_activate(self) -> None:
        win = Adw.Window(application=self, title="QDVC Spork", modal=True,
                         default_width=440, resizable=False)
        view = Adw.ToolbarView()
        header = Adw.HeaderBar(show_title=False)
        view.add_top_bar(header)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=6, margin_bottom=18, margin_start=24, margin_end=24)
        icon = {"confirm": "dialog-question-symbolic", "text": "avatar-default-symbolic"}.get(
            self.kind, "dialog-password-symbolic")
        img = Gtk.Image(icon_name=icon, pixel_size=48)
        img.add_css_class("dim-label")
        box.append(img)
        title = {"confirm": "Confirm Server Identity", "text": "Sign In"}.get(
            self.kind, "Password Needed")
        box.append(label(title, "title-3", xalign=0.5))
        msg = label(self.prompt, wrap=True, selectable=True, xalign=0.5)
        msg.set_justify(Gtk.Justification.CENTER)
        box.append(msg)

        entry = None
        if self.kind == "secret":
            entry = Gtk.PasswordEntry(show_peek_icon=True, activates_default=True)
        elif self.kind == "text":
            entry = Gtk.Entry(activates_default=True)
        if entry is not None:
            box.append(entry)

        buttons = Gtk.Box(spacing=12, homogeneous=True, margin_top=6)
        cancel = Gtk.Button(label="No" if self.kind == "confirm" else "Cancel")
        ok = Gtk.Button(label="Yes" if self.kind == "confirm" else "OK")
        ok.add_css_class("suggested-action")
        cancel.add_css_class("pill")
        ok.add_css_class("pill")
        buttons.append(cancel)
        buttons.append(ok)
        box.append(buttons)
        view.set_content(box)
        win.set_content(view)
        win.set_default_widget(ok)

        def finish(accept: bool):
            if accept:
                if self.kind == "confirm":
                    self.answer = "yes"
                else:
                    self.answer = entry.get_text() if entry is not None else ""
            elif self.kind == "confirm":
                self.answer = "no"
            win.close()

        ok.connect("clicked", lambda *_: finish(True))
        cancel.connect("clicked", lambda *_: finish(False))
        win.present()
        if entry is not None:
            entry.grab_focus()


def run(argv: list[str]) -> int:
    prompt = " ".join(argv) if argv else "Password:"
    app = AskpassApp(prompt)
    app.run([sys.argv[0]])
    if app.answer is None:
        return 1
    sys.stdout.write(app.answer + "\n")
    sys.stdout.flush()
    return 0
