"""The Commit tab: details of one commit, or the commit composer."""

from __future__ import annotations

from typing import Callable

from gi.repository import Adw, Gtk, Pango

from ..core.models import Commit, FileChange, WorkingStatus
from ..core.parsers import parse_refs
from .util import copy_text, full_time, label, plural, relative_time


def _copy_button(text: str, tip: str) -> Gtk.Button:
    b = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER, tooltip_text=tip)
    b.add_css_class("flat")
    b.connect("clicked", lambda *_: copy_text(text))
    return b


class CommitInfoPage(Adw.Bin):
    def __init__(self, on_open_parent: Callable[[str], None]):
        super().__init__()
        self.on_open_parent = on_open_parent
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.clamp = Adw.Clamp(maximum_size=760, tightening_threshold=560)
        self.scroller.set_child(self.clamp)
        self.set_child(self.scroller)

    def show_commit(self, c: Commit, changes: list[FileChange] | None) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                      margin_top=24, margin_bottom=24, margin_start=12, margin_end=12)
        refs = parse_refs(c.refs)
        if refs:
            row = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=12,
                              column_spacing=4, row_spacing=4)
            for r in refs:
                pill = Gtk.Label(label=r.name)
                pill.add_css_class("ref-pill")
                pill.add_css_class(r.kind.value)
                row.append(pill)
            box.append(row)
        title = label(c.subject or "(no message)", "title-2", wrap=True, selectable=True)
        box.append(title)
        if c.body:
            body = label(c.body, "body", wrap=True, selectable=True)
            body.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            box.append(body)

        group = Adw.PreferencesGroup(title="Details")
        sha_row = Adw.ActionRow(title="Commit", subtitle=c.sha, use_markup=False, subtitle_selectable=True)
        sha_row.add_css_class("property")
        sha_row.add_suffix(_copy_button(c.sha, "Copy Commit Hash"))
        group.add(sha_row)
        author = Adw.ActionRow(title="Author", use_markup=False,
                               subtitle=f"{c.author_name} <{c.author_email}>\n"
                                        f"{full_time(c.author_time)} ({relative_time(c.author_time)})",
                               subtitle_selectable=True)
        author.add_css_class("property")
        group.add(author)
        if (c.committer_name, c.committer_email) != (c.author_name, c.author_email) or \
                abs(c.committer_time - c.author_time) > 60:
            committer = Adw.ActionRow(title="Committer", use_markup=False,
                                      subtitle=f"{c.committer_name} <{c.committer_email}>\n"
                                               f"{full_time(c.committer_time)}",
                                      subtitle_selectable=True)
            committer.add_css_class("property")
            group.add(committer)
        box.append(group)

        if c.parents:
            pg = Adw.PreferencesGroup(title="Parents" if len(c.parents) > 1 else "Parent")
            if len(c.parents) > 1:
                pg.set_description("Changes are shown against the first parent.")
            for p in c.parents:
                prow = Adw.ActionRow(title=p[:12], activatable=True)
                prow.add_css_class("monospace")
                prow.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
                prow.connect("activated", lambda _r, p=p: self.on_open_parent(p))
                pg.add(prow)
            box.append(pg)

        if changes is not None:
            adds = sum(ch.additions or 0 for ch in changes)
            dels = sum(ch.deletions or 0 for ch in changes)
            sg = Adw.PreferencesGroup(title="Changes")
            srow = Adw.ActionRow(title=plural(len(changes), "file") + " changed",
                                 subtitle=f"+{adds} −{dels}")
            srow.add_css_class("property")
            sg.add(srow)
            box.append(sg)
        self.clamp.set_child(box)
        self.scroller.get_vadjustment().set_value(0)


