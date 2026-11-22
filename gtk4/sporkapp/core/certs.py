"""Reading self-signed server certificates (cert.pem) and matching them to remotes.

A Forgejo `cert.pem` made with `forgejo cert --host myserver.local,myserver`
is a self-signed leaf certificate. Spork doesn't touch the system trust store
or ~/.gitconfig: it copies the file into its own data folder and passes
`-c http.<url>.sslCAInfo=<copy>` to every git command it runs.

The X.509 reader below is a deliberately small DER walker: it extracts just
the fields Spork shows (names, validity, CA flag, fingerprint), so there's no
dependency on a crypto library.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import ipaddress
import re
from dataclasses import dataclass, field

from .urls import Endpoint, normalise_server_url, parse_remote_url

OID_CN = bytes([0x55, 0x04, 0x03])
OID_SAN = bytes([0x55, 0x1D, 0x11])
OID_BASIC_CONSTRAINTS = bytes([0x55, 0x1D, 0x13])


class CertificateError(ValueError):
    pass


@dataclass
class CertInfo:
    subject_cn: str | None
    dns_names: list[str]
    ip_addresses: list[str]
    not_before: dt.datetime
    not_after: dt.datetime
    is_ca: bool
    sha256: str           # colon-separated hex

    @property
    def names(self) -> list[str]:
        out = list(self.dns_names) + list(self.ip_addresses)
        if not out and self.subject_cn:
            out.append(self.subject_cn)
        return out

    def days_left(self, now: dt.datetime | None = None) -> int:
        now = now or dt.datetime.now(dt.timezone.utc)
        return (self.not_after - now).days

    def covers(self, host: str) -> bool:
        host = host.lower().strip("[]")
        for name in self.names:
            n = name.lower()
            if n == host:
                return True
            if n.startswith("*.") and "." in host and host.split(".", 1)[1] == n[2:]:
                return True
        return False


# -- PEM / DER ---------------------------------------------------------------

_PEM_RE = re.compile(
    r"-----BEGIN CERTIFICATE-----\s*(.+?)\s*-----END CERTIFICATE-----", re.S)


def pem_blocks(text: str) -> list[bytes]:
    return [base64.b64decode("".join(m.group(1).split())) for m in _PEM_RE.finditer(text)]


def load_certificates(data: bytes) -> list[CertInfo]:
    """Read every certificate in a PEM (or single DER) file."""
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError:
        text = ""
    if "-----BEGIN" in text:
        if "PRIVATE KEY-----" in text and "CERTIFICATE-----" not in text:
            raise CertificateError(
                "This is a private key (key.pem), not a certificate. Use cert.pem "
                "and never copy key.pem off the server.")
        blocks = pem_blocks(text)
        if not blocks:
            raise CertificateError("No certificate found in this file.")
        return [parse_der(b) for b in blocks]
    return [parse_der(data)]


def _read_tlv(buf: bytes, pos: int) -> tuple[int, int, int, int]:
    """Returns (tag, content_start, content_end, next_pos)."""
    if pos + 2 > len(buf):
        raise CertificateError("Truncated certificate")
    tag = buf[pos]
    length = buf[pos + 1]
    pos += 2
    if length & 0x80:
        n = length & 0x7F
        if n == 0 or n > 4 or pos + n > len(buf):
            raise CertificateError("Unsupported DER length")
        length = int.from_bytes(buf[pos:pos + n], "big")
        pos += n
    end = pos + length
    if end > len(buf):
        raise CertificateError("Truncated certificate")
    return tag, pos, end, end


def _children(buf: bytes, start: int, end: int) -> list[tuple[int, int, int]]:
    out = []
    pos = start
    while pos < end:
        tag, cs, ce, pos = _read_tlv(buf, pos)
        out.append((tag, cs, ce))
    return out


def _parse_time(tag: int, raw: bytes) -> dt.datetime:
    s = raw.decode("ascii").rstrip("Z")
    if tag == 0x17:  # UTCTime YYMMDDHHMM[SS]
        year = int(s[:2])
        year += 2000 if year < 50 else 1900
        rest = s[2:]
    elif tag == 0x18:  # GeneralizedTime YYYYMMDDHHMM[SS]
        year = int(s[:4])
        rest = s[4:]
    else:
        raise CertificateError("Unknown time format")
    rest = rest.split(".")[0]
    month, day, hour, minute = int(rest[0:2]), int(rest[2:4]), int(rest[4:6]), int(rest[6:8])
    sec = int(rest[8:10]) if len(rest) >= 10 else 0
    return dt.datetime(year, month, day, hour, minute, sec, tzinfo=dt.timezone.utc)


def _find_cn(buf: bytes, start: int, end: int) -> str | None:
    # Name ::= SEQUENCE OF SET OF SEQUENCE { type OID, value ANY }
    for _, ss, se in _children(buf, start, end):
        for _, as_, ae in _children(buf, ss, se):
            parts = _children(buf, as_, ae)
            if len(parts) >= 2 and parts[0][0] == 0x06 and buf[parts[0][1]:parts[0][2]] == OID_CN:
                return buf[parts[1][1]:parts[1][2]].decode("utf-8", errors="replace")
    return None


def parse_der(der: bytes) -> CertInfo:
    tag, cs, ce, _ = _read_tlv(der, 0)
    if tag != 0x30:
        raise CertificateError("Not a DER certificate")
    top = _children(der, cs, ce)
    if not top or top[0][0] != 0x30:
        raise CertificateError("Not a DER certificate")
    tbs = _children(der, top[0][1], top[0][2])
    i = 0
    if tbs and tbs[0][0] == 0xA0:   # [0] version
        i += 1
    # serial, signature algorithm, issuer, validity, subject, spki
    if len(tbs) < i + 6:
        raise CertificateError("Malformed certificate")
    validity = _children(der, tbs[i + 3][1], tbs[i + 3][2])
    not_before = _parse_time(validity[0][0], der[validity[0][1]:validity[0][2]])
    not_after = _parse_time(validity[1][0], der[validity[1][1]:validity[1][2]])
    subject_cn = _find_cn(der, tbs[i + 4][1], tbs[i + 4][2])

    dns: list[str] = []
    ips: list[str] = []
    is_ca = False
    for t, s, e in tbs[i + 6:]:
        if t != 0xA3:   # [3] extensions
            continue
        exts_seq = _children(der, s, e)
        if not exts_seq:
            continue
        for _, xs, xe in _children(der, exts_seq[0][1], exts_seq[0][2]):
            parts = _children(der, xs, xe)
            oid = der[parts[0][1]:parts[0][2]]
            value = parts[-1]     # OCTET STRING (critical flag may sit between)
            if oid == OID_SAN:
                inner = _children(der, value[1], value[2])
                if inner:
                    for gt, gs, ge in _children(der, inner[0][1], inner[0][2]):
                        if gt == 0x82:
                            dns.append(der[gs:ge].decode("ascii", errors="replace"))
                        elif gt == 0x87:
                            try:
                                ips.append(str(ipaddress.ip_address(der[gs:ge])))
                            except ValueError:
                                pass
            elif oid == OID_BASIC_CONSTRAINTS:
                inner = _children(der, value[1], value[2])
                if inner:
                    bc = _children(der, inner[0][1], inner[0][2])
                    if bc and bc[0][0] == 0x01:
                        is_ca = der[bc[0][1]:bc[0][2]] != b"\x00"
    digest = hashlib.sha256(der).hexdigest().upper()
    fp = ":".join(digest[k:k + 2] for k in range(0, len(digest), 2))
    return CertInfo(subject_cn, dns, ips, not_before, not_after, is_ca, fp)


# -- matching certificates to remotes -----------------------------------------

@dataclass
class UrlSuggestion:
    url: str
    remotes: list[str] = field(default_factory=list)   # "repo: remote" labels


def suggest_server_urls(cert: CertInfo, remote_urls: dict[str, str]) -> list[UrlSuggestion]:
    """Which `https://host:port/` prefixes should this certificate apply to?

    `remote_urls` maps a label (e.g. "dotfiles: nas") to the remote's URL.
    HTTPS remotes whose host the certificate covers come first; otherwise one
    suggestion per certificate name.
    """
    found: dict[str, UrlSuggestion] = {}
    for label, url in remote_urls.items():
        ep: Endpoint = parse_remote_url(url)
        base = ep.https_base
        if base and ep.scheme == "https" and cert.covers(ep.host):
            found.setdefault(base, UrlSuggestion(base)).remotes.append(label)
    if found:
        return sorted(found.values(), key=lambda s: (-len(s.remotes), s.url))
    return [UrlSuggestion(normalise_server_url(f"https://{n}/")) for n in cert.names]


def server_for_url(url: str, servers: list[tuple[str, str]]) -> tuple[str, str] | None:
    """The (server_url, cert_path) entry git would apply to `url`, if any."""
    ep = parse_remote_url(url)
    best = None
    for srv_url, path in servers:
        s = parse_remote_url(srv_url)
        if s.scheme != ep.scheme or s.host.lower() != ep.host.lower():
            continue
        if s.effective_port != ep.effective_port:
            continue
        if not (ep.path or "/").startswith(s.path or "/"):
            continue
        if best is None or len(srv_url) > len(best[0]):
            best = (srv_url, path)
    return best


def file_name_for(url: str) -> str:
    """A safe file name for the stored copy of a server's certificate."""
    ep = parse_remote_url(normalise_server_url(url))
    host = re.sub(r"[^A-Za-z0-9._-]", "_", ep.host or "server")
    port = ep.effective_port or 443
    return f"{host}-{port}.pem"
