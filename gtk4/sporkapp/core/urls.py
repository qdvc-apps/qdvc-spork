"""Understanding remote URLs: which server (host and port) does each talk to?"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from urllib.parse import urlsplit

DEFAULT_PORTS = {"https": 443, "http": 80, "ssh": 22, "git": 9418}


@dataclass(frozen=True)
class Endpoint:
    scheme: str            # https, http, ssh, git, file
    host: str              # "" for local paths
    port: int | None
    user: str | None
    path: str

    @property
    def is_local(self) -> bool:
        return self.scheme == "file"

    @property
    def effective_port(self) -> int | None:
        return self.port or DEFAULT_PORTS.get(self.scheme)

    @property
    def server_key(self) -> str:
        """Groups remotes that live on the same server."""
        if self.is_local:
            return "local"
        return f"{self.host.lower()}:{self.effective_port}"

    @property
    def https_base(self) -> str | None:
        """The `http.<url>` prefix git uses for this server, port included."""
        if self.scheme not in ("https", "http"):
            return None
        port = f":{self.port}" if self.port else ""
        return f"{self.scheme}://{self.host.lower()}{port}/"


_SCP_RE = re.compile(r"^(?:(?P<user>[^@/]+)@)?(?P<host>[^:/]+):(?P<path>(?!//).*)$")


def parse_remote_url(url: str) -> Endpoint:
    url = url.strip()
    if "://" in url:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme == "file":
            return Endpoint("file", "", None, None, parts.path)
        if scheme.startswith("git+ssh") or scheme.startswith("ssh+git"):
            scheme = "ssh"
        try:
            port = parts.port
        except ValueError:
            port = None
        return Endpoint(scheme, parts.hostname or "", port, parts.username, parts.path)
    # Windows drive letters and plain paths are local
    if re.match(r"^[A-Za-z]:[\\/]", url) or url.startswith("/") or url.startswith(".") \
            or url.startswith("~"):
        return Endpoint("file", "", None, None, url)
    m = _SCP_RE.match(url)
    if m:
        return Endpoint("ssh", m.group("host"), None, m.group("user"), m.group("path"))
    return Endpoint("file", "", None, None, url)


def normalise_server_url(url: str) -> str:
    """Normalise a user-typed server URL to `scheme://host[:port]/`.

    git matches `http.<url>.*` by scheme, host, port and path prefix, so we
    keep the port exactly as typed and always end with a slash.
    """
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        port = None
    path = parts.path or "/"
    if not path.endswith("/"):
        path += "/"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{scheme}://{host}{':' + str(port) if port else ''}{path}"


def resolve_ssh_alias(host: str, timeout: float = 3.0) -> tuple[str, int | None]:
    """Use `ssh -G` to resolve ~/.ssh/config aliases to (hostname, port)."""
    try:
        res = subprocess.run(["ssh", "-G", host], capture_output=True, text=True,
                             timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return host, None
    if res.returncode != 0:
        return host, None
    hostname, port = host, None
    for line in res.stdout.splitlines():
        key, _, value = line.partition(" ")
        if key == "hostname" and value:
            hostname = value
        elif key == "port" and value.isdigit():
            port = int(value)
    return hostname, port