class ComposerPage(Adw.Bin):
    """Commit message entry for the Uncommitted Changes row."""

    def __init__(self, on_commit: Callable[[str, bool], None],
                 get_last_message: Callable[[Callable[[str], None]], None]):
        super().__init__()
        self.on_commit = on_commit
        self.get_last_message = get_last_message
        self.status: WorkingStatus | None = None
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        clamp = Adw.Clamp(maximum_size=760, tightening_threshold=560)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                      margin_top=24, margin_bottom=24, margin_start=12, margin_end=12)

        self.heading = label("Commit", "title-2")
        self.subheading = label("", "dim-label", wrap=True)
        box.append(self.heading)
        box.append(self.subheading)

        group = Adw.PreferencesGroup()
        self.summary = Adw.EntryRow(title="Summary")
        self.count = label("", "caption", "dim-label", "numeric")
        self.summary.add_suffix(self.count)
        group.add(self.summary)
        box.append(group)

        desc_label = label("Description", "heading")
        box.append(desc_label)
        self.description = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=10,
                                        bottom_margin=10, left_margin=12, right_margin=12,
                                        accepts_tab=False)
        self.description.set_size_request(-1, 140)
        frame = Gtk.Frame(child=self.description)
        frame.add_css_class("view")
        box.append(frame)

        opts = Adw.PreferencesGroup()
        self.amend = Adw.SwitchRow(title="Amend Last Commit",
                                   subtitle="Replace the previous commit instead of adding a new one")
        opts.add(self.amend)
        box.append(opts)

        self.identity = label("", "caption", "dim-label", ellipsize=True)
        self.identity.set_hexpand(True)
        self.commit_button = Gtk.Button(label="Commit")
        self.commit_button.add_css_class("pill")
        self.commit_button.add_css_class("suggested-action")
        self.commit_button.set_tooltip_text("Commit (Ctrl+Enter)")
        self.commit_button.connect("clicked", lambda *_: self.commit())
        row = Gtk.Box(spacing=12)
        row.append(self.identity)
        row.append(self.commit_button)
        box.append(row)

        clamp.set_child(box)
        scroller.set_child(clamp)
        self.set_child(scroller)

        # Handlers are connected after initial values are set (Cursed Knowledge:
        # live notify handlers firing during setup).
        self.summary.connect("changed", lambda *_: self._update())
        self.description.get_buffer().connect("changed", lambda *_: self._update())
        self.amend.connect("notify::active", self._on_amend)
        self.summary.connect("entry-activated", lambda *_: self.description.grab_focus())
        keys = Gtk.ShortcutController()
        keys.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("<Control>Return"),
                                           Gtk.CallbackAction.new(lambda *_: (self.commit(), True)[1])))
        self.add_controller(keys)
        self._update()

    def set_status(self, status: WorkingStatus, branch: str | None, identity: str) -> None:
        self.status = status
        n = len(status.staged)
        where = f"on {branch}" if branch else "with a detached HEAD"
        if n:
            self.subheading.set_text(f"{plural(n, 'staged file')} will be committed {where}.")
        else:
            self.subheading.set_text(f"Nothing is staged yet. Tick files in the Changes tab "
                                     f"to stage them ({where}).")
        self.identity.set_text(f"Committing as {identity}" if identity else
                               "Set user.name and user.email in git to commit")
        self._update()

    def message(self) -> str:
        buf = self.description.get_buffer()
        body = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()
        summary = self.summary.get_text().strip()
        return summary + ("\n\n" + body if body else "") + "\n"

    def clear(self) -> None:
        self.summary.set_text("")
        self.description.get_buffer().set_text("")
        self.amend.set_active(False)

    def commit(self) -> None:
        if not self.commit_button.get_sensitive():
            return
        self.on_commit(self.message(), self.amend.get_active())

    def _on_amend(self, *_a):
        if self.amend.get_active() and not self.summary.get_text().strip():
            def fill(msg: str):
                subject, _, body = msg.partition("\n")
                self.summary.set_text(subject.strip())
                self.description.get_buffer().set_text(body.strip())
            self.get_last_message(fill)
        self._update()

    def _update(self) -> None:
        n = len(self.summary.get_text())
        self.count.set_text(str(n) if n else "")
        if n > 72:
            self.count.add_css_class("warning")
            self.count.set_tooltip_text("Summaries over 72 characters get cut off in many tools")
        else:
            self.count.remove_css_class("warning")
            self.count.set_tooltip_text(None)
        staged = len(self.status.staged) if self.status else 0
        ok = bool(self.summary.get_text().strip()) and (staged > 0 or self.amend.get_active())
        if self.status and self.status.conflicted:
            ok = False
        self.commit_button.set_sensitive(ok)
        self.commit_button.set_label("Amend Commit" if self.amend.get_active() else
                                     (f"Commit {plural(staged, 'File')}" if staged else "Commit"))

