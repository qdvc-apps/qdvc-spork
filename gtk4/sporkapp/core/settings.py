"""Settings file: bookmarks, trusted servers and preferences.

The format is shared with the macOS edition and documented in
docs/SETTINGS_FORMAT.md. Unknown keys are preserved when saving.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field

from .certs import file_name_for
from .gitrunner import TrustedServer
from .urls import normalise_server_url

SETTINGS_VERSION = 1


def config_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "qdvc-spork")


def data_dir() -> str:
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "qdvc-spork")


@dataclass
class Bookmark:
    path: str
    name: str | None = None

    @property
    def display_name(self) -> str:
        return self.name or os.path.basename(self.path.rstrip("/")) or self.path


@dataclass
class Server:
    url: str
    certificate: str
    label: str | None = None


@dataclass
class Settings:
    bookmarks: list[Bookmark] = field(default_factory=list)
    servers: list[Server] = field(default_factory=list)
    editor: str = ""                 # command; "" = default app for the file
    terminal: str = ""               # command; "" = auto-detect
    fetch_timeout: int = 180
    show_all_branches: bool = False
    path: str = ""
    _extra: dict = field(default_factory=dict)

    # -- persistence ------------------------------------------------------------

    @classmethod
    def load(cls, path: str | None = None) -> "Settings":
        path = path or os.path.join(config_dir(), "settings.json")
        s = cls(path=path)
        try:
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except FileNotFoundError:
            return s
        except (OSError, ValueError):
            # Keep the broken file for the user; start fresh.
            try:
                shutil.copy2(path, path + ".broken")
            except OSError:
                pass
            return s
        s.bookmarks = [Bookmark(b["path"], b.get("name")) for b in raw.get("bookmarks", [])
                       if isinstance(b, dict) and b.get("path")]
        s.servers = [Server(v["url"], v["certificate"], v.get("label"))
                     for v in raw.get("servers", [])
                     if isinstance(v, dict) and v.get("url") and v.get("certificate")]
        s.editor = raw.get("editor", "") or ""
        s.terminal = raw.get("terminal", "") or ""
        s.fetch_timeout = int(raw.get("fetchTimeout", 180) or 180)
        s.show_all_branches = bool(raw.get("showAllBranches", False))
        known = {"version", "bookmarks", "servers", "editor", "terminal",
                 "fetchTimeout", "showAllBranches"}
        s._extra = {k: v for k, v in raw.items() if k not in known}
        return s

    def to_json(self) -> dict:
        out = dict(self._extra)
        out.update({
            "version": SETTINGS_VERSION,
            "bookmarks": [{"path": b.path, **({"name": b.name} if b.name else {})}
                          for b in self.bookmarks],
            "servers": [{"url": v.url, "certificate": v.certificate,
                         **({"label": v.label} if v.label else {})} for v in self.servers],
            "editor": self.editor,
            "terminal": self.terminal,
            "fetchTimeout": self.fetch_timeout,
            "showAllBranches": self.show_all_branches,
        })
        return out

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".settings-", dir=os.path.dirname(self.path))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self.to_json(), fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.chmod(tmp, 0o644)   # mkstemp creates 0600 files
        os.replace(tmp, self.path)

    # -- bookmarks --------------------------------------------------------------

    def add_bookmark(self, path: str) -> Bookmark:
        path = os.path.realpath(path)
        for b in self.bookmarks:
            if os.path.realpath(b.path) == path:
                return b
        b = Bookmark(path)
        self.bookmarks.append(b)
        return b

    def remove_bookmark(self, path: str) -> None:
        self.bookmarks = [b for b in self.bookmarks if b.path != path]

    def move_bookmark(self, path: str, new_index: int) -> None:
        cur = next((b for b in self.bookmarks if b.path == path), None)
        if cur is None:
            return
        self.bookmarks.remove(cur)
        self.bookmarks.insert(max(0, min(new_index, len(self.bookmarks))), cur)

    # -- servers ----------------------------------------------------------------

    def add_server(self, url: str, cert_source: str, label: str | None = None,
                   certs_dir: str | None = None) -> Server:
        """Copy `cert_source` into Spork's data folder and trust it for `url`."""
        url = normalise_server_url(url)
        certs_dir = certs_dir or os.path.join(data_dir(), "certs")
        os.makedirs(certs_dir, exist_ok=True)
        dest = os.path.join(certs_dir, file_name_for(url))
        if os.path.realpath(cert_source) != os.path.realpath(dest):
            shutil.copyfile(cert_source, dest)
        os.chmod(dest, 0o644)
        self.servers = [v for v in self.servers if v.url != url]
        srv = Server(url, dest, label)
        self.servers.append(srv)
        return srv

    def remove_server(self, url: str, delete_file: bool = True) -> None:
        for v in self.servers:
            if v.url == url and delete_file:
                still_used = any(o.certificate == v.certificate for o in self.servers if o is not v)
                if not still_used:
                    try:
                        os.remove(v.certificate)
                    except OSError:
                        pass
        self.servers = [v for v in self.servers if v.url != url]

    def trusted_servers(self) -> list[TrustedServer]:
        return [TrustedServer(v.url, v.certificate) for v in self.servers
                if os.path.exists(v.certificate)]
