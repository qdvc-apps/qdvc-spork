"""Dashboard: census-style overview of every bookmarked repository."""

from __future__ import annotations

import datetime as dt
import os
from typing import Callable

from gi.repository import Adw, GLib, Gtk, Pango

from ..core import certs
from ..core.models import Level, RepoSummary
from .util import label, plural, relative_time

LEVEL_ICON = {
    Level.OK: ("emblem-ok-symbolic", "level-ok", "Clean and in sync"),
    Level.ATTENTION: ("dialog-warning-symbolic", "level-attention", "Needs attention"),
    Level.UNKNOWN: ("dialog-question-symbolic", "level-unknown", "Some remotes couldn't be checked"),
    Level.MISSING: ("action-unavailable-symbolic", "level-missing", "Not found"),
}


def _tile(title: str, value: str, css: str | None = None) -> tuple[Gtk.Widget, Gtk.Label]:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
    box.add_css_class("card")
    box.add_css_class("dashboard-tile")
    num = label(value, "big-number")
    if css:
        num.add_css_class(css)
    box.append(num)
    box.append(label(title, "caption", "dim-label"))
    return box, num


class RepoCard(Gtk.Button):
    def __init__(self, s: RepoSummary, display_name: str, on_open: Callable[[str], None]):
        super().__init__()
        self.add_css_class("card")
        self.add_css_class("repo-card")
        self.connect("clicked", lambda *_: on_open(s.path))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        top = Gtk.Box(spacing=8)
        icon_name, css, tip = LEVEL_ICON[s.level]
        icon = Gtk.Image(icon_name=icon_name, tooltip_text=tip)
        icon.add_css_class(css)
        top.append(icon)
        name = label(display_name, "heading", ellipsize=True)
        name.set_hexpand(True)
        top.append(name)
        if s.branch or s.detached:
            br = Gtk.Label(label=s.branch or "detached")
            br.add_css_class("ref-pill")
            br.add_css_class("head" if s.branch else "detached")
            br.set_ellipsize(Pango.EllipsizeMode.END)
            br.set_max_width_chars(18)
            top.append(br)
        box.append(top)
        path = label(s.path.replace(os.path.expanduser("~"), "~", 1), "caption", "dim-label",
                     ellipsize=True)
        path.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        box.append(path)

        if s.remotes:
            rbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=4)
            for st in s.remotes:
                row = Gtk.Box(spacing=6)
                ic = "network-offline-symbolic" if st.remote in s.offline_remotes else \
                    "dialog-warning-symbolic" if st.remote in s.fetch_errors else "network-server-symbolic"
                row.append(Gtk.Image(icon_name=ic, pixel_size=12))
                row.append(label(st.remote, "caption"))
                row.append(Gtk.Box(hexpand=True))
                if st.remote in s.offline_remotes:
                    row.append(label("offline", "caption", "dim-label"))
                elif not st.exists:
                    row.append(label(f"no {s.branch}" if s.branch else "—", "caption", "dim-label"))
                elif not st.ahead and not st.behind:
                    row.append(label("in sync", "caption", "level-ok"))
                else:
                    if st.ahead:
                        row.append(label(f"↑{st.ahead}", "caption", "numeric", "level-attention"))
                    if st.behind:
                        row.append(label(f"↓{st.behind}", "caption", "numeric", "level-attention"))
                rbox.append(row)
            box.append(rbox)
        else:
            box.append(label("No remotes", "caption", "dim-label"))

        if s.reasons:
            reasons = label(" · ".join(s.reasons), "reason", "dim-label", wrap=True)
            reasons.set_lines(3)
            reasons.set_ellipsize(Pango.EllipsizeMode.END)
            box.append(reasons)

        if s.last_commit_time:
            foot = label(f"{relative_time(s.last_commit_time)} — {s.last_commit_subject or ''}",
                         "caption", "dim-label", ellipsize=True)
            foot.set_margin_top(4)
            box.append(foot)
        self.set_child(box)
        self.set_tooltip_text("\n".join(s.reasons) if s.reasons else tip)


