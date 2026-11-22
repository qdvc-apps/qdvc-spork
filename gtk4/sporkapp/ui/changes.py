"""The Changes tab: changed-file tree on the left, diff on the right."""

from __future__ import annotations

import os
from typing import Callable

from gi.repository import Adw, Gdk, GLib, Gtk, Pango

from ..core.models import FileChange, FileDiff, StatusEntry, WorkingStatus
from .diff_view import DiffView
from .util import label

LETTER_CLASS = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed",
                "C": "renamed", "T": "modified", "U": "conflict", "?": "added"}
LETTER_TIP = {"A": "Added", "M": "Modified", "D": "Deleted", "R": "Renamed", "C": "Copied",
              "T": "Type changed", "U": "Conflicted", "?": "Untracked"}


def _letter(code: str) -> Gtk.Label:
    lbl = Gtk.Label(label=code)
    lbl.add_css_class("change-letter")
    lbl.add_css_class(LETTER_CLASS.get(code, "modified"))
    lbl.set_tooltip_text(LETTER_TIP.get(code, code))
    return lbl


def _group_by_dir(paths: list[str]) -> list[tuple[str, list[str]]]:
    groups: dict[str, list[str]] = {}
    for p in sorted(paths, key=lambda x: (os.path.dirname(x), os.path.basename(x).lower())):
        groups.setdefault(os.path.dirname(p), []).append(p)
    return sorted(groups.items(), key=lambda kv: kv[0])


class FileRow(Gtk.ListBoxRow):
    def __init__(self, path: str, code: str, *, display: str | None = None,
                 additions: int | None = None, deletions: int | None = None,
                 indent: bool = True, check: bool | None = None,
                 on_check: Callable[[bool], None] | None = None, staged_side: bool = False):
        super().__init__()
        self.path = path
        self.code = code
        self.staged_side = staged_side
        box = Gtk.Box(spacing=8, margin_start=18 if indent else 6, margin_end=6,
                      margin_top=3, margin_bottom=3)
        if check is not None:
            cb = Gtk.CheckButton(active=check, valign=Gtk.Align.CENTER)
            cb.set_tooltip_text("Unstage" if check else "Stage")
            if on_check:
                cb.connect("toggled", lambda b: on_check(b.get_active()))
            box.append(cb)
        box.append(_letter(code))
        name = label(display or os.path.basename(path), ellipsize=True)
        name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        name.set_hexpand(True)
        name.set_tooltip_text(path)
        box.append(name)
        if additions is not None or deletions is not None:
            if additions:
                box.append(label(f"+{additions}", "caption", "numeric", "additions"))
            if deletions:
                box.append(label(f"−{deletions}", "caption", "numeric", "deletions"))
        self.set_child(box)


class DirRow(Gtk.ListBoxRow):
    def __init__(self, directory: str):
        super().__init__(selectable=False, activatable=False)
        box = Gtk.Box(spacing=6, margin_start=6, margin_top=6, margin_bottom=2)
        box.append(Gtk.Image(icon_name="folder-symbolic", pixel_size=14))
        lbl = label(directory or "/", "caption", "dim-label", ellipsize=True)
        lbl.set_ellipsize(Pango.EllipsizeMode.START)
        box.append(lbl)
        self.set_child(box)


class SectionRow(Gtk.ListBoxRow):
    def __init__(self, title: str, button_label: str | None = None,
                 on_button: Callable[[], None] | None = None):
        super().__init__(selectable=False, activatable=False)
        box = Gtk.Box(spacing=6, margin_start=6, margin_end=6, margin_top=10, margin_bottom=4)
        box.append(label(title, "heading", ellipsize=True))
        spacer = Gtk.Box(hexpand=True)
        box.append(spacer)
        if button_label and on_button:
            b = Gtk.Button(label=button_label)
            b.add_css_class("flat")
            b.add_css_class("caption")
            b.connect("clicked", lambda *_: on_button())
            box.append(b)
        self.set_child(box)


