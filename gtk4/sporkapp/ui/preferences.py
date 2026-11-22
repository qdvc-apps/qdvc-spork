"""Preferences: general tools, trusted servers & certificates, the `spork` command."""

from __future__ import annotations

import os
import stat

from gi.repository import Adw, Gio, GLib, Gtk

from ..core import certs
from ..core.gitrunner import GitError
from ..core.repo import Repository
from ..core.urls import normalise_server_url, parse_remote_url
from . import dialogs
from .util import IN_FLATPAK, label, run_async

APP_ID = "qdvc.spork.gtk4app"
ENTRY_SCRIPT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "qdvc_spork.py"))


def spork_command_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".local", "bin", "spork")


def spork_command_script() -> str:
    if IN_FLATPAK:
        launch = f'exec flatpak run {APP_ID} "$top"'
    else:
        import sys
        launch = f'exec "{sys.executable}" "{ENTRY_SCRIPT}" "$top"'
    return f"""#!/bin/sh
# spork — open a Git repository in QDVC Spork.  Usage: spork [path]
# Installed by QDVC Spork (Preferences › Command Line).
target="${{1:-.}}"
if [ ! -e "$target" ]; then
    echo "spork: $target: no such file or directory" >&2
    exit 2
fi
dir=$(cd "$target" 2>/dev/null && pwd -P) || dir=$(cd "$(dirname "$target")" && pwd -P)
top=$dir
if command -v git >/dev/null 2>&1; then
    top=$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null) || {{
        echo "spork: $dir is not inside a Git repository" >&2
        exit 1
    }}
fi
{launch}
"""


