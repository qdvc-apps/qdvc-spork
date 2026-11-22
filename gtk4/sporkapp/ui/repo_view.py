"""One open repository: commit list pane + detail pane, and every git action."""

from __future__ import annotations

import os
from typing import Callable

from gi.repository import Adw, Gio, GLib, Gtk

from ..core.gitrunner import GitError
from ..core.models import (
    Branch, Commit, FileChange, FileDiff, Remote, RemoteBranchState, StatusEntry, WorkingStatus,
)
from ..core.repo import LogQuery, Repository
from . import dialogs
from .commit_list import PAGE, CommitItem, CommitListPane
from .detail import DetailPane
from .util import (
    copy_text, open_file, open_folder, open_terminal, reveal_file, run_async,
    terminal_available,
)


class RepoView(Adw.Bin):
    def __init__(self, window, state, path: str):
        super().__init__()
        self.window = window
        self.state = state
        self.repo = Repository(state.runner, path)
        self.status: WorkingStatus | None = None
        self.branches: list[Branch] = []
        self.remotes: list[Remote] = []
        self.remote_states: list[RemoteBranchState] = []
        self.branch: str | None = None
        self.identity = ""
        self.operation: str | None = None
        self.selected: str | None = None      # "WORKING", a sha, or None
        self._tokens: dict[str, int] = {}
        self._busy = 0
        self._loaded_tab: tuple[str, str] | None = None
        self._commit_cache: dict[str, Commit] = {}

        self.actions = Gio.SimpleActionGroup()
        self.insert_action_group("repo", self.actions)
        self._install_actions()

        self.list_pane = CommitListPane(on_select=self._on_select,
                                        on_query_changed=self.reload_log,
                                        on_load_more=self._load_more,
                                        remote_menu=self._remote_chip_menu)
        self.detail = DetailPane(self)
        self.paned = Gtk.Paned(position=420, shrink_start_child=False, shrink_end_child=False,
                               resize_start_child=False)
        self.list_pane.set_size_request(300, -1)
        self.detail.set_size_request(420, -1)
        self.paned.set_start_child(self.list_pane)
        self.paned.set_end_child(self.detail)
        self.set_child(self.paned)
        self.list_pane.view_menu.set_menu_model(self._view_menu())
        self.detail.set_busy(False)
        self.refresh(full=True)

    @property
    def path(self) -> str:
        return self.repo.path

    # -- plumbing -----------------------------------------------------------------

    def _token(self, key: str) -> int:
        self._tokens[key] = self._tokens.get(key, 0) + 1
        return self._tokens[key]

    def _current(self, key: str, tok: int) -> bool:
        return self._tokens.get(key) == tok

    def _bg(self, key: str, work: Callable, done: Callable, error_title: str | None = None,
            busy: bool = False) -> None:
        tok = self._token(key)
        if busy:
            self._set_busy(+1)

        def ok(value):
            if busy:
                self._set_busy(-1)
            if self._current(key, tok):
                done(value)

        def fail(exc):
            if busy:
                self._set_busy(-1)
            if not self._current(key, tok):
                return
            if error_title:
                dialogs.show_error(self.window, error_title, exc)
            else:
                print(f"spork: {key}: {exc}")

        run_async(work, ok, fail)

    def _set_busy(self, delta: int) -> None:
        self._busy = max(0, self._busy + delta)
        self.detail.set_busy(self._busy > 0)

    def toast(self, text: str) -> None:
        self.window.toast(text)

    # -- loading ------------------------------------------------------------------

    def refresh(self, full: bool = False) -> None:
        """Reload status, branches and remotes; full=True also reloads history."""
        repo = self.repo

        def work():
            status = repo.status()
            branches = repo.branches()
            remotes = repo.remotes()
            branch = status.branch.head
            states = repo.remote_states(branch, remotes)
            name = repo.git("config", "user.name", check=False).text.strip()
            email = repo.git("config", "user.email", check=False).text.strip()
            op = repo.operation_in_progress()
            return status, branches, remotes, states, f"{name} <{email}>" if name else "", op

        def done(result):
            status, branches, remotes, states, identity, op = result
            self.operation = op
            old_head = self.status.branch.oid if self.status else None
            old_branch = self.branch
            self.status = status
            self.branches = branches
            self.remotes = remotes
            self.remote_states = states
            self.branch = status.branch.head
            self.identity = identity
            self._update_header()
            self._update_remote_menus()
            if full or old_head != status.branch.oid or old_branch != self.branch:
                self.reload_log()
            else:
                self.list_pane.update_working(status)
                if self.selected == "WORKING":
                    if status.is_clean:
                        self.reload_log()   # the row disappears; select the newest commit
                    else:
                        self._loaded_tab = None
                        self.on_tab_changed()
            self.state.refresh_summary(self.path)

        self._bg("refresh", work, done, "Couldn't Read Repository")

    def reload_log(self) -> None:
        q = self._query()
        repo = self.repo
        keep_sha = self.selected if self.selected not in (None, "WORKING") else None
        keep_working = self.selected == "WORKING"
        self.list_pane.loading = True

        def work():
            return repo.log(q)

        def done(commits):
            self.list_pane.loading = False
            self.list_pane.set_commits(commits, self.status, keep_sha=keep_sha,
                                       keep_working=keep_working)

        self._bg("log", work, done, "Couldn't Load History")

    def _load_more(self) -> None:
        if self.list_pane.search_text:
            return
        q = self._query()
        q.skip = self.list_pane.commit_count()
        repo = self.repo
        self.list_pane.loading = True

        def done(commits):
            self.list_pane.loading = False
            self.list_pane.append_commits(commits)

        self._bg("more", lambda: repo.log(q), done)

    def _query(self) -> LogQuery:
        return LogQuery(all_branches=self.state.settings.show_all_branches,
                        text=self.list_pane.search_text, field=self.list_pane.search_field,
                        limit=PAGE)

    def _update_header(self) -> None:
        st = self.status
        name = os.path.basename(self.path)
        self.list_pane.title.set_title(name)
        if st is None:
            return
        if st.branch.head is None and st.branch.oid:
            self.list_pane.set_branch_label(f"Detached at {st.branch.oid[:8]}")
        else:
            self.list_pane.set_branch_label(st.branch.head or "")
        sub = []
        if self.operation:
            sub.append(f"{self.operation.capitalize()} in progress")
        self.list_pane.title.set_subtitle(" · ".join(sub))
        self.list_pane.branch_button.set_menu_model(self._branch_menu())
        act = self.actions.lookup_action("switch-branch")
        act.set_state(GLib.Variant.new_string(st.branch.head or ""))
        report = self.state.last_fetch
        offline, errors = [], {}
        if report is not None:
            errors = {r: m for (p, r), m in report.errors.items() if p == self.path}
            offline = [r for (p, r), k in report.index.items()
                       if p == self.path and k in report.servers and report.servers[k].online is False]
        self.list_pane.set_remotes(self.remote_states, self.branch, offline, errors)
        has_remotes = bool(self.remotes)
        for a in ("fetch-all", "pull", "push"):
            self.actions.lookup_action(a).set_enabled(has_remotes and (a == "fetch-all" or bool(self.branch)))
        self.actions.lookup_action("push-all").set_enabled(len(self.remotes) > 1 and bool(self.branch))

    # -- selection & tabs -----------------------------------------------------------

    def _on_select(self, item: CommitItem | None) -> None:
        if item is None:
            self.selected = None
            self.detail.show_empty()
            return
        self.selected = "WORKING" if item.is_working else item.commit.sha
        self._loaded_tab = None
        self.on_tab_changed()

    def on_tab_changed(self) -> None:
        sel = self.selected
        tab = self.detail.tab
        if sel is None:
            return
        key = (sel, tab)
        if key == self._loaded_tab and sel != "WORKING":
            return
        self._loaded_tab = key
        if sel == "WORKING":
            if tab == "commit":
                self._show_working()
            elif tab == "changes":
                if self.status:
                    self.detail.changes.show_working(self.status)
            else:
                head = self.status.branch.oid if self.status else None
                if head:
                    self._load_browse(head)
                else:
                    self.detail.browse.show_revision("", [])
            return
        if tab == "commit":
            self._load_commit_info(sel)
        elif tab == "changes":
            self._load_changes(sel)
        else:
            self._load_browse(sel)

    def _show_working(self) -> None:
        if self.status is None:
            return
        self.detail.composer.set_status(self.status, self.branch, self.identity)
        self.detail.commit_stack.set_visible_child_name("composer")

    def _load_commit_info(self, sha: str) -> None:
        repo = self.repo

        def work():
            return repo.commit_details(sha), repo.commit_changes(sha)

        def done(result):
            commit, changes = result
            self._commit_cache[sha] = commit
            self.detail.commit_page.show_commit(commit, changes)
            self.detail.commit_stack.set_visible_child_name("commit")

        self._bg("info", work, done, "Couldn't Load Commit")

    def _load_changes(self, sha: str) -> None:
        repo = self.repo
        self._bg("changes", lambda: repo.commit_changes(sha),
                 lambda ch: self.detail.changes.show_commit(sha, ch), "Couldn't Load Changes")

    def _load_browse(self, rev: str, reveal: str | None = None) -> None:
        repo = self.repo
        self._bg("tree", lambda: repo.tree(rev),
                 lambda entries: self.detail.browse.show_revision(rev, entries, reveal),
                 "Couldn't List Files")

    def open_commit(self, sha: str) -> None:
        """Show a commit (from a parent link or file history)."""
        if self.list_pane.select_sha(sha):
            if self.detail.tab == "browse":
                self.detail.set_tab("commit")
            return
        # Not in the loaded list (e.g. far back, or filtered out): show it directly.
        self.list_pane.selection.set_selected(Gtk.INVALID_LIST_POSITION)
        self.selected = sha
        self._loaded_tab = None
        self.detail.set_tab("commit")
        self.on_tab_changed()

    # -- context API used by the pages ---------------------------------------------

    def load_commit_diff(self, sha: str, change: FileChange, cb) -> None:
        repo = self.repo
        self._bg("diff", lambda: repo.commit_diff(sha, change), cb, "Couldn't Load Diff")

    def load_working_diff(self, entry: StatusEntry, staged: bool, cb) -> None:
        repo = self.repo

        def work():
            if staged:
                return repo.staged_diff(entry.path, entry.old_path)
            if entry.conflicted:
                return repo.unstaged_diff(entry.path)
            return repo.unstaged_diff(entry.path, untracked=entry.untracked)

        self._bg("diff", work, cb, "Couldn't Load Diff")

    def _after_change(self) -> None:
        self._loaded_tab = None
        self.refresh()

    def stage(self, paths: list[str]) -> None:
        repo = self.repo
        self._bg("stage", lambda: repo.stage(paths), lambda _r: self._after_change(), "Couldn't Stage")

    def unstage(self, paths: list[str]) -> None:
        repo = self.repo
        self._bg("stage", lambda: repo.unstage(paths), lambda _r: self._after_change(), "Couldn't Unstage")

    def stage_hunk(self, fd: FileDiff, index: int) -> None:
        repo = self.repo
        self._bg("stage", lambda: repo.stage_hunk(fd, index), lambda _r: self._after_change(),
                 "Couldn't Stage Hunk")

    def unstage_hunk(self, fd: FileDiff, index: int) -> None:
        repo = self.repo
        self._bg("stage", lambda: repo.unstage_hunk(fd, index), lambda _r: self._after_change(),
                 "Couldn't Unstage Hunk")

    def discard_hunk(self, fd: FileDiff, index: int) -> None:
        repo = self.repo
        dialogs.confirm(self.window, "Discard This Hunk?",
                        "The change is removed from the file on disk. This can't be undone.",
                        "Discard",
                        lambda: self._bg("stage", lambda: repo.discard_hunk(fd, index),
                                         lambda _r: self._after_change(), "Couldn't Discard"))

    def discard_paths(self, paths: list[str]) -> None:
        if self.status is None:
            return
        entries = [e for e in self.status.entries if e.path in paths]
        untracked = [e.path for e in entries if e.untracked]
        tracked = [e.path for e in entries if not e.untracked]
        repo = self.repo
        names = ", ".join(os.path.basename(p) for p in paths[:3]) + ("…" if len(paths) > 3 else "")
        body = "Your changes to these files will be lost. This can't be undone."
        if untracked:
            body = "Untracked files will be deleted, and changes to other files lost. This can't be undone."
        dialogs.confirm(self.window, f"Discard Changes to {names}?", body, "Discard",
                        lambda: self._bg("stage", lambda: repo.discard(tracked, untracked),
                                         lambda _r: self._after_change(), "Couldn't Discard"))

    def do_commit(self, message: str, amend: bool) -> None:
        repo = self.repo

        def done(_sha):
            self.detail.composer.clear()
            self.selected = None
            self.toast("Commit amended" if amend else "Committed")
            self.refresh(full=True)

        self._bg("commit", lambda: repo.commit(message, amend), done, "Couldn't Commit", busy=True)

    def get_last_message(self, cb) -> None:
        repo = self.repo
        self._bg("lastmsg", repo.last_commit_message, cb)

    def load_history(self, path: str, cb) -> None:
        rev = self.detail.browse.rev or "HEAD"
        repo = self.repo
        self._bg("history", lambda: repo.file_history(path, rev), cb, "Couldn't Load History")

    def load_blame(self, path: str, cb) -> None:
        rev = self.detail.browse.rev or "HEAD"
        repo = self.repo
        self._bg("blame", lambda: repo.blame(path, rev), cb, "Couldn't Load Blame")

    def load_blob(self, path: str, cb) -> None:
        rev = self.detail.browse.rev or "HEAD"
        repo = self.repo
        self._bg("blob", lambda: repo.blob(rev, path), cb, "Couldn't Read File")

    def file_menu(self, path: str, mode: str, staged_side: bool, code: str) -> Gio.Menu:
        m = Gio.Menu()
        v = GLib.Variant.new_string(path)
        if mode == "working":
            s1 = Gio.Menu()
            item = Gio.MenuItem.new("Unstage" if staged_side else "Stage", None)
            item.set_action_and_target_value("repo.file-unstage" if staged_side else "repo.file-stage", v)
            s1.append_item(item)
            if not staged_side:
                item = Gio.MenuItem.new("Discard Changes…", None)
                item.set_action_and_target_value("repo.file-discard", v)
                s1.append_item(item)
            m.append_section(None, s1)
        s2 = Gio.Menu()
        full = os.path.join(self.path, path)
        if os.path.exists(full):
            for text, act in (("Open", "repo.file-open"), ("Open With…", "repo.file-open-with"),
                              ("Show in Files", "repo.file-reveal")):
                item = Gio.MenuItem.new(text, None)
                item.set_action_and_target_value(act, v)
                s2.append_item(item)
        item = Gio.MenuItem.new("Copy Path", None)
        item.set_action_and_target_value("repo.file-copy-path", v)
        s2.append_item(item)
        item = Gio.MenuItem.new("File History", None)
        item.set_action_and_target_value("repo.file-history", v)
        s2.append_item(item)
        m.append_section(None, s2)
        return m

    def browse_menu(self, path: str) -> Gio.Menu:
        return self.file_menu(path, "browse", False, "")

    # -- menus --------------------------------------------------------------------

    def _branch_menu(self) -> Gio.Menu:
        menu = Gio.Menu()
        sec = Gio.Menu()
        for b in self.branches[:40]:
            item = Gio.MenuItem.new(b.name + (f"  {b.track}" if b.track else ""), None)
            item.set_action_and_target_value("repo.switch-branch", GLib.Variant.new_string(b.name))
            sec.append_item(item)
        menu.append_section("Branches", sec)
        ops = Gio.Menu()
        ops.append("New Branch…", "repo.new-branch")
        others = [b for b in self.branches if not b.is_head]
        if others:
            dmenu = Gio.Menu()
            for b in others[:40]:
                item = Gio.MenuItem.new(b.name, None)
                item.set_action_and_target_value("repo.delete-branch", GLib.Variant.new_string(b.name))
                dmenu.append_item(item)
            ops.append_submenu("Delete Branch", dmenu)
        menu.append_section(None, ops)
        return menu

    def _view_menu(self) -> Gio.Menu:
        menu = Gio.Menu()
        s1 = Gio.Menu()
        s1.append("Show All Branches", "repo.all-branches")
        s1.append("Refresh", "repo.refresh")
        menu.append_section(None, s1)
        s2 = Gio.Menu()
        s2.append("Open in Files", "repo.open-files")
        if terminal_available(self.state.settings.terminal):
            s2.append("Open in Terminal", "repo.open-terminal")
        s2.append("Copy Path", "repo.copy-path")
        menu.append_section(None, s2)
        return menu

    def _remote_chip_menu(self, remote: str) -> Gio.Menu:
        v = GLib.Variant.new_string(remote)
        menu = Gio.Menu()
        for text, act in (("Fetch", "repo.fetch"), ("Pull", "repo.pull-from"), ("Push", "repo.push-to"),
                          ("Copy URL", "repo.copy-remote-url")):
            item = Gio.MenuItem.new(f"{text} {remote}" if text != "Copy URL" else text, None)
            item.set_action_and_target_value(act, v)
            menu.append_item(item)
        return menu

    def _update_remote_menus(self) -> None:
        def per_remote(action: str, verb: str, extra: list[tuple[str, str]] | None = None) -> Gio.Menu:
            m = Gio.Menu()
            sec = Gio.Menu()
            for r in self.remotes:
                item = Gio.MenuItem.new(f"{verb} {r.name}", None)
                item.set_action_and_target_value(action, GLib.Variant.new_string(r.name))
                sec.append_item(item)
            m.append_section(None, sec)
            if extra:
                e = Gio.Menu()
                for text, act in extra:
                    e.append(text, act)
                m.append_section(None, e)
            return m

        self.detail.set_remote_menus(
            per_remote("repo.fetch", "Fetch from"),
            per_remote("repo.pull-from", "Pull from"),
            per_remote("repo.push-to", "Push to",
                       [("Push to All Remotes", "repo.push-all")] if len(self.remotes) > 1 else None))

    # -- actions ------------------------------------------------------------------

    def _install_actions(self) -> None:
        def add(name, cb, ptype=None):
            act = Gio.SimpleAction.new(name, GLib.VariantType.new(ptype) if ptype else None)
            act.connect("activate", lambda a, p: cb(p.unpack() if p is not None else None))
            self.actions.add_action(act)
            return act

        add("refresh", lambda _p: self.refresh(full=True))
        add("fetch-all", lambda _p: self.fetch(None))
        add("fetch", lambda r: self.fetch(r), "s")
        add("pull", lambda _p: self.pull(None))
        add("pull-from", lambda r: self.pull(r), "s")
        add("push", lambda _p: self.push([self._default_remote()] if self._default_remote() else []))
        add("push-to", lambda r: self.push([r]), "s")
        add("push-all", lambda _p: self.push([r.name for r in self.remotes]))
        add("new-branch", lambda _p: self.new_branch())
        add("delete-branch", lambda b: self.delete_branch(b), "s")
        add("open-files", lambda _p: open_folder(self.window, self.path))
        add("open-terminal", lambda _p: open_terminal(self.path, self.state.settings.terminal))
        add("copy-path", lambda _p: (copy_text(self.path), self.toast("Path copied")))
        add("copy-remote-url", lambda r: self._copy_remote_url(r), "s")
        add("file-stage", lambda p: self.stage([p]), "s")
        add("file-unstage", lambda p: self.unstage([p]), "s")
        add("file-discard", lambda p: self.discard_paths([p]), "s")
        add("file-open", lambda p: open_file(self.window, os.path.join(self.path, p),
                                             self.state.settings.editor), "s")
        add("file-open-with", lambda p: open_file(self.window, os.path.join(self.path, p), ask=True), "s")
        add("file-reveal", lambda p: reveal_file(self.window, os.path.join(self.path, p)), "s")
        add("file-copy-path", lambda p: (copy_text(os.path.join(self.path, p)), self.toast("Path copied")), "s")
        add("file-history", lambda p: self.show_file_history(p), "s")
        add("search", lambda _p: self.list_pane.toggle_search())
        add("tab", lambda t: self.detail.set_tab(t), "s")

        switch = Gio.SimpleAction.new_stateful("switch-branch", GLib.VariantType.new("s"),
                                               GLib.Variant.new_string(""))
        switch.connect("activate", lambda a, p: self.switch_branch(p.unpack()))
        self.actions.add_action(switch)
        allb = Gio.SimpleAction.new_stateful("all-branches", None,
                                             GLib.Variant.new_boolean(self.state.settings.show_all_branches))
        allb.connect("activate", self._toggle_all_branches)
        self.actions.add_action(allb)

    def _toggle_all_branches(self, action, _p):
        new = not action.get_state().unpack()
        action.set_state(GLib.Variant.new_boolean(new))
        self.state.settings.show_all_branches = new
        self.state.settings.save()
        self.reload_log()

    def _copy_remote_url(self, name: str) -> None:
        r = next((r for r in self.remotes if r.name == name), None)
        if r:
            copy_text(r.fetch_url)
            self.toast(f"Copied URL of {GLib.markup_escape_text(name)}")

    def show_file_history(self, path: str) -> None:
        self.detail.set_tab("browse")
        if self.detail.browse.rev:
            self.detail.browse.reveal(path)
        self.detail.browse.mode_history.set_active(True)

    def _default_remote(self) -> str | None:
        st = self.status
        if st and st.branch.upstream and "/" in st.branch.upstream:
            name = st.branch.upstream.split("/", 1)[0]
            if any(r.name == name for r in self.remotes):
                return name
        return self.remotes[0].name if self.remotes else None

    # -- network operations ---------------------------------------------------------

    def fetch(self, remote: str | None) -> None:
        if remote is None:
            self._set_busy(+1)

            def done(report):
                self._set_busy(-1)
                errs = {r: m for (p, r), m in report.errors.items() if p == self.path}
                offline = [r for (p, r), k in report.index.items()
                           if p == self.path and report.servers[k].online is False]
                if errs:
                    first = next(iter(errs.items()))
                    dialogs.show_error(self.window, f"Couldn't Fetch from {first[0]}",
                                       GitError(["fetch"], 1, first[1]))
                elif offline:
                    self.toast(f"Fetched; offline: {GLib.markup_escape_text(', '.join(offline))}")
                else:
                    self.toast("Fetched all remotes")
                self.refresh(full=True)

            self.state.fetch([self.path], done)
            return
        repo = self.repo
        self._bg("fetch", lambda: repo.fetch(remote),
                 lambda _r: (self.toast(f"Fetched {GLib.markup_escape_text(remote)}"), self.refresh(full=True)),
                 f"Couldn't Fetch from {remote}", busy=True)

    def pull(self, remote: str | None, mode: str = "ff-only") -> None:
        remote = remote or self._default_remote()
        branch = self.branch
        if not remote or not branch:
            return
        repo = self.repo

        def work():
            return repo.pull(remote, branch, mode)

        def done(_out):
            self.toast(f"Pulled from {GLib.markup_escape_text(remote)}")
            self.refresh(full=True)

        def fail(exc):
            self._set_busy(-1)
            if isinstance(exc, GitError) and mode == "ff-only" and \
                    ("not possible to fast-forward" in exc.stderr.lower() or
                     "diverg" in exc.stderr.lower()):
                dialogs.choose(self.window, "Branches Have Diverged",
                               f"Both {branch} and {remote}/{branch} have new commits. "
                               "Merge creates a merge commit; Rebase replays your commits on top.",
                               [("merge", "Merge"), ("rebase", "Rebase")],
                               lambda choice: self.pull(remote, choice), suggested="merge")
                return
            dialogs.show_error(self.window, f"Couldn't Pull from {remote}", exc)
            self.refresh(full=True)

        self._set_busy(+1)
        run_async(work, lambda r: (self._set_busy(-1), done(r)), fail)

    def push(self, remotes: list[str]) -> None:
        branch = self.branch
        if not remotes or not branch:
            return
        repo = self.repo
        st = self.status
        set_upstream = bool(st and not st.branch.upstream and len(remotes) == 1)

        def work():
            results = {}
            for r in remotes:
                try:
                    repo.push(r, branch, set_upstream=set_upstream)
                    results[r] = None
                except GitError as e:
                    results[r] = e
            return results

        def done(results):
            self._set_busy(-1)
            failed = {r: e for r, e in results.items() if e is not None}
            ok = [r for r, e in results.items() if e is None]
            if failed:
                r, e = next(iter(failed.items()))
                title = f"Couldn't Push to {r}" if len(failed) == 1 else \
                    f"Couldn't Push to {', '.join(failed)}"
                dialogs.show_error(self.window, title, e)
            if ok:
                self.toast(f"Pushed {GLib.markup_escape_text(branch)} to {GLib.markup_escape_text(', '.join(ok))}")
            self.refresh()

        self._set_busy(+1)
        run_async(work, done, lambda e: (self._set_busy(-1), dialogs.show_error(self.window, "Push Failed", e)))

    # -- branches -----------------------------------------------------------------

    def switch_branch(self, name: str) -> None:
        if not name or name == self.branch:
            return
        repo = self.repo
        self._bg("switch", lambda: repo.switch(name),
                 lambda _r: (self.toast(f"Switched to {GLib.markup_escape_text(name)}"), self.refresh(full=True)),
                 f"Couldn't Switch to {name}", busy=True)

    def new_branch(self) -> None:
        start = None
        if self.selected not in (None, "WORKING"):
            start = self.selected
        body = "The new branch starts at the selected commit." if start else \
            "The new branch starts at the current commit."
        repo = self.repo

        def create(name: str):
            self._bg("switch", lambda: repo.create_branch(name, start),
                     lambda _r: (self.toast(f"Created {GLib.markup_escape_text(name)}"),
                                 self.refresh(full=True)),
                     "Couldn't Create Branch", busy=True)

        dialogs.prompt(self.window, "New Branch", body, "Create", create,
                       placeholder="feature/my-change",
                       validate=lambda t: " " not in t and not t.startswith("-") and ".." not in t)

    def delete_branch(self, name: str) -> None:
        repo = self.repo

        def do(force: bool):
            def fail_or_force(exc):
                if not force and isinstance(exc, GitError) and "not fully merged" in exc.stderr:
                    dialogs.confirm(self.window, f"{name} Isn't Merged",
                                    "Its commits aren't on any other branch and will be hard to "
                                    "recover. Delete it anyway?", "Delete Anyway", lambda: do(True))
                else:
                    dialogs.show_error(self.window, "Couldn't Delete Branch", exc)

            run_async(lambda: repo.delete_branch(name, force),
                      lambda _r: (self.toast(f"Deleted {GLib.markup_escape_text(name)}"), self.refresh()),
                      fail_or_force)

        dialogs.confirm(self.window, f"Delete Branch {name}?",
                        "The branch label is removed. Commits that are on other branches stay.",
                        "Delete", lambda: do(False))