class ChangesPage(Adw.Bin):
    """Callbacks are supplied by the repo view (it owns the Repository)."""

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx    # RepoView: load_diff, stage, unstage, discard, file_menu
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, position=280,
                               shrink_start_child=False, shrink_end_child=False,
                               resize_start_child=False)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.add_css_class("navigation-sidebar")
        self.list.connect("row-selected", self._on_row_selected)
        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.connect("pressed", self._on_right_click)
        self.list.add_controller(click)
        left = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        left.set_child(self.list)
        left.set_size_request(200, -1)
        self.diff = DiffView()
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bar = Gtk.Box(spacing=6, margin_start=10, margin_end=6, margin_top=4, margin_bottom=4)
        self.path_label = label("", "caption", "dim-label", ellipsize=True)
        self.path_label.set_ellipsize(Pango.EllipsizeMode.START)
        self.path_label.set_hexpand(True)
        bar.append(self.path_label)
        self.wrap_toggle = Gtk.ToggleButton(icon_name="format-justify-fill-symbolic",
                                            tooltip_text="Wrap Long Lines")
        self.wrap_toggle.add_css_class("flat")
        self.wrap_toggle.connect("toggled", lambda b: self.diff.set_wrap(b.get_active()))
        bar.append(self.wrap_toggle)
        right.append(bar)
        right.append(Gtk.Separator())
        self.diff.set_vexpand(True)
        right.append(self.diff)
        self.paned.set_start_child(left)
        self.paned.set_end_child(right)
        self.set_child(self.paned)
        self.mode = "none"
        self.sha: str | None = None
        self.changes: list[FileChange] = []
        self.working: WorkingStatus | None = None
        self._selected_key: tuple[str, bool] | None = None
        self._rebuilding = False

    # -- commit mode ------------------------------------------------------------

    def show_commit(self, sha: str, changes: list[FileChange]) -> None:
        self.mode = "commit"
        self.sha = sha
        self.changes = changes
        self._rebuilding = True
        self._clear()
        by_path = {c.path: c for c in changes}
        first = None
        for directory, paths in _group_by_dir(list(by_path)):
            self.list.append(DirRow(directory))
            for p in paths:
                c = by_path[p]
                disp = os.path.basename(p)
                if c.old_path and c.old_path != c.path:
                    disp = f"{os.path.basename(c.old_path)} → {os.path.basename(p)}" \
                        if os.path.dirname(c.old_path) == directory else f"{c.old_path} → {disp}"
                row = FileRow(p, c.status, display=disp, additions=c.additions,
                              deletions=c.deletions)
                self.list.append(row)
                first = first or row
        self._rebuilding = False
        if first is not None:
            self.list.select_row(first)
        else:
            self.path_label.set_text("")
            self.diff.show_message("No Changes", "This commit doesn't change any files "
                                   "(compared with its first parent).", icon="emblem-ok-symbolic")

    # -- working copy mode --------------------------------------------------------

    def show_working(self, status: WorkingStatus) -> None:
        self.mode = "working"
        self.working = status
        keep = self._selected_key
        self._rebuilding = True
        self._clear()
        staged = status.staged
        unstaged = status.unstaged
        target = None
        if status.conflicted:
            self.list.append(SectionRow(f"Conflicts ({len(status.conflicted)})"))
            for e in status.conflicted:
                row = FileRow(e.path, "U", display=e.path, indent=False, staged_side=False,
                              check=False, on_check=lambda on, e=e: on and self.ctx.stage([e.path]))
                self.list.append(row)
                target = target or (row if keep is None else None)
                if keep == (e.path, False):
                    target = row
        self.list.append(SectionRow(f"Staged ({len(staged)})",
                                    "Unstage All" if staged else None,
                                    lambda: self.ctx.unstage([e.path for e in staged] +
                                                             [e.old_path for e in staged if e.old_path])))
        for e in staged:
            code = e.index if e.index != "." else "M"
            disp = e.path if not e.old_path else f"{e.old_path} → {e.path}"
            row = FileRow(e.path, code, display=disp, indent=False, check=True, staged_side=True,
                          on_check=lambda on, e=e: (not on) and self.ctx.unstage(
                              [p for p in (e.old_path, e.path) if p]))
            self.list.append(row)
            if keep == (e.path, True) or (keep is None and target is None):
                target = row
        others = [e for e in unstaged if not e.conflicted]
        self.list.append(SectionRow(f"Changes ({len(others)})",
                                    "Stage All" if others else None,
                                    lambda: self.ctx.stage([e.path for e in others])))
        for e in others:
            code = "?" if e.untracked else (e.worktree if e.worktree != "." else "M")
            row = FileRow(e.path, code, display=e.path, indent=False, check=False,
                          staged_side=False,
                          on_check=lambda on, e=e: on and self.ctx.stage([e.path]))
            self.list.append(row)
            if keep == (e.path, False) or (keep is None and target is None):
                target = row
        self._rebuilding = False
        if target is not None:
            self.list.select_row(target)
        else:
            self._selected_key = None
            self.path_label.set_text("")
            self.diff.show_message("Working Copy Clean", "There are no uncommitted changes.",
                                   icon="emblem-ok-symbolic")

    # -- events ---------------------------------------------------------------------

    def _clear(self) -> None:
        self.list.remove_all()

    def _on_row_selected(self, _lb, row):
        if self._rebuilding or not isinstance(row, FileRow):
            return
        self._selected_key = (row.path, row.staged_side)
        self.path_label.set_text(row.path)
        if self.mode == "commit":
            change = next((c for c in self.changes if c.path == row.path), None)
            if change is not None:
                self.ctx.load_commit_diff(self.sha, change, self._show_diffs_for_commit)
        elif self.mode == "working":
            entry = self._entry(row.path)
            if entry is None:
                return
            self.ctx.load_working_diff(entry, row.staged_side,
                                       lambda diffs, e=entry, s=row.staged_side:
                                       self._show_working_diffs(diffs, e, s))

    def _entry(self, path: str) -> StatusEntry | None:
        if self.working is None:
            return None
        return next((e for e in self.working.entries if e.path == path), None)

    def _show_diffs_for_commit(self, diffs: list[FileDiff]) -> None:
        if not diffs:
            self.diff.show_message("No Textual Changes", "Only file metadata changed.")
            return
        self.diff.show_diffs(diffs)

    def _show_working_diffs(self, diffs: list[FileDiff], entry: StatusEntry, staged: bool) -> None:
        if entry.conflicted:
            if diffs:
                self.diff.show_diffs(diffs)
            else:
                self.diff.show_message(
                    "Conflicted File", "Resolve the conflict in an editor, then stage the file.",
                    icon="dialog-warning-symbolic")
            return
        if staged:
            actions = [("Unstage Hunk", self.ctx.unstage_hunk, False)]
        elif entry.untracked:
            actions = []
        else:
            actions = [("Stage Hunk", self.ctx.stage_hunk, False),
                       ("Discard Hunk…", self.ctx.discard_hunk, True)]
        if not diffs:
            self.diff.show_message("No Textual Changes", "Only file metadata changed.")
            return
        self.diff.show_diffs(diffs, actions)

    def _on_right_click(self, gesture, _n, x, y):
        row = self.list.get_row_at_y(int(y))
        if not isinstance(row, FileRow):
            return
        self.list.select_row(row)
        menu = self.ctx.file_menu(row.path, self.mode, row.staged_side, row.code)
        if menu is None:
            return
        pop = Gtk.PopoverMenu.new_from_model(menu)
        pop.set_parent(self.list)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        pop.set_pointing_to(rect)
        pop.set_has_arrow(False)
        pop.connect("closed", lambda p: GLib.idle_add(lambda: (p.unparent(), False)[1]))
        pop.popup()

    def selected_path(self) -> str | None:
        row = self.list.get_selected_row()
        return row.path if isinstance(row, FileRow) else None