class Dashboard(Adw.Bin):
    def __init__(self, state, on_open: Callable[[str], None]):
        super().__init__()
        self.state = state
        self.on_open = on_open
        self.toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar(show_start_title_buttons=False)
        self.title = Adw.WindowTitle(title="Dashboard", subtitle="")
        header.set_title_widget(self.title)
        self.fetch_button = Gtk.Button(label="Fetch All")
        self.fetch_button.add_css_class("suggested-action")
        self.fetch_button.set_action_name("win.fetch-everything")
        self.fetch_button.set_tooltip_text("Check every server, then fetch every reachable remote")
        header.pack_start(self.fetch_button)
        self.spinner = Gtk.Spinner()
        header.pack_start(self.spinner)
        self.toolbar.add_top_bar(header)
        self.banner = Adw.Banner(title="", revealed=False)
        self.toolbar.add_top_bar(self.banner)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24,
                          margin_top=24, margin_bottom=24, margin_start=18, margin_end=18)
        tiles = Gtk.Box(spacing=12, homogeneous=True)
        t1, self.n_total = _tile("Repositories", "0")
        t2, self.n_ok = _tile("Clean & in sync", "0", "level-ok")
        t3, self.n_attn = _tile("Need attention", "0", "level-attention")
        t4, self.n_servers = _tile("Servers offline", "—")
        for t in (t1, t2, t3, t4):
            tiles.append(t)
        content.append(tiles)

        content.append(label("Repositories", "heading"))
        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                min_children_per_line=1, max_children_per_line=3,
                                column_spacing=12, row_spacing=12, valign=Gtk.Align.START)
        content.append(self.flow)

        self.servers_group = Adw.PreferencesGroup(title="Servers",
                                                  description="Checked once each before fetching. "
                                                              "Online means the port accepted a connection.")
        content.append(self.servers_group)
        self.server_rows: list[Gtk.Widget] = []

        clamp = Adw.Clamp(maximum_size=1200, tightening_threshold=900, child=content)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroller.set_child(clamp)
        self.empty = Adw.StatusPage(icon_name="view-grid-symbolic", title="Nothing to Show Yet",
                                    description="Bookmark repositories to see their health here.")
        self.stack = Gtk.Stack()
        self.stack.add_named(scroller, "content")
        self.stack.add_named(self.empty, "empty")
        self.toolbar.set_content(self.stack)
        self.set_child(self.toolbar)

    def update(self) -> None:
        state = self.state
        bookmarks = state.settings.bookmarks
        self.stack.set_visible_child_name("content" if bookmarks else "empty")
        c = state.counts()
        self.n_total.set_text(str(c["total"]))
        self.n_ok.set_text(str(c["ok"]))
        self.n_attn.set_text(str(c["attention"] + c["missing"]))

        child = self.flow.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.flow.remove(child)
            child = nxt
        order = {Level.MISSING: 0, Level.ATTENTION: 1, Level.UNKNOWN: 2, Level.OK: 3}
        items = [(b, state.summaries.get(b.path)) for b in bookmarks]
        items.sort(key=lambda bs: (order[bs[1].level] if bs[1] else 4, bs[0].display_name.lower()))
        for b, s in items:
            if s is None:
                ph = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                ph.add_css_class("card")
                ph.add_css_class("repo-card")
                ph.append(label(b.display_name, "heading"))
                ph.append(label("Checking…", "caption", "dim-label"))
                self.flow.append(ph)
                continue
            self.flow.append(RepoCard(s, b.display_name, self.on_open))

        for r in self.server_rows:
            self.servers_group.remove(r)
        self.server_rows = []
        report = state.last_fetch
        trusted = [(v.url, v.certificate) for v in state.settings.servers]
        if report is None or not report.servers:
            row = Adw.ActionRow(title="Not checked yet",
                                subtitle="Press Fetch All to check every server.")
            self.servers_group.add(row)
            self.server_rows.append(row)
            self.n_servers.set_text("—")
            self.banner.set_revealed(False)
            self.title.set_subtitle("")
        else:
            offline = [s for s in report.servers.values() if s.online is False]
            self.n_servers.set_text(f"{len(offline)}/{len(report.servers)}")
            for srv in sorted(report.servers.values(), key=lambda s: (s.online is not False, s.key)):
                name = srv.host if srv.key.startswith("local:") else \
                    f"{srv.host}:{srv.port}" if srv.port else srv.host
                sub = plural(len(srv.remotes), "remote") + ": " + ", ".join(srv.remotes[:6])
                if srv.online is False:
                    sub = f"Offline — {srv.error or 'no answer'}\n" + sub
                row = Adw.ActionRow(title=name, subtitle=sub, use_markup=False)
                icon = Gtk.Image(icon_name="network-server-symbolic" if srv.online else
                                 "network-offline-symbolic")
                icon.add_css_class("level-ok" if srv.online else "level-missing")
                row.add_prefix(icon)
                match = certs.server_for_url(f"https://{srv.host}:{srv.port}/", trusted) \
                    if srv.port else None
                if match:
                    lock = Gtk.Image(icon_name="channel-secure-symbolic",
                                     tooltip_text=f"Trusted certificate: {match[1]}")
                    row.add_suffix(lock)
                self.servers_group.add(row)
                self.server_rows.append(row)
            if offline:
                self.banner.set_title(GLib.markup_escape_text(
                    f"{plural(len(offline), 'server')} didn't answer; "
                    "their repositories show the last fetched state."))
                self.banner.set_revealed(True)
            else:
                self.banner.set_revealed(False)
            when = dt.datetime.fromtimestamp(report.finished_at).strftime("%H:%M")
            self.title.set_subtitle(f"Last fetched at {when}")

        self._cert_warnings()

    def _cert_warnings(self) -> None:
        soon = []
        for v in self.state.settings.servers:
            try:
                with open(v.certificate, "rb") as fh:
                    info = certs.load_certificates(fh.read())[0]
            except (OSError, certs.CertificateError):
                soon.append(f"{v.url} (certificate file missing)")
                continue
            days = info.days_left()
            if days < 30:
                soon.append(f"{v.url} ({'expired' if days < 0 else f'expires in {days} days'})")
        if soon and not self.banner.get_revealed():
            self.banner.set_title(GLib.markup_escape_text("Certificate attention: " + "; ".join(soon)))
            self.banner.set_revealed(True)

    def set_fetching(self, busy: bool) -> None:
        self.spinner.set_spinning(busy)
        self.fetch_button.set_sensitive(not busy)
        self.fetch_button.set_label("Fetching…" if busy else "Fetch All")