def install_spork_command() -> str:
    path = spork_command_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(spork_command_script())
    os.chmod(path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    return path


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self, state, initial_page: str | None = None):
        super().__init__(title="Preferences")
        self.state = state
        self.add(self._general_page())
        self.servers_page = self._servers_page()
        self.add(self.servers_page)
        self.add(self._cli_page())
        if initial_page:
            self.set_visible_page_name(initial_page)
        self.connect("closed", self._on_closed)

    # -- General ------------------------------------------------------------------

    def _general_page(self) -> Adw.PreferencesPage:
        s = self.state.settings
        page = Adw.PreferencesPage(title="General", icon_name="preferences-system-symbolic",
                                   name="general")
        tools = Adw.PreferencesGroup(title="Tools")
        self.editor_row = Adw.EntryRow(title="Editor Command", text=s.editor)
        self.editor_row.set_tooltip_text("Leave empty to open files with their default app")
        tools.add(self.editor_row)
        self.terminal_row = Adw.EntryRow(title="Terminal Command", text=s.terminal)
        self.terminal_row.set_tooltip_text("Leave empty to detect one; {dir} is replaced by the "
                                           "repository folder")
        if IN_FLATPAK:
            self.editor_row.set_sensitive(False)
            self.terminal_row.set_sensitive(False)
            tools.set_description("The Flatpak sandbox opens files with their default app "
                                  "and can't start a terminal.")
        tools.add(self.terminal_row)
        page.add(tools)

        net = Adw.PreferencesGroup(title="Network")
        adj = Gtk.Adjustment(lower=10, upper=3600, step_increment=10, page_increment=60,
                             value=s.fetch_timeout)
        # No live notify::value handler (Cursed Knowledge: Adw.SpinRow crash);
        # the value is read when the dialog closes.
        self.timeout_row = Adw.SpinRow(title="Network Timeout", subtitle="Seconds before a fetch, "
                                       "pull or push is abandoned", adjustment=adj)
        net.add(self.timeout_row)
        page.add(net)

        about = Adw.PreferencesGroup(title="Git")
        ver = self.state.runner.version() or "git not found"
        row = Adw.ActionRow(title="Git Version", subtitle=ver, use_markup=False)
        row.add_css_class("property")
        about.add(row)
        page.add(about)
        return page

    def _on_closed(self, *_a):
        s = self.state.settings
        s.editor = self.editor_row.get_text().strip()
        s.terminal = self.terminal_row.get_text().strip()
        s.fetch_timeout = int(self.timeout_row.get_value())
        self.state.save("servers")

    # -- Servers & certificates ------------------------------------------------------

    def _servers_page(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title="Servers", icon_name="network-server-symbolic",
                                   name="servers")
        intro = Adw.PreferencesGroup(
            title="Self-Signed Certificates",
            description="Add a local git server's cert.pem (never key.pem) and Spork trusts it "
                        "for that server's HTTPS URL. Spork passes the certificate to git itself; "
                        "your ~/.gitconfig and the system trust store are not changed.")
        page.add(intro)
        self.servers_group = Adw.PreferencesGroup(title="Trusted Servers")
        add = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER,
                         tooltip_text="Add Certificate…")
        add.add_css_class("flat")
        add.connect("clicked", lambda *_: self._choose_cert())
        self.servers_group.set_header_suffix(add)
        page.add(self.servers_group)
        self._server_rows: list[Gtk.Widget] = []
        self._fill_servers()
        return page

    def _fill_servers(self) -> None:
        for r in self._server_rows:
            self.servers_group.remove(r)
        self._server_rows = []
        servers = self.state.settings.servers
        if not servers:
            row = Adw.ActionRow(title="No trusted servers",
                                subtitle="Use + to add a server's cert.pem.")
            self.servers_group.add(row)
            self._server_rows.append(row)
            return
        for v in servers:
            sub = v.certificate
            warn = False
            try:
                with open(v.certificate, "rb") as fh:
                    info = certs.load_certificates(fh.read())[0]
                days = info.days_left()
                exp = info.not_after.astimezone().strftime("%d %b %Y")
                state = "expired" if days < 0 else f"expires {exp} ({days} days)"
                warn = days < 30
                sub = f"{', '.join(info.names)} · {state}"
            except (OSError, certs.CertificateError) as e:
                sub = f"Can't read certificate: {e}"
                warn = True
            row = Adw.ActionRow(title=v.url, subtitle=sub, use_markup=False)
            row.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic" if warn else
                                     "channel-secure-symbolic"))
            test = Gtk.Button(label="Test", valign=Gtk.Align.CENTER,
                              tooltip_text="Contact a remote on this server with these settings")
            test.add_css_class("flat")
            test.connect("clicked", lambda b, v=v: self._test_server(v.url, b))
            row.add_suffix(test)
            rm = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                            tooltip_text="Stop Trusting")
            rm.add_css_class("flat")
            rm.connect("clicked", lambda _b, v=v: self._remove_server(v.url))
            row.add_suffix(rm)
            self.servers_group.add(row)
            self._server_rows.append(row)

    def _remove_server(self, url: str) -> None:
        def go():
            self.state.settings.remove_server(url)
            self.state.save("servers")
            self._fill_servers()
        dialogs.confirm(self, "Stop Trusting This Server?",
                        f"Spork will no longer accept the certificate for {url}.", "Remove", go)

    def _test_server(self, url: str, button: Gtk.Button) -> None:
        # Find a bookmarked remote that lives under this URL.
        target = None
        for b in self.state.settings.bookmarks:
            if not os.path.isdir(b.path):
                continue
            try:
                for r in Repository(self.state.runner, b.path).remotes():
                    if certs.server_for_url(r.fetch_url, [(url, "")]):
                        target = (b.path, r.name, f"{b.display_name}: {r.name}")
                        break
            except (GitError, OSError):
                continue
            if target:
                break
        if target is None:
            self.add_toast(Adw.Toast(title="No bookmarked repository has a remote on this server"))
            return
        button.set_sensitive(False)
        path, remote, lbl = target
        repo = Repository(self.state.runner, path)

        def done(_r):
            button.set_sensitive(True)
            self.add_toast(Adw.Toast(title=GLib.markup_escape_text(f"Connected: {lbl}")))

        def fail(exc):
            button.set_sensitive(True)
            dialogs.show_error(self, f"Couldn't Reach {lbl}", exc)

        run_async(lambda: repo.ls_remote_check(remote), done, fail)

    def _choose_cert(self) -> None:
        dlg = Gtk.FileDialog(title="Choose the Server's cert.pem")
        filt = Gtk.FileFilter()
        filt.set_name("Certificates (*.pem, *.crt, *.cer)")
        for pat in ("*.pem", "*.crt", "*.cer"):
            filt.add_pattern(pat)
        filters = Gio.ListStore(item_type=Gtk.FileFilter)
        filters.append(filt)
        dlg.set_filters(filters)
        dlg.set_default_filter(filt)
        root = self.get_root()

        def opened(d, result):
            try:
                f = d.open_finish(result)
            except GLib.Error:
                return
            if f is not None and f.get_path():
                self._review_cert(f.get_path())

        dlg.open(root if isinstance(root, Gtk.Window) else None, None, opened)

    def _review_cert(self, path: str) -> None:
        try:
            with open(path, "rb") as fh:
                infos = certs.load_certificates(fh.read())
        except (OSError, certs.CertificateError) as e:
            dialogs.show_error(self, "Not a Usable Certificate", str(e))
            return
        info = infos[0]
        remote_urls = self.state.remote_urls_by_label()
        suggestions = certs.suggest_server_urls(info, remote_urls)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        facts = Gtk.Grid(column_spacing=12, row_spacing=4)
        rows = [("Covers", ", ".join(info.names) or "(no names)"),
                ("Expires", info.not_after.astimezone().strftime("%d %B %Y") +
                 (f" ({info.days_left()} days)" if info.days_left() >= 0 else " (expired)")),
                ("SHA-256", info.sha256[:47] + "…")]
        for i, (k, v) in enumerate(rows):
            facts.attach(label(k, "dim-label"), 0, i, 1, 1)
            val = label(v, selectable=True, wrap=True)
            facts.attach(val, 1, i, 1, 1)
        box.append(facts)
        if info.is_ca:
            box.append(label("This is a CA certificate. That's fine for git, though Forgejo's "
                             "own cert.pem normally isn't one.", "caption", "dim-label", wrap=True))
        box.append(label("Trust it for:", "heading"))
        checks: list[tuple[Gtk.CheckButton, str]] = []
        for i, sug in enumerate(suggestions):
            text = sug.url
            if sug.remotes:
                text += f"  — used by {', '.join(sug.remotes[:3])}" + \
                        ("…" if len(sug.remotes) > 3 else "")
            cb = Gtk.CheckButton(label=text, active=(i == 0))
            box.append(cb)
            checks.append((cb, sug.url))
        custom = Gtk.Entry(placeholder_text="Another URL, e.g. https://myserver.local:3000/")
        box.append(custom)
        box.append(label("Include the port: git matches the server URL exactly.",
                         "caption", "dim-label", wrap=True))

        dlg = Adw.AlertDialog(heading="Trust This Certificate?",
                              body=f"From {os.path.basename(path)}")
        dlg.set_extra_child(box)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("trust", "Trust")
        dlg.set_response_appearance("trust", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("trust")
        dlg.set_close_response("cancel")

        def respond(_d, response):
            if response != "trust":
                return
            urls = [u for cb, u in checks if cb.get_active()]
            extra = custom.get_text().strip()
            if extra:
                urls.append(normalise_server_url(extra))
            bad = [u for u in urls if parse_remote_url(u).scheme != "https"]
            urls = [u for u in urls if u not in bad]
            for u in urls:
                self.state.settings.add_server(u, path)
            self.state.save("servers")
            self._fill_servers()
            if urls:
                self.add_toast(Adw.Toast(title=GLib.markup_escape_text(
                    f"Trusted for {', '.join(urls)}")))

        dlg.connect("response", respond)
        dlg.present(self)

    # -- Command line ----------------------------------------------------------------

    def _cli_page(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title="Command Line", icon_name="utilities-terminal-symbolic",
                                   name="cli")
        group = Adw.PreferencesGroup(
            title="The spork Command",
            description="Run spork . in a terminal to open the repository you're in.")
        path = spork_command_path()
        self.cli_row = Adw.ActionRow(title="Install spork", subtitle=path.replace(
            os.path.expanduser("~"), "~", 1), use_markup=False)
        btn = Gtk.Button(label="Reinstall" if os.path.exists(path) else "Install",
                         valign=Gtk.Align.CENTER)
        btn.add_css_class("suggested-action" if not os.path.exists(path) else "flat")

        def do_install(_b):
            try:
                install_spork_command()
            except OSError as e:
                dialogs.show_error(self, "Couldn't Install the Command", str(e))
                return
            btn.set_label("Reinstall")
            self.add_toast(Adw.Toast(title="Installed ~/.local/bin/spork"))

        btn.connect("clicked", do_install)
        self.cli_row.add_suffix(btn)
        group.add(self.cli_row)
        on_path = os.path.dirname(path) in os.environ.get("PATH", "").split(":")
        if not on_path and not IN_FLATPAK:
            hint = Adw.ActionRow(title="Add ~/.local/bin to your PATH",
                                 subtitle="Most distributions do this already; if spork isn't "
                                          "found, add it in your shell's startup file.")
            hint.add_prefix(Gtk.Image(icon_name="dialog-information-symbolic"))
            group.add(hint)
        page.add(group)
        return page
